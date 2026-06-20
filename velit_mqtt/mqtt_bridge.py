"""MQTT side of the bridge.

Connects to the broker, publishes device state (retained JSON) and availability,
and routes inbound ``set/`` commands to the right device. Optionally publishes
Home Assistant discovery configs. Maintains its own reconnect loop so a broker
restart is transparent; device polling continues regardless and the latest
state is re-published on reconnect.

Availability uses two layers so consumers can tell the difference between "the
bridge is down" and "this device is unreachable over BLE":
  <base>/bridge/availability   — bridge process liveness (also the MQTT LWT)
  <base>/<node>/availability   — per-device BLE reachability
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from .config import AppConfig, DeviceConfig
from .devices import VelitDevice
from .discovery import discovery_messages, discovery_topics

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY = 5  # seconds between broker reconnect attempts


class MqttBridge:
    """Owns the MQTT connection and translates between MQTT and devices."""

    def __init__(
        self,
        config: AppConfig,
        devices: dict[str, VelitDevice],
        device_configs: dict[str, DeviceConfig],
    ) -> None:
        self._config = config
        self._mqtt = config.mqtt
        self._devices = devices
        self._device_configs = device_configs
        self._client: aiomqtt.Client | None = None
        self._connected = False
        self._stopping = False

    # ------------------------------------------------------------------
    # Topic helpers
    # ------------------------------------------------------------------

    @property
    def _base(self) -> str:
        return self._mqtt.base_topic

    def state_topic(self, node_id: str) -> str:
        return f"{self._base}/{node_id}/state"

    def availability_topic(self, node_id: str) -> str:
        return f"{self._base}/{node_id}/availability"

    @property
    def bridge_availability_topic(self) -> str:
        return f"{self._base}/bridge/availability"

    @property
    def _command_filter(self) -> str:
        return f"{self._base}/+/set/#"

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Publishing (called from device callbacks)
    # ------------------------------------------------------------------

    async def publish_state(self, device: VelitDevice) -> None:
        if not self._connected or self._client is None:
            return
        try:
            await self._client.publish(
                self.state_topic(device.node_id),
                json.dumps(device.state),
                qos=1,
                retain=True,
            )
        except aiomqtt.MqttError as exc:
            _LOGGER.debug("State publish failed for %s: %s", device.node_id, exc)

    async def publish_availability(self, device: VelitDevice, online: bool) -> None:
        if not self._connected or self._client is None:
            return
        try:
            await self._client.publish(
                self.availability_topic(device.node_id),
                "online" if online else "offline",
                qos=1,
                retain=True,
            )
        except aiomqtt.MqttError as exc:
            _LOGGER.debug("Availability publish failed for %s: %s", device.node_id, exc)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Maintain the broker connection and dispatch inbound messages."""
        will = aiomqtt.Will(
            topic=self.bridge_availability_topic,
            payload="offline",
            qos=1,
            retain=True,
        )
        while not self._stopping:
            try:
                async with aiomqtt.Client(
                    hostname=self._mqtt.host,
                    port=self._mqtt.port,
                    username=self._mqtt.username,
                    password=self._mqtt.password,
                    identifier=self._mqtt.client_id,
                    keepalive=self._mqtt.keepalive,
                    will=will,
                ) as client:
                    self._client = client
                    self._connected = True
                    _LOGGER.info("Connected to MQTT broker %s:%d", self._mqtt.host, self._mqtt.port)
                    await self._on_connect()
                    async for message in client.messages:
                        await self._handle_message(message)
            except aiomqtt.MqttError as exc:
                _LOGGER.warning(
                    "MQTT connection error: %s; reconnecting in %ds", exc, _RECONNECT_DELAY
                )
            finally:
                self._connected = False
                self._client = None
            if not self._stopping:
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _on_connect(self) -> None:
        """Subscribe, announce online, (re)publish discovery and current state."""
        await self._client.publish(  # type: ignore[union-attr]
            self.bridge_availability_topic, "online", qos=1, retain=True
        )
        await self._client.subscribe(self._command_filter, qos=1)  # type: ignore[union-attr]

        for node_id, device in self._devices.items():
            cfg = self._device_configs[node_id]
            if self._mqtt.discovery:
                await self._publish_discovery(cfg, device)
            # Re-publish retained availability + state so a reconnect reflects reality.
            await self.publish_availability(device, device.connected)
            if device.state:
                await self.publish_state(device)

    async def _publish_discovery(self, cfg: DeviceConfig, device: VelitDevice) -> None:
        for topic, payload in discovery_messages(
            cfg,
            self._base,
            self._mqtt.discovery_prefix,
            firmware=getattr(device, "firmware", None),
        ):
            # Inject the bridge availability topic so entities go unavailable if
            # the bridge process dies (LWT), as well as on per-device BLE loss.
            payload = self._with_bridge_availability(payload, cfg.node_id)
            await self._client.publish(  # type: ignore[union-attr]
                topic, json.dumps(payload), qos=1, retain=True
            )

    def _with_bridge_availability(self, payload: dict, node_id: str) -> dict:
        """Convert a single availability_topic into a two-topic 'all' list."""
        device_avail = payload.pop("availability_topic", self.availability_topic(node_id))
        payload.pop("payload_available", None)
        payload.pop("payload_not_available", None)
        payload["availability_mode"] = "all"
        payload["availability"] = [
            {
                "topic": self.bridge_availability_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
            },
            {
                "topic": device_avail,
                "payload_available": "online",
                "payload_not_available": "offline",
            },
        ]
        return payload

    async def announce_device(self, cfg: DeviceConfig, device: VelitDevice) -> None:
        """Publish discovery + current state for a device added at runtime.

        The command subscription is a wildcard (`<base>/+/set/#`) so no
        re-subscribe is needed for a newly added node.
        """
        if not self._connected or self._client is None:
            return
        if self._mqtt.discovery:
            await self._publish_discovery(cfg, device)
        await self.publish_availability(device, device.connected)
        if device.state:
            await self.publish_state(device)

    async def retract_device(self, node_id: str, cfg: DeviceConfig) -> None:
        """Clear all retained topics for a device removed at runtime."""
        if not self._connected or self._client is None:
            return
        if self._mqtt.discovery:
            for topic in discovery_topics(cfg, self._base, self._mqtt.discovery_prefix):
                await self._client.publish(topic, b"", qos=1, retain=True)
        for topic in (self.state_topic(node_id), self.availability_topic(node_id)):
            await self._client.publish(topic, b"", qos=1, retain=True)

    # ------------------------------------------------------------------
    # Inbound command handling
    # ------------------------------------------------------------------

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        topic = message.topic.value
        parts = topic.split("/")
        # Expect: <base>/<node>/set/<field>
        if len(parts) < 4 or parts[-2] != "set":
            _LOGGER.debug("Ignoring message on unexpected topic %s", topic)
            return
        node_id = parts[-3]
        field = parts[-1]
        device = self._devices.get(node_id)
        if device is None:
            _LOGGER.warning("Command for unknown node %r (topic %s)", node_id, topic)
            return

        payload = message.payload
        value = payload.decode(errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        value = value.strip()
        _LOGGER.info("Command %s/%s = %r", node_id, field, value)
        try:
            await self._dispatch(device, field, value)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error handling command %s/%s", node_id, field)

    async def _dispatch(self, device: VelitDevice, field: str, value: str) -> None:
        if field == "mode":
            await device.set_mode(value)
        elif field == "preset":
            await device.set_preset(value)
        elif field == "fan":
            await device.set_fan(value)
        elif field == "temperature":
            await device.set_temperature(float(value))
        elif field == "ble":
            if value.upper() == "ON":
                await device.ble_connect()
            else:
                await device.ble_disconnect()
        elif field == "prime" and hasattr(device, "prime_on"):
            if value.upper() == "ON":
                await device.prime_on()  # type: ignore[attr-defined]
            else:
                await device.prime_off()  # type: ignore[attr-defined]
        elif field == "cleaning" and hasattr(device, "cleaning_on"):
            if value.upper() == "ON":
                await device.cleaning_on()  # type: ignore[attr-defined]
        else:
            _LOGGER.warning("Unsupported command field %r for %s", field, device.node_id)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def announce_offline(self) -> None:
        """Publish offline availability before a clean shutdown (no LWT on clean exit)."""
        if not self._connected or self._client is None:
            return
        try:
            for device in self._devices.values():
                await self.publish_availability(device, False)
            await self._client.publish(
                self.bridge_availability_topic, "offline", qos=1, retain=True
            )
        except aiomqtt.MqttError:
            pass

    async def clear_discovery(self) -> None:
        """Remove discovery configs by publishing empty retained payloads."""
        if not self._connected or self._client is None or not self._mqtt.discovery:
            return
        for cfg in self._device_configs.values():
            for topic in discovery_topics(cfg, self._base, self._mqtt.discovery_prefix):
                try:
                    await self._client.publish(topic, b"", qos=1, retain=True)
                except aiomqtt.MqttError:
                    pass

    def stop(self) -> None:
        self._stopping = True
