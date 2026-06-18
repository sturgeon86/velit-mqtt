"""Service orchestration: wire devices to the MQTT bridge and run the loop."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from . import __version__, const
from .config import AppConfig, ConfigError, DeviceConfig, load_config
from .devices import VelitACDevice, VelitDevice, VelitHeaterDevice
from .mqtt_bridge import MqttBridge

_LOGGER = logging.getLogger(__name__)


def build_device(cfg: DeviceConfig) -> VelitDevice:
    """Instantiate the right device manager for a config entry."""
    if cfg.type == const.DEVICE_TYPE_HEATER:
        return VelitHeaterDevice(
            cfg.name, cfg.address, cfg.node_id, cfg.poll_interval, cfg.fallback_unit
        )
    return VelitACDevice(
        cfg.name, cfg.address, cfg.node_id, cfg.poll_interval, cfg.fallback_unit
    )


async def run_service(config: AppConfig) -> None:
    """Start the bridge and all device loops, and run until signalled to stop."""
    devices: dict[str, VelitDevice] = {}
    device_configs: dict[str, DeviceConfig] = {}
    for cfg in config.devices:
        devices[cfg.node_id] = build_device(cfg)
        device_configs[cfg.node_id] = cfg

    bridge = MqttBridge(config, devices, device_configs)
    for device in devices.values():
        device.on_state = bridge.publish_state
        device.on_availability = bridge.publish_availability

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, AttributeError):
            # Signal handlers are unavailable on some platforms (e.g. Windows).
            pass

    _LOGGER.info(
        "velit-mqtt %s starting — %d device(s), broker %s:%d",
        __version__, len(devices), config.mqtt.host, config.mqtt.port,
    )

    bridge_task = asyncio.create_task(bridge.run())
    for device in devices.values():
        await device.start()

    await stop_event.wait()

    _LOGGER.info("Shutting down...")
    bridge.stop()
    for device in devices.values():
        await device.stop()
    await bridge.announce_offline()
    bridge_task.cancel()
    try:
        await bridge_task
    except asyncio.CancelledError:
        pass


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="velit-mqtt",
        description="Bridge Velit BLE heaters and air conditioners to MQTT.",
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to the YAML config file (default: ./config.yaml or "
             "$VELIT_MQTT_CONFIG or /etc/velit-mqtt/config.yaml).",
    )
    parser.add_argument("--version", action="version", version=f"velit-mqtt {__version__}")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        # Logging may not be configured yet — print plainly and exit non-zero.
        print(f"Configuration error: {exc}")
        return 2

    _setup_logging(config.log_level)

    try:
        asyncio.run(run_service(config))
    except KeyboardInterrupt:
        pass
    return 0
