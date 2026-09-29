"""Risk classification for tool calls the agent was denied, and grant scoping.

Threat model: speech recognition is lossy, so a misheard instruction can yield a
destructive plan. Claude Code's own permission system is the enforcement point
(nothing outside the allowlist runs); this module decides how loudly to ask.
CRITICAL requests cannot be approved by voice and require a deliberate
hold-to-confirm in the UI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import Risk

_CRITICAL = [re.compile(p, re.I) for p in (
    r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b",       # rm -rf / -fr
    r"\brm\s+-r\b.*\s/(\s|$)",
    r"\bremove-item\b.*-recurse",
    r"\b(del|erase)\s+/[sq]",
    r"\brmdir\s+/s\b",
    r"\bformat\s+[a-z]:",
    r"\bgit\s+push\b.*(--force|-f\b|--force-with-lease)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-z]*f",
    r"\bgit\s+(branch\s+-D|filter-branch|filter-repo)\b",
    r"\bdrop\s+(table|database|schema)\b",
    r"\btruncate\s+table\b",
    r"\b(curl|wget|iwr|invoke-webrequest)\b[^|]*\|\s*(sh|bash|zsh|iex|invoke-expression|python)",
    r"\b(npm|pnpm|yarn)\s+publish\b",
    r"\b(railway\s+(up|down|delete)|vercel\b.*--prod|fly\s+deploy|heroku\b.*destroy)",
    r"\b(terraform|tofu)\s+(apply|destroy)\b",
    r"\bkubectl\s+(delete|apply)\b",
    r"\bchmod\s+-R\s+777\b",
    r"\bmkfs\b|\bdd\s+if=",
    r"\bshutdown\b|\breboot\b",
)]

_HIGH = [re.compile(p, re.I) for p in (
    r"\bgit\s+(push|commit|merge|rebase|checkout\s+--|stash\s+drop|tag)\b",
    r"\b(npm|pnpm|yarn|pip|uv|cargo|go)\s+(install|add|remove|uninstall|get)\b",
    r"\bnpx\b",
    r"\b(rm|del|move|mv|ren|rename)\b",
    r"\b(alembic\s+(upgrade|downgrade)|manage\.py\s+(migrate|flush)|prisma\s+migrate)\b",
    r"\bdocker\s+(rm|rmi|system\s+prune|volume\s+rm|compose\s+down)\b",
    r"\b(curl|wget|iwr|invoke-webrequest|ssh|scp)\b",
    r"\bsudo\b|\brunas\b",
    r">\s*[^\s&|]",                                      # redirection writes a file
)]

_OUTBOUND_TOOLS = {"WebFetch", "WebSearch"}


@dataclass(frozen=True)
class Assessment:
    risk: Risk
    summary: str
    voice_approvable: bool


def assess(tool_name: str, tool_input: dict[str, Any]) -> Assessment:
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        desc = str(tool_input.get("description", "")).strip()
        summary = desc or cmd
        if any(p.search(cmd) for p in _CRITICAL):
            return Assessment(Risk.CRITICAL, summary, voice_approvable=False)
        if any(p.search(cmd) for p in _HIGH):
            return Assessment(Risk.HIGH, summary, voice_approvable=True)
        return Assessment(Risk.NORMAL, summary, voice_approvable=True)
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or "a file"
        # Denied edits only happen outside the workspace (acceptEdits covers inside).
        return Assessment(Risk.HIGH, f"{tool_name} {path} (outside the project folder)", True)
    if tool_name in _OUTBOUND_TOOLS:
        target = tool_input.get("url") or tool_input.get("query") or ""
        return Assessment(Risk.NORMAL, f"{tool_name}: {target}", True)
    return Assessment(Risk.HIGH, f"Use tool {tool_name}", True)


@dataclass(frozen=True)
class Grant:
    """Extra permissions for exactly one resumed turn."""
    allow: tuple[str, ...] = ()
    add_dirs: tuple[str, ...] = ()
    open_lanes: tuple[str, ...] = ()  # conversation: busy task lanes the user let this turn edit


@dataclass(frozen=True)
class Scope:
    """Folder lanes for one turn (see tasks.py).

    * ``allow_lanes``: a background task may edit only these folders without asking.
    * ``ask_lanes``: the conversation must ask before editing these (another task holds them).
    Both are canonical project-relative paths from ``tasks.validate_lanes``.
    """
    allow_lanes: tuple[str, ...] = ()
    ask_lanes: tuple[str, ...] = ()


_BAD_RULE_PATH = re.compile(r"[*?\[\]{}()!~$`\"'\x00-\x1f\x7f]")
_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _project_relative(workspace: str, raw: str) -> str | None:
    """``raw`` as a project-relative posix path, or None if it isn't inside the project."""
    root = Path(workspace).resolve()
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved == root or root not in resolved.parents:
        return None
    return resolved.relative_to(root).as_posix()


