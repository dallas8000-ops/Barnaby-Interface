"""A git worktree per local background task.

Each Claude/Cursor task in a git project gets its own working copy on its own
branch (``barnaby/task-<id>-<slug>``), cut from the project's current HEAD. The
agent and its checks run there, so parallel tasks can't overwrite each other's
files and the user's own checkout is never touched.

Where: ``<data dir>/worktrees/<repo>-<hash>/<task id>`` (``%APPDATA%\\Barnaby`` on
Windows), not inside the repo. A worktree under the project folder would show
up to every tool that walks it: test discovery and globbing (vitest, pytest)
would collect each task's copy of the tests, the IDE and file watchers would
index it, and agents grepping the project would see N copies of every file.
Keeping it outside also means nothing has to be added to ``.gitignore``.

git is run as argv (``git.exe`` is a real executable, so nothing re-parses it);
commit messages go over stdin. Barnaby never merges or pushes; it commits the
task's changes to the task's branch and the user merges it.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BRANCH_PREFIX = "barnaby/task-"
GIT_TIMEOUT_S = 120


class WorktreeError(RuntimeError):
    """A git step failed. The message is safe to show."""


@dataclass(frozen=True)
class Repo:
    top: Path        # the repository's top folder (the main checkout, or the linked worktree we're in)
    prefix: str      # the project folder relative to ``top`` ("" when they're the same), posix


def git_exe() -> str | None:
    return shutil.which("git")


async def git(args: list[str], cwd: str | Path, *, stdin: bytes | None = None,
              timeout_s: float = GIT_TIMEOUT_S) -> tuple[int, str]:
    """(exit code, stdout+stderr). Exit code 127 when git isn't installed."""
    exe = git_exe()
    if exe is None:
        return 127, "git isn't installed or isn't on PATH."
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, *args, cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs)  # noqa: S603
    except OSError as exc:
        return 127, f"Couldn't run git: {exc}"
    try:
        async with asyncio.timeout(timeout_s):
            out, _ = await proc.communicate(stdin)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return 124, f"git {args[0]} took longer than {round(timeout_s)} s."
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


async def repo_of(workspace: str | Path) -> Repo | None:
    """The git repository containing ``workspace``, or None (not a repo, no commits yet, or no git)."""
    if not Path(workspace).is_dir():
        return None
    code, out = await git(["rev-parse", "--show-toplevel", "--show-prefix", "--verify", "HEAD"], workspace)
    if code != 0:
        return None
    lines = out.splitlines()
    if len(lines) < 3:
        return None
    return Repo(Path(lines[0]), lines[1].strip().strip("/"))


_SLUG = re.compile(r"[^a-z0-9]+")


def branch_name(task_id: str, title: str) -> str:
    slug = _SLUG.sub("-", title.lower()).strip("-")[:32].rstrip("-")
    return f"{BRANCH_PREFIX}{task_id[:8]}" + (f"-{slug}" if slug else "")


def worktree_path(base: Path, repo_top: Path, task_id: str) -> Path:
    """``base/<repo name>-<hash of its path>/<task id>``: short (Windows path limits) and unique per repo."""
    digest = hashlib.sha1(str(repo_top.resolve()).casefold().encode("utf-8")).hexdigest()[:8]
    name = _SLUG.sub("-", repo_top.name.lower()).strip("-")[:24] or "repo"
    return base / f"{name}-{digest}" / task_id


async def is_worktree(path: str | Path) -> bool:
    p = Path(path)
    if not (p / ".git").exists():
        return False
    code, out = await git(["rev-parse", "--show-toplevel"], p)
    return code == 0 and Path(out).resolve() == p.resolve()


async def branch_exists(repo_top: Path, branch: str) -> bool:
    code, _ = await git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo_top)
    return code == 0


