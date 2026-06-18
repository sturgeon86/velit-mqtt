"""Configuration loading.

Reads a YAML file describing the MQTT broker and the list of Velit devices.
Secrets (the broker password) may be supplied directly or via an environment
variable using ``password_env`` so they need not live in the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from . import const
from .temperature import UNIT_CELSIUS, UNIT_FAHRENHEIT
from .util import slugify

DEFAULT_CONFIG_PATHS = (
    "config.yaml",
    "/etc/velit-mqtt/config.yaml",
)


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class MqttConfig:
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    client_id: str = "velit-mqtt"
    base_topic: str = "velit"
    keepalive: int = 60
    # Home Assistant MQTT discovery.
    discovery: bool = True
    discovery_prefix: str = "homeassistant"


@dataclass
class DeviceConfig:
    name: str
    address: str
    type: str
    poll_interval: int = 30
    # Unit to assume when the device's setpoint is outside both known ranges
    # (e.g. on first boot). Display conversion still happens device-side.
    fallback_unit: str = UNIT_CELSIUS
    node_id: str = ""

    def __post_init__(self) -> None:
        if not self.node_id:
            self.node_id = slugify(self.name) or slugify(self.address)


@dataclass
class AppConfig:
    mqtt: MqttConfig
    devices: list[DeviceConfig]
    log_level: str = "INFO"


def load_config(path: str | None = None) -> AppConfig:
    """Load and validate the configuration from ``path`` (or a default location)."""
    resolved = _resolve_path(path)
    try:
        with open(resolved, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {resolved}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {resolved}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Top-level config must be a mapping")

    mqtt = _parse_mqtt(raw.get("mqtt", {}))
    devices = _parse_devices(raw.get("devices", []))
    log_level = str(raw.get("log_level", "INFO")).upper()

    if not devices:
        raise ConfigError("No devices configured — add at least one under 'devices'")

    return AppConfig(mqtt=mqtt, devices=devices, log_level=log_level)


def _resolve_path(path: str | None) -> str:
    if path:
        return path
    env_path = os.environ.get("VELIT_MQTT_CONFIG")
    if env_path:
        return env_path
    for candidate in DEFAULT_CONFIG_PATHS:
        if os.path.exists(candidate):
            return candidate
    # Fall through to the first default so the error message is concrete.
    return DEFAULT_CONFIG_PATHS[0]


def _parse_mqtt(data: dict) -> MqttConfig:
    if not isinstance(data, dict):
        raise ConfigError("'mqtt' section must be a mapping")
    password = data.get("password")
    password_env = data.get("password_env")
    if password_env:
        password = os.environ.get(password_env, password)
    return MqttConfig(
        host=data.get("host", "localhost"),
        port=int(data.get("port", 1883)),
        username=data.get("username"),
        password=password,
        client_id=data.get("client_id", "velit-mqtt"),
        base_topic=data.get("base_topic", "velit"),
        keepalive=int(data.get("keepalive", 60)),
        discovery=bool(data.get("discovery", True)),
        discovery_prefix=data.get("discovery_prefix", "homeassistant"),
    )


def _parse_devices(items: list) -> list[DeviceConfig]:
    if not isinstance(items, list):
        raise ConfigError("'devices' must be a list")
    valid_types = {const.DEVICE_TYPE_HEATER, const.DEVICE_TYPE_AC}
    valid_units = {UNIT_CELSIUS, UNIT_FAHRENHEIT}
    devices: list[DeviceConfig] = []
    seen_nodes: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            raise ConfigError(f"Each device must be a mapping, got: {entry!r}")
        for required in ("name", "address", "type"):
            if not entry.get(required):
                raise ConfigError(f"Device entry missing required '{required}': {entry!r}")
        dtype = str(entry["type"]).lower()
        if dtype not in valid_types:
            raise ConfigError(
                f"Device '{entry['name']}' has unknown type {dtype!r} "
                f"(expected one of {sorted(valid_types)})"
            )
        fallback_unit = str(entry.get("fallback_unit", UNIT_CELSIUS)).upper()
        if fallback_unit not in valid_units:
            raise ConfigError(
                f"Device '{entry['name']}' has invalid fallback_unit {fallback_unit!r}"
            )
        device = DeviceConfig(
            name=str(entry["name"]),
            address=str(entry["address"]).upper(),
            type=dtype,
            poll_interval=int(entry.get("poll_interval", 30)),
            fallback_unit=fallback_unit,
            node_id=str(entry.get("node_id", "")),
        )
        if device.node_id in seen_nodes:
            raise ConfigError(f"Duplicate node id '{device.node_id}' — set distinct names")
        seen_nodes.add(device.node_id)
        devices.append(device)
    return devices
