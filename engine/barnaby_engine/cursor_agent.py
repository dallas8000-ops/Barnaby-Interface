"""Cursor CLI adapter: runs one turn of Cursor's headless agent (`agent -p`).

Verified against real runs of Cursor CLI 2026.09.26 on Windows; see
tests/fixtures/cursor_*.jsonl. Load-bearing decisions:

* **No cmd.exe.** Cursor installs as ``agent.cmd`` -> ``agent.ps1`` wrappers,
  and the CLI takes the prompt only as an argument. Passing a transcript
  through cmd.exe would reintroduce command injection, so on Windows we
  resolve and exec the bundled ``node.exe index.js`` directly (CreateProcess,
  no shell). The prompt follows ``--`` so text starting with a dash can't be
  read as a flag.
* **Shell permissions per turn.** ``CURSOR_CONFIG_DIR`` points at a
  Barnaby-owned directory whose ``cli-config.json`` is rewritten every turn
  with ``approvalMode: allowlist``. Verified rule semantics:
  ``Shell(git diff)`` is a *prefix* rule (also allows ``git diff --stat``);
  ``Shell(git:diff)`` is *exact* (``command:args``); chained commands
  (``&&``, ``;``, ``|``) are rejected unless each part is allowed. A command
  outside the list comes back as ``result.rejected`` on the tool call, which
  becomes an approval card. The user's own Cursor config is never touched.
* **File edits are NOT gated by Cursor** in headless mode: ``Write()`` allow
  scopes and even explicit ``deny`` rules were ignored, and ``--sandbox`` is
  unavailable on Windows. :class:`CursorGuard` is therefore the enforcement
  point: any edit outside the project, or any command beyond what Barnaby's
  own (Claude-equivalent, exact) rules allow, stops the turn the moment the
  tool call starts.
* The directory is **persistent**, not temporary: Cursor stores chat
  history there, and ``--resume`` needs it on the next turn.
  Login tokens are stored elsewhere (verified), so scoping doesn't log out.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .agent import (VOICE_SYSTEM_PROMPT, AgentError, AgentEvent, CliAgent, Denial, Launch,
                    SessionStarted, TextDelta, ToolEnd, ToolStart, TurnResult, _preview)
from .safety import Grant, Scope, lane_of, valid_session_id

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_]{0,63}$")
_VERSION_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-\d{2}-\d{2}-\d{2})?-[a-f0-9]+$")
# Windows command lines cap at 32,767 chars; leave room for node/index paths and flags.
MAX_PROMPT_CHARS = 24_000
# Characters that would change the meaning of a Shell(...) token (glob syntax,
# or the token's own parentheses); a grant containing them is refused.
_UNSAFE_RULE_CHARS = set("*?[]{}()\n\r")

# Cursor tool object key -> (Barnaby tool name, input-key mapping)
_TOOLS: dict[str, str] = {
    "shellToolCall": "Bash",
    "readToolCall": "Read",
    "editToolCall": "Edit",
    "writeToolCall": "Write",
    "deleteToolCall": "Delete",
    "grepToolCall": "Grep",
    "globToolCall": "Glob",
    "lsToolCall": "Glob",
    "webFetchToolCall": "WebFetch",
    "webSearchToolCall": "WebSearch",
    "todoToolCall": "TodoWrite",
    "updateTodosToolCall": "TodoWrite",
}


def resolve_cursor(override: str = "") -> list[str]:
    """argv prefix that runs Cursor's CLI without going through a shell."""
    if override:
        if not Path(override).exists():
            raise AgentError(f"Cursor CLI not found at {override}.")
        return [override]
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent"
        versions = root / "versions"
        found: list[tuple[tuple[int, int, int], Path]] = []
        if versions.is_dir():
            for d in versions.iterdir():
                m = _VERSION_RE.match(d.name)
                if m and (d / "node.exe").exists() and (d / "index.js").exists():
                    found.append(((int(m[1]), int(m[2]), int(m[3])), d))
        if found:
            latest = max(found, key=lambda t: t[0])[1]
            return [str(latest / "node.exe"), str(latest / "index.js")]
        if (root / "node.exe").exists() and (root / "index.js").exists():
            return [str(root / "node.exe"), str(root / "index.js")]
    else:
        exe = shutil.which("cursor-agent") or shutil.which("agent")
        if exe:
            return [exe]
    raise AgentError(
        "Cursor's command-line agent was not found. Install it "
        "(PowerShell: irm 'https://cursor.com/install?win32=true' | iex), then run `agent login` once."
    )


