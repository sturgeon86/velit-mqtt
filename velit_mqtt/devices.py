"""Device managers: BLE polling, state mapping, and commands.

Each device owns a BLE client and runs an asyncio poll loop. On every cycle it
queries the device, parses the response into a normalised public state dict, and
invokes an async ``on_state`` callback (the MQTT bridge publishes it). Commands
are coroutine methods that write to the device and then wake the loop for an
immediate refresh so published state reflects the change within seconds.

This replaces the Home Assistant DataUpdateCoordinator + entity platforms. The
parse logic and command codes are ported from that integration; the public
state schema is documented in build_state() of each subclass.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from . import const
from .ble import VelitACBleClient, VelitHeaterBleClient
from .temperature import (
    UNIT_CELSIUS,
    UNIT_FAHRENHEIT,
    celsius_to_native,
    decode_sensor_temp,
    detect_unit,
    native_to_celsius,
)

_LOGGER = logging.getLogger(__name__)

_ACTIVE_POLL_INTERVAL = 5  # seconds — used during transitions / after commands
# Consecutive poll failures tolerated before the device is marked offline. A
# single BLE notification timeout is common and should not flap availability.
_POLL_FAILURE_TOLERANCE = 2
# Number of fast (5s) poll cycles to run after any command so the result of the
# command is reflected quickly rather than after a full poll interval.
_POST_COMMAND_FAST_POLLS = 6

StateCallback = Callable[["VelitDevice"], Awaitable[None]]
AvailabilityCallback = Callable[["VelitDevice", bool], Awaitable[None]]


class VelitDevice:
    """Base device manager shared by the heater and AC implementations."""

    device_type: str = ""

    def __init__(
        self,
        name: str,
        address: str,
        node_id: str,
        poll_interval: int,
        fallback_unit: str = UNIT_CELSIUS,
    ) -> None:
        self.name = name
        self.address = address
        self.node_id = node_id
        self._configured_interval = poll_interval
        self._interval = poll_interval
        self._fallback_unit = fallback_unit

        # Detected on first connect — preserved for the device's lifetime.
        self.temp_unit = UNIT_CELSIUS
        self._unit_detected = False
        self.firmware: str | None = None

        # Latest parsed state. ``state`` is the public dict published to MQTT.
        self._last_raw: dict | None = None
        self.state: dict[str, Any] = {}

        # Publish hooks, wired up by the bridge.
        self.on_state: StateCallback | None = None
        self.on_availability: AvailabilityCallback | None = None

        # Poll loop control.
        self._running = False
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._consecutive_failures = 0
        self._available = False
        self._post_command_fast_polls = 0
        # Set when the user deliberately releases the BLE connection so the
        # device is free for the Velit mobile app. While released, the loop does
        # not auto-reconnect (until ble_connect() is called).
        self._ble_released = False
        # Backoff for connection attempts when the device is unreachable.
        self._connect_backoff = 0

        # Subclasses assign their BLE client.
        self._client: VelitHeaterBleClient | VelitACBleClient

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the poll loop.

        The loop establishes (and re-establishes) the BLE connection itself, so
        startup never fails just because the device is momentarily out of range.
        """
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the poll loop and disconnect."""
        self._running = False
        self._wake.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.disconnect()

    @property
    def connected(self) -> bool:
        return self._client.connected

    async def ble_connect(self) -> None:
        """Re-acquire the BLE connection (clears a prior manual release)."""
        self._ble_released = False
        self._connect_backoff = 0
        if not self._client.connected:
            try:
                await self._client.connect()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Reconnect failed for %s: %s", self.name, exc)
        self._trigger_fast_refresh()

    async def ble_disconnect(self) -> None:
        """Release the BLE connection so another app (e.g. the Velit app) can pair."""
        self._ble_released = True
        await self._client.disconnect()
        await self._set_available(False)
        await self._emit()

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            # While deliberately released, idle until ble_connect() wakes us.
            if self._ble_released:
                await self._sleep(self._configured_interval)
                continue
            if not self._client.connected and not await self._ensure_connected():
                # Connection failed — back off (capped) before retrying.
                await self._sleep(min(_ACTIVE_POLL_INTERVAL * (self._connect_backoff or 1), 60))
                continue
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — never let the loop die
                _LOGGER.exception("Unexpected error in poll loop for %s", self.name)
            await self._sleep(self._interval)

    async def _ensure_connected(self) -> bool:
        """Attempt to (re)connect. Returns True on success."""
        try:
            await self._client.connect()
            self._connect_backoff = 0
            return True
        except Exception as exc:  # noqa: BLE001
            self._connect_backoff = min(self._connect_backoff + 1, 12)
            _LOGGER.warning("Connect failed for %s: %s", self.name, exc)
            await self._set_available(False)
            return False

    async def _sleep(self, timeout: float) -> None:
        """Sleep until the timeout elapses or a command wakes the loop early."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def _poll_once(self) -> None:
        try:
            raw = await self._poll_raw()
        except Exception as exc:  # noqa: BLE001
            self._consecutive_failures += 1
            if (
                self._consecutive_failures < _POLL_FAILURE_TOLERANCE
                and self._last_raw is not None
            ):
                _LOGGER.debug(
                    "Poll failed for %s (%d/%d), keeping last state: %s",
                    self.name, self._consecutive_failures, _POLL_FAILURE_TOLERANCE, exc,
                )
                return
            _LOGGER.warning("Poll failed for %s: %s", self.name, exc)
            await self._set_available(False)
            return

        self._consecutive_failures = 0
        self._last_raw = raw
        await self._after_poll(raw)
        self._adjust_interval(raw)
        await self._set_available(True)
        await self._emit()

    def _adjust_interval(self, raw: dict) -> None:
        """Use the fast interval during active transitions / the post-command window."""
        if self._is_active(raw) or self._post_command_fast_polls > 0:
            self._interval = _ACTIVE_POLL_INTERVAL
            if self._post_command_fast_polls > 0:
                self._post_command_fast_polls -= 1
        else:
            self._interval = self._configured_interval

    def _trigger_fast_refresh(self) -> None:
        """Arm fast polling and wake the loop for an immediate refresh."""
        self._post_command_fast_polls = _POST_COMMAND_FAST_POLLS
        self._interval = _ACTIVE_POLL_INTERVAL
        self._wake.set()

    async def _emit(self) -> None:
        self.state = self.build_state()
        if self.on_state is not None:
            await self.on_state(self)

    async def _set_available(self, available: bool) -> None:
        if available != self._available:
            self._available = available
            _LOGGER.info(
                "%s is now %s", self.name, "available" if available else "unavailable"
            )
            if self.on_availability is not None:
                await self.on_availability(self, available)

    # ------------------------------------------------------------------
    # Hooks / abstract methods
    # ------------------------------------------------------------------

    async def _poll_raw(self) -> dict:
        """Query the device and return the internal parsed dict. Raise on failure."""
        raise NotImplementedError

    async def _after_poll(self, raw: dict) -> None:
        """Optional per-cycle bookkeeping after a successful poll."""

    def _is_active(self, raw: dict) -> bool:
        """Return True when the device is mid-transition and should poll fast."""
        return False

    def build_state(self) -> dict[str, Any]:
        """Map the latest internal data to the public MQTT state dict."""
        raise NotImplementedError


