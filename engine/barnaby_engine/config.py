"""User-tunable configuration, persisted as JSON in the per-user data dir."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .languages import SPOKEN_CHOICES, WHISPER_MODELS

APP_NAME = "Barnaby"

# Bash commands the agent may run without asking. Exact-prefix rules in Claude
# Code permission syntax. Everything else that mutates state is surfaced as an
# approval card. Keep this list read-only / test-only.
DEFAULT_SAFE_BASH: tuple[str, ...] = (
    "git status", "git diff:*", "git log:*", "git show:*", "git branch",
    "ls:*", "dir:*", "pwd", "cat:*", "type:*",
    "npm test:*", "npm run test:*", "npm run lint:*", "npm run typecheck:*",
    "npx vitest run:*", "npx jest:*", "npx tsc --noEmit:*", "npx playwright test:*",
    "pnpm test:*", "yarn test:*",
    "pytest:*", "python -m pytest:*", "python -m unittest:*",
    "ruff check:*", "mypy:*",
    "go test:*", "go vet:*", "cargo test:*", "cargo check:*", "cargo clippy:*",
    "dotnet test:*", "dotnet build:*", "mvn test:*", "./gradlew test:*", "gradle test:*",
    "php artisan test:*", "vendor/bin/phpunit:*", "bundle exec rspec:*",
)


def data_dir() -> Path:
    override = os.environ.get("BARNABY_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Config:
    workspace: str = ""                  # project folder the agent works in
    claude_bin: str = ""                 # override path to claude executable
    claude_model: str = ""               # e.g. "opus", "sonnet"; empty = CLI default
    engine: str = "claude"               # which coding agent runs turns: claude | cursor | devin
    cursor_bin: str = ""                 # override path to Cursor's CLI (node entry or binary)
    cursor_model: str = ""               # Cursor model id; empty = Cursor's default ("Auto")
    whisper_model: str = "small.en"      # .en models are swapped for a multilingual sibling outside English
    spoken_language: str = "en"          # "auto" (detect per utterance) or a languages.LANGUAGES code
    whisper_device: str = "auto"         # auto | cpu | cuda
    input_device: int | None = None      # sounddevice index; None = system default
    tts_enabled: bool = True
    tts_voice: str = "am_michael"
    tts_speed: float = 1.05
    max_spoken_chars: int = 700          # longer replies: speak the head, show the rest
    review_transcripts: bool = True      # show editable transcript before sending
    save_voice_samples: bool = False     # opt-in: keep audio+corrected text for fine-tuning
    tablet_access: bool = False          # serve a paired tablet over HTTPS on the LAN (off by default)
    tablet_port: int = 8766              # fixed so the tablet's bookmark keeps working
    safe_bash: list[str] = field(default_factory=lambda: list(DEFAULT_SAFE_BASH))
    turn_timeout_s: int = 900
    # Background tasks (task board): how many run at once, how long one may run, and
    # named folder lanes per project ({workspace: {"backend": ["engine"], ...}}).
    max_parallel_tasks: int = 3
    task_timeout_s: int = 3600
    task_lanes: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    # Continuous mode settings
    continuous_mode_enabled: bool = False
    continuous_mode_timeout: float = 1.2  # seconds of silence that end a hands-free utterance

    # Command chaining settings
    command_chain_enabled: bool = True
    command_chain_max_depth: int = 5

    # IDE bridge settings
    ide_bridge_enabled: bool = False
    ide_bridge_port: int = 8765

    # Workflow settings
    workflows_enabled: bool = True
    workflows_directory: str = ""        # empty = <data dir>/workflows; see resolve_workflows_dir()

    # Keys the UI is allowed to change. safe_bash is deliberately excluded:
    # widening the auto-approve list must be a conscious edit of config.json,
    # not something a compromised renderer can flip over the socket.
    UI_MUTABLE = frozenset({
        "workspace", "claude_model", "engine", "cursor_model", "continuous_mode_enabled", "continuous_mode_timeout", "whisper_model", "whisper_device", "input_device",
        "spoken_language",
        "tts_enabled", "tts_voice", "tts_speed", "max_spoken_chars",
        "review_transcripts", "save_voice_samples", "tablet_access", "tablet_port",
        "max_parallel_tasks", "task_timeout_s", "task_lanes",
    })

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or data_dir() / "config.json"
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        # A hand-edited value the engine can't use falls back to the default instead
        # of failing later inside speech recognition.
        checked = {k for k in ("whisper_model", "spoken_language") if k in raw and not _VALIDATORS[k](raw[k])}
        return cls(**{k: v for k, v in raw.items() if k in known and k not in checked})

    def save(self, path: Path | None = None) -> None:
        path = path or data_dir() / "config.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on NTFS and POSIX

    def apply_ui_update(self, values: dict[str, Any]) -> list[str]:
        """Apply whitelisted, validated changes. Returns rejected keys."""
        rejected: list[str] = []
        for key, value in values.items():
            validator = _VALIDATORS.get(key)
            if key not in self.UI_MUTABLE or validator is None or not validator(value):
                rejected.append(key)
                continue
            setattr(self, key, float(value) if key in ("tts_speed", "continuous_mode_timeout") else value)
        return rejected

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("claude_bin", None)
        d.pop("cursor_bin", None)
        return d


def resolve_workflows_dir(configured: str) -> Path:
    """Where workflow files live. Empty -> the per-user data dir.

    Earlier builds defaulted to the literal string "%APPDATA%\\Barnaby\\workflows";
    Path() doesn't expand variables, so that created a folder literally named
    "%APPDATA%" inside whatever the working directory was (the repo). Expand
    variables and ~, and never accept a relative or still-unexpanded path.
    """
    if configured:
        expanded = Path(os.path.expanduser(os.path.expandvars(configured)))
        if expanded.is_absolute() and "%" not in str(expanded) and "$" not in str(expanded):
            return expanded
    return data_dir() / "workflows"


def validate_config(config: Config) -> list[str]:
    """Validate configuration and return list of error messages."""
    errors = []

    if config.continuous_mode_timeout < 0.5:
        errors.append("continuous_mode_timeout must be at least 0.5 seconds")

    if config.command_chain_max_depth < 1 or config.command_chain_max_depth > 20:
        errors.append("command_chain_max_depth must be between 1 and 20")

    if config.ide_bridge_port < 1024 or config.ide_bridge_port > 65535:
        errors.append("ide_bridge_port must be between 1024 and 65535")

    return errors


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _short_str(v: Any, limit: int = 64) -> bool:
    return isinstance(v, str) and len(v) <= limit


_LANE_BAD = frozenset("*?[]{}()!~$`\"'\\")
_LANE_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_")


def _lane_path(v: Any) -> bool:
    # Syntax only; tasks.validate_lanes checks the folder exists inside the project when used.
    parts = v.replace("\\", "/").split("/") if isinstance(v, str) else []
    return (isinstance(v, str) and 0 < len(v) <= 200 and not (_LANE_BAD & set(v))
            and all(ord(c) >= 32 for c in v) and ".." not in parts and not v.startswith("/")
            and not (len(v) > 1 and v[1] == ":") and any(p not in ("", ".") for p in parts))


def _task_lanes(v: Any) -> bool:
    if not isinstance(v, dict) or len(v) > 20:
        return False
    for ws, names in v.items():
        if not _short_str(ws, 1024) or not isinstance(names, dict) or len(names) > 20:
            return False
        if len({str(n).casefold() for n in names}) != len(names):
            return False  # voice matches names case-insensitively, so "Backend" would hide "backend"
        for name, lanes in names.items():
            if not (isinstance(name, str) and 0 < len(name) <= 32 and set(name) <= _LANE_NAME_OK):
                return False
            if not isinstance(lanes, list) or not 0 < len(lanes) <= 8 or not all(map(_lane_path, lanes)):
                return False
    return True


_VALIDATORS: dict[str, Any] = {
    "workspace": lambda v: isinstance(v, str) and (v == "" or Path(v).is_dir()),
    "claude_model": lambda v: _short_str(v) and all(c.isalnum() or c in "-._[]" for c in v),
    "engine": lambda v: v in {"claude", "cursor", "devin"},
    "continuous_mode_enabled": lambda v: isinstance(v, bool),
    "continuous_mode_timeout": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0.6 <= v <= 5.0,
    "cursor_model": lambda v: _short_str(v) and all(c.isalnum() or c in "-._" for c in v),
    "whisper_model": lambda v: isinstance(v, str) and v in WHISPER_MODELS,
    "spoken_language": lambda v: isinstance(v, str) and v in SPOKEN_CHOICES,
    "whisper_device": lambda v: v in {"auto", "cpu", "cuda"},
    "input_device": lambda v: v is None or (_is_int(v) and v >= 0),
    "tts_enabled": lambda v: isinstance(v, bool),
    "tts_voice": lambda v: _short_str(v, 32) and v.replace("_", "").isalnum(),
    "tts_speed": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0.5 <= v <= 2.0,
    "max_spoken_chars": lambda v: _is_int(v) and 0 <= v <= 5000,
    "review_transcripts": lambda v: isinstance(v, bool),
    "save_voice_samples": lambda v: isinstance(v, bool),
    "tablet_access": lambda v: isinstance(v, bool),
    "tablet_port": lambda v: _is_int(v) and 1024 <= v <= 65535,
    "max_parallel_tasks": lambda v: _is_int(v) and 1 <= v <= 6,
    "task_timeout_s": lambda v: _is_int(v) and 300 <= v <= 14_400,
    "task_lanes": _task_lanes,
}
