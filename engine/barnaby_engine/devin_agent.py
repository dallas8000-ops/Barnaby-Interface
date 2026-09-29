"""Devin as a Barnaby engine, over Devin's v1 REST API.

Devin is unlike the Claude and Cursor engines. It runs in Devin's cloud on its
own machine, against repositories connected in Devin, not on this PC's files.
It is asynchronous: a turn starts a Devin session (or messages the existing
one), then polls it and streams each new Devin message into the transcript.

A turn ends when Devin is ``blocked`` (waiting for you: its last message is the
question) or ``finished``, or the session expires. Two cases need care:

* Right after a follow-up message the session can still report ``blocked``
  from before. That is not the answer, so a turn only ends once Devin has shown
  activity for it: a new message, or a working/resumed status.
* A long task outlasting ``watch_timeout_s`` is not a failure. Barnaby stops
  watching and says where to follow it; Devin carries on. Stop does the same:
  v1 has no documented way to terminate a session, so Stop ends Barnaby's turn
  and leaves Devin's session running (say so rather than pretend otherwise).

Security:
* The key comes from the vault per request and is only ever sent in the
  Authorization header, over HTTPS, to the fixed API host.
* Session ids are validated before use in a URL path.
* The repository hint is taken from ``.git/config`` with any embedded
  credentials (``https://user:token@host``) stripped, so a token in a remote URL
  never reaches Devin.
* Response bodies are size-capped. Devin's text is displayed and spoken as
  data; nothing in it is executed.
"""
from __future__ import annotations

import asyncio
import configparser
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from .agent import AgentError, AgentEvent, SessionStarted, TextDelta, ToolEnd, ToolStart, TurnResult

log = logging.getLogger(__name__)

API_BASE = "https://api.devin.ai/v1"
MAX_BODY = 5 * 1024 * 1024
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
_DONE = frozenset({"blocked", "finished"})
_ACTIVE = frozenset({"working", "resumed"})

VOICE_NOTE = (
    "Sent from Barnaby, a voice interface. Your messages are read aloud: open each one with "
    "one or two plain sentences saying the outcome or what you need, and put code, logs and "
    "long lists after that."
)