class VelitHeaterDevice(VelitDevice):
    """Velit heater manager (protocol V1.02).

    Public state schema published to ``<base>/<node>/state``:
      power               "on" | "off"
      mode                "heat" | "off"
      preset              "Auto" | "Manual"
      action              "off" | "heating" | "fan" | "idle"
      target_temperature  float  — setpoint in °C
      current_temperature float  — inlet temperature in °C (or null)
      fan                 "1".."5"  — current gear
      machine_state       str
      fault_code          int
      fault               str    — human-readable fault description
      fault_active        bool
      altitude            int | null   — in device unit (m or ft)
      voltage             float | null
      fan_rpm             int | null
      heater_power_w      int
      fuel_pump_hz        float
      temp_unit           "C" | "F"
      firmware            str | null
      priming             bool
      prime_remaining     int    — seconds left in the prime cycle
      cleaning            bool
    """

    device_type = const.DEVICE_TYPE_HEATER

    def __init__(self, name, address, node_id, poll_interval, fallback_unit=UNIT_CELSIUS):
        super().__init__(name, address, node_id, poll_interval, fallback_unit)
        self._client = VelitHeaterBleClient(address)
        # Prime cycle state.
        self.priming = False
        self.prime_remaining = 0
        self._prime_task: asyncio.Task | None = None
        # Cleaning cycle tracking.
        self.cleaning = False
        self._cleaning_seen_active = False
        self._cleaning_timeout_polls = 0

    # -- polling --------------------------------------------------------

    async def _poll_raw(self) -> dict:
        q1 = await self._client.send_command(const.HEATER_FUNC_QUERY1, bytes([0x00]))
        if q1 is None:
            raise RuntimeError("No response to Query 1 (0x0A)")
        q2 = await self._client.send_command(const.HEATER_FUNC_QUERY2, bytes([0x00]))
        if q2 is None:
            raise RuntimeError("No response to Query 2 (0x0B)")
        return self._parse(q1["data"], q2["data"])

    def _parse(self, q1: bytes, q2: bytes) -> dict:
        """Parse Query 1 and Query 2 payloads into the internal data dict."""
        # Query 1: [fault][work_mode][gear][set_temp][machine_state][power][pump_freq]
        fault_code = q1[0]
        work_mode = q1[1]
        current_gear = q1[2]
        set_temp_raw = q1[3]
        machine_state = q1[4]
        heater_power_w = q1[5]
        fuel_pump_hz = q1[6] / 10.0

        if not self._unit_detected:
            self.temp_unit = detect_unit(set_temp_raw, self._fallback_unit)
            self._unit_detected = True
            _LOGGER.debug("Heater %s: detected temp unit %s", self.address, self.temp_unit)

        # Query 2: [fault][voltage 2B][fan_rpm 2B][inlet 2B][casing 2B][outlet 2B][alt 2B]
        def u16(data: bytes, offset: int) -> int:
            return (data[offset] << 8) | data[offset + 1]

        voltage_raw = u16(q2, 1)  # offset 0 = fault byte, skip
        fan_raw = u16(q2, 3)
        inlet_native = decode_sensor_temp(u16(q2, 5), self.temp_unit)
        casing_native = decode_sensor_temp(u16(q2, 7), self.temp_unit)
        outlet_native = decode_sensor_temp(u16(q2, 9), self.temp_unit)
        alt_raw = u16(q2, 11)

        return {
            "fault_code": fault_code,
            "fault_name": const.HEATER_FAULT_CODES.get(fault_code, f"Unknown ({fault_code})"),
            "work_mode": work_mode,
            "current_gear": current_gear,
            "set_temp_c": native_to_celsius(set_temp_raw, self.temp_unit),
            "machine_state": machine_state,
            "machine_state_str": const.HEATER_MACHINE_STATES.get(
                machine_state, f"Unknown ({machine_state})"
            ),
            "heater_power_w": heater_power_w,
            "fuel_pump_hz": fuel_pump_hz,
            "voltage_v": voltage_raw / 10.0 if voltage_raw != 0xFFFF else None,
            "fan_rpm": fan_raw if fan_raw != 0xFFFF else None,
            "inlet_temp_c": native_to_celsius(inlet_native, self.temp_unit)
            if inlet_native is not None else None,
            "casing_temp_c": native_to_celsius(casing_native, self.temp_unit)
            if casing_native is not None else None,
            "outlet_temp_c": native_to_celsius(outlet_native, self.temp_unit)
            if outlet_native is not None else None,
            "altitude": self._validate_altitude(alt_raw),
        }

    def _validate_altitude(self, raw: int) -> int | None:
        if raw == 0xFFFF:
            return None
        ceiling = (
            const.MAX_ALTITUDE_FT
            if self.temp_unit == UNIT_FAHRENHEIT
            else const.MAX_ALTITUDE_M
        )
        return raw if raw <= ceiling else None

    def _is_active(self, raw: dict) -> bool:
        return (
            self.cleaning
            or raw.get("machine_state", 0) in const.HEATER_ACTIVE_MACHINE_STATES
        )

    async def _after_poll(self, raw: dict) -> None:
        if self.firmware is None:
            await self._query_firmware()
        if self.cleaning:
            self._track_cleaning(raw.get("machine_state", 0))

    async def _query_firmware(self) -> None:
        """Query firmware version via func 0x6A and cache it.

        Response payload bytes [14:16] encode the version as a big-endian uint16
        decimal integer: e.g. 0x0139 = 313 → "3.13".
        """
        try:
            rsp = await self._client.send_command(const.HEATER_FUNC_FIRMWARE, bytes([0x01]))
            if rsp is None or len(rsp.get("data", b"")) < 16:
                _LOGGER.warning("Firmware query (0x6A) returned no usable response")
                return
            val = int.from_bytes(rsp["data"][14:16], "big")
            self.firmware = f"{val // 100}.{val % 100:02d}"
            _LOGGER.debug("Heater %s: firmware %s", self.address, self.firmware)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Firmware query (0x6A) failed: %s", exc)

    def _track_cleaning(self, machine_state: int) -> None:
        """Clear the cleaning flag once a started cycle returns to Standby."""
        if machine_state != 0:
            self._cleaning_seen_active = True
            self._cleaning_timeout_polls = 0
        elif self._cleaning_seen_active:
            self.cleaning = False
            self._cleaning_seen_active = False
            self._cleaning_timeout_polls = 0
        else:
            self._cleaning_timeout_polls += 1
            if self._cleaning_timeout_polls >= 6:
                # ~30s at the 5s active rate with no transition — not accepted.
                _LOGGER.warning("Cleaning command not confirmed by device — clearing")
                self.cleaning = False
                self._cleaning_seen_active = False
                self._cleaning_timeout_polls = 0

    # -- state mapping --------------------------------------------------

    def build_state(self) -> dict[str, Any]:
        raw = self._last_raw or {}
        machine_state = raw.get("machine_state", 0)
        fault_code = raw.get("fault_code", 0)

        if machine_state in (0, 2):
            mode = const.HVAC_MODE_OFF
        else:
            mode = const.HVAC_MODE_HEAT

        if fault_code != 0 or machine_state == 0:
            action = const.HVAC_ACTION_OFF
        elif machine_state == 1:
            action = const.HVAC_ACTION_HEATING
        elif machine_state == 2:
            # Fan running to cool the combustion chamber after shutdown.
            action = const.HVAC_ACTION_FAN
        else:
            action = const.HVAC_ACTION_IDLE

        preset = (
            const.HEATER_PRESET_AUTO
            if raw.get("work_mode") == const.HEATER_MODE_THERMOSTAT
            else const.HEATER_PRESET_MANUAL
        )

        return {
            "power": "on" if mode == const.HVAC_MODE_HEAT else "off",
            "mode": mode,
            "preset": preset,
            "action": action,
            "target_temperature": raw.get("set_temp_c"),
            "current_temperature": raw.get("inlet_temp_c"),
            "fan": str(raw.get("current_gear")) if raw.get("current_gear") is not None else None,
            "machine_state": raw.get("machine_state_str"),
            "fault_code": fault_code,
            "fault": raw.get("fault_name"),
            "fault_active": fault_code != 0,
            "altitude": raw.get("altitude"),
            "voltage": raw.get("voltage_v"),
            "fan_rpm": raw.get("fan_rpm"),
            "heater_power_w": raw.get("heater_power_w"),
            "fuel_pump_hz": raw.get("fuel_pump_hz"),
            "temp_unit": self.temp_unit,
            "firmware": self.firmware,
            "priming": self.priming,
            "prime_remaining": self.prime_remaining,
            "cleaning": self.cleaning,
        }

    # -- commands -------------------------------------------------------

    async def set_mode(self, mode: str) -> None:
        """Set HVAC mode: 'off' or 'heat'."""
        mode = mode.lower()
        if mode == const.HVAC_MODE_OFF:
            await self._client.send_command(const.HEATER_FUNC_POWER_OFF, bytes([0x00]))
        elif mode == const.HVAC_MODE_HEAT:
            # Power-on byte depends on the current work mode (Auto vs Manual).
            auto = self.state.get("preset", const.HEATER_PRESET_AUTO) == const.HEATER_PRESET_AUTO
            await self._client.send_command(
                const.HEATER_FUNC_POWER_ON, bytes([0x02 if auto else 0x01])
            )
        else:
            _LOGGER.warning("Heater %s: unknown mode %r", self.name, mode)
            return
        self._trigger_fast_refresh()

    async def set_preset(self, preset: str) -> None:
        """Set work mode preset: 'Auto' (thermostat) or 'Manual'."""
        auto = preset == const.HEATER_PRESET_AUTO
        await self._client.send_command(
            const.HEATER_FUNC_SET_WORK_MODE, bytes([0x02 if auto else 0x01])
        )
        self._trigger_fast_refresh()

    async def set_fan(self, fan: str) -> None:
        """Set gear/fan level 1–5."""
        try:
            level = int(fan)
        except (TypeError, ValueError):
            _LOGGER.warning("Heater %s: invalid fan value %r", self.name, fan)
            return
        await self._client.send_command(const.HEATER_FUNC_SET_GEAR, bytes([level]))
        self._trigger_fast_refresh()

    async def set_temperature(self, temp_c: float) -> None:
        """Set target temperature, given in °C; sent in the device's active unit."""
        value = celsius_to_native(temp_c, self.temp_unit)
        await self._client.send_command(const.HEATER_FUNC_SET_TEMP, bytes([value]))
        self._trigger_fast_refresh()

    async def prime_on(self) -> None:
        """Start the fuel pump prime cycle (auto-stops after 30s)."""
        if self.priming:
            return
        await self._client.send_command(const.HEATER_FUNC_PRIME_START, bytes([0x00]))
        self.priming = True
        self.prime_remaining = const.HEATER_PRIME_DURATION_S
        self._prime_task = asyncio.create_task(self._run_prime())
        await self._emit()
        self._trigger_fast_refresh()

    async def prime_off(self) -> None:
        """Stop the prime cycle early (the task sends the stop command)."""
        if self.priming and self._prime_task and not self._prime_task.done():
            self._prime_task.cancel()

    async def _run_prime(self) -> None:
        """Count down, re-publishing each second; send stop on completion/cancel."""
        try:
            while self.prime_remaining > 0:
                await asyncio.sleep(1)
                self.prime_remaining -= 1
                await self._emit()
            await self._client.send_command(const.HEATER_FUNC_PRIME_STOP, bytes([0x00]))
            _LOGGER.debug("Fuel pump prime auto-stopped after %ds", const.HEATER_PRIME_DURATION_S)
        except asyncio.CancelledError:
            await self._client.send_command(const.HEATER_FUNC_PRIME_STOP, bytes([0x00]))
            _LOGGER.debug("Fuel pump prime stopped early")
        finally:
            self.priming = False
            self.prime_remaining = 0
            self._prime_task = None
            await self._emit()
            self._trigger_fast_refresh()

    async def cleaning_on(self) -> None:
        """Start the residual fuel cleaning cycle."""
        if self.cleaning:
            return
        await self._client.send_command(const.HEATER_FUNC_CLEANING, bytes([0x00]))
        self.cleaning = True
        self._cleaning_seen_active = False
        self._cleaning_timeout_polls = 0
        await self._emit()
        self._trigger_fast_refresh()


