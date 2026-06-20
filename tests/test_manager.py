"""Tests for the runtime manager's non-network logic.

These avoid starting BLE/MQTT — they exercise device construction and the
read-only config summary the web UI depends on.
"""

from __future__ import annotations

from velit_mqtt.config import AppConfig, DeviceConfig, MqttConfig
from velit_mqtt.manager import BridgeManager, build_device


def test_build_device_types():
    heater = build_device(DeviceConfig(name="H", address="AA:BB:CC:DD:EE:FF", type="heater"))
    ac = build_device(DeviceConfig(name="A", address="11:22:33:44:55:66", type="ac"))
    assert heater.device_type == "heater"
    assert ac.device_type == "ac"


def test_config_summary_redacts_password():
    config = AppConfig(
        mqtt=MqttConfig(host="b", port=1883, password="secret"),
        devices=[DeviceConfig(name="Camper Heater", address="AA:BB:CC:DD:EE:FF", type="heater")],
    )
    manager = BridgeManager(config, "/tmp/unused.yaml")
    summary = manager.config_summary()

    # Password value is never exposed — only whether one is set.
    assert "password" not in summary["mqtt"]
    assert summary["mqtt"]["has_password"] is True
    assert summary["mqtt"]["connected"] is False

    assert len(summary["devices"]) == 1
    dev = summary["devices"][0]
    assert dev["node_id"] == "camper_heater"
    assert dev["type"] == "heater"
    # No device started yet, so it reports not connected.
    assert dev["connected"] is False
    assert dev["state"] == {}
