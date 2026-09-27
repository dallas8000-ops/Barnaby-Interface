"""WebSocket bridge for IDE extensions (VS Code, Cursor, Claude Desktop).

Security model: the same bar as the UI socket (server.py), adapted for
extensions, which connect from Node and therefore send **no** ``Origin``:

* Binds 127.0.0.1 only.
* Any request carrying an ``Origin`` header is refused at the handshake. Only
  browsers send one, so this blocks every web page, even one that guessed
  the port (cross-site WebSocket hijacking). Extensions are unaffected.
* The first frame must be ``{"type": "auth", "token": ...}`` within 5 s,
  compared in constant time. Only authenticated clients receive broadcasts;
  they carry transcripts and file paths.
* Extensions discover the port and token from a discovery file in the per-user
  data dir (``ide-bridge.json``). It's written atomically and owner-only on
  POSIX, and removed on stop. On Windows, %APPDATA% is already per-user.
* The configured port is preferred. If it's taken, an OS-assigned port is
  used and published in the discovery file, so a collision never stops
  the engine.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger(__name__)

AUTH_TIMEOUT_S = 5
MAX_FRAME = 256 * 1024


class IdeBridge:
    def __init__(self, port: int = 8765, token: str | None = None,
                 discovery_path: Path | None = None) -> None:
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        if len(self.token) < 32:
            raise ValueError("token must be at least 32 characters")
        self.discovery_path = discovery_path
        self.is_running = False
        self._server: Server | None = None
        self._clients: set[ServerConnection] = set()

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.is_running:
            return
        kwargs: dict[str, Any] = {"process_request": self._process_request, "max_size": MAX_FRAME,
                                  "ping_interval": 20, "ping_timeout": 20}
        try:
            self._server = await serve(self._handle_client, "127.0.0.1", self.port, **kwargs)
        except OSError as exc:
            log.warning("IDE bridge port %s unavailable (%s); using an OS-assigned port", self.port, exc)
            self._server = await serve(self._handle_client, "127.0.0.1", 0, **kwargs)
        self.port = self._server.sockets[0].getsockname()[1]
        self.is_running = True
        self._write_discovery()

    async def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        # Copy first: each close() lets the handler's finally discard from the set
        # mid-iteration ("Set changed size during iteration").
        for client in list(self._clients):
            await client.close()
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.discovery_path:
            try:
                self.discovery_path.unlink()
            except OSError:
                pass

    def _write_discovery(self) -> None:
        if not self.discovery_path:
            return
        self.discovery_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.discovery_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, fh)
        os.replace(tmp, self.discovery_path)

    # ------------------------------------------------------------ connections
    def _process_request(self, conn: ServerConnection, request: Request) -> Response | None:
        if request.headers.get("Origin") is not None:
            log.warning("IDE bridge refused a browser origin %r", request.headers.get("Origin"))
            return conn.respond(403, "Forbidden\n")
        if request.headers.get("Upgrade", "").lower() != "websocket":
            return conn.respond(426, "WebSocket required\n")
        return None

    async def _handle_client(self, websocket: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=AUTH_TIMEOUT_S)
            msg = json.loads(raw)
            token = msg.get("token") if isinstance(msg, dict) and msg.get("type") == "auth" else None
            if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self.token.encode()):
                raise PermissionError
        except (asyncio.TimeoutError, json.JSONDecodeError, PermissionError, ConnectionClosed, TypeError):
            await websocket.close(code=4401, reason="unauthorized")
            return

        self._clients.add(websocket)
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    await self._handle_ide_message(websocket, data)
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _handle_ide_message(self, websocket: ServerConnection, data: dict[str, Any]) -> None:
        """Messages from IDE extensions. Nothing is acted on yet: context updates will
        feed the orchestrator once the extension protocol is defined and validated."""
        return None

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send an event to every authenticated IDE client."""
        if not self._clients:
            return
        message = json.dumps(event, default=str)
        await asyncio.gather(*(c.send(message) for c in list(self._clients)), return_exceptions=True)

    def get_client_count(self) -> int:
        return len(self._clients)
