"""WebSocket hub between the UIs and the orchestrator.

Two kinds of client share one hub:

* **desktop**: the Barnaby window, over this module's own listener.
  * Binds 127.0.0.1 only, OS-assigned port (announced on stdout to the parent).
  * The first frame must be ``{"type":"auth","token":...}`` matching a secret the
    Tauri shell generated and handed to both sides; compared in constant time,
    5 s deadline. Browsers cannot set headers on WebSocket, and a query-string
    token would land in logs, hence first-frame auth.
  * Browser pages always send ``Origin``; any origin outside the allowlist is
    refused at the handshake. That closes cross-site WebSocket hijacking from a
    malicious web page even if it guessed the port.
  * One desktop client. A newer authenticated connection replaces the old one.
* **remote**: a paired tablet, admitted by ``remote.RemoteServer`` (TLS, device
  tokens), which hands the socket to :meth:`Bridge.run_client`. One connection
  per device (newer replaces older), at most ``MAX_REMOTE_CLIENTS`` in total.

Every event is broadcast to all clients; ``hello`` goes only to the newcomer.
Every inbound frame is schema-validated (pydantic) and then checked against the
client's role by :func:`permit` before dispatch. A tablet can do ordinary work
(instructions, approvals, sessions) but can't change what the PC trusts: the
project folder, API keys, the microphone, tablet access itself.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .protocol import PROTOCOL_VERSION, Auth, Command, SetConfig, ev, ev_error, parse_command

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
MAX_REMOTE_CLIENTS = 4
# A client that stops reading (a frozen tab) must not stall every other client:
# a send that can't complete in this time drops that client.
SEND_TIMEOUT_S = 5.0

# What a tablet may do. Commands are denied unless listed; set_config keys likewise.
REMOTE_COMMANDS = frozenset({
    "text_submit", "discard_draft", "cancel", "stop_speaking", "approve", "deny",
    "new_session", "load_session", "list_sessions", "set_config", "ping",
    "get_voice_profile", "list_templates", "run_template", "audio_clip",
})
REMOTE_CONFIG_KEYS = frozenset({"engine", "review_transcripts", "claude_model", "cursor_model"})
REMOTE_ONLY = frozenset({"audio_clip"})  # the PC has its own microphone path


def permit(role: str, cmd: Command) -> str | None:
    """None if ``role`` may run ``cmd``, else a short reason safe to show."""
    if role == "desktop":
        return "Only a paired tablet sends recorded audio." if cmd.type in REMOTE_ONLY else None
    if cmd.type not in REMOTE_COMMANDS:
        return "That can only be done on the PC."
    if isinstance(cmd, SetConfig):
        blocked = sorted(set(cmd.values) - REMOTE_CONFIG_KEYS)
        if blocked:
            return f"Only the PC can change: {', '.join(blocked)}."
    return None


@dataclass(eq=False)
class ClientInfo:
    role: str                      # "desktop" | "remote"
    device_id: str | None = None   # remote clients: the paired device
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Bridge:
    def __init__(self, token: str, allowed_origins: frozenset[str] = DEFAULT_ORIGINS) -> None:
        if len(token) < 32:
            raise ValueError("token must be at least 32 characters")
        self._token = token.encode()
        self._origins = allowed_origins
        self._clients: dict[ServerConnection, ClientInfo] = {}
        self.orchestrator: Any = None  # set by main before serving

    # ------------------------------------------------------------ outbound
    async def _send(self, ws: ServerConnection, info: ClientInfo, event: dict[str, Any]) -> None:
        try:
            async with info.lock:
                await asyncio.wait_for(ws.send(json.dumps(event, default=str)), SEND_TIMEOUT_S)
        except ConnectionClosed:
            pass
        except asyncio.TimeoutError:
            log.warning("dropping a %s client that stopped reading", info.role)
            self._drop(ws, info)

    def _drop(self, ws: Any, info: ClientInfo) -> None:
        if self._clients.get(ws) is info:
            del self._clients[ws]
            if info.role == "remote":
                self._notify_remote_count()
        transport = getattr(ws, "transport", None)
        if transport is not None:
            transport.abort()  # frees the stuck buffers now; no close handshake with a dead peer

    async def emit(self, event: dict[str, Any]) -> None:
        clients = list(self._clients.items())
        if clients:
            await asyncio.gather(*(self._send(ws, info, event) for ws, info in clients))

    async def emit_desktop(self, event: dict[str, Any]) -> None:
        """Events a tablet must not see (e.g. a pairing link with its one-time code)."""
        clients = [(ws, i) for ws, i in self._clients.items() if i.role == "desktop"]
        if clients:
            await asyncio.gather(*(self._send(ws, info, event) for ws, info in clients))

    def remote_count(self) -> int:
        return sum(1 for i in self._clients.values() if i.role == "remote")

    def connected_devices(self) -> set[str]:
        return {i.device_id for i in self._clients.values() if i.role == "remote" and i.device_id}

    async def disconnect_device(self, device_id: str, *, code: int = 4403, reason: str = "device removed") -> bool:
        """Close a device's live connection. 4403 means it was un-paired (the tablet forgets
        its token); pass 1001 when tablet access is merely stopping."""
        hit = [ws for ws, i in self._clients.items() if i.role == "remote" and i.device_id == device_id]
        for ws in hit:
            await ws.close(code=code, reason=reason)
        return bool(hit)

    def _notify_remote_count(self) -> None:
        notify = getattr(self.orchestrator, "set_remote_listeners", None)
        if notify is not None:
            notify(self.remote_count())

    # ------------------------------------------------------------ local listener
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
        await self.run_client(ws, ClientInfo(role="desktop"))

    # ------------------------------------------------------------ shared client loop
    async def run_client(self, ws: ServerConnection, info: ClientInfo,
                         admit: Callable[[], bool] | None = None) -> None:
        """Serve an authenticated socket until it closes.

        Registration is synchronous (no await between the cap check, removing the
        replaced socket and adding this one), and ``admit`` is re-checked right
        after, so a device revoked while it was reconnecting can't slip in.
        """
        if info.role == "desktop":
            stale = [w for w, i in self._clients.items() if i.role == "desktop"]
        else:
            stale = [w for w, i in self._clients.items() if i.role == "remote" and i.device_id == info.device_id]
            if self.remote_count() - len(stale) >= MAX_REMOTE_CLIENTS:
                await ws.close(code=4430, reason="too many tablets connected")
                return
        for old in stale:
            self._clients.pop(old, None)
        self._clients[ws] = info
        if admit is not None and not admit():
            del self._clients[ws]
            await ws.close(code=4403, reason="device removed")
            return
        for old in stale:  # an unresponsive peer can take seconds to close: don't wait for it
            asyncio.get_running_loop().create_task(old.close(code=4000, reason="replaced by newer client"))
        if info.role == "remote":
            self._notify_remote_count()
        try:
            hello = await self.orchestrator.hello()
            hello["protocol"] = PROTOCOL_VERSION
            hello["role"] = info.role
            await self._send(ws, info, hello)
            async for raw in ws:
                try:
                    cmd = parse_command(raw)
                except ValidationError as exc:
                    await self._send(ws, info, ev_error("bad_command", exc.errors(include_url=False)[0]["msg"]))
                    continue
                if isinstance(cmd, Auth):
                    continue
                reason = permit(info.role, cmd)
                if reason is None and info.role == "remote":
                    check = getattr(self.orchestrator, "remote_check", None)
                    reason = check(cmd) if check is not None else None
                if reason:
                    await self._send(ws, info, ev_error("not_allowed", reason))
                    continue
                try:
                    await self.orchestrator.handle(cmd)
                except Exception as exc:  # noqa: BLE001 - one bad command must not drop the socket
                    log.exception("command failed: %s", cmd.type)
                    await self._send(ws, info, ev_error("internal", str(exc)))
        except ConnectionClosed:
            pass
        finally:
            if self._clients.get(ws) is info:
                del self._clients[ws]
                if info.role == "remote":
                    self._notify_remote_count()
                elif getattr(self.orchestrator, "_recording", False):
                    # Releasing the PC mic if the window vanished mid push-to-talk.
                    await self.orchestrator.handle(parse_command(json.dumps({"type": "cancel"})))

    async def serve(self, host: str = "127.0.0.1", port: int = 0):
        return await serve(self._handler, host, port, process_request=self._process_request,
                           max_size=MAX_FRAME, ping_interval=20, ping_timeout=20)


def allowed_origins_from_env() -> frozenset[str]:
    extra = {o.strip() for o in os.environ.get("BARNABY_ALLOWED_ORIGINS", "").split(",") if o.strip()}
    return DEFAULT_ORIGINS | extra


__all__ = ["Bridge", "ClientInfo", "permit", "allowed_origins_from_env", "ev"]
