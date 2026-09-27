"""User-tunable configuration, persisted as JSON in the per-user data dir."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

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
    engine: str = "claude"               # which coding agent runs turns: claude | cursor
    cursor_bin: str = ""                 # override path to Cursor's CLI (node entry or binary)
    cursor_model: str = ""               # Cursor model id; empty = Cursor's default ("Auto")
    whisper_model: str = "small.en"
    whisper_device: str = "auto"         # auto | cpu | cuda
    input_device: int | None = None      # sounddevice index; None = system default
    tts_enabled: bool = True
    tts_voice: str = "am_michael"
    tts_speed: float = 1.05
    max_spoken_chars: int = 700          # longer replies: speak the head, show the rest
    review_transcripts: bool = True      # show editable transcript before sending
    save_voice_samples: bool = False     # opt-in: keep audio+corrected text for fine-tuning
    safe_bash: list[str] = field(default_factory=lambda: list(DEFAULT_SAFE_BASH))
    turn_timeout_s: int = 900

    # Keys the UI is allowed to change. safe_bash is deliberately excluded:
    # widening the auto-approve list must be a conscious edit of config.json,
    # not something a compromised renderer can flip over the socket.
    UI_MUTABLE = frozenset({
        "workspace", "claude_model", "engine", "cursor_model", "whisper_model", "whisper_device", "input_device",
        "tts_enabled", "tts_voice", "tts_speed", "max_spoken_chars",
        "review_transcripts", "save_voice_samples",
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
        return cls(**{k: v for k, v in raw.items() if k in known})

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
            setattr(self, key, float(value) if key == "tts_speed" else value)
        return rejected

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("claude_bin", None)
        d.pop("cursor_bin", None)
        return d


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _short_str(v: Any, limit: int = 64) -> bool:
    return isinstance(v, str) and len(v) <= limit


_VALIDATORS: dict[str, Any] = {
    "workspace": lambda v: isinstance(v, str) and (v == "" or Path(v).is_dir()),
    "claude_model": lambda v: _short_str(v) and all(c.isalnum() or c in "-._[]" for c in v),
    "engine": lambda v: v in {"claude", "cursor"},
    "cursor_model": lambda v: _short_str(v) and all(c.isalnum() or c in "-._" for c in v),
    "whisper_model": lambda v: v in {"tiny.en", "base.en", "small.en", "medium.en", "large-v3", "distil-large-v3"},
    "whisper_device": lambda v: v in {"auto", "cpu", "cuda"},
    "input_device": lambda v: v is None or (_is_int(v) and v >= 0),
    "tts_enabled": lambda v: isinstance(v, bool),
    "tts_voice": lambda v: _short_str(v, 32) and v.replace("_", "").isalnum(),
    "tts_speed": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0.5 <= v <= 2.0,
    "max_spoken_chars": lambda v: _is_int(v) and 0 <= v <= 5000,
    "review_transcripts": lambda v: isinstance(v, bool),
    "save_voice_samples": lambda v: isinstance(v, bool),
}
