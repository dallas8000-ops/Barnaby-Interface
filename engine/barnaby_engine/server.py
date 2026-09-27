"""Localhost WebSocket bridge between the UI and the orchestrator.

Security model:
* Binds 127.0.0.1 only, OS-assigned port (announced on stdout to the parent).
* The first frame must be ``{"type":"auth","token":...}`` matching a secret the
  Tauri shell generated and handed to both sides; compared in constant time,
  5 s deadline. Browsers cannot set headers on WebSocket, and a query-string
  token would land in logs, hence first-frame auth.
* Browser pages always send ``Origin``; any origin outside the allowlist is
  refused at the handshake. That closes cross-site WebSocket hijacking from a
  malicious web page even if it guessed the port.
* One active client. A newer authenticated connection replaces the old one.
* Every inbound frame is schema-validated (pydantic) before dispatch.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Any

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .protocol import PROTOCOL_VERSION, Auth, ev, ev_error, parse_command

log = logging.getLogger(__name__)

DEFAULT_ORIGINS = frozenset({
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",   # vite dev server
    "http://127.0.0.1:1420",
})
AUTH_TIMEOUT_S = 5
MAX_FRAME = 256 * 1024


class Bridge:
    def __init__(self, token: str, allowed_origins: frozenset[str] = DEFAULT_ORIGINS) -> None:
        if len(token) < 32:
            raise ValueError("token must be at least 32 characters")
        self._token = token.encode()
        self._origins = allowed_origins
        self._client: ServerConnection | None = None
        self._send_lock = asyncio.Lock()
        self.orchestrator: Any = None  # set by main before serving

    async def emit(self, event: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            return
        try:
            async with self._send_lock:
                await client.send(json.dumps(event, default=str))
        except ConnectionClosed:
            pass

    def _process_request(self, _conn: ServerConnection, request: Request) -> Response | None:
        if request.headers.get("Upgrade", "").lower() != "websocket":
            # Plain HTTP (readiness probes): answer instead of logging a failed handshake.
            return _conn.respond(200, "barnaby engine ok\n")
        origin = request.headers.get("Origin")
        if origin is not None and origin not in self._origins:
            log.warning("rejected websocket origin %r", origin)
            return _conn.respond(403, "Forbidden origin\n")
        return None

    async def _handler(self, ws: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=AUTH_TIMEOUT_S)
            cmd = parse_command(raw)
            if not isinstance(cmd, Auth) or not hmac.compare_digest(cmd.token.encode(), self._token):
                raise PermissionError
        except (asyncio.TimeoutError, ValidationError, PermissionError, ConnectionClosed):
            await ws.close(code=4401, reason="unauthorized")
            return

        previous, self._client = self._client, ws
        if previous is not None:
            await previous.close(code=4000, reason="replaced by newer client")
        hello = await self.orchestrator.hello()
        hello["protocol"] = PROTOCOL_VERSION
        await self.emit(hello)

        try:
            async for raw in ws:
                try:
                    cmd = parse_command(raw)
                except ValidationError as exc:
                    await self.emit(ev_error("bad_command", exc.errors(include_url=False)[0]["msg"]))
                    continue
                if isinstance(cmd, Auth):
                    continue
                try:
                    await self.orchestrator.handle(cmd)
                except Exception as exc:  # noqa: BLE001 - one bad command must not drop the socket
                    log.exception("command failed: %s", cmd.type)
                    await self.emit(ev_error("internal", str(exc)))
        except ConnectionClosed:
            pass
        finally:
            if self._client is ws:
                self._client = None
                # Releasing the mic if the UI vanished mid push-to-talk.
                if getattr(self.orchestrator, "_recording", False):
                    await self.orchestrator.handle(parse_command(json.dumps({"type": "cancel"})))

    async def serve(self, host: str = "127.0.0.1", port: int = 0):
        return await serve(self._handler, host, port, process_request=self._process_request,
                           max_size=MAX_FRAME, ping_interval=20, ping_timeout=20)


def allowed_origins_from_env() -> frozenset[str]:
    extra = {o.strip() for o in os.environ.get("BARNABY_ALLOWED_ORIGINS", "").split(",") if o.strip()}
    return DEFAULT_ORIGINS | extra


__all__ = ["Bridge", "allowed_origins_from_env", "ev"]
