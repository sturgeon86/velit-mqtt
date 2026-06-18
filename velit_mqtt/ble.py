"""Standalone BLE transport for Velit devices.

Replaces the Home Assistant Bluetooth stack with direct ``bleak`` usage:
device resolution via ``BleakScanner``, connection via ``bleak_retry_connector``
(which transparently handles transient failures and a GATT service cache).

Two clients mirror the two protocols:

  VelitHeaterBleClient — one command in flight at a time, simple queue.
  VelitACBleClient     — adds the protocol-mandated 400 ms inter-command
                         interval, one automatic retry, and an "unavailable"
                         latch after repeated failures.

Both expose the same ``connect`` / ``disconnect`` / ``send_command`` surface so
the device layer can treat them uniformly.
"""

from __future__ import annotations

import asyncio
import logging
import time

from bleak import BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    AC_COMMAND_INTERVAL_MS,
    AC_MAX_RETRIES,
    AC_RESPONSE_TIMEOUT_S,
    HEATER_MASTER_ADDR,
    HEATER_SLAVE_ADDR,
    UUID_READ_NOTIFY,
    UUID_WRITE,
)
from .protocol import ac as ac_protocol
from .protocol import heater as heater_protocol

_LOGGER = logging.getLogger(__name__)

_SCAN_TIMEOUT = 20.0             # seconds to look for the device before connect
_HEATER_COMMAND_TIMEOUT = 5.0    # seconds to wait for a notification response
_AC_COMMAND_INTERVAL = AC_COMMAND_INTERVAL_MS / 1000.0


