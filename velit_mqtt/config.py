"""Configuration loading and saving.

Reads (and, when the web UI is used, writes back) a YAML file describing the
MQTT broker, the optional web UI, and the list of Velit devices. Secrets (the
broker password) may be supplied directly or via an environment variable using
``password_env`` so they need not live in the file.
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
    # When set, the password is read from this environment variable on load and
    # is preserved (not the resolved value) when the config is written back.
    password_env: str | None = None
    client_id: str = "velit-mqtt"
    base_topic: str = "velit"
    keepalive: int = 60
    # Home Assistant MQTT discovery.
    discovery: bool = True
    discovery_prefix: str = "homeassistant"


@dataclass
class WebConfig:
    # The onboarding/settings UI. Bound to localhost by default — it can add
    # devices and change broker settings on a service that controls a combustion
    # heater, so it should not be exposed to an untrusted network without a token.
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8099
    token: str | None = None


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
    web: WebConfig = field(default_factory=WebConfig)
    log_level: str = "INFO"


def resolve_config_path(path: str | None) -> str:
    """Resolve the config path from an explicit arg, env var, or default locations."""
    if path:
        return path
    env_path = os.environ.get("VELIT_MQTT_CONFIG")
    if env_path:
        return env_path
    for candidate in DEFAULT_CONFIG_PATHS:
        if os.path.exists(candidate):
            return candidate
    # Fall through to the first default so error messages are concrete.
    return DEFAULT_CONFIG_PATHS[0]


def load_config(path: str | None = None) -> AppConfig:
    """Load and validate the configuration from ``path`` (or a default location)."""
    resolved = resolve_config_path(path)
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
    web = _parse_web(raw.get("web", {}))
    devices = _parse_devices(raw.get("devices", []))
    log_level = str(raw.get("log_level", "INFO")).upper()

    # Devices may be empty — they can be added later through the web UI.
    return AppConfig(mqtt=mqtt, devices=devices, web=web, log_level=log_level)


def save_config(config: AppConfig, path: str) -> None:
    """Write the configuration back to ``path`` as YAML.

    Used by the web UI when devices or broker settings change. This rewrites the
    file (comments in a hand-edited file are not preserved).
    """
    data: dict = {"mqtt": _mqtt_to_dict(config.mqtt)}
    if config.web != WebConfig():
        data["web"] = _web_to_dict(config.web)
    data["devices"] = [_device_to_dict(d) for d in config.devices]
    data["log_level"] = config.log_level

    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)  # atomic on the same filesystem


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

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
        password_env=password_env,
        client_id=data.get("client_id", "velit-mqtt"),
        base_topic=data.get("base_topic", "velit"),
        keepalive=int(data.get("keepalive", 60)),
        discovery=bool(data.get("discovery", True)),
        discovery_prefix=data.get("discovery_prefix", "homeassistant"),
    )


def _parse_web(data: dict) -> WebConfig:
    if not isinstance(data, dict):
        raise ConfigError("'web' section must be a mapping")
    return WebConfig(
        enabled=bool(data.get("enabled", True)),
        host=data.get("host", "127.0.0.1"),
        port=int(data.get("port", 8099)),
        token=data.get("token"),
    )


def _parse_devices(items: list) -> list[DeviceConfig]:
    if not isinstance(items, list):
        raise ConfigError("'devices' must be a list")
    devices: list[DeviceConfig] = []
    seen_nodes: set[str] = set()
    for entry in items:
        device = parse_device(entry)
        if device.node_id in seen_nodes:
            raise ConfigError(f"Duplicate node id '{device.node_id}' — set distinct names")
        seen_nodes.add(device.node_id)
        devices.append(device)
    return devices


def parse_device(entry: dict) -> DeviceConfig:
    """Validate and build a single DeviceConfig (shared by file load and the API)."""
    valid_types = {const.DEVICE_TYPE_HEATER, const.DEVICE_TYPE_AC}
    valid_units = {UNIT_CELSIUS, UNIT_FAHRENHEIT}
    if not isinstance(entry, dict):
        raise ConfigError(f"Each device must be a mapping, got: {entry!r}")
    for required in ("name", "address", "type"):
        if not entry.get(required):
            raise ConfigError(f"Device entry missing required '{required}'")
    dtype = str(entry["type"]).lower()
    if dtype not in valid_types:
        raise ConfigError(
            f"Device '{entry['name']}' has unknown type {dtype!r} "
            f"(expected one of {sorted(valid_types)})"
        )
    fallback_unit = str(entry.get("fallback_unit", UNIT_CELSIUS)).upper()
    if fallback_unit not in valid_units:
        raise ConfigError(f"Device '{entry['name']}' has invalid fallback_unit {fallback_unit!r}")
    return DeviceConfig(
        name=str(entry["name"]),
        address=str(entry["address"]).upper(),
        type=dtype,
        poll_interval=int(entry.get("poll_interval", 30)),
        fallback_unit=fallback_unit,
        node_id=str(entry.get("node_id", "")),
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _mqtt_to_dict(m: MqttConfig) -> dict:
    out: dict = {"host": m.host, "port": m.port}
    if m.username:
        out["username"] = m.username
    # Prefer password_env when present so secrets stay out of the file.
    if m.password_env:
        out["password_env"] = m.password_env
    elif m.password:
        out["password"] = m.password
    if m.client_id != "velit-mqtt":
        out["client_id"] = m.client_id
    out["base_topic"] = m.base_topic
    if m.keepalive != 60:
        out["keepalive"] = m.keepalive
    out["discovery"] = m.discovery
    out["discovery_prefix"] = m.discovery_prefix
    return out


def _web_to_dict(w: WebConfig) -> dict:
    out: dict = {"enabled": w.enabled, "host": w.host, "port": w.port}
    if w.token:
        out["token"] = w.token
    return out


def _device_to_dict(d: DeviceConfig) -> dict:
    out: dict = {
        "name": d.name,
        "address": d.address,
        "type": d.type,
        "poll_interval": d.poll_interval,
    }
    if d.fallback_unit != UNIT_CELSIUS:
        out["fallback_unit"] = d.fallback_unit
    # Preserve the node id so it stays stable even if the name is edited later.
    if d.node_id != slugify(d.name):
        out["node_id"] = d.node_id
    return out
