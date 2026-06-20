"""Service orchestration: run the bridge, devices, and the web UI together."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from . import __version__
from .config import AppConfig, ConfigError, load_config, resolve_config_path
from .manager import BridgeManager
from .web import start_web

_LOGGER = logging.getLogger(__name__)


async def run_service(config: AppConfig, config_path: str) -> None:
    """Start the bridge, device loops, and (if enabled) the web UI; run until stopped."""
    manager = BridgeManager(config, config_path)

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
        __version__, len(config.devices), config.mqtt.host, config.mqtt.port,
    )

    await manager.start()

    web_runner = None
    if config.web.enabled:
        web_runner = await start_web(manager, config.web)

    await stop_event.wait()

    _LOGGER.info("Shutting down...")
    if web_runner is not None:
        await web_runner.cleanup()
    await manager.stop()


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

    path = resolve_config_path(args.config)
    try:
        config = load_config(path)
    except ConfigError as exc:
        # Logging may not be configured yet — print plainly and exit non-zero.
        print(f"Configuration error: {exc}")
        return 2

    _setup_logging(config.log_level)

    try:
        asyncio.run(run_service(config, path))
    except KeyboardInterrupt:
        pass
    return 0
