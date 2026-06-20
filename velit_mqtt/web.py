"""Web UI and JSON API for onboarding and MQTT settings.

A small aiohttp app served alongside the bridge. It lets you scan for Velit
devices over BLE, add or remove them, and edit the MQTT broker settings — all
applied live via the BridgeManager and persisted to the config file.

Bound to localhost by default (see WebConfig). If exposed beyond localhost, set
a ``token`` in the config: it is then required as an ``X-Auth-Token`` header or
``?token=`` query parameter on every request.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from .config import ConfigError, WebConfig
from .manager import BridgeManager

_LOGGER = logging.getLogger(__name__)

_INDEX = Path(__file__).parent / "web" / "index.html"
_MANAGER_KEY = web.AppKey("manager", BridgeManager)


def _manager(request: web.Request) -> BridgeManager:
    return request.app[_MANAGER_KEY]


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    token = _manager(request).config.web.token
    if token:
        supplied = request.headers.get("X-Auth-Token") or request.query.get("token")
        if supplied != token:
            return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(request)


@web.middleware
async def _error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except (ValueError, ConfigError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except KeyError as exc:
        return web.json_response({"error": f"Not found: {exc}"}, status=404)
    except web.HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Web request failed: %s %s", request.method, request.path)
        return web.json_response({"error": f"Internal error: {exc}"}, status=500)


async def _index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(_INDEX)


async def _get_config(request: web.Request) -> web.Response:
    return web.json_response(_manager(request).config_summary())


async def _scan(request: web.Request) -> web.Response:
    try:
        timeout = float(request.query.get("timeout", 8.0))
    except ValueError:
        timeout = 8.0
    timeout = max(3.0, min(timeout, 20.0))
    devices = await _manager(request).scan(timeout)
    return web.json_response({"devices": devices})


async def _add_device(request: web.Request) -> web.Response:
    body = await request.json()
    cfg = await _manager(request).add_device(body)
    return web.json_response({"node_id": cfg.node_id, "name": cfg.name}, status=201)


async def _remove_device(request: web.Request) -> web.Response:
    node_id = request.match_info["node_id"]
    await _manager(request).remove_device(node_id)
    return web.json_response({"removed": node_id})


async def _update_mqtt(request: web.Request) -> web.Response:
    body = await request.json()
    await _manager(request).update_mqtt(body)
    return web.json_response({"ok": True})


def create_app(manager: BridgeManager) -> web.Application:
    app = web.Application(middlewares=[_error_middleware, _auth_middleware])
    app[_MANAGER_KEY] = manager
    app.router.add_get("/", _index)
    app.router.add_get("/api/config", _get_config)
    app.router.add_get("/api/scan", _scan)
    app.router.add_post("/api/devices", _add_device)
    app.router.add_delete("/api/devices/{node_id}", _remove_device)
    app.router.add_put("/api/mqtt", _update_mqtt)
    return app


async def start_web(manager: BridgeManager, webcfg: WebConfig) -> web.AppRunner:
    """Start the web server and return its runner (call cleanup() to stop)."""
    app = create_app(manager)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, webcfg.host, webcfg.port)
    await site.start()
    _LOGGER.info("Web UI available on http://%s:%d", webcfg.host, webcfg.port)
    if webcfg.host not in ("127.0.0.1", "localhost", "::1") and not webcfg.token:
        _LOGGER.warning(
            "Web UI is bound to %s with no token set — anyone on the network can "
            "add devices and change settings. Set web.token in the config.",
            webcfg.host,
        )
    return runner
