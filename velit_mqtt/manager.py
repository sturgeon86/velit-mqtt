"""Runtime coordinator.

Owns the device managers and the MQTT bridge, and exposes the mutating
operations the web UI needs: scan for devices, add/remove a device, and change
the broker settings — all applied live and persisted back to the config file,
without restarting the process.
"""

from __future__ import annotations

import asyncio
import logging

from . import const
from .ble import scan_velit_devices
from .config import AppConfig, DeviceConfig, parse_device, save_config
from .devices import VelitACDevice, VelitDevice, VelitHeaterDevice
from .mqtt_bridge import MqttBridge

_LOGGER = logging.getLogger(__name__)

# MQTT fields the UI is allowed to change.
_MQTT_SETTABLE = (
    "host", "port", "username", "password", "client_id",
    "base_topic", "keepalive", "discovery", "discovery_prefix",
)


def build_device(cfg: DeviceConfig) -> VelitDevice:
    """Instantiate the right device manager for a config entry."""
    if cfg.type == const.DEVICE_TYPE_HEATER:
        return VelitHeaterDevice(
            cfg.name, cfg.address, cfg.node_id, cfg.poll_interval, cfg.fallback_unit
        )
    return VelitACDevice(
        cfg.name, cfg.address, cfg.node_id, cfg.poll_interval, cfg.fallback_unit
    )


class BridgeManager:
    """Holds the bridge + devices and applies runtime changes from the UI."""

    def __init__(self, config: AppConfig, config_path: str) -> None:
        self.config = config
        self.config_path = config_path
        self.device_configs: dict[str, DeviceConfig] = {
            c.node_id: c for c in config.devices
        }
        self.devices: dict[str, VelitDevice] = {}
        self.bridge = MqttBridge(config, self.devices, self.device_configs)
        self._bridge_task: asyncio.Task | None = None
        # Serialises mutating operations so concurrent UI requests can't race.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        for cfg in list(self.config.devices):
            self.devices[cfg.node_id] = self._make_device(cfg)
        self._bridge_task = asyncio.create_task(self.bridge.run())
        for device in self.devices.values():
            await device.start()

    async def stop(self) -> None:
        self.bridge.stop()
        for device in self.devices.values():
            await device.stop()
        await self.bridge.announce_offline()
        await self._cancel_bridge_task()

    def _make_device(self, cfg: DeviceConfig) -> VelitDevice:
        device = build_device(cfg)
        device.on_state = self.bridge.publish_state
        device.on_availability = self.bridge.publish_availability
        return device

    async def _cancel_bridge_task(self) -> None:
        if self._bridge_task and not self._bridge_task.done():
            self._bridge_task.cancel()
            try:
                await self._bridge_task
            except asyncio.CancelledError:
                pass
        self._bridge_task = None

    async def _restart_bridge(self) -> None:
        await self._cancel_bridge_task()
        self.bridge._stopping = False
        self._bridge_task = asyncio.create_task(self.bridge.run())

    # ------------------------------------------------------------------
    # Operations used by the web UI
    # ------------------------------------------------------------------

    async def scan(self, timeout: float = 8.0) -> list[dict]:
        """Scan for Velit devices, flagging any already in the config."""
        results = await scan_velit_devices(timeout)
        configured = {c.address.upper() for c in self.config.devices}
        for r in results:
            r["configured"] = r["address"].upper() in configured
        return results

    async def add_device(self, entry: dict) -> DeviceConfig:
        """Validate, persist, and start a new device live."""
        async with self._lock:
            cfg = parse_device(entry)
            if cfg.node_id in self.devices:
                raise ValueError(
                    f"A device with node id '{cfg.node_id}' already exists "
                    f"(pick a different name)"
                )
            if any(c.address == cfg.address for c in self.config.devices):
                raise ValueError(f"Address {cfg.address} is already configured")

            device = self._make_device(cfg)
            self.device_configs[cfg.node_id] = cfg
            self.devices[cfg.node_id] = device
            self.config.devices.append(cfg)
            save_config(self.config, self.config_path)

            await device.start()
            await self.bridge.announce_device(cfg, device)
            _LOGGER.info("Added device %s (%s)", cfg.name, cfg.address)
            return cfg

    async def remove_device(self, node_id: str) -> None:
        """Stop a device, clear its retained MQTT topics, and persist."""
        async with self._lock:
            device = self.devices.pop(node_id, None)
            if device is None:
                raise KeyError(node_id)
            cfg = self.device_configs.pop(node_id)
            await device.stop()
            await self.bridge.retract_device(node_id, cfg)
            self.config.devices = [c for c in self.config.devices if c.node_id != node_id]
            save_config(self.config, self.config_path)
            _LOGGER.info("Removed device %s", node_id)

    async def update_mqtt(self, settings: dict) -> None:
        """Apply new broker settings, persist, and reconnect with them."""
        async with self._lock:
            mqtt = self.config.mqtt
            # Clear the old discovery configs (on the old broker / topic) first.
            if self.bridge.connected and mqtt.discovery:
                await self.bridge.clear_discovery()
            for key in _MQTT_SETTABLE:
                if key in settings and settings[key] is not None:
                    setattr(mqtt, key, settings[key])
            # A literal password supersedes any password_env reference.
            if settings.get("password"):
                mqtt.password_env = None
            save_config(self.config, self.config_path)
            await self._restart_bridge()
            _LOGGER.info("Updated MQTT settings; reconnecting to %s:%d", mqtt.host, mqtt.port)

    # ------------------------------------------------------------------
    # Read-only views for the UI
    # ------------------------------------------------------------------

    def config_summary(self) -> dict:
        """Current config for the UI (never returns the raw password)."""
        m = self.config.mqtt
        return {
            "mqtt": {
                "host": m.host,
                "port": m.port,
                "username": m.username or "",
                "base_topic": m.base_topic,
                "discovery": m.discovery,
                "discovery_prefix": m.discovery_prefix,
                "has_password": bool(m.password),
                "password_env": m.password_env,
                "connected": self.bridge.connected,
            },
            "devices": [self._device_status(c.node_id) for c in self.config.devices],
        }

    def _device_status(self, node_id: str) -> dict:
        cfg = self.device_configs[node_id]
        device = self.devices.get(node_id)
        return {
            "node_id": node_id,
            "name": cfg.name,
            "type": cfg.type,
            "address": cfg.address,
            "poll_interval": cfg.poll_interval,
            "connected": device.connected if device else False,
            "available": device.available if device else False,
            "state": device.state if device else {},
        }