def exact_shell_rule(command: str) -> str | None:
    """Exact-match Cursor token for one command: ``git push origin main`` ->
    ``Shell(git:push origin main)``; ``hostname`` -> ``Shell(hostname:)``."""
    command = " ".join(command.split())
    if not command or any(c in _UNSAFE_RULE_CHARS for c in command):
        return None
    head, _, rest = command.partition(" ")
    if ":" in head:
        return None
    return f"Shell({head}:{rest})"


def translate_rules(safe_bash: list[str], grant: Grant | None) -> list[str]:
    """Claude-syntax safe list + grant -> Cursor shell permission tokens.

    Claude ``x:*`` (prefix) -> Cursor ``Shell(x)`` (prefix); Claude exact
    ``x`` -> Cursor ``Shell(head:rest)`` (exact). File scopes are omitted:
    Cursor ignores them headless, and CursorGuard enforces the project fence.
    """
    allow = ["Read(**)"]
    for rule in safe_bash:
        if rule.endswith(":*"):
            prefix = rule[:-2].strip()
            if prefix and not any(c in _UNSAFE_RULE_CHARS for c in prefix):
                allow.append(f"Shell({prefix})")
        else:
            exact = exact_shell_rule(rule)
            if exact:
                allow.append(exact)
    if grant:
        for rule in grant.allow:
            if rule.startswith("Bash(") and rule.endswith(")"):
                exact = exact_shell_rule(rule[5:-1])
                if exact:
                    allow.append(exact)
            elif rule.startswith("WebFetch(domain:") and rule.endswith(")"):
                allow.append(f"WebFetch({rule[len('WebFetch(domain:'):-1]})")
    return allow


def cursor_grantable(tool_name: str, tool_input: dict[str, Any]) -> bool:
    if tool_name == "Bash":
        return exact_shell_rule(str(tool_input.get("command", ""))) is not None
    return True


def build_config(allow: list[str]) -> dict[str, Any]:
    return {
        "version": 1,
        "editor": {"vimMode": False},
        "approvalMode": "allowlist",
        "permissions": {"allow": allow, "deny": []},
    }


_CHAIN_RE = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")
_REDIRECT_OR_SUBST = re.compile(r"[<>`]|\$\(")


class CursorGuard:
    """Enforces what Cursor's headless config can't (see module docstring).

    Fires only where Cursor would *allow* something Barnaby's rules don't, so
    commands Cursor is about to reject still flow into approval cards.
    """

    def __init__(self, workspace: str, safe_bash: list[str], grant: Grant | None,
                 scope: Scope | None = None) -> None:
        self.roots = [Path(workspace).resolve()]
        self.allow_lanes = scope.allow_lanes if scope else ()
        opened = {x.casefold() for x in (grant.open_lanes if grant else ())}
        self.busy_lanes = tuple(x for x in (scope.ask_lanes if scope else ()) if x.casefold() not in opened)
        self.prefixes: list[str] = []
        self.exact: set[str] = set()
        for rule in safe_bash:
            if rule.endswith(":*"):
                self.prefixes.append(" ".join(rule[:-2].split()))
            else:
                self.exact.add(" ".join(rule.split()))
        if grant:
            for rule in grant.allow:
                if rule.startswith("Bash(") and rule.endswith(")"):
                    self.exact.add(" ".join(rule[5:-1].split()))
            self.roots += [Path(d).resolve() for d in grant.add_dirs]
        self.workspace = self.roots[0]

    def _inside(self, raw: str) -> bool:
        path = Path(raw)
        if not path.is_absolute():
            path = self.workspace / path
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return any(resolved == r or r in resolved.parents for r in self.roots)

    def _cursor_allows(self, seg: str) -> bool:
        # Cursor semantics (verified): Shell(x) = plain string prefix; Shell(h:r) = exact.
        return seg in self.exact or any(seg.startswith(p) for p in self.prefixes)

    def _barnaby_allows(self, seg: str) -> bool:
        # Claude semantics: exact, or prefix on a word boundary; never with redirects/substitution.
        if _REDIRECT_OR_SUBST.search(seg):
            return seg in self.exact
        return seg in self.exact or any(seg == p or seg.startswith(p + " ") for p in self.prefixes)

    def _lane_problem(self, raw: str) -> str | None:
        # Cursor can't pause for approval on edits, so a lane breach stops the turn.
        # The edit may already be on disk by the time the tool-call event arrives.
        ws = str(self.workspace)
        if self.allow_lanes and lane_of(ws, raw, self.allow_lanes) is None:
            return (f"Stopped Cursor: this task tried to change {raw}, which is outside its folders "
                    f"({', '.join(self.allow_lanes)}). That edit may already be saved; check it. "
                    "Add the folder to the task, or use Claude, which can ask first.")
        busy = lane_of(ws, raw, self.busy_lanes) if self.busy_lanes else None
        if busy is not None:
            where = f"in {busy}, which" if busy else "in the project, which"
            return (f"Stopped Cursor: {raw} is {where} a background task is working on. "
                    "That edit may already be saved; check it. Wait for the task, or use Claude, "
                    "which can ask first.")
        return None

    def check(self, name: str, tool_input: dict[str, Any]) -> str | None:
        if name in ("Edit", "Write", "Delete", "Read"):
            raw = str(tool_input.get("file_path") or "")
            if raw and not self._inside(raw):
                verb = "read" if name == "Read" else "change"
                return (f"Stopped Cursor: it tried to {verb} a file outside your project ({raw}). "
                        "Cursor can't be fenced in on Windows, so Barnaby blocks this. Use Claude for "
                        "work outside the project folder.")
            if raw and name != "Read":
                return self._lane_problem(raw)
            return None
        if name == "Bash":
            command = " ".join(str(tool_input.get("command", "")).split())
            if not command or command in self.exact:
                return None
            segments = [s for s in _CHAIN_RE.split(command) if s]
            if all(self._cursor_allows(s) for s in segments) and not all(self._barnaby_allows(s) for s in segments):
                return (f"Stopped Cursor: `{command[:160]}` goes beyond what you've allowed. "
                        "Ask for it directly and approve it when the card appears.")
        return None


