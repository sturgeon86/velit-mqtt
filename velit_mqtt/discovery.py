"""Home Assistant MQTT discovery payload builders.

Optional: when enabled, the bridge publishes retained discovery configs so the
device appears in Home Assistant automatically — without any custom component.
The discovery entities point at the *same* generic state and command topics the
bridge already uses, so there is a single source of truth.

Each builder returns a list of (topic, payload) tuples. Publishing an empty
payload to the same topic removes the entity (used on clean shutdown).
"""

from __future__ import annotations

from typing import Any

from . import const
from .config import DeviceConfig


def _device_block(dev: DeviceConfig, firmware: str | None) -> dict[str, Any]:
    block = {
        "identifiers": [f"velit_{dev.node_id}"],
        "name": dev.name,
        "manufacturer": "Velit",
        "model": "Heater" if dev.type == const.DEVICE_TYPE_HEATER else "Air Conditioner",
    }
    if firmware:
        block["sw_version"] = firmware
    return block


def _topics(base_topic: str, node_id: str) -> dict[str, str]:
    root = f"{base_topic}/{node_id}"
    return {
        "state": f"{root}/state",
        "availability": f"{root}/availability",
        "cmd": f"{root}/set",
    }


def discovery_messages(
    dev: DeviceConfig,
    base_topic: str,
    discovery_prefix: str,
    firmware: str | None = None,
) -> list[tuple[str, dict]]:
    """Return all discovery (topic, payload) pairs for a device."""
    if dev.type == const.DEVICE_TYPE_HEATER:
        return _heater_messages(dev, base_topic, discovery_prefix, firmware)
    return _ac_messages(dev, base_topic, discovery_prefix, firmware)


def discovery_topics(
    dev: DeviceConfig, base_topic: str, discovery_prefix: str
) -> list[str]:
    """Return just the discovery config topics (used to clear them on shutdown)."""
    return [topic for topic, _ in discovery_messages(dev, base_topic, discovery_prefix)]


