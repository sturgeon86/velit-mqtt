"""Velit BLE-to-MQTT bridge.

A standalone service that connects to Velit camping heaters and air
conditioners over Bluetooth Low Energy and exposes them on an MQTT broker.

It reuses the Velit wire-protocol implementation (packet construction,
parsing, checksums) and replaces the Home Assistant runtime with a plain
asyncio service: BLE polling on one side, MQTT publish/subscribe on the other.

Entry point: ``python -m velit_mqtt`` (see service.py / __main__.py).
"""

from __future__ import annotations

__version__ = "0.1.0"
