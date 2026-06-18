"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest

from velit_mqtt.config import ConfigError, load_config

_VALID = """
mqtt:
  host: broker.local
  port: 8883
  username: user
  base_topic: velit
  discovery: false
devices:
  - name: Camper Heater
    address: aa:bb:cc:dd:ee:ff
    type: heater
    poll_interval: 15
  - name: Camper AC
    address: 11:22:33:44:55:66
    type: ac
log_level: debug
"""


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_valid(tmp_path):
    cfg = load_config(_write(tmp_path, _VALID))
    assert cfg.mqtt.host == "broker.local"
    assert cfg.mqtt.port == 8883
    assert cfg.mqtt.discovery is False
    assert cfg.log_level == "DEBUG"
    assert len(cfg.devices) == 2

    heater, ac = cfg.devices
    assert heater.node_id == "camper_heater"   # derived from the name
    assert heater.address == "AA:BB:CC:DD:EE:FF"  # normalised to upper-case
    assert heater.poll_interval == 15
    assert ac.type == "ac"
    assert ac.node_id == "camper_ac"


def test_password_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_PW", "s3cret")
    text = """
mqtt:
  host: localhost
  password_env: MY_PW
devices:
  - name: H
    address: aa:bb:cc:dd:ee:ff
    type: heater
"""
    cfg = load_config(_write(tmp_path, text))
    assert cfg.mqtt.password == "s3cret"


def test_missing_devices(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "mqtt:\n  host: localhost\n"))


def test_unknown_device_type(tmp_path):
    text = """
devices:
  - name: Mystery
    address: aa:bb:cc:dd:ee:ff
    type: toaster
"""
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))


def test_missing_required_field(tmp_path):
    text = """
devices:
  - name: NoAddress
    type: heater
"""
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))


def test_duplicate_node_id(tmp_path):
    text = """
devices:
  - name: Same Name
    address: aa:bb:cc:dd:ee:ff
    type: heater
  - name: Same Name
    address: 11:22:33:44:55:66
    type: ac
"""
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))


def test_missing_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/path/to/config.yaml")