def _base_availability(t: dict[str, str]) -> dict[str, Any]:
    return {
        "availability_topic": t["availability"],
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def _sensor(
    dev, t, device, prefix, key, name, template, **extra
) -> tuple[str, dict]:
    payload: dict[str, Any] = {
        "name": name,
        "unique_id": f"velit_{dev.node_id}_{key}",
        "state_topic": t["state"],
        "value_template": template,
        "device": device,
        **_base_availability(t),
        **extra,
    }
    topic = f"{prefix}/sensor/{dev.node_id}_{key}/config"
    return topic, payload


def _switch(dev, t, device, prefix, key, name, field, value_template, **extra):
    payload: dict[str, Any] = {
        "name": name,
        "unique_id": f"velit_{dev.node_id}_{key}",
        "state_topic": t["state"],
        "value_template": value_template,
        "command_topic": f"{t['cmd']}/{field}",
        "payload_on": "ON",
        "payload_off": "OFF",
        "state_on": "ON",
        "state_off": "OFF",
        "device": device,
        **_base_availability(t),
        **extra,
    }
    topic = f"{prefix}/switch/{dev.node_id}_{key}/config"
    return topic, payload


def _heater_messages(dev, base_topic, prefix, firmware):
    t = _topics(base_topic, dev.node_id)
    device = _device_block(dev, firmware)

    climate = {
        "name": None,  # use the device name for the primary entity
        "unique_id": f"velit_{dev.node_id}_climate",
        "device": device,
        **_base_availability(t),
        "temperature_unit": "C",
        "min_temp": const.HEATER_MIN_TEMP_C,
        "max_temp": const.HEATER_MAX_TEMP_C,
        "temp_step": 1,
        "modes": [const.HVAC_MODE_OFF, const.HVAC_MODE_HEAT],
        "mode_state_topic": t["state"],
        "mode_state_template": "{{ value_json.mode }}",
        "mode_command_topic": f"{t['cmd']}/mode",
        "action_topic": t["state"],
        "action_template": "{{ value_json.action }}",
        "current_temperature_topic": t["state"],
        "current_temperature_template": "{{ value_json.current_temperature }}",
        "temperature_state_topic": t["state"],
        "temperature_state_template": "{{ value_json.target_temperature }}",
        "temperature_command_topic": f"{t['cmd']}/temperature",
        "preset_modes": const.HEATER_PRESETS,
        "preset_mode_state_topic": t["state"],
        "preset_mode_value_template": "{{ value_json.preset }}",
        "preset_mode_command_topic": f"{t['cmd']}/preset",
        "fan_modes": const.FAN_MODES,
        "fan_mode_state_topic": t["state"],
        "fan_mode_state_template": "{{ value_json.fan }}",
        "fan_mode_command_topic": f"{t['cmd']}/fan",
    }

    messages: list[tuple[str, dict]] = [
        (f"{prefix}/climate/{dev.node_id}/config", climate),
        _sensor(dev, t, device, prefix, "temperature", "Temperature",
                "{{ value_json.current_temperature }}",
                device_class="temperature", unit_of_measurement="°C",
                state_class="measurement"),
        _sensor(dev, t, device, prefix, "altitude", "Altitude",
                "{{ value_json.altitude }}", state_class="measurement",
                entity_category="diagnostic"),
        _sensor(dev, t, device, prefix, "machine_state", "Machine State",
                "{{ value_json.machine_state }}", entity_category="diagnostic"),
        _sensor(dev, t, device, prefix, "fault", "Fault",
                "{{ value_json.fault }}", entity_category="diagnostic"),
        _sensor(dev, t, device, prefix, "voltage", "Voltage",
                "{{ value_json.voltage }}", device_class="voltage",
                unit_of_measurement="V", state_class="measurement",
                entity_category="diagnostic"),
        _sensor(dev, t, device, prefix, "fan_rpm", "Fan Speed",
                "{{ value_json.fan_rpm }}", unit_of_measurement="rpm",
                state_class="measurement", entity_category="diagnostic"),
        _sensor(dev, t, device, prefix, "heater_power", "Heater Power",
                "{{ value_json.heater_power_w }}", device_class="power",
                unit_of_measurement="W", state_class="measurement",
                entity_category="diagnostic"),
        _sensor(dev, t, device, prefix, "prime_remaining", "Fuel Pump Prime Remaining",
                "{{ value_json.prime_remaining }}", device_class="duration",
                unit_of_measurement="s", entity_category="diagnostic"),
        # Fault as a binary sensor for automations / dashboard cards.
        (
            f"{prefix}/binary_sensor/{dev.node_id}_fault_active/config",
            {
                "name": "Fault Active",
                "unique_id": f"velit_{dev.node_id}_fault_active",
                "state_topic": t["state"],
                "value_template": "{{ value_json.fault_active }}",
                "payload_on": "true",
                "payload_off": "false",
                "device_class": "problem",
                "device": device,
                **_base_availability(t),
            },
        ),
        _switch(dev, t, device, prefix, "ble", "BLE Connection", "ble",
                "{% if value_json is defined %}ON{% else %}OFF{% endif %}",
                icon="mdi:bluetooth", entity_category="diagnostic"),
        _switch(dev, t, device, prefix, "prime", "Fuel Pump Prime", "prime",
                "{{ 'ON' if value_json.priming else 'OFF' }}",
                icon="mdi:fuel", entity_category="diagnostic"),
        _switch(dev, t, device, prefix, "cleaning", "Cleaning", "cleaning",
                "{{ 'ON' if value_json.cleaning else 'OFF' }}",
                icon="mdi:broom", entity_category="diagnostic"),
    ]
    return messages


def _ac_messages(dev, base_topic, prefix, firmware):
    t = _topics(base_topic, dev.node_id)
    device = _device_block(dev, firmware)

    climate = {
        "name": None,
        "unique_id": f"velit_{dev.node_id}_climate",
        "device": device,
        **_base_availability(t),
        "temperature_unit": "C",
        "min_temp": const.AC_MIN_TEMP_C,
        "max_temp": const.AC_MAX_TEMP_C,
        "temp_step": 1,
        "modes": [const.HVAC_MODE_OFF, const.HVAC_MODE_COOL, const.HVAC_MODE_FAN_ONLY],
        "mode_state_topic": t["state"],
        "mode_state_template": "{{ value_json.mode }}",
        "mode_command_topic": f"{t['cmd']}/mode",
        "action_topic": t["state"],
        "action_template": "{{ value_json.action }}",
        "current_temperature_topic": t["state"],
        "current_temperature_template": "{{ value_json.current_temperature }}",
        "temperature_state_topic": t["state"],
        "temperature_state_template": "{{ value_json.target_temperature }}",
        "temperature_command_topic": f"{t['cmd']}/temperature",
        "preset_modes": const.AC_PRESETS,
        "preset_mode_state_topic": t["state"],
        # Preset is null outside cool mode; fall back to "Cooling" so HA shows a value.
        "preset_mode_value_template": "{{ value_json.preset if value_json.preset else 'Cooling' }}",
        "preset_mode_command_topic": f"{t['cmd']}/preset",
        "fan_modes": const.FAN_MODES,
        "fan_mode_state_topic": t["state"],
        "fan_mode_state_template": "{{ value_json.fan }}",
        "fan_mode_command_topic": f"{t['cmd']}/fan",
    }

    messages: list[tuple[str, dict]] = [
        (f"{prefix}/climate/{dev.node_id}/config", climate),
        _sensor(dev, t, device, prefix, "temperature", "Temperature",
                "{{ value_json.current_temperature }}",
                device_class="temperature", unit_of_measurement="°C",
                state_class="measurement"),
        _sensor(dev, t, device, prefix, "fault", "Fault",
                "{{ value_json.fault }}", entity_category="diagnostic"),
        _switch(dev, t, device, prefix, "ble", "BLE Connection", "ble",
                "{% if value_json is defined %}ON{% else %}OFF{% endif %}",
                icon="mdi:bluetooth", entity_category="diagnostic"),
    ]
    return messages