class CursorStreamParser:
    """Cursor stream-json (with --stream-partial-output) -> AgentEvents."""

    def __init__(self) -> None:
        self._inputs: dict[str, tuple[str, dict[str, Any]]] = {}
        self._denials: list[Denial] = []
        self._emitted_text = False
        self._tool_since_text = False

    @staticmethod
    def _unwrap(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        for key, body in tool_call.items():
            if (key.endswith("ToolCall") or key == "function") and isinstance(body, dict):
                return key, body
        return "", {}

    @staticmethod
    def _normalize(key: str, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        args = body.get("args") or {}
        if key == "function":
            name = str(body.get("name") or "Tool")
            raw = body.get("arguments")
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = {}
            return name, parsed if isinstance(parsed, dict) else {}
        name = _TOOLS.get(key, key.removesuffix("ToolCall") or "Tool")
        if name == "Bash":
            return name, {"command": str(args.get("command", ""))}
        if name in ("Read", "Edit", "Write", "Delete"):
            return name, {"file_path": str(args.get("path", ""))}
        if name in ("Grep", "Glob"):
            return name, {"pattern": str(args.get("pattern") or args.get("globPattern") or args.get("path") or "")}
        if name == "WebFetch":
            return name, {"url": str(args.get("url", ""))}
        if name == "WebSearch":
            return name, {"query": str(args.get("searchTerm") or args.get("query") or "")}
        return name, {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool))}

    @staticmethod
    def _outcome(name: str, result: dict[str, Any]) -> tuple[bool, str]:
        if "success" in result:
            ok_body = result["success"] or {}
            if name == "Bash":
                code = ok_body.get("exitCode", 0)
                out = ok_body.get("interleavedOutput") or ok_body.get("stdout") or ok_body.get("stderr") or ""
                return code == 0, _preview(out)
            if name in ("Edit", "Write"):
                return True, _preview(ok_body.get("diffString") or ok_body.get("message") or "")
            if name == "Read":
                return True, _preview(ok_body.get("content") or "")
            return True, _preview(json.dumps(ok_body)[:600])
        if "rejected" in result:
            return False, "Needs your approval"
        failure = result.get("failure")
        if isinstance(failure, dict) and name == "Bash":
            # Verified shape: non-zero exit -> {"failure": {exitCode, stdout, stderr, ...}}
            out = failure.get("interleavedOutput") or failure.get("stderr") or failure.get("stdout") or ""
            return False, _preview(f"exit {failure.get('exitCode')}\n{out}".strip())
        err = result.get("error") or failure or result
        msg = err.get("message") or err.get("error") if isinstance(err, dict) else err
        return False, _preview(str(msg or "Failed"))

    def feed(self, obj: dict[str, Any]) -> list[AgentEvent]:
        out: list[AgentEvent] = []
        kind, sub = obj.get("type"), obj.get("subtype")
        if kind == "system" and sub == "init":
            sid = obj.get("session_id")
            if isinstance(sid, str):
                out.append(SessionStarted(sid, obj.get("model")))
        elif kind == "assistant":
            # Partial output: timestamp_ms without model_call_id is new text.
            # With model_call_id it's a pre-tool duplicate; with neither it's the
            # final flush duplicate. Both are skipped (else every sentence doubles).
            if "timestamp_ms" not in obj or "model_call_id" in obj:
                return out
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    text = block["text"]
                    if self._emitted_text and self._tool_since_text:
                        text = "\n\n" + text
                    self._emitted_text, self._tool_since_text = True, False
                    out.append(TextDelta(text))
        elif kind == "tool_call":
            cid = str(obj.get("call_id") or "")
            key, body = self._unwrap(obj.get("tool_call") or {})
            if not cid or not key:
                return out
            if sub == "started" and cid not in self._inputs:
                name, tin = self._normalize(key, body)
                self._inputs[cid] = (name, tin)
                self._tool_since_text = True
                out.append(ToolStart(cid, name, tin))
            elif sub == "completed":
                name, tin = self._inputs.get(cid) or self._normalize(key, body)
                result = body.get("result") or {}
                ok, preview = self._outcome(name, result if isinstance(result, dict) else {})
                if isinstance(result, dict) and "rejected" in result:
                    self._denials.append(Denial(cid, name, tin))
                out.append(ToolEnd(cid, ok, preview))
        elif kind == "result":
            out.append(TurnResult(
                text=str(obj.get("result") or ""),
                session_id=obj.get("session_id"),
                is_error=bool(obj.get("is_error")) or sub != "success",
                cost_usd=None,  # Cursor bills against the plan; no per-turn cost is reported
                duration_ms=obj.get("duration_ms"),
                denials=tuple(self._denials),
            ))
        return out


