"""Claude Code adapter: runs one turn of ``claude -p`` and streams typed events.

Design constraints (all load-bearing):

* The prompt goes over **stdin**, never argv. On Windows an npm-installed
  ``claude.cmd`` is executed through cmd.exe, which re-parses argv; a spoken
  transcript containing ``&`` or ``|`` would otherwise be a command-injection
  vector. Every argv element here is a fixed flag, a validated UUID, a
  validated model alias, or a path we generated.
* Permissions travel in a per-turn ``--settings`` file, so approvals are scoped
  to one resumed turn and never persist into the user's own settings.
* stdout lines from ``stream-json`` can exceed asyncio's 64 KiB default
  StreamReader limit (large tool results); the limit is raised explicitly,
  otherwise ``readline`` raises LimitOverrunError mid-turn.
* Cancellation kills the whole process tree: Claude Code spawns shells, and
  killing only the node process orphans a running ``npm test``.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from .safety import Grant, Scope, valid_session_id

VOICE_SYSTEM_PROMPT = """\
You are being driven by voice through an app called Barnaby.
- The user's messages are speech-to-text transcripts. Technical terms may be
  misheard (e.g. "zoo stand" for Zustand). Interpret charitably from context.
  If a destructive or irreversible action depends on an ambiguous word, ask
  instead of guessing.
- Your final reply is read aloud. Open with one or two plain sentences stating
  the outcome. Put code, diffs, paths and command output in fenced blocks or
  after the summary; those are shown on screen, not spoken.
