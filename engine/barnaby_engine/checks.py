"""Deterministic checks: commands Barnaby itself runs to decide whether a task is done.

A task's agent saying "all tests pass" proves nothing. After a local task's turn
ends, Barnaby runs the project's check commands (``pytest -q``, ``npm test``) in
the task's folder and only marks it done when every one exits 0.

Safety (same model as the agents' own commands):

* No shell. A command is split into argv here and started with
  ``create_subprocess_exec``. Shell metacharacters are refused outright, because
  an npm ``.cmd`` shim on Windows runs through cmd.exe, which re-parses argv.
* A check must match the auto-approve list (``safe_bash`` in config.json), the
  same rules that let an agent run it without asking. The UI can't widen that
  list, so it can't make Barnaby run anything an agent couldn't already run.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_CHECKS = 8
MAX_CHECK_CHARS = 300
OUTPUT_TAIL_CHARS = 4000          # kept per check (card, store, retry prompt)
_READ_TAIL_BYTES = 64 * 1024
# Anything a shell (cmd.exe re-parsing a .cmd shim, or sh) would treat specially.
_META = frozenset("&|<>^%!;$`\r\n'")
_LAUNCHER_EXT = (".exe", ".cmd", ".bat", ".com")


class CheckError(ValueError):
    """A check command was refused. The message is safe to show."""


@dataclass(frozen=True)
class CheckSpec:
    run: str            # the command, e.g. "pytest -q"
    dir: str = ""       # folder inside the project to run it in; "" = the project root

    @property
    def label(self) -> str:
        return f"{self.run} (in {self.dir}/)" if self.dir else self.run


@dataclass
class CheckResult:
    command: str
    dir: str
    exit_code: int | None
    duration_s: float
    output: str          # tail of stdout+stderr, interleaved as written
    passed: bool
    timed_out: bool = False
    error: str = ""      # the check couldn't start (not allowed, not found, …)

    def public(self) -> dict[str, Any]:
        return asdict(self)


def specs_from(raw: Any) -> tuple[CheckSpec, ...]:
    """Config/protocol data ({"run", "dir"} dicts or plain strings) -> specs. Bad items are skipped."""
    out: list[CheckSpec] = []
    for item in raw or ():
        if isinstance(item, str):
            item = {"run": item}
        if isinstance(item, CheckSpec):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("run"), str) and item["run"].strip():
            out.append(CheckSpec(item["run"].strip(), _norm_dir(item.get("dir") or "")))
    return tuple(out)


def _norm_dir(raw: str) -> str:
    d = str(raw).strip().replace("\\", "/")
    while d.startswith("./"):
        d = d[2:]
    return d.strip("/") if d != "." else ""


def split_command(cmd: str) -> list[str]:
    """Whitespace-separated argv; double quotes group (no escapes). CheckError if unsafe."""
    if not cmd.strip():
        raise CheckError("A check needs a command.")
    if len(cmd) > MAX_CHECK_CHARS:
        raise CheckError(f"Check command too long: {cmd[:40]}…")
    bad = sorted(_META & set(cmd))
    if bad:
        shown = " ".join("newline" if c in "\r\n" else c for c in bad)
        raise CheckError(f"\"{cmd[:60]}\" uses characters a check can't contain ({shown}). "
                         "Checks run without a shell; use one plain command per check.")
    argv: list[str] = []
    cur: list[str] = []
    quoted = in_token = False
    for ch in cmd:
        if ch == '"':
            quoted, in_token = not quoted, True
        elif ch.isspace() and not quoted:
            if in_token:
                argv.append("".join(cur))
                cur, in_token = [], False
        else:
            cur.append(ch)
            in_token = True
    if quoted:
        raise CheckError(f"\"{cmd[:60]}\" has an unclosed quote.")
    if in_token:
        argv.append("".join(cur))
    if not argv or not argv[0]:
        raise CheckError("A check needs a command.")
    return argv


def _stem(exe: str) -> str:
    name = exe.replace("\\", "/").rsplit("/", 1)[-1]
    low = name.lower()
    for ext in _LAUNCHER_EXT:
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def allowed_by(argv: list[str], safe_bash: list[str]) -> bool:
    """True if the command matches an auto-approve rule (Claude Code syntax: ``x:*`` is a
    prefix rule, anything else exact). The program may be given as a path
    (``.venv/Scripts/python``); it is matched by its name without the extension."""
    forms = {" ".join(argv), " ".join([_stem(argv[0]), *argv[1:]])}
    return any(_matches(form, rule) for rule in safe_bash for form in forms)


def _matches(form: str, rule: str) -> bool:
    prefix = rule[:-2] if rule.endswith(":*") else None
    head, _, rest = form.partition(" ")
    rhead, _, rrest = " ".join((prefix if prefix is not None else rule).split()).partition(" ")
    if head.casefold() != rhead.casefold():  # program names: Windows doesn't care about case
        return False
    if prefix is None:
        return rest == rrest
    return not rrest or rest == rrest or rest.startswith(rrest + " ")


def problem_with(specs: tuple[CheckSpec, ...] | list[CheckSpec], safe_bash: list[str] | None) -> str | None:
    """Why these checks can't be used (None: fine). Syntax and the auto-approve list only;
    folders are checked when the check runs. ``safe_bash=None`` skips the list (worktree
    setup commands, which only config.json can set)."""
    if len(specs) > MAX_CHECKS:
        return f"A project can have at most {MAX_CHECKS} checks."
    for spec in specs:
        if spec.dir and (spec.dir.startswith("/") or (len(spec.dir) > 1 and spec.dir[1] == ":")
                         or ".." in spec.dir.split("/") or _META & set(spec.dir) or '"' in spec.dir):
            return f"The folder for \"{spec.run[:60]}\" must be inside the project, like engine or app."
        try:
            argv = split_command(spec.run)
        except CheckError as exc:
            return str(exc)
        if safe_bash is not None and not allowed_by(argv, safe_bash):
            return (f"\"{spec.run[:60]}\" isn't on the auto-approve list, so Barnaby won't run it as a check. "
                    "Use a test or lint command (npm test, pytest, …), or add a rule to safe_bash in config.json.")
    return None


def _resolve(exe: str, cwd: Path) -> str | None:
    if "/" in exe or "\\" in exe:
        base = Path(exe) if Path(exe).is_absolute() else cwd / exe
        candidates = [base] + ([base.with_name(base.name + e) for e in _LAUNCHER_EXT] if sys.platform == "win32" else [])
        return next((str(c) for c in candidates if c.is_file()), None)
    return shutil.which(exe)


def _inside(root: Path, rel: str) -> Path | None:
    target = (root / rel).resolve() if rel else root.resolve()
    r = root.resolve()
    return target if (target == r or r in target.parents) and target.is_dir() else None


async def run_check(spec: CheckSpec, root: str | Path, *, safe_bash: list[str] | None,
                    timeout_s: float) -> CheckResult:
    """Run one check in ``root``/``spec.dir``. Never raises for a failing or missing command;
    a cancel kills the process tree and re-raises."""
    root = Path(root)

    def refused(why: str) -> CheckResult:
        return CheckResult(spec.run, spec.dir, None, 0.0, "", False, error=why)

    problem = problem_with([spec], safe_bash)
    if problem:
        return refused(problem)
    cwd = _inside(root, spec.dir)
    if cwd is None:
        return refused(f"There's no folder \"{spec.dir}\" in the task's copy of the project.")
    argv = split_command(spec.run)
    exe = _resolve(argv[0], cwd)
    if exe is None:
        return refused(f"\"{argv[0]}\" wasn't found (not on PATH, or not in {spec.dir or 'the project'}).")
    if exe.lower().endswith((".cmd", ".bat")) and any('"' in a for a in argv[1:]):
        return refused("Quoted arguments can't be passed safely to a .cmd launcher.")
    env = dict(os.environ)
    env["CI"] = "true"           # test runners: no watch mode, no interactive prompts
    env.setdefault("PYTHONUNBUFFERED", "1")
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, *argv[1:], cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs)  # noqa: S603 - argv checked above
    except OSError as exc:
        return refused(f"Couldn't start \"{argv[0]}\": {exc}")
    chunks: collections.deque[bytes] = collections.deque()
    size = 0

    async def read() -> None:
        nonlocal size
        assert proc.stdout
        while True:
            block = await proc.stdout.read(8192)
            if not block:
                return
            chunks.append(block)
            size += len(block)
            while size - len(chunks[0]) >= _READ_TAIL_BYTES and len(chunks) > 1:
                size -= len(chunks.popleft())

    timed_out = False
    try:
        try:
            async with asyncio.timeout(timeout_s):
                await read()
                await proc.wait()
        except TimeoutError:
            timed_out = True
            await kill_tree(proc)
    except asyncio.CancelledError:
        await kill_tree(proc)
        raise
    duration = round(time.monotonic() - started, 2)
    output = b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")
    output = output[-OUTPUT_TAIL_CHARS:]
    if timed_out:
        output += f"\n[Barnaby stopped this check after {round(timeout_s)} s.]"
    code = None if timed_out else proc.returncode
    return CheckResult(spec.run, spec.dir, code, duration, output, code == 0, timed_out=timed_out)


async def run_checks(specs: tuple[CheckSpec, ...], root: str | Path, *, safe_bash: list[str] | None,
                     timeout_s: float) -> list[CheckResult]:
    """All checks in order (a failing one doesn't stop the rest: the agent sees every failure)."""
    return [await run_check(s, root, safe_bash=safe_bash, timeout_s=timeout_s) for s in specs]


async def kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            await killer.wait()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=5)


def failure_report(results: list[CheckResult], *, per_check_chars: int = 3000) -> str:
    """The failing checks, for the agent's retry turn and the task's error."""
    parts = []
    for r in results:
        if r.passed:
            continue
        where = f" (in {r.dir}/)" if r.dir else ""
        if r.error:
            parts.append(f"$ {r.command}{where}\n{r.error}")
            continue
        how = "timed out" if r.timed_out else f"exit code {r.exit_code}"
        tail = r.output.strip()[-per_check_chars:]
        parts.append(f"$ {r.command}{where}\n{how} after {r.duration_s} s\n{tail}")
    return "\n\n".join(parts)


def failure_headline(results: list[CheckResult]) -> str:
    bad = [r for r in results if not r.passed]
    if not bad:
        return ""
    first = bad[0]
    why = first.error or ("timed out" if first.timed_out else f"exited with code {first.exit_code}")
    more = f" ({len(bad) - 1} more failed)" if len(bad) > 1 else ""
    return f"Check \"{first.command}\" {why}{more}."
