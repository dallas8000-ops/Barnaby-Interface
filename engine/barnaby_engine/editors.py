"""Jump-to-file integration for VS Code and Cursor via their CLI launchers.

Both ship ``code`` / ``cursor`` launchers that are ``.cmd`` shims on Windows, so
argv passes through cmd.exe. The path therefore must (a) resolve inside the
active workspace and (b) contain no cmd.exe metacharacters. An agent can create
a file named ``x&calc.ts``; without (b) clicking "open" would run ``calc``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_LAUNCHERS = {"vscode": "code", "cursor": "cursor"}
_CMD_META = set('&|<>^%!"\r\n')


class EditorError(RuntimeError):
    pass


def resolve_in_workspace(workspace: str, path: str) -> Path:
    if not workspace:
        raise EditorError("No project folder is set.")
    root = Path(workspace).resolve()
    candidate = Path(path)
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if target != root and root not in target.parents:
        raise EditorError("That file is outside the project folder.")
    return target


def open_in_editor(editor: str, workspace: str, path: str, line: int | None = None) -> None:
    launcher_name = _LAUNCHERS.get(editor)
    if not launcher_name:
        raise EditorError(f"Unknown editor: {editor}")
    launcher = shutil.which(launcher_name)
    if not launcher:
        pretty = "VS Code" if editor == "vscode" else "Cursor"
        raise EditorError(f"{pretty} command-line launcher '{launcher_name}' is not on PATH. "
                          f"In {pretty}: Command Palette → \"Shell Command: Install '{launcher_name}' command\".")
    target = resolve_in_workspace(workspace, path)
    target_arg = f"{target}:{line}" if line else str(target)
    if _CMD_META & set(target_arg) or _CMD_META & set(str(Path(workspace).resolve())):
        raise EditorError("File path contains characters that cannot be passed safely to the editor launcher.")
    argv = [launcher, "--reuse-window", str(Path(workspace).resolve()), "-g", target_arg]
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kwargs)  # noqa: S603 - argv validated above