- Do not read secrets, tokens or .env values back in prose.
"""

_STREAM_LIMIT = 32 * 1024 * 1024
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_\[\]]{0,63}$")


# ------------------------------------------------------------------ events
@dataclass(frozen=True)
class SessionStarted:
    session_id: str
    model: str | None


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolStart:
    tool_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolEnd:
    tool_id: str
    ok: bool
    preview: str


@dataclass(frozen=True)
class Denial:
    tool_id: str
    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class TurnResult:
    text: str
    session_id: str | None
    is_error: bool
    cost_usd: float | None
    duration_ms: int | None
    denials: tuple[Denial, ...] = field(default_factory=tuple)


AgentEvent = SessionStarted | TextDelta | ToolStart | ToolEnd | TurnResult


class AgentError(RuntimeError):
    pass


class Agent(Protocol):
    def run(self, prompt: str, *, workspace: str, session_id: str | None,
            grant: Grant | None, scope: Scope | None = None) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self) -> None: ...


# ------------------------------------------------------------------ parser
def _preview(content: Any, limit: int = 600) -> str:
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    text = str(content or "")
    return text if len(text) <= limit else text[:limit] + "…"


SIGN_IN_HINT = ("\n\nClaude Code needs you to sign in again. Open a terminal, run `claude`, then type /login. "
                "If you set CLAUDE_CODE_OAUTH_TOKEN, replace it with a fresh `claude setup-token` or remove it.")


class StreamParser:
    """Turns stream-json objects into AgentEvents.

    Works with and without ``--include-partial-messages``: when text deltas were
    streamed for a message id, the aggregated ``assistant`` message's text is
    not re-emitted (that would double every sentence in the UI and in speech).
    Tool starts come from the aggregated message, where ``input`` is complete.
    """

    def __init__(self) -> None:
        self._streamed_msg_ids: set[str] = set()
        self._current_msg_id: str | None = None
        self._tools_seen: set[str] = set()
        self._text_msg_ids: set[str] = set()  # messages that already produced text

    def _text(self, msg_id: str | None, text: str) -> TextDelta:
        # Separate assistant messages (text / tools / more text) with a paragraph
        # break; raw concatenation yields "suite first.All tests pass".
        key = msg_id or "?"
        prefix = "\n\n" if self._text_msg_ids and key not in self._text_msg_ids else ""
        self._text_msg_ids.add(key)
        return TextDelta(prefix + text)

    def feed(self, obj: dict[str, Any]) -> list[AgentEvent]:
        out: list[AgentEvent] = []
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            sid = obj.get("session_id")
            if isinstance(sid, str):
                out.append(SessionStarted(sid, obj.get("model")))
        elif kind == "stream_event":
            if obj.get("parent_tool_use_id"):
                return out  # subagent chatter: not user-facing speech
            event = obj.get("event") or {}
            etype = event.get("type")
            if etype == "message_start":
                self._current_msg_id = (event.get("message") or {}).get("id")
            elif etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    if self._current_msg_id:
                        self._streamed_msg_ids.add(self._current_msg_id)
                    out.append(self._text(self._current_msg_id, delta["text"]))
        elif kind == "assistant":
            if obj.get("parent_tool_use_id"):
                return out
            msg = obj.get("message") or {}
            streamed = msg.get("id") in self._streamed_msg_ids
            for block in msg.get("content") or []:
                btype = block.get("type")
                if btype == "text" and not streamed and block.get("text"):
                    out.append(self._text(msg.get("id"), block["text"]))
                elif btype == "tool_use":
                    tid = str(block.get("id", ""))
                    if tid and tid not in self._tools_seen:
                        self._tools_seen.add(tid)
                        out.append(ToolStart(tid, str(block.get("name", "")), dict(block.get("input") or {})))
        elif kind == "user":
            if obj.get("parent_tool_use_id"):
                return out
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append(ToolEnd(str(block.get("tool_use_id", "")),
                                       not bool(block.get("is_error")),
                                       _preview(block.get("content"))))
        elif kind == "result":
            denials = tuple(
                Denial(str(d.get("tool_use_id", "")), str(d.get("tool_name", "")), dict(d.get("tool_input") or {}))
                for d in obj.get("permission_denials") or [] if isinstance(d, dict)
            )
            text = str(obj.get("result") or "")
            is_error = bool(obj.get("is_error")) or obj.get("subtype") != "success"
            if is_error and (obj.get("api_error_status") == 401 or "authenticate" in text.lower()):
                text += SIGN_IN_HINT
            out.append(TurnResult(
                text=text,
                session_id=obj.get("session_id"),
                is_error=is_error,
                cost_usd=obj.get("total_cost_usd"),
                duration_ms=obj.get("duration_ms"),
                denials=denials,
            ))
        return out


# ------------------------------------------------------------------ runner
def resolve_claude(override: str = "") -> str:
    candidate = override or shutil.which("claude") or ""
    if not candidate or not Path(candidate).exists():
        raise AgentError(
            "Claude Code was not found. Install it (https://docs.claude.com/en/docs/claude-code) "
            "and sign in once by running `claude` in a terminal."
        )
    return candidate


def build_settings(safe_bash: list[str], grant: Grant | None, scope: Scope | None = None) -> dict[str, Any]:
    """Per-turn permissions. With task lanes the turn runs in ``default`` mode and may
    edit only its lanes (anything else comes back as a permission denial, i.e. an
    approval card). With busy lanes the conversation keeps ``acceptEdits`` but must
    ask before editing a folder a background task holds. Verified on Claude Code
    2.1.282; see the task-board spec."""
    allow = [f"Bash({rule})" for rule in safe_bash]
    add_dirs: list[str] = []
    if grant:
        allow.extend(grant.allow)
        add_dirs.extend(grant.add_dirs)
    mode = "acceptEdits"
    ask: list[str] = []
    if scope and scope.allow_lanes:
        mode = "default"
        allow.extend(f"Edit(./{lane}/**)" for lane in scope.allow_lanes)
    if scope and scope.ask_lanes:
        opened = {x.casefold() for x in (grant.open_lanes if grant else ())}
        # "" = a whole-project task is running: ask before any edit.
        ask = [f"Edit(./{lane}/**)" if lane else "Edit(./**)"
               for lane in scope.ask_lanes if lane.casefold() not in opened]
    perms: dict[str, Any] = {"allow": allow, "defaultMode": mode}
    if ask:
        perms["ask"] = ask
    if add_dirs:
        perms["additionalDirectories"] = add_dirs
    return {"permissions": perms}


_PERMISSION_MODES = frozenset({"acceptEdits", "default"})


def build_argv(claude: str, *, settings_path: str, prompt_path: str,
               session_id: str | None, model: str, permission_mode: str = "acceptEdits") -> list[str]:
    if permission_mode not in _PERMISSION_MODES:
        raise ValueError(f"unsupported permission mode {permission_mode!r}")
    argv = [
        claude, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--permission-mode", permission_mode,
        "--settings", settings_path,
        "--append-system-prompt-file", prompt_path,
    ]
    if session_id:
        if not valid_session_id(session_id):
            raise AgentError("Refusing to resume: malformed session id.")
        argv += ["--resume", session_id]
    if model:
        if not _MODEL_RE.match(model):
            raise AgentError("Refusing to run: malformed model name.")
        argv += ["--model", model]
    return argv


@dataclass
class Launch:
    """Everything needed to start one CLI turn. ``stdin`` of None means the
    prompt travelled some other safe way (see the subclass) and stdin is closed."""
    argv: list[str]
    stdin: bytes | None = None
    env: dict[str, str] | None = None
    cleanup: list[str] = field(default_factory=list)


class CliAgent:
    """Runs one turn of a coding-agent CLI and streams typed events.

    Subclasses provide ``_prepare`` (argv/env/stdin for this turn) and
    ``_parser`` (stream-json -> AgentEvent). Process lifecycle is shared:
    raised stream limit, stderr tail for diagnostics, timeout, process-tree
    kill on cancel, and cleanup of per-turn private files.
    """

    label = "The agent"

    def __init__(self, *, timeout_s: int = 900, scratch_dir: Path | None = None) -> None:
        self.timeout_s = timeout_s
        self.scratch_dir = scratch_dir or Path(tempfile.gettempdir())
        self._proc: asyncio.subprocess.Process | None = None
        self._cancelled = False

    def _prepare(self, prompt: str, *, workspace: str, session_id: str | None,
                 grant: Grant | None, scope: Scope | None = None) -> Launch:
        raise NotImplementedError

    def _parser(self) -> Any:
        raise NotImplementedError

    async def run(self, prompt: str, *, workspace: str, session_id: str | None,
                  grant: Grant | None, scope: Scope | None = None) -> AsyncIterator[AgentEvent]:
        if not workspace or not Path(workspace).is_dir():
            raise AgentError("Pick a project folder first (Settings → Project folder).")
        self._cancelled = False
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        launch = self._prepare(prompt, workspace=workspace, session_id=session_id, grant=grant, scope=scope)
        stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        try:
            kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            self._proc = proc = await asyncio.create_subprocess_exec(
                *launch.argv, cwd=workspace, limit=_STREAM_LIMIT, env=launch.env,
                stdin=asyncio.subprocess.PIPE if launch.stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
            )
            assert proc.stdout and proc.stderr
            if launch.stdin is not None:
                assert proc.stdin
                proc.stdin.write(launch.stdin)
                await proc.stdin.drain()
                proc.stdin.close()

            async def drain_stderr() -> None:
                assert proc.stderr
                async for line in proc.stderr:
                    stderr_tail.append(line.decode("utf-8", "replace").rstrip())

            stderr_task = asyncio.create_task(drain_stderr())
            parser = self._parser()
            got_result = False
            try:
                async with asyncio.timeout(self.timeout_s):
                    async for raw in proc.stdout:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # non-JSON noise (e.g. a warning) must not kill the turn
                        if not isinstance(obj, dict):
                            continue
                        for event in parser.feed(obj):
                            got_result = got_result or isinstance(event, TurnResult)
                            yield event
                    await proc.wait()
            except TimeoutError:
                await self._kill_tree()
                raise AgentError(f"Turn exceeded {self.timeout_s}s and was stopped.") from None
            finally:
                await asyncio.wait({stderr_task}, timeout=2)
                stderr_task.cancel()

            if self._cancelled:
                return
            if not got_result:
                detail = "\n".join(list(stderr_tail)[-6:]) or f"exit code {proc.returncode}"
                raise AgentError(f"{self.label} ended without a result: {detail}")
        finally:
            if self._proc and self._proc.returncode is None:
                await self._kill_tree()
            self._proc = None
            for path in launch.cleanup:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def cancel(self) -> None:
        self._cancelled = True
        await self._kill_tree()

    def _write_private(self, content: str, suffix: str) -> str:
        fd, path = tempfile.mkstemp(prefix="barnaby-", suffix=suffix, dir=self.scratch_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # mkstemp creates 0600
            fh.write(content)
        return path

    async def _kill_tree(self) -> None:
        proc = self._proc
        if not proc or proc.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await killer.wait()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except TimeoutError:
                    os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            pass


class ClaudeCodeAgent(CliAgent):
    label = "Claude Code"

    def __init__(self, *, claude_bin: str = "", model: str = "",
                 safe_bash: list[str] | None = None, timeout_s: int = 900,
                 scratch_dir: Path | None = None) -> None:
        super().__init__(timeout_s=timeout_s, scratch_dir=scratch_dir)
        self.claude_bin = claude_bin
        self.model = model
        self.safe_bash = list(safe_bash or [])

    def _parser(self) -> StreamParser:
        return StreamParser()

    def _prepare(self, prompt: str, *, workspace: str, session_id: str | None,
                 grant: Grant | None, scope: Scope | None = None) -> Launch:
        claude = resolve_claude(self.claude_bin)
        settings = build_settings(self.safe_bash, grant, scope)
        mode = settings["permissions"]["defaultMode"]
        # Validate before writing anything so a refused turn leaves no temp files.
        build_argv(claude, settings_path="-", prompt_path="-", session_id=session_id, model=self.model,
                   permission_mode=mode)
        settings_path = self._write_private(json.dumps(settings), ".json")
        prompt_path = self._write_private(VOICE_SYSTEM_PROMPT, ".md")
        argv = build_argv(claude, settings_path=settings_path, prompt_path=prompt_path,
                          session_id=session_id, model=self.model, permission_mode=mode)
        return Launch(argv=argv, stdin=prompt.encode("utf-8"), cleanup=[settings_path, prompt_path])