class VelitACDevice(VelitDevice):
    """Velit air conditioner manager (protocol V1.01).

    Public state schema published to ``<base>/<node>/state``:
      power               "on" | "off"
      mode                "off" | "cool" | "fan_only"
      preset              "Cooling" | "Eco" | "Sleep" | "Turbo" | null
      action              "off" | "cooling" | "fan" | "idle"
      target_temperature  float  — setpoint in °C
      current_temperature float  — inlet temperature in °C (or null)
      fan                 "1".."5"
      fault_code          int
      fault               str
      temp_unit           "C" | "F"
    """

    device_type = const.DEVICE_TYPE_AC

    # Protocol mode code -> public HVAC mode.
    _MODE_TO_HVAC = {
        const.AC_MODE_COOL: const.HVAC_MODE_COOL,
        const.AC_MODE_FAN: const.HVAC_MODE_FAN_ONLY,
        const.AC_MODE_VENT: const.HVAC_MODE_FAN_ONLY,  # functional difference unconfirmed
    }
    # Preset mode codes — these modify cool mode rather than replacing it.
    _PRESET_CODES = {
        const.AC_MODE_ENERGY_SAVING: const.AC_PRESET_ENERGY_SAVING,
        const.AC_MODE_SLEEP: const.AC_PRESET_SLEEP,
        const.AC_MODE_TURBO: const.AC_PRESET_TURBO,
    }
    _HVAC_TO_MODE = {
        const.HVAC_MODE_COOL: const.AC_MODE_COOL,
        const.HVAC_MODE_FAN_ONLY: const.AC_MODE_FAN,
    }
    _PRESET_TO_CODE = {
        const.AC_PRESET_ENERGY_SAVING: const.AC_MODE_ENERGY_SAVING,
        const.AC_PRESET_SLEEP: const.AC_MODE_SLEEP,
        const.AC_PRESET_TURBO: const.AC_MODE_TURBO,
    }

    def __init__(self, name, address, node_id, poll_interval, fallback_unit=UNIT_CELSIUS):
        super().__init__(name, address, node_id, poll_interval, fallback_unit)
        self._client = VelitACBleClient(address)
        # Last non-preset base mode, for restoring when a preset is cleared and
        # for resolving preset codes back to a displayed mode.
        self._last_base_mode = const.HVAC_MODE_COOL

    async def _poll_raw(self) -> dict:
        power = await self._client.send_command(const.AC_FUNC_POWER, bytes([0x00]))
        if power is None:
            raise RuntimeError("No response to power query (0x01)")
        mode = await self._client.send_command(const.AC_FUNC_MODE, bytes([0x00]))
        if mode is None:
            raise RuntimeError("No response to mode query (0x02)")
        temp = await self._client.send_command(const.AC_FUNC_TEMP, bytes([0x00]))
        if temp is None:
            raise RuntimeError("No response to temperature query (0x03)")
        fan = await self._client.send_command(const.AC_FUNC_FAN, bytes([0x00]))
        if fan is None:
            raise RuntimeError("No response to fan speed query (0x04)")

        # Inlet temp and fault may not respond while powered off — do not raise.
        inlet = await self._client.send_command(const.AC_FUNC_INLET, bytes([0x00]))
        fault = await self._client.send_command(const.AC_FUNC_FAULT, bytes([0x00]))

        set_temp_raw = temp["data"][0]
        if not self._unit_detected:
            self.temp_unit = detect_unit(set_temp_raw, self._fallback_unit)
            self._unit_detected = True
            _LOGGER.debug("AC %s: detected temp unit %s", self.address, self.temp_unit)

        inlet_temp_c = (
            float(inlet["data"][0]) if inlet is not None and inlet["data"] else None
        )
        fault_code = fault["data"][0] if fault is not None and fault["data"] else 0

        return {
            "power": power["data"][0],
            "mode": mode["data"][0],
            "set_temp_c": native_to_celsius(set_temp_raw, self.temp_unit),
            "fan_speed": fan["data"][0],
            "inlet_temp_c": inlet_temp_c,
            "fault_code": fault_code,
            "fault_name": "No Fault" if fault_code == 0 else f"Unknown ({fault_code})",
        }

    def build_state(self) -> dict[str, Any]:
        raw = self._last_raw or {}
        fault_code = raw.get("fault_code", 0)
        power_on = raw.get("power") == const.AC_POWER_ON

        if not power_on:
            mode = const.HVAC_MODE_OFF
            preset = None
        else:
            code = raw.get("mode")
            if code in self._PRESET_CODES:
                mode = self._last_base_mode
                preset = self._PRESET_CODES[code]
            else:
                mode = self._MODE_TO_HVAC.get(code, self._last_base_mode)
                self._last_base_mode = mode
                preset = const.AC_PRESET_NONE if mode == const.HVAC_MODE_COOL else None

        if fault_code != 0 or not power_on:
            action = const.HVAC_ACTION_OFF
        elif mode == const.HVAC_MODE_COOL:
            action = const.HVAC_ACTION_COOLING
        elif mode == const.HVAC_MODE_FAN_ONLY:
            action = const.HVAC_ACTION_FAN
        else:
            action = const.HVAC_ACTION_IDLE

        return {
            "power": "on" if power_on else "off",
            "mode": mode,
            "preset": preset,
            "action": action,
            "target_temperature": raw.get("set_temp_c"),
            "current_temperature": raw.get("inlet_temp_c"),
            "fan": str(raw.get("fan_speed")) if raw.get("fan_speed") is not None else None,
            "fault_code": fault_code,
            "fault": raw.get("fault_name"),
            "temp_unit": self.temp_unit,
        }

    # -- commands -------------------------------------------------------

    async def set_mode(self, mode: str) -> None:
        """Set HVAC mode: 'off', 'cool', or 'fan_only'."""
        mode = mode.lower()
        if mode == const.HVAC_MODE_OFF:
            await self._client.send_command(const.AC_FUNC_POWER, bytes([const.AC_POWER_OFF]))
        else:
            code = self._HVAC_TO_MODE.get(mode)
            if code is None:
                _LOGGER.warning("AC %s: unknown mode %r", self.name, mode)
                return
            # Power on first if currently off, then set the mode.
            if self.state.get("power") == "off":
                await self._client.send_command(const.AC_FUNC_POWER, bytes([const.AC_POWER_ON]))
            await self._client.send_command(const.AC_FUNC_MODE, bytes([code]))
        self._trigger_fast_refresh()

    async def set_preset(self, preset: str) -> None:
        """Set a preset ('Cooling' restores plain cool mode; Eco/Sleep/Turbo)."""
        if preset == const.AC_PRESET_NONE:
            code = self._HVAC_TO_MODE.get(self._last_base_mode, const.AC_MODE_COOL)
        else:
            code = self._PRESET_TO_CODE.get(preset)
            if code is None:
                _LOGGER.warning("AC %s: unknown preset %r", self.name, preset)
                return
        await self._client.send_command(const.AC_FUNC_MODE, bytes([code]))
        self._trigger_fast_refresh()

    async def set_fan(self, fan: str) -> None:
        """Set fan speed 1–5."""
        try:
            level = int(fan)
        except (TypeError, ValueError):
            _LOGGER.warning("AC %s: invalid fan value %r", self.name, fan)
            return
        await self._client.send_command(const.AC_FUNC_FAN, bytes([level]))
        self._trigger_fast_refresh()

    async def set_temperature(self, temp_c: float) -> None:
        """Set target temperature, given in °C; sent in the device's active unit."""
        value = celsius_to_native(temp_c, self.temp_unit)
        await self._client.send_command(const.AC_FUNC_TEMP, bytes([value]))
        self._trigger_fast_refresh()