def lane_of(workspace: str, raw_path: str, lanes: tuple[str, ...]) -> str | None:
    """The lane containing ``raw_path`` (absolute, or relative to the project), if any."""
    rel = _project_relative(workspace, raw_path)
    if rel is None:
        return None
    low = rel.casefold()
    for lane in lanes:
        key = lane.casefold()
        if not key or low == key or low.startswith(key + "/"):  # "" = the whole project
            return lane
    return None


def grant_for_task(tool_name: str, tool_input: dict[str, Any], workspace: str) -> Grant | None:
    """Like :func:`grant_for`, but an edit is allowed as exactly one project file
    (``Edit(./rel/path)``) because task turns run in ``default`` mode with lane rules.
    Edits outside the project can't be approved in a task."""
    if tool_name in _EDIT_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(raw, str) or not raw or not Path(raw).is_absolute() or ".." in Path(raw).parts:
            return None
        rel = _project_relative(workspace, raw)
        if rel is None or _BAD_RULE_PATH.search(rel):
            return None
        return Grant(allow=(f"Edit(./{rel})",))
    return grant_for(tool_name, tool_input)


def grant_for(tool_name: str, tool_input: dict[str, Any]) -> Grant | None:
    """Narrowest Claude Code permission that allows exactly this call.

    Returns None when no narrow grant can be expressed safely; the caller must
    then refuse rather than fall back to granting the whole tool.
    """
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{0,127}", tool_name):
        return None
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        # Rule syntax is Bash(<command>) with ':*' prefix wildcards. Parentheses
        # or newlines inside the command make the rule ambiguous, and a trailing
        # ':*' would silently widen it into a prefix grant.
        if not cmd or len(cmd) > 2000 or any(c in cmd for c in "()\r\n") or cmd.rstrip().endswith(":*"):
            return None
        return Grant(allow=(f"Bash({cmd})",))
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(raw, str) or not raw:
            return None
        path = Path(raw)
        if not path.is_absolute() or ".." in path.parts:
            return None
        return Grant(add_dirs=(str(path.parent),))
    if tool_name == "WebFetch":
        url = str(tool_input.get("url", ""))
        m = re.match(r"https?://([A-Za-z0-9.\-]+)(?:[:/]|$)", url)
        return Grant(allow=(f"WebFetch(domain:{m.group(1)})",)) if m else None
    return Grant(allow=(tool_name,))  # other tools: per-tool, one turn only


_SESSION_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def valid_session_id(value: str | None) -> bool:
    return bool(value and _SESSION_ID.match(value))


_SECRETS = [re.compile(p) for p in (
    r"sk-(?:ant-)?[A-Za-z0-9_\-]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",  # JWT
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S+",
)]


def redact_for_speech(text: str) -> str:
    """Never read credentials aloud: the room is not a trusted channel."""
    for pattern in _SECRETS:
        text = pattern.sub("[redacted secret]", text)
    return text
