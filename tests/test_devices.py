"""Tests for device parsing, public state mapping, and command encoding.

A fake BLE client stands in for the real transport so these tests run without
hardware: it records every (func, data) sent and returns canned responses.
"""

from __future__ import annotations

import pytest

from velit_mqtt.devices import VelitACDevice, VelitHeaterDevice


class FakeClient:
    """Records sent commands and returns canned responses keyed by func code."""

    def __init__(self, responses: dict[int, bytes] | None = None) -> None:
        self.connected = True
        self.responses = responses or {}
        self.sent: list[tuple[int, bytes]] = []

    async def send_command(self, func: int, data: bytes):
        self.sent.append((func, bytes(data)))
        if func in self.responses:
            return {"func": func, "data": self.responses[func]}
        return None

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False


def make_heater() -> VelitHeaterDevice:
    dev = VelitHeaterDevice("Test Heater", "AA:BB:CC:DD:EE:FF", "test_heater", 30)
    dev._client = FakeClient()
    return dev


def make_ac(responses=None) -> VelitACDevice:
    dev = VelitACDevice("Test AC", "11:22:33:44:55:66", "test_ac", 30)
    dev._client = FakeClient(responses)
    return dev


# ---------------------------------------------------------------------------
# Heater parsing and state mapping
# ---------------------------------------------------------------------------

# Q1: [fault, work_mode, gear, set_temp, machine_state, power(W), pump_freq*10]
_Q1 = bytes([0x00, 0x02, 0x03, 21, 0x01, 100, 25])
# Q2: [fault][volt 2B][fan 2B][inlet 2B][casing 2B][outlet 2B][alt 2B]
#   voltage 132 -> 13.2V, fan 2400 rpm, inlet raw 71 -> 21C, casing/outlet FFFF, alt 120
_Q2 = bytes([0x00, 0x00, 0x84, 0x09, 0x60, 0x00, 0x47, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x78])


def test_heater_parse():
    dev = make_heater()
    raw = dev._parse(_Q1, _Q2)
    assert raw["fault_code"] == 0
    assert raw["work_mode"] == 2
    assert raw["current_gear"] == 3
    assert raw["set_temp_c"] == 21.0
    assert raw["machine_state"] == 1
    assert raw["machine_state_str"] == "Normal"
    assert raw["heater_power_w"] == 100
    assert raw["fuel_pump_hz"] == 2.5
    assert raw["voltage_v"] == 13.2
    assert raw["fan_rpm"] == 2400
    assert raw["inlet_temp_c"] == 21.0
    assert raw["casing_temp_c"] is None
    assert raw["outlet_temp_c"] is None
    assert raw["altitude"] == 120
    # Detected Celsius from the in-range setpoint.
    assert dev.temp_unit == "C"


def test_heater_build_state_running():
    dev = make_heater()
    dev._last_raw = dev._parse(_Q1, _Q2)
    state = dev.build_state()
    assert state["power"] == "on"
    assert state["mode"] == "heat"
    assert state["preset"] == "Auto"  # work_mode 2 = thermostat
    assert state["action"] == "heating"
    assert state["target_temperature"] == 21.0
    assert state["current_temperature"] == 21.0
    assert state["fan"] == "3"
    assert state["fault_active"] is False


def test_heater_build_state_standby():
    dev = make_heater()
    q1 = bytearray(_Q1)
    q1[4] = 0  # machine_state Standby
    dev._last_raw = dev._parse(bytes(q1), _Q2)
    state = dev.build_state()
    assert state["mode"] == "off"
    assert state["power"] == "off"
    assert state["action"] == "off"


def test_heater_fault_active():
    dev = make_heater()
    q1 = bytearray(_Q1)
    q1[0] = 7  # Fuel Pump Fault
    dev._last_raw = dev._parse(bytes(q1), _Q2)
    state = dev.build_state()
    assert state["fault_code"] == 7
    assert state["fault"] == "Fuel Pump Fault"
    assert state["fault_active"] is True
    assert state["action"] == "off"


# ---------------------------------------------------------------------------
# Heater commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heater_set_temperature():
    dev = make_heater()
    dev.temp_unit = "C"
    await dev.set_temperature(20)
    assert dev._client.sent[-1] == (0x08, bytes([20]))


@pytest.mark.asyncio
async def test_heater_set_mode_heat_auto():
    dev = make_heater()
    dev.state = {"preset": "Auto"}
    await dev.set_mode("heat")
    # Auto preset powers on with data 0x02.
    assert dev._client.sent[-1] == (0x01, bytes([0x02]))


@pytest.mark.asyncio
async def test_heater_set_mode_heat_manual():
    dev = make_heater()
    dev.state = {"preset": "Manual"}
    await dev.set_mode("heat")
    assert dev._client.sent[-1] == (0x01, bytes([0x01]))


@pytest.mark.asyncio
async def test_heater_set_mode_off():
    dev = make_heater()
    await dev.set_mode("off")
    assert dev._client.sent[-1] == (0x02, bytes([0x00]))


@pytest.mark.asyncio
async def test_heater_set_fan():
    dev = make_heater()
    await dev.set_fan("4")
    assert dev._client.sent[-1] == (0x07, bytes([4]))


# ---------------------------------------------------------------------------
# AC polling, state mapping, and commands
# ---------------------------------------------------------------------------

_AC_RESPONSES = {
    0x01: bytes([0x02]),  # power on
    0x02: bytes([0x01]),  # mode cool
    0x03: bytes([24]),    # setpoint 24
    0x04: bytes([0x03]),  # fan 3
    0x07: bytes([24]),    # inlet 24C
    0x0B: bytes([0x00]),  # no fault
}


@pytest.mark.asyncio
async def test_ac_poll_and_state():
    dev = make_ac(_AC_RESPONSES)
    raw = await dev._poll_raw()
    assert raw["power"] == 0x02
    assert raw["set_temp_c"] == 24.0
    assert dev.temp_unit == "C"

    dev._last_raw = raw
    state = dev.build_state()
    assert state["power"] == "on"
    assert state["mode"] == "cool"
    assert state["preset"] == "Cooling"
    assert state["action"] == "cooling"
    assert state["target_temperature"] == 24.0
    assert state["current_temperature"] == 24.0
    assert state["fan"] == "3"


@pytest.mark.asyncio
async def test_ac_power_off_state():
    responses = dict(_AC_RESPONSES, **{0x01: bytes([0x01])})  # power off
    dev = make_ac(responses)
    dev._last_raw = await dev._poll_raw()
    state = dev.build_state()
    assert state["mode"] == "off"
    assert state["preset"] is None
    assert state["action"] == "off"


@pytest.mark.asyncio
async def test_ac_set_mode_from_off_powers_on_first():
    dev = make_ac()
    dev.state = {"power": "off"}
    await dev.set_mode("cool")
    # First powers on (0x01/0x02), then sets cool mode (0x02/0x01).
    assert (0x01, bytes([0x02])) in dev._client.sent
    assert dev._client.sent[-1] == (0x02, bytes([0x01]))


@pytest.mark.asyncio
async def test_ac_set_preset_turbo():
    dev = make_ac()
    await dev.set_preset("Turbo")
    assert dev._client.sent[-1] == (0x02, bytes([0x06]))


@pytest.mark.asyncio
async def test_ac_set_temperature():
    dev = make_ac()
    dev.temp_unit = "C"
    await dev.set_temperature(22)
    assert dev._client.sent[-1] == (0x03, bytes([22]))