class DevinApiError(AgentError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


class DevinClient:
    """Minimal JSON client. Blocking urllib runs in a worker thread (no new dependency)."""

    def __init__(self, get_key: Callable[[], str | None], *, base: str = API_BASE, timeout_s: float = 30.0,
                 opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        if not base.startswith("https://"):
            raise ValueError("Devin API base must be https")
        self._get_key = get_key
        self._base = base.rstrip("/")
        self._timeout = timeout_s
        self._open = opener

    def has_key(self) -> bool:
        return bool(self._get_key())

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._call, method, path, body)

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        key = self._get_key()
        if not key:
            raise DevinApiError("No Devin API key saved. Add it in Settings → Devin.")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {key}", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {})})
        try:
            with self._open(req, timeout=self._timeout) as resp:
                raw = resp.read(MAX_BODY + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in (401, 403):
                raise DevinApiError("Devin rejected the API key. Check it in Settings → Devin "
                                    "(personal keys start with apk_user_).", status) from None
            if status == 404:
                raise DevinApiError("Devin couldn't find that session.", status) from None
            if status == 429:
                raise DevinApiError("Devin is rate limiting requests.", status,
                                    _retry_after(exc.headers.get("Retry-After") if exc.headers else None)) from None
            raise DevinApiError(f"Devin's API returned HTTP {status}.", status) from None
        except urllib.error.URLError as exc:
            raise DevinApiError(f"Couldn't reach Devin: {exc.reason}") from None
        except TimeoutError:
            raise DevinApiError("Devin's API didn't answer in time.") from None
        if len(raw) > MAX_BODY:
            raise DevinApiError("Devin's API sent an unexpectedly large response.")
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise DevinApiError("Devin's API sent a response that isn't JSON.") from None


def repo_hint(workspace: str) -> str | None:
    """The workspace's ``origin`` remote, credentials stripped, for Devin to find the repo."""
    cfg_path = Path(workspace) / ".git" / "config"
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")[:65536]
    except OSError:
        return None
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    url = parser.get('remote "origin"', "url", fallback="").strip()
    if not url or any(c.isspace() for c in url):
        return None
    if re.match(r"^[\w.-]+@[\w.-]+:[\w./-]+$", url):  # scp-style git@github.com:owner/repo.git
        return url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http", "ssh") or not parts.hostname:
        return None
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    user = f"{parts.username}@" if parts.scheme == "ssh" and parts.username and not parts.password else ""
    return urllib.parse.urlunsplit((parts.scheme, user + host, parts.path, "", ""))


def build_prompt(prompt: str, workspace: str) -> str:
    lines = [prompt.strip(), "", "---", VOICE_NOTE]
    repo = repo_hint(workspace) if workspace else None
    if repo:
        lines.append(f"Repository: {repo} (local folder: {Path(workspace).name})")
    return "\n".join(lines)


def _title(prompt: str) -> str:
    first = " ".join(prompt.split())
    return first if len(first) <= 80 else first[:77].rstrip() + "..."


class DevinAgent:
    def __init__(self, client: DevinClient, *, poll_s: float = 5.0, watch_timeout_s: float = 1800.0,
                 max_errors: int = 5, clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self.poll_s = poll_s
        self.watch_timeout_s = watch_timeout_s
        self.max_errors = max_errors
        self._clock = clock
        self._stop = asyncio.Event()
        self._urls: dict[str, str] = {}

    async def cancel(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def run(self, prompt: str, *, workspace: str, session_id: str | None,
                  grant: Any = None, scope: Any = None) -> AsyncIterator[AgentEvent]:
        # scope (folder lanes) is advisory for Devin: it works on its own cloud copy of
        # the repo, and the task preamble already names the folders.
        self._stop = asyncio.Event()
        started = self._clock()
        seen: set[str] = set()
        notice: str | None = None

        if session_id is not None and not _SESSION_RE.match(session_id):
            session_id = None  # not a Devin id (e.g. a stale value): start fresh
        if session_id is not None:
            try:
                current = await self.client.call("GET", f"/sessions/{session_id}")
            except DevinApiError as exc:
                if exc.status != 404:
                    raise
                current, session_id = None, None
                notice = "The previous Devin session is gone, so I started a new one."
            if current is not None and (current.get("status_enum") == "expired"):
                session_id = None
                notice = "The previous Devin session expired, so I started a new one."
            elif current is not None:
                seen = {str(m.get("event_id")) for m in current.get("messages") or [] if isinstance(m, dict)}
                reply = await self.client.call("POST", f"/sessions/{session_id}/message", {"message": prompt})
                if isinstance(reply, dict) and reply.get("detail"):
                    log.info("Devin message reply: %s", str(reply["detail"])[:200])

        if session_id is None:
            created = await self.client.call("POST", "/sessions", {
                "prompt": build_prompt(prompt, workspace), "title": _title(prompt), "tags": ["barnaby"]})
            if not isinstance(created, dict) or not _SESSION_RE.match(str(created.get("session_id", ""))):
                raise AgentError("Devin didn't return a session id.")
            session_id = str(created["session_id"])
            if isinstance(created.get("url"), str) and created["url"].startswith("https://"):
                self._urls[session_id] = created["url"]

        yield SessionStarted(session_id=session_id, model=None)
        url = self._urls.get(session_id)
        yield ToolStart(tool_id="devin-session", name="Devin", input={"session_id": session_id, **({"url": url} if url else {})})
        if notice:
            yield TextDelta(notice + "\n\n")

        texts: list[str] = []
        active = False
        errors = 0
        delay = self.poll_s
        status: str | None = None
        pr_url: str | None = None
        outcome = "watching"
        while True:
            await self._sleep(delay)
            if self._stop.is_set():
                outcome = "stopped"
                break
            if self._clock() - started > self.watch_timeout_s:
                outcome = "timeout"
                break
            try:
                detail = await self.client.call("GET", f"/sessions/{session_id}")
            except DevinApiError as exc:
                if exc.status in (401, 403, 404):
                    raise
                errors += 1
                if errors >= self.max_errors:
                    raise AgentError(f"{exc} Gave up after {errors} tries; the session may still be running.") from None
                delay = min(max(exc.retry_after or 0, delay * 2), 60.0)
                continue
            errors, delay = 0, self.poll_s
            if not isinstance(detail, dict):
                continue
            for msg in detail.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                eid = str(msg.get("event_id"))
                if eid in seen:
                    continue
                seen.add(eid)
                text = str(msg.get("message") or "").strip()
                if msg.get("type") == "devin_message" and text:
                    active = True
                    texts.append(text)
                    yield TextDelta(text + "\n\n")
            status = detail.get("status_enum") or detail.get("status")
            pr = detail.get("pull_request")
            if isinstance(pr, dict) and isinstance(pr.get("url"), str) and pr["url"].startswith("https://"):
                pr_url = pr["url"]
            if status in _ACTIVE:
                active = True
            if status == "expired":
                outcome = "expired"
                break
            if status in _DONE and active:
                outcome = status
                break

        where = f" Follow it at {url}." if url else " It's in your Devin sessions list."
        tail = {
            "blocked": "",
            "finished": "" if texts else "Devin finished.",
            "expired": "The Devin session expired.",
            "timeout": f"Devin is still working. I stopped watching after {int(self.watch_timeout_s // 60)} minutes.{where}",
            "stopped": f"Stopped watching. Devin's session keeps running in the cloud.{where}",
        }[outcome]
        parts = texts + ([tail] if tail else [])
        if pr_url:
            parts.append(f"Pull request: {pr_url}")
        yield ToolEnd(tool_id="devin-session", ok=outcome != "expired", preview=pr_url or url or session_id)
        yield TurnResult(text="\n\n".join(parts), session_id=session_id, is_error=outcome == "expired" and not texts,
                         cost_usd=None, duration_ms=int((self._clock() - started) * 1000))