async def ensure(repo_top: Path, path: Path, branch: str) -> bool:
    """Make ``path`` a worktree on ``branch``. True if it was created now, False if reused.

    A new branch starts at the project's current HEAD; an existing branch (the worktree
    folder was deleted, or Barnaby restarted mid-task) is checked out again as it was."""
    if await is_worktree(path):
        return False
    if path.exists() and any(path.iterdir()):
        raise WorktreeError(f"{path} exists but isn't this task's worktree. Move it away and try again.")
    path.parent.mkdir(parents=True, exist_ok=True)
    await git(["worktree", "prune"], repo_top)
    if await branch_exists(repo_top, branch):
        code, out = await git(["worktree", "add", str(path), branch], repo_top)
    else:
        code, out = await git(["worktree", "add", "-b", branch, str(path), "HEAD"], repo_top)
    if code != 0:
        raise WorktreeError(f"Couldn't create a worktree: {_last_line(out)}")
    return True


async def head(path: str | Path) -> str:
    code, out = await git(["rev-parse", "--short", "HEAD"], path)
    return out if code == 0 else ""


async def current_branch(path: str | Path) -> str:
    code, out = await git(["symbolic-ref", "--quiet", "--short", "HEAD"], path)
    return out if code == 0 else ""


async def changed_files(path: str | Path) -> int:
    """Uncommitted changes (tracked and untracked, not ignored). -1 if git failed."""
    code, out = await git(["status", "--porcelain", "--untracked-files=normal"], path)
    return len([x for x in out.splitlines() if x.strip()]) if code == 0 else -1


async def commit_all(path: str | Path, message: str) -> tuple[str, str]:
    """Commit everything in the worktree to its branch. (short sha, "") on a commit,
    ("", "") when there was nothing to commit, ("", reason) when the commit failed.
    The user's hooks and signing settings apply as for any commit."""
    if await changed_files(path) == 0:
        return "", ""
    code, out = await git(["add", "-A"], path)
    if code != 0:
        return "", _last_line(out)
    ident: list[str] = []
    c1, email = await git(["config", "user.email"], path)
    if c1 != 0 or not email:
        ident = ["-c", "user.name=Barnaby", "-c", "user.email=barnaby@localhost"]
    code, out = await git([*ident, "commit", "-q", "-F", "-"], path, stdin=message.encode("utf-8"))
    if code != 0:
        return "", _last_line(out) or f"git commit exited with {code}"
    return await head(path), ""


async def unmerged_work(repo_top: Path, path: str | Path, branch: str) -> str:
    """Why removing this worktree would lose work ("" = safe to remove): uncommitted changes,
    or commits on its branch that no other local branch contains."""
    if await is_worktree(path):
        n = await changed_files(path)
        if n != 0:
            return "it has uncommitted changes" if n > 0 else "git couldn't read its status"
    if await branch_exists(repo_top, branch):
        # --exclude takes the short name: --branches matches refs below refs/heads/.
        code, out = await git(["rev-list", "--count", f"refs/heads/{branch}", "--not",
                               f"--exclude={branch}", "--branches"], repo_top)
        if code != 0:
            return "git couldn't compare its branch"
        if out.strip() != "0":
            n = int(out.strip()) if out.strip().isdigit() else 0
            return f"{branch} has {n} commit{'s' if n != 1 else ''} that aren't merged"
    return ""


async def remove(repo_top: Path, path: str | Path, branch: str) -> None:
    """Delete the worktree folder and its branch. Only Barnaby's own task branches are deleted."""
    p = Path(path)
    if await is_worktree(p):
        code, out = await git(["worktree", "remove", "--force", str(p)], repo_top)
        if code != 0:
            raise WorktreeError(f"Couldn't remove the worktree: {_last_line(out)}")
    elif p.exists():
        shutil.rmtree(p, ignore_errors=True)
    await git(["worktree", "prune"], repo_top)
    if branch.startswith(BRANCH_PREFIX) and await branch_exists(repo_top, branch):
        code, out = await git(["branch", "-D", branch], repo_top)
        if code != 0:
            raise WorktreeError(f"Couldn't delete branch {branch}: {_last_line(out)}")
    with contextlib.suppress(OSError):
        p.parent.rmdir()  # the per-repo folder, once its last task is gone


def _last_line(out: str) -> str:
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    return (lines[-1] if lines else "git failed")[:300]