class _VelitBleClient:
    """Shared BLE connection lifecycle for both Velit protocols.

    Handles device resolution, connection, notification subscription, and a
    single-slot "pending response" future that the notification handler
    resolves. Unexpected disconnects are recorded but not retried here —
    reconnection is driven by the device poll loop. Subclasses implement the
    command queue (``_queue_runner``) and packet construction (``_build_packet``).
    """

    def __init__(self, address: str) -> None:
        self._address = address
        self._client: BleakClientWithServiceCache | None = None
        self._connected = False
        self._queue: asyncio.Queue[
            tuple[int, bytes, asyncio.Future[dict | None]]
        ] = asyncio.Queue()
        # Resolved by the notification handler for whichever command is in flight.
        self._pending: asyncio.Future[dict | None] | None = None
        self._queue_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Return True when the BLE connection is established and ready."""
        return self._connected

    async def connect(self) -> None:
        """Resolve, connect, and subscribe to response notifications."""
        device = await self._resolve_device()
        # establish_connection handles transient failures and uses the service
        # cache to skip GATT re-discovery on reconnect.
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self._address,
            disconnected_callback=self._on_disconnect,
        )
        self._client = client
        try:
            await client.start_notify(UUID_READ_NOTIFY, self._on_notification)
        except Exception:
            # Disconnect before re-raising so the adapter releases this
            # connection and any stale notification subscription.
            try:
                await client.disconnect()
            except Exception:
                pass
            raise
        self._connected = True
        self._on_connected()
        self._queue_task = asyncio.create_task(self._queue_runner())
        _LOGGER.info("Connected to %s", self._address)

    async def disconnect(self) -> None:
        """Disconnect cleanly and suppress the auto-reconnect loop.

        Setting ``_connected`` False first means the disconnect callback is a
        no-op — reconnection is driven by the device poll loop, not here.
        """
        self._connected = False

        if self._queue_task and not self._queue_task.done():
            self._queue_task.cancel()

        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(UUID_READ_NOTIFY)
            except Exception:
                pass
            await self._client.disconnect()

        _LOGGER.info("Disconnected from %s", self._address)

    async def send_command(self, func: int, data: bytes) -> dict | None:
        """Queue a command and wait for the parsed response (None on failure)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Hooks / abstract methods for subclasses
    # ------------------------------------------------------------------

    def _on_connected(self) -> None:
        """Called after a successful connect — subclasses reset failure state."""

    def _build_packet(self, func: int, data: bytes) -> bytes:
        raise NotImplementedError

    async def _queue_runner(self) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_device(self) -> BLEDevice:
        device = await BleakScanner.find_device_by_address(
            self._address, timeout=_SCAN_TIMEOUT
        )
        if device is None:
            raise RuntimeError(
                f"Device {self._address} not found during BLE scan "
                f"(is it powered on, in range, and not held by another app?)"
            )
        return device

    def _on_notification(
        self, _char: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle an incoming notification from the device."""
        parsed = self._parse(bytes(data))
        if self._pending and not self._pending.done():
            self._pending.set_result(parsed)
        elif parsed:
            _LOGGER.debug("Unsolicited notification: func 0x%02X", parsed.get("func"))

    def _parse(self, raw: bytes) -> dict | None:
        raise NotImplementedError

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        """Called by bleak when the connection is lost.

        Just records the drop and unblocks any in-flight command. The device
        poll loop notices ``connected`` is False and drives reconnection (unless
        the device was deliberately released), so reconnection lives in one place.
        """
        if not self._connected:
            return
        self._connected = False
        _LOGGER.warning("Connection lost to %s", self._address)
        if self._pending and not self._pending.done():
            self._pending.set_result(None)


class VelitHeaterBleClient(_VelitBleClient):
    """BLE client for the Velit heater protocol (V1.02)."""

    def __init__(
        self,
        address: str,
        master_addr: bytes = HEATER_MASTER_ADDR,
        slave_addr: bytes = HEATER_SLAVE_ADDR,
    ) -> None:
        super().__init__(address)
        self._master_addr = master_addr
        self._slave_addr = slave_addr

    def _build_packet(self, func: int, data: bytes) -> bytes:
        return heater_protocol.build_command(
            self._master_addr, self._slave_addr, func, data
        )

    def _parse(self, raw: bytes) -> dict | None:
        return heater_protocol.parse_response(raw)

    async def send_command(self, func: int, data: bytes) -> dict | None:
        if not self._connected:
            _LOGGER.debug("send_command called while not connected")
            return None
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict | None] = loop.create_future()
        await self._queue.put((func, data, fut))
        try:
            # Headroom beyond the per-command timeout so the future is never
            # orphaned inside the queue.
            return await asyncio.wait_for(fut, timeout=_HEATER_COMMAND_TIMEOUT + 2.0)
        except asyncio.TimeoutError:
            _LOGGER.warning("send_command timed out (func 0x%02X)", func)
            return None

    async def _queue_runner(self) -> None:
        """Process commands one at a time until disconnected."""
        while self._connected:
            try:
                func, data, caller_fut = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            result: dict | None = None
            try:
                packet = self._build_packet(func, data)
                await self._client.write_gatt_char(UUID_WRITE, packet, response=True)  # type: ignore[union-attr]
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(self._pending), timeout=_HEATER_COMMAND_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "No notification within %.0fs for func 0x%02X",
                        _HEATER_COMMAND_TIMEOUT, func,
                    )
            except Exception as exc:
                _LOGGER.warning("Command write failed (func 0x%02X): %s", func, exc)
            finally:
                self._pending = None
                self._queue.task_done()

            if not caller_fut.done():
                caller_fut.set_result(result)


class VelitACBleClient(_VelitBleClient):
    """BLE client for the Velit AC protocol (V1.01).

    Adds the protocol-mandated timing: minimum 400 ms between commands, one
    automatic retry on no response within 3 s, and an "unavailable" latch after
    AC_MAX_RETRIES consecutive failures.
    """

    def __init__(
        self,
        address: str,
        product_code: int = ac_protocol.DEFAULT_PRODUCT_CODE,
    ) -> None:
        super().__init__(address)
        self._product_code = product_code
        self.unavailable = False
        self._consecutive_failures = 0
        self._last_command_time = 0.0

    def _on_connected(self) -> None:
        self.unavailable = False
        self._consecutive_failures = 0

    def _build_packet(self, func: int, data: bytes) -> bytes:
        return ac_protocol.build_command(func, data, self._product_code)

    def _parse(self, raw: bytes) -> dict | None:
        return ac_protocol.parse_response(raw)

    async def send_command(self, func: int, data: bytes) -> dict | None:
        if self.unavailable:
            _LOGGER.debug("send_command skipped — device marked unavailable")
            return None
        if not self._connected:
            _LOGGER.debug("send_command called while not connected")
            return None
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict | None] = loop.create_future()
        await self._queue.put((func, data, fut))
        try:
            timeout = AC_RESPONSE_TIMEOUT_S * 2 + _AC_COMMAND_INTERVAL + 2.0
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning("send_command timed out (func 0x%02X)", func)
            return None

    async def _queue_runner(self) -> None:
        """Process commands one at a time, enforcing inter-command timing."""
        while self._connected:
            try:
                func, data, caller_fut = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            result = await self._execute_with_retry(func, data)
            self._queue.task_done()
            if not caller_fut.done():
                caller_fut.set_result(result)

    async def _execute_with_retry(self, func: int, data: bytes) -> dict | None:
        """Send with one automatic retry; latch unavailable after repeated fails."""
        for attempt in range(2):  # initial attempt + one retry
            await self._enforce_interval()
            result = await self._write_and_wait(func, data)
            if result is not None:
                self._consecutive_failures = 0
                return result
            if attempt == 0:
                _LOGGER.debug("No response for func 0x%02X, retrying once", func)

        self._consecutive_failures += 1
        _LOGGER.warning(
            "Command failed (func 0x%02X), consecutive failures: %d/%d",
            func, self._consecutive_failures, AC_MAX_RETRIES,
        )
        if self._consecutive_failures >= AC_MAX_RETRIES:
            self.unavailable = True
            _LOGGER.error(
                "Device at %s marked unavailable after %d consecutive failures",
                self._address, self._consecutive_failures,
            )
        return None

    async def _write_and_wait(self, func: int, data: bytes) -> dict | None:
        """Write one command and wait up to AC_RESPONSE_TIMEOUT_S for a response."""
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        result: dict | None = None
        try:
            packet = self._build_packet(func, data)
            await self._client.write_gatt_char(UUID_WRITE, packet, response=True)  # type: ignore[union-attr]
            self._last_command_time = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(self._pending), timeout=float(AC_RESPONSE_TIMEOUT_S)
                )
            except asyncio.TimeoutError:
                pass
        except Exception as exc:
            _LOGGER.warning("Write failed (func 0x%02X): %s", func, exc)
        finally:
            self._pending = None
        return result

    async def _enforce_interval(self) -> None:
        """Sleep until at least AC_COMMAND_INTERVAL_MS has elapsed since the last write."""
        wait = _AC_COMMAND_INTERVAL - (time.monotonic() - self._last_command_time)
        if wait > 0:
            await asyncio.sleep(wait)