class CursorAgent(CliAgent):
    label = "Cursor"

    def __init__(self, *, config_dir: Path, cursor_bin: str = "", model: str = "",
                 safe_bash: list[str] | None = None, timeout_s: int = 900,
                 scratch_dir: Path | None = None) -> None:
        super().__init__(timeout_s=timeout_s, scratch_dir=scratch_dir)
        self.config_dir = config_dir
        self.cursor_bin = cursor_bin
        self.model = model
        self.safe_bash = list(safe_bash or [])

    def _parser(self) -> CursorStreamParser:
        return CursorStreamParser()

    async def run(self, prompt: str, *, workspace: str, session_id: str | None,
                  grant: Grant | None, scope: Scope | None = None) -> AsyncIterator[AgentEvent]:
        guard = CursorGuard(workspace, self.safe_bash, grant, scope) if workspace and Path(workspace).is_dir() else None
        async with contextlib.aclosing(super().run(prompt, workspace=workspace, session_id=session_id,
                                                   grant=grant, scope=scope)) as stream:
            async for event in stream:
                if guard and isinstance(event, ToolStart):
                    problem = guard.check(event.name, event.input)
                    if problem:
                        await self._kill_tree()  # before Cursor gets further; the base finally re-checks
                        yield event
                        yield ToolEnd(event.tool_id, False, "Blocked by Barnaby")
                        raise AgentError(problem)
                yield event

    def _write_config(self, config: dict[str, Any]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        target = self.config_dir / "cli-config.json"
        tmp = self.config_dir / "cli-config.json.tmp"
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        os.replace(tmp, target)  # atomic: Cursor never reads a half-written file

    def _prepare(self, prompt: str, *, workspace: str, session_id: str | None,
                 grant: Grant | None, scope: Scope | None = None) -> Launch:
        base = resolve_cursor(self.cursor_bin)
        if session_id and not valid_session_id(session_id):
            raise AgentError("Refusing to resume: malformed session id.")
        if self.model and not _MODEL_RE.match(self.model):
            raise AgentError("Refusing to run: malformed model name.")
        if "\x00" in prompt:
            raise AgentError("Refusing to run: prompt contains a NUL byte.")
        # Cursor has no system-prompt flag; the voice guidance rides on the
        # first message of a chat and persists in its history after that.
        full = prompt if session_id else f"<barnaby_voice_interface>\n{VOICE_SYSTEM_PROMPT}</barnaby_voice_interface>\n\n{prompt}"
        if len(full) > MAX_PROMPT_CHARS:
            raise AgentError(f"That instruction is too long for Cursor ({len(full):,} characters; "
                             f"limit {MAX_PROMPT_CHARS:,}). Shorten it or use Claude.")

        self._write_config(build_config(translate_rules(self.safe_bash, grant)))
        argv = [*base, "-p", "--output-format", "stream-json", "--stream-partial-output",
                "--trust", "--workspace", workspace]
        if session_id:
            argv += ["--resume", session_id]
        if self.model:
            argv += ["--model", self.model]
        argv += ["--", full]
        env = dict(os.environ, CURSOR_CONFIG_DIR=str(self.config_dir), CURSOR_INVOKED_AS="agent.cmd")
        return Launch(argv=argv, stdin=None, env=env)
