"""Background tasks: several coding agents working at once in folder lanes.

A task is one engine, one instruction and a *lane*: the folders inside the
project it may change. Local tasks (Claude, Cursor) whose lanes overlap never
run at the same time; Devin works on its own cloud copy of the repo, so it
holds no local lane. See docs/superpowers/specs/2026-09-29-barnaby-task-board-design.md.

This module's top half is pure logic (validation, overlap, scheduling, the
voice grammar). :class:`TaskManager` below runs the tasks.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .checks import CheckSpec

MAX_LANES = 8
MAX_LANE_CHARS = 200
LOCAL_ENGINES = frozenset({"claude", "cursor"})
# "verifying": the agent's turn ended and Barnaby is running the task's checks. It keeps
# its slot and its lanes (the files must not change under the checks).
ACTIVE = frozenset({"queued", "running", "verifying", "needs_approval"})
WORKING = frozenset({"running", "verifying"})     # uses a parallel slot
HOLDS_LANES = frozenset({"running", "verifying", "needs_approval"})
FINISHED = frozenset({"done", "failed", "cancelled", "interrupted"})

# Anything that could change the meaning of an `Edit(./<lane>/**)` permission rule
# (glob syntax, negation, home/variable expansion, the rule's own parentheses),
# plus quotes and control characters.
_BAD_LANE = re.compile(r"[*?\[\]{}()!~$`\"'\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")


class LaneError(ValueError):
    """A lane was refused. The message is safe to show and names the lane."""


def _normalize(raw: str) -> str:
    lane = str(raw).strip().replace("\\", "/")
    while lane.startswith("./"):
        lane = lane[2:]
    return lane.rstrip("/")


def validate_lanes(workspace: str, lanes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Canonical lanes (project-relative, forward slashes, on-disk spelling) or LaneError.

    An empty result means the whole project. Lanes inside another lane are dropped.
    """
    raw = [str(x) for x in lanes]
    if len(raw) > MAX_LANES:
        raise LaneError(f"A task can have at most {MAX_LANES} folders.")
    root = Path(workspace).resolve()
    out: list[str] = []
    for item in raw:
        shown = item.strip()[:60] or "(empty)"
        if len(item) > MAX_LANE_CHARS:
            raise LaneError(f"Folder name too long: {shown}…")
        if _BAD_LANE.search(item):
            raise LaneError(f"\"{shown}\" has characters a folder lane can't use.")
        lane = _normalize(item)
        if not lane:
            raise LaneError("That's the whole project. Leave the folders empty instead.")
        if lane.startswith("/") or _DRIVE.match(lane):
            raise LaneError(f"\"{shown}\" must be a folder inside the project, not a full path.")
        if any(part == ".." for part in lane.split("/")):
            raise LaneError(f"\"{shown}\" points outside the project.")
        target = (root / lane).resolve()
        if target == root:
            raise LaneError("That's the whole project. Leave the folders empty instead.")
        if root not in target.parents:
            raise LaneError(f"\"{shown}\" points outside the project.")
        if not target.is_dir():
            raise LaneError(f"There's no folder \"{shown}\" in the project.")
        out.append(target.relative_to(root).as_posix())
    # Dedupe, then drop lanes that another lane already contains.
    uniq = list(dict.fromkeys(out))
    return tuple(a for a in uniq if not any(b != a and _contains(b, a) for b in uniq))


def _contains(outer: str, inner: str) -> bool:
    o, i = outer.casefold(), inner.casefold()
    return i == o or i.startswith(o + "/")


def lanes_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True if two lanes share any folder. Empty means the whole project."""
    if not a or not b:
        return True
    return any(_contains(x, y) or _contains(y, x) for x in a for y in b)


# ---------------------------------------------------------------- records
@dataclass
class TaskRecord:
    id: str
    workspace: str
    engine: str
    title: str
    instruction: str
    lanes: tuple[str, ...] = ()
    status: str = "queued"
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    summary: str = ""
    result_text: str = ""
    error: str = ""
    cost_usd: float | None = None
    cli_session_id: str | None = None
    turns: int = 0
    # Verification: the checks Barnaby runs after the agent's turn, how many times it has run
    # them since the last instruction, and the latest results. verification is "" (not yet),
    # "passed", "failed" or "unverified" (no checks, or a Devin task).
    checks: tuple[CheckSpec, ...] = ()
    attempts: int = 0
    check_results: list[dict[str, Any]] = field(default_factory=list)
    verification: str = ""
    # Isolation: "worktree" (own git worktree + branch), "shared" (the project folder itself)
    # or "" (Devin; or a task from before worktrees, which stays shared).
    isolation: str = ""
    worktree: str = ""                                           # top folder of the task's worktree
    branch: str = ""
    notice: str = ""                                             # isolation/commit note for the card
    warning: str = ""                                            # shown on the card; not persisted
    run_dir: str = field(default="", repr=False)                 # where the agent runs (not persisted)
    pending_prompt: str | None = field(default=None, repr=False)  # next turn to run (not shown)

    @property
    def local(self) -> bool:
        return self.engine in LOCAL_ENGINES

    @property
    def isolated(self) -> bool:
        return self.local and self.isolation == "worktree"

    @property
    def root(self) -> str:
        """The folder the agent and its checks work in: the task's worktree, or the project.
        (Never the project folder for a worktree task, even before its run has started.)"""
        if self.isolated:
            return self.run_dir or self.worktree
        return self.run_dir or self.workspace

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("pending_prompt", None)
        d["lanes"] = list(self.lanes)
        d["checks"] = [asdict(c) for c in self.checks]
        d["run_dir"] = self.root if self.isolated else ""  # where Open folder goes
        return d


def pick_next(tasks: list[TaskRecord], max_parallel: int, *, worktrees_share_lanes: bool = False) -> list[TaskRecord]:
    """Queued tasks (in creation order) that may start now.

    Running and verifying tasks use a slot and hold their lanes. Tasks waiting for
    approval hold their lanes but not a slot. A blocked task doesn't stop later tasks
    whose lanes are free.

    ``worktrees_share_lanes``: a task in its own worktree edits its own copy of the
    project, so it can't clobber another task's files. With this on, worktree tasks
    neither hold lanes nor wait for them (the price is merge conflicts later, when the
    user merges two branches that changed the same files). Off by default.
    """
    def lane_bound(t: TaskRecord) -> bool:
        return t.local and not (worktrees_share_lanes and t.isolated)

    slots = max_parallel - sum(1 for t in tasks if t.status in WORKING)
    held = [t for t in tasks if lane_bound(t) and t.status in HOLDS_LANES]
    started: list[TaskRecord] = []
    for t in tasks:
        if slots <= 0:
            break
        if t.status != "queued":
            continue
        if lane_bound(t) and any(h.workspace == t.workspace and lanes_overlap(h.lanes, t.lanes) for h in held):
            continue
        started.append(t)
        slots -= 1
        if lane_bound(t):
            held.append(t)
    return started


# ---------------------------------------------------------------- voice grammar
_ENGINE_WORDS = {"claude": "claude", "clod": "claude", "cloud": "claude", "cursor": "cursor",
                 "curser": "cursor", "devin": "devin", "devon": "devin", "deven": "devin"}
_ENG = r"(?P<engine>claude|clod|cloud|cursor|curser|devin|devon|deven)"
_SEP = r"\s*[:,.\-]\s*"
_TASK_FOR = re.compile(
    rf"^\s*(?:(?:new|start\s+an?|add\s+an?|create\s+an?)\s+)?task\s+(?:for|to)\s+{_ENG}"
    rf"(?:\s+(?:in|on|for)\s+(?P<lanes>[^:]+?))?{_SEP}(?P<rest>.+)$", re.IGNORECASE | re.DOTALL)
_ASSIGN = re.compile(rf"^\s*assign\s+(?P<lanes>.+?)\s+to\s+{_ENG}{_SEP}(?P<rest>.+)$",
                     re.IGNORECASE | re.DOTALL)
_FILLER_WORDS = {"the", "folder", "folders", "lane", "lanes", "directory", "part", "side"}


def _lane_words(text: str | None) -> list[str]:
    if not text:
        return []
    words = []
    for chunk in re.split(r"\s*(?:,|&|\band\b|\bplus\b)\s*", text, flags=re.IGNORECASE):
        kept = [w for w in chunk.split() if w.lower() not in _FILLER_WORDS]
        if kept:
            words.append(" ".join(kept))
    return words


def parse_task_command(text: str) -> tuple[str, list[str], str] | None:
    """"task for Devin in backend: add orders" -> ("devin", ["backend"], "add orders")."""
    for rx in (_TASK_FOR, _ASSIGN):
        m = rx.match(text)
        if m:
            rest = m.group("rest").strip()
            if not rest:
                return None
            return _ENGINE_WORDS[m.group("engine").lower()], _lane_words(m.group("lanes")), rest
    return None


def resolve_lane_words(workspace: str, words: list[str], saved: dict[str, list[str]]) -> list[str]:
    """Spoken lane names -> folders: a saved lane name, else a top-level folder name
    (case-insensitive), else the text as a relative path (validated later)."""
    root = Path(workspace)
    by_name = {k.casefold(): v for k, v in saved.items()}
    top = {p.name.casefold(): p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else {}
    out: list[str] = []
    for w in words:
        key = w.strip().casefold()
        if key in by_name:
            out.extend(by_name[key])
        elif key in top:
            out.append(top[key])
        elif (root / _normalize(w)).is_dir():
            out.append(_normalize(w))
        else:
            names = sorted(saved) + sorted(top.values())
            hint = f" Known: {', '.join(names[:12])}." if names else ""
            raise LaneError(f"I don't know a lane called \"{w}\".{hint}")
    return out


# ---------------------------------------------------------------- running tasks
import asyncio  # noqa: E402  (kept next to the runtime half of the module)
import json  # noqa: E402
import contextlib  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from typing import Awaitable, Callable  # noqa: E402

from . import worktrees  # noqa: E402
from .agent import AgentError, SessionStarted, TextDelta, ToolStart, TurnResult  # noqa: E402
from .checks import (CheckResult, failure_headline, failure_report, problem_with, run_checks,  # noqa: E402
                     specs_from)
from .cursor_agent import cursor_grantable  # noqa: E402
from .protocol import ev, ev_error  # noqa: E402
from .safety import Grant, Scope, assess, grant_for_task, lane_of, redact_for_speech  # noqa: E402
from .speech import SpeechComposer  # noqa: E402

log = logging.getLogger(__name__)

MAX_ACTIVE = 20
ENGINE_LABELS = {"claude": "Claude", "cursor": "Cursor", "devin": "Devin"}
_HIDDEN_FOLDERS = {"node_modules", "__pycache__", "venv", "dist", "build", "target", "out", "coverage"}


def _first_sentences(text: str, n: int = 2, limit: int = 300) -> str:
    flat = " ".join(text.replace("```", " ").split())
    parts = re.split(r"(?<=[.!?])\s+", flat)
    out = " ".join(parts[:n]).strip()
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def _speakable_first(text: str) -> str:
    composer = SpeechComposer(220)
    sentences = composer.feed(text) + composer.flush()  # redacted, code fences skipped
    return sentences[0] if sentences else ""


def build_task_prompt(t: TaskRecord) -> str:
    folders = ", ".join(f"{lane}/" for lane in t.lanes)
    if not t.local:
        where = f"Keep your changes to these folders: {folders}. " if t.lanes else ""
        where += "Open a pull request when you're done. "
    elif t.lanes:
        others = ("" if t.isolated else "Other agents may be changing files elsewhere in the project right now, "
                  "so don't edit, format or revert anything outside them. ")
        where = (f"Only change files inside these folders: {folders}. {others}"
                 "If you truly must change something outside them, try it and Barnaby will ask the user. ")
    else:
        where = "You have the whole project to yourself for now. "
    if t.isolated:
        where = (f"You are working in your own git worktree (a separate copy of the project) on branch {t.branch}. "
                 "Don't switch branches, merge, rebase or push; Barnaby commits your changes to this branch when "
                 "you're done and the user merges it. " + where)
    if t.local and t.checks:
        listed = "; ".join(f"`{c.run}`" + (f" in {c.dir}/" if c.dir else "") for c in t.checks)
        where += (f"When you finish, Barnaby runs these checks itself: {listed}. The task only counts as done if "
                  "they all pass, so run them yourself before you stop, and fix the code rather than weakening "
                  "or skipping a check. ")
    return (f"(Barnaby background task \"{t.title}\". You are one of several coding agents working on this "
            f"project at the same time. {where}Work on your own without asking questions; if something is "
            "ambiguous, make the most reasonable choice and say so. When you're done, start your reply with "
            "one or two plain sentences saying what you changed and how you checked it.)\n\n" + t.instruction)


def build_retry_prompt(t: TaskRecord, results: list[Any], allowed: int) -> str:
    """The follow-up turn after the task's checks failed: the failing commands and their output."""
    head = (f"(Barnaby ran this task's checks after your last turn and they failed (check run {t.attempts} of "
            f"{allowed}). Fix the cause in the code; don't weaken, skip or delete a check. Barnaby runs the checks "
            "again when you finish. Start your reply with one or two plain sentences saying what you fixed.)\n\n")
    report = failure_report(results)
    if t.cli_session_id:
        return head + report
    # No agent session to resume: give the whole task again.
    return build_task_prompt(t) + "\n\n" + head + report


@dataclass
class _Pending:
    request_id: str
    tool_name: str
    tool_input: dict[str, Any]
    summary: str
    grant: Grant | None
    card: dict[str, Any] = field(default_factory=dict)  # the task_approval_required event, for snapshots
    decision: bool | None = None


class TaskManager:
    """Runs background tasks in parallel, each with its own agent instance.

    Concurrency rules (from the engine review): every state change that decides
    what runs happens synchronously, before the first ``await`` that follows it,
    and every turn carries a *generation* number. Cancel, shutdown and a new turn
    bump the generation, so a turn that finishes late (a killed CLI ends its
    stream cleanly) sees it's stale and changes nothing.

    One run (an asyncio task in ``_runs``) covers preparing the task's worktree, the
    agent's turn, Barnaby's checks and any automatic retry turns after failed checks;
    it holds the task's slot and lanes throughout.
    """

    TEXT_INTERVAL_S = 0.25

    def __init__(self, *, store: Any, config: Any, agent_factory: Callable[[str, str], Any] | None,
                 engine_available: Callable[[str], str | None], emit: Callable[[dict[str, Any]], Awaitable[None]],
                 speak: Callable[[str], None], clock: Callable[[], float] = time.time,
                 on_forget: Callable[[str], None] | None = None, worktree_base: Path | None = None,
                 open_folder: Callable[[str], None] | None = None) -> None:
        self.store = store
        self.config = config
        self._factory = agent_factory
        self._available = engine_available
        self._emit = emit
        self._speak_raw = speak
        self._clock = clock
        self._on_forget = on_forget
        self._worktree_base = worktree_base
        self._open_folder = open_folder or open_in_file_manager
        self.tasks: dict[str, TaskRecord] = {}
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._agents: dict[str, Any] = {}
        self._gen: dict[str, int] = {}
        self._grants: dict[str, Grant | None] = {}
        self._groups: dict[str, dict[str, _Pending]] = {}   # task id -> request id -> item
        self._busy_git: set[str] = set()                     # tasks being discarded right now
        self._closing = False

    def _speak(self, text: str) -> None:
        self._speak_raw(redact_for_speech(text))

    def _bump(self, tid: str) -> int:
        self._gen[tid] = self._gen.get(tid, 0) + 1
        return self._gen[tid]

    def _current(self, t: TaskRecord, gen: int) -> bool:
        return not self._closing and self._gen.get(t.id) == gen and t.status in WORKING

    @property
    def worktree_base(self) -> Path:
        if self._worktree_base is None:
            from .config import data_dir
            self._worktree_base = data_dir() / "worktrees"
        return self._worktree_base

    # ------------------------------------------------------------ lifecycle
    def load(self) -> None:
        self.store.interrupt_active_tasks()
        for t in reversed(self.store.list_tasks()):
            self.tasks[t.id] = t

    async def shutdown(self) -> None:
        self._closing = True
        for t in self.tasks.values():
            if t.status in ACTIVE:
                self._bump(t.id)
                t.status = "interrupted"
                t.finished_at = t.finished_at or self._clock()
                if t.engine == "devin":
                    t.error = "Barnaby closed; the Devin session may still be running in Devin's app."
                self.store.save_task(t)
        self._groups.clear()
        await self._stop_runs(list(self._runs))

    async def _stop_runs(self, tids: list[str]) -> None:
        pairs = [(tid, self._runs.pop(tid, None), self._agents.pop(tid, None)) for tid in tids]
        for _tid, _run, agent in pairs:
            if agent is not None:
                with contextlib.suppress(Exception):
                    await agent.cancel()
        for _tid, run, _agent in pairs:
            if run is not None and not run.done():
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run

    # ------------------------------------------------------------ queries
    def snapshot(self) -> dict[str, Any]:
        ws = self.config.workspace
        # Undecided cards ride along, so a client that (re)connects after they were sent
        # (a tablet waking up, a reloaded window) can still approve them.
        approvals = [p.card for g in self._groups.values() for p in g.values() if p.decision is None and p.card]
        return ev("tasks", items=[t.public() for t in self.tasks.values()],
                  folders=self.folders(ws), saved_lanes=dict(self.config.task_lanes.get(ws, {})),
                  max_parallel=self.config.max_parallel_tasks, approvals=approvals,
                  checks=[asdict(c) for c in self.project_checks(ws)])

    def project_checks(self, workspace: str) -> tuple[CheckSpec, ...]:
        return specs_from(getattr(self.config, "task_checks", {}).get(workspace, []))

    def task_of_request(self, request_id: str) -> TaskRecord | None:
        tid = next((k for k, g in self._groups.items() if request_id in g), None)
        return self.tasks.get(tid) if tid else None

    @staticmethod
    def folders(workspace: str) -> list[str]:
        root = Path(workspace) if workspace else None
        if not root or not root.is_dir():
            return []
        try:
            return sorted((p.name for p in root.iterdir()
                           if p.is_dir() and not p.name.startswith(".") and p.name not in _HIDDEN_FOLDERS),
                          key=str.casefold)
        except OSError:
            return []

    def busy_lanes(self, workspace: str, *, exclude: str | None = None) -> tuple[str, ...]:
        """Lanes held by local tasks working in the project folder itself ("" = the whole
        project). A task in its own worktree never touches the project folder, so it holds
        nothing here."""
        out: list[str] = []
        for t in self.tasks.values():
            if (t.id != exclude and t.local and not t.isolated and t.status in HOLDS_LANES
                    and t.workspace == workspace):
                if not t.lanes:
                    return ("",)
                out.extend(t.lanes)
        return tuple(dict.fromkeys(out))

    def owns_request(self, request_id: str) -> bool:
        return any(request_id in g for g in self._groups.values())

    # ------------------------------------------------------------ commands
    async def create(self, engine: str, instruction: str, lanes: list[str], title: str | None,
                     checks: list[Any] | None = None) -> TaskRecord | None:
        """``checks``: None = the project's checks from Settings, a list = this task's own
        (an empty list means none)."""
        ws = self.config.workspace
        instruction = instruction.strip()
        problem = None
        if self._factory is None:
            problem = "Background tasks aren't available in this build."
        elif not ws or not Path(ws).is_dir():
            problem = "Pick a project folder in Settings first."
        elif not instruction:
            problem = "Give the task an instruction."
        elif sum(1 for t in self.tasks.values() if t.status in ACTIVE) >= MAX_ACTIVE:
            problem = (f"There are already {MAX_ACTIVE} tasks queued or running. "
                       "Wait for some to finish or cancel a few.")
        else:
            problem = self._available(engine)
        specs = self.project_checks(ws) if checks is None else specs_from(checks)
        if not problem and engine in LOCAL_ENGINES and specs:
            problem = problem_with(specs, list(self.config.safe_bash))
        if problem:
            await self._emit(ev_error("tasks", problem))
            return None
        try:
            canon = validate_lanes(ws, lanes)
        except LaneError as exc:
            await self._emit(ev_error("tasks", str(exc)))
            return None
        title = (title or "").strip() or instruction.splitlines()[0][:80]
        t = TaskRecord(id=uuid.uuid4().hex[:12], workspace=ws, engine=engine, title=title,
                       instruction=instruction, lanes=canon, created_at=self._clock(),
                       checks=specs if engine in LOCAL_ENGINES else ())
        if engine in LOCAL_ENGINES:
            await self._choose_isolation(t)
        if engine == "claude" and canon:
            t.warning = settings_warning(ws)
        t.pending_prompt = build_task_prompt(t)
        self.tasks[t.id] = t
        await self._changed(t)
        await self.pump()
        return t

    async def _choose_isolation(self, t: TaskRecord) -> None:
        if not getattr(self.config, "task_worktrees", True):
            t.isolation = "shared"
            return
        repo = await worktrees.repo_of(t.workspace)
        if repo is None:
            t.isolation = "shared"
            why = ("git isn't installed" if worktrees.git_exe() is None
                   else "this project isn't a git repository (or has no commits yet)")
            t.notice = (f"No worktree: {why}, so this task works in the project folder itself, "
                        "alongside you and any other task.")
            return
        t.isolation = "worktree"
        t.branch = worktrees.branch_name(t.id, t.title)
        t.worktree = str(worktrees.worktree_path(self.worktree_base, repo.top, t.id))

    async def cancel(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if t is None or t.status not in ACTIVE:
            return
        # Synchronous: from here on, a late end of the old turn is stale.
        self._bump(task_id)
        self._groups.pop(task_id, None)
        self._grants.pop(task_id, None)
        t.status, t.finished_at = "cancelled", self._clock()
        self.store.save_task(t)
        await self._stop_runs([task_id])
        await self._emit(ev("task_update", task=t.public()))
        await self.pump()

    async def follow_up(self, task_id: str, text: str) -> None:
        t = self.tasks.get(task_id)
        text = text.strip()
        if t is None:
            await self._emit(ev_error("tasks", "That task is no longer on the board."))
            return
        if t.status in ACTIVE or task_id in self._runs or task_id in self._busy_git:
            await self._emit(ev_error("tasks", "That task is still going. Follow up when it finishes."))
            return
        reason = self._available(t.engine) if text else "Say what the follow-up should do."
        if not reason and t.local and t.checks:
            reason = problem_with(t.checks, list(self.config.safe_bash))
        if reason:
            await self._emit(ev_error("tasks", reason))
            return
        try:
            lanes = validate_lanes(t.workspace, list(t.lanes))
        except LaneError as exc:
            await self._emit(ev_error("tasks", str(exc)))
            return
        t.lanes = lanes
        if t.local and not t.isolation:
            t.isolation = "shared"  # a task from before worktrees: its agent session lives in the project folder
        # With an agent session the task's context is already there; without one (it never
        # got going) start over with the original instruction plus the follow-up.
        t.pending_prompt = (f"(Follow-up from the user for this task.) {text}" if t.cli_session_id
                            else build_task_prompt(replace(t, instruction=f"{t.instruction}\n\nFollow-up: {text}")))
        self._grants.pop(task_id, None)
        # A follow-up is a new run: its time starts when it starts, not when the task first did,
        # and its checks get a fresh set of attempts.
        t.status, t.error, t.started_at, t.finished_at = "queued", "", None, None
        t.attempts, t.verification, t.check_results = 0, "", []
        await self._changed(t)
        await self.pump()

    async def clear_finished(self) -> None:
        """Remove finished tasks. A task's worktree goes with it unless it holds work that
        isn't merged anywhere (uncommitted changes or commits only on its branch); such a
        task stays on the board, so its work can still be merged or discarded."""
        gone: list[str] = []
        kept: list[tuple[TaskRecord, str]] = []
        for tid, t in list(self.tasks.items()):
            if t.status not in FINISHED or tid in self._runs or tid in self._busy_git:
                continue
            if t.worktree and t.isolated:
                why = await self._worktree_keep_reason(t)
                if why:
                    kept.append((t, why))
                    continue
            gone.append(tid)
        removed: list[TaskRecord] = []
        for tid in gone:
            t = self.tasks.get(tid)
            # The git checks above awaited: a follow-up may have queued this task meanwhile.
            if t is None or t.status not in FINISHED or tid in self._runs or tid in self._busy_git:
                continue
            del self.tasks[tid]
            self._gen.pop(tid, None)
            removed.append(t)
            if self._on_forget is not None:
                with contextlib.suppress(Exception):
                    self._on_forget(tid)
        self.store.delete_tasks([t.id for t in removed])
        for t in removed:
            if t.worktree and t.isolated:
                try:
                    await self._remove_worktree(t)
                except Exception:  # noqa: BLE001 - the task is gone either way; say why in the log
                    log.warning("couldn't remove the worktree of task %s at %s", t.id, t.worktree, exc_info=True)
        await self._emit(self.snapshot())
        if kept:
            names = "; ".join(f"{t.title} ({why})" for t, why in kept[:4])
            more = f" and {len(kept) - 4} more" if len(kept) > 4 else ""
            await self._emit(ev("notice", message=f"Kept {len(kept)} task{'s' if len(kept) != 1 else ''} whose "
                                                   f"work isn't merged yet: {names}{more}. Merge the branch, or "
                                                   "use Discard on the card."))

    async def _worktree_keep_reason(self, t: TaskRecord) -> str:
        repo = await worktrees.repo_of(t.workspace)
        if repo is None:
            return "its project isn't reachable as a git repository right now"
        return await worktrees.unmerged_work(repo.top, t.worktree, t.branch)

    async def _remove_worktree(self, t: TaskRecord) -> None:
        repo = await worktrees.repo_of(t.workspace)
        if repo is None:
            raise worktrees.WorktreeError("The project isn't reachable as a git repository right now.")
        await worktrees.remove(repo.top, Path(t.worktree), t.branch)

    async def discard(self, task_id: str) -> None:
        """Delete a finished task's worktree and branch (the UI confirms first), and the task."""
        t = self.tasks.get(task_id)
        if t is None:
            await self._emit(ev_error("tasks", "That task is no longer on the board."))
            return
        if t.status in ACTIVE or task_id in self._runs:
            await self._emit(ev_error("tasks", "Cancel the task before discarding its work."))
            return
        if not (t.isolated and t.worktree) or task_id in self._busy_git:
            await self._emit(ev_error("tasks", "That task has no worktree to discard."))
            return
        self._busy_git.add(task_id)
        try:
            await self._remove_worktree(t)
        except Exception as exc:  # noqa: BLE001
            await self._emit(ev_error("tasks", str(exc) or "Couldn't discard the task's worktree."))
            return
        finally:
            self._busy_git.discard(task_id)
        self.tasks.pop(task_id, None)
        self._gen.pop(task_id, None)
        self.store.delete_tasks([task_id])
        if self._on_forget is not None:
            with contextlib.suppress(Exception):
                self._on_forget(task_id)
        await self._emit(self.snapshot())
        await self._emit(ev("notice", message=f"Discarded {t.title}: deleted {t.branch} and its worktree."))

    async def open_folder(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        path = None if t is None else Path(t.run_dir or t.worktree) if t.isolated else Path(t.workspace)
        if path is None or not path.is_dir():
            await self._emit(ev_error("tasks", "That task's folder isn't there any more."))
            return
        try:
            self._open_folder(str(path))
        except Exception as exc:  # noqa: BLE001
            await self._emit(ev_error("tasks", f"Couldn't open the folder: {exc}"))

    async def decide(self, request_id: str, approve: bool) -> bool:
        tid = next((k for k, g in self._groups.items() if request_id in g), None)
        if tid is None:
            return False
        group = self._groups[tid]
        item = group[request_id]
        if item.decision is not None:
            return True
        if approve and item.grant is None:
            approve = False
            refused = True
        else:
            refused = False
        item.decision = approve
        done = all(p.decision is not None for p in group.values())
        t = self.tasks.get(tid)
        if done and self._groups.get(tid) is group and t is not None and t.status == "needs_approval":
            # Synchronous hand-off: a second client deciding at the same moment finds no group.
            del self._groups[tid]
            approved = [p for p in group.values() if p.decision]
            denied = [p for p in group.values() if not p.decision]
            parts = []
            if approved:
                parts.append("The user approved: " + "; ".join(p.summary for p in approved)
                             + ". Perform exactly those actions now and continue the task.")
            if denied:
                parts.append("The user declined: " + "; ".join(p.summary for p in denied)
                             + ". Do not attempt those; finish what you can inside your folders and say what's left.")
            self._grants[tid] = (Grant(allow=tuple(r for p in approved if p.grant for r in p.grant.allow))
                                 if approved else None)
            t.pending_prompt = " ".join(parts)
            t.status = "queued"
        if refused:
            await self._emit(ev_error("approval", "That can't be approved narrowly for a background task. "
                                                  "Cancel the task or give it a different instruction."))
        await self._emit(ev("task_approval_decided", task_id=tid, request_id=request_id, approved=approve))
        if done and t is not None and t.status == "queued":
            await self._changed(t)
            await self.pump()
        return True

    # ------------------------------------------------------------ scheduling
    async def pump(self) -> None:
        if self._closing or self._factory is None:
            return
        ordered = sorted(self.tasks.values(), key=lambda x: x.created_at)
        started: list[TaskRecord] = []
        loop = asyncio.get_running_loop()
        share = bool(getattr(self.config, "task_worktree_parallel", False))
        for t in pick_next(ordered, self.config.max_parallel_tasks, worktrees_share_lanes=share):
            if t.id in self._runs or t.id in self._busy_git:  # still unwinding an earlier turn
                continue
            # Synchronous claim: status, generation and run are set before any await,
            # so a second pump running meanwhile can't start this task again.
            t.status = "running"
            t.started_at = t.started_at or self._clock()
            gen = self._bump(t.id)
            self._runs[t.id] = loop.create_task(self._run(t, gen))
            started.append(t)
        for t in started:
            await self._changed(t)

    async def _run(self, t: TaskRecord, gen: int) -> None:
        me = asyncio.current_task()
        try:
            await self._drive(t, gen)
        except asyncio.CancelledError:
            if self._runs.get(t.id) is me:
                del self._runs[t.id]
            if not self._current(t, gen):
                return  # cancelled or shutting down: already recorded
            raise
        except Exception as exc:  # noqa: BLE001 - a task must never take the engine down
            log.exception("task %s failed", t.id)
            if self._current(t, gen):
                await self._finish(t, "failed", "", error=f"Unexpected error: {exc}")
        if self._runs.get(t.id) is me:
            del self._runs[t.id]
        await self.pump()  # a task that ended while its run was unwinding may be queued again

    async def _drive(self, t: TaskRecord, gen: int) -> None:
        """Worktree, then agent turn -> checks -> (retry turn -> checks)… until done or failed."""
        if t.isolated:
            problem = await self._prepare_worktree(t)
            if not self._current(t, gen):
                return
            if problem:
                await self._finish(t, "failed", "", error=problem)
                return
        elif t.local:
            t.run_dir = ""
        while True:
            turn = await self._turn(t, gen)
            if turn is None:
                return  # stale, failed or waiting for approval: already handled
            text = turn
            if not (t.local and t.checks):
                t.verification, t.check_results = "unverified", []
                await self._commit(t, text)
                await self._finish(t, "done", text)
                return
            t.status = "verifying"
            t.attempts += 1
            await self._changed(t)
            results = await run_checks(t.checks, t.root, safe_bash=list(self.config.safe_bash),
                                       timeout_s=self.config.task_check_timeout_s)
            if not self._current(t, gen):
                return
            t.check_results = [r.public() for r in results]
            if all(r.passed for r in results):
                t.verification = "passed"
                await self._commit(t, text)
                await self._finish(t, "done", text)
                return
            allowed = 1 + max(0, int(self.config.task_check_retries))
            if t.attempts >= allowed:
                t.verification = "failed"
                tries = f" after {t.attempts} check runs" if t.attempts > 1 else ""
                report = failure_report(results, per_check_chars=1500)
                await self._finish(t, "failed", text, error=f"{failure_headline(results)} Still failing{tries}."
                                   f"\n\n{report}")
                return
            # Automatic retry: same agent session, the failing commands and their output.
            t.pending_prompt = build_retry_prompt(t, results, allowed)
            t.status = "running"
            await self._changed(t)
            who = ENGINE_LABELS.get(t.engine, t.engine)
            await self._emit(ev("task_activity", task_id=t.id, tool="Checks",
                                path=f"failed; {who} is fixing it (run {t.attempts + 1} of {allowed})"))

    async def _prepare_worktree(self, t: TaskRecord) -> str | None:
        """Create (or reuse) the task's worktree and point the agent at it. A reason on failure."""
        repo = await worktrees.repo_of(t.workspace)
        if repo is None:
            return ("The project is no longer a git repository with commits, so the task's worktree "
                    f"({t.worktree}) can't be used.")
        try:
            created = await worktrees.ensure(repo.top, Path(t.worktree), t.branch)
        except worktrees.WorktreeError as exc:
            return str(exc)
        t.run_dir = str(Path(t.worktree) / repo.prefix) if repo.prefix else t.worktree
        if not created:
            return None
        base = await worktrees.current_branch(repo.top) or "a detached HEAD"
        sha = await worktrees.head(t.worktree)
        dirty = await worktrees.changed_files(repo.top)
        note = f"Branch {t.branch} started from {sha or 'HEAD'} on {base}."
        if dirty > 0:
            note += (f" Your {dirty} uncommitted change{'s' if dirty != 1 else ''} in the project folder "
                     "aren't in this worktree.")
        t.notice = note
        setup = specs_from(getattr(self.config, "task_worktree_setup", {}).get(t.workspace, []))
        if setup:
            await self._emit(ev("task_activity", task_id=t.id, tool="Setup", path="preparing the worktree"))
            # Setup commands come only from config.json (not the UI), so they aren't limited
            # to the auto-approve list; they still run without a shell.
            results = await run_checks(setup, t.root, safe_bash=None, timeout_s=self.config.task_check_timeout_s)
            if not all(r.passed for r in results):
                return f"Worktree setup failed. {failure_headline(results)}\n\n{failure_report(results, per_check_chars=1500)}"
        await self._changed(t)
        return None

    async def _commit(self, t: TaskRecord, text: str) -> None:
        if not (t.isolated and t.run_dir):
            return
        body = _first_sentences(text, 3, 600)
        verified = {"passed": "Checks passed: " + ", ".join(c.run for c in t.checks) + "."}.get(t.verification, "")
        message = f"{t.title}\n\n{body}\n\n{verified}\nBarnaby background task {t.id} ({t.engine}).\n".replace(
            "\n\n\n", "\n\n")
        sha, problem = await worktrees.commit_all(t.worktree, message)
        if sha:
            t.notice = f"Committed {sha} on {t.branch}. Merge it when you're happy with it."
        elif problem:
            t.notice = f"Couldn't commit to {t.branch} ({problem}). The changes are in the worktree, uncommitted."
        elif not t.notice.startswith("Committed"):
            t.notice = f"No file changes to commit on {t.branch}."

    async def _turn(self, t: TaskRecord, gen: int) -> str | None:
        """One agent turn. The reply text when it ended well; None when it failed, needs
        approval, or went stale (each already handled)."""
        prompt, t.pending_prompt = t.pending_prompt or build_task_prompt(t), None
        grant = self._grants.pop(t.id, None)
        scope = Scope(allow_lanes=t.lanes) if t.lanes else None
        streamed: list[str] = []
        pending_text: list[str] = []
        last_text = 0.0
        result: TurnResult | None = None
        agent: Any = None
        outcome: tuple[str, str] | None = None  # (status, error) when the turn failed

        async def flush_text() -> None:
            nonlocal last_text
            if pending_text and self._current(t, gen):
                await self._emit(ev("task_text", task_id=t.id, delta="".join(pending_text)))
            pending_text.clear()
            last_text = time.monotonic()

        try:
            agent = self._agents[t.id] = self._factory(t.engine, t.id)  # type: ignore[misc]
            async with asyncio.timeout(self.config.task_timeout_s):
                # Lanes, permission rules and the Cursor guard are all relative to this folder:
                # for a worktree task, "the project" is its worktree.
                async with contextlib.aclosing(agent.run(prompt, workspace=t.root, session_id=t.cli_session_id,
                                                         grant=grant, scope=scope)) as stream:
                    async for event in stream:
                        if isinstance(event, SessionStarted):
                            t.cli_session_id = event.session_id
                        elif isinstance(event, TextDelta):
                            streamed.append(event.text)
                            pending_text.append(event.text)
                            if time.monotonic() - last_text >= self.TEXT_INTERVAL_S:
                                await flush_text()
                        elif isinstance(event, ToolStart):
                            if self._current(t, gen):
                                await self._emit(ev("task_activity", task_id=t.id, tool=event.name,
                                                    path=_rel(t.root, event.input)))
                        elif isinstance(event, TurnResult):
                            result = event
            await flush_text()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if agent is not None:
                with contextlib.suppress(Exception):
                    await agent.cancel()
            minutes = max(1, round(self.config.task_timeout_s / 60))
            outcome = ("failed", f"It took longer than {minutes} minutes and was stopped.")
        except AgentError as exc:
            outcome = ("failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - a task must never take the engine down
            log.exception("task %s failed", t.id)
            outcome = ("failed", f"Unexpected error: {exc}")
        finally:
            if self._agents.get(t.id) is agent:
                self._agents.pop(t.id, None)

        if not self._current(t, gen):
            return None  # cancelled/shut down while the agent was unwinding
        t.turns += 1
        text = result.text if result and result.text else "".join(streamed)
        if outcome is not None:
            await self._finish(t, outcome[0], text, error=outcome[1])
            return None
        if result is not None and result.cost_usd is not None:
            t.cost_usd = round((t.cost_usd or 0.0) + result.cost_usd, 4)
        if result is not None and result.denials:
            await self._open_approvals(t, result, text)
            return None
        if result is None or result.is_error:
            await self._finish(t, "failed", text,
                               error=_first_sentences(text, 2) or "The agent stopped without reporting a result.")
            return None
        return text

    async def _finish(self, t: TaskRecord, status: str, text: str, *, error: str = "") -> None:
        t.status = status
        t.finished_at = self._clock()
        t.result_text = text
        if status == "done":
            t.summary = _first_sentences(text)
        t.error = error
        await self._changed(t)
        await self._emit(ev("task_done", task_id=t.id, status=status, title=t.title, engine=t.engine,
                            summary=t.summary, error=error, verification=t.verification))
        who = ENGINE_LABELS.get(t.engine, t.engine)
        if status == "done":
            first = _speakable_first(text)
            checked = " Its checks passed." if t.verification == "passed" else ""
            self._speak(f"{who} finished {t.title}.{checked}" + (f" {first}" if first else ""))
        else:
            self._speak(f"{who}'s task {t.title} failed. {_speakable_first(error)}".strip())
        await self.pump()

    async def _open_approvals(self, t: TaskRecord, result: TurnResult, text: str) -> None:
        # Build the group and change status synchronously, then announce the cards.
        # Edits are judged against the folder the agent works in (its worktree, if it has one).
        others = () if t.isolated else self.busy_lanes(t.workspace, exclude=t.id)
        group: dict[str, _Pending] = {}
        cards: list[dict[str, Any]] = []
        for d in result.denials:
            a = assess(d.tool_name, d.tool_input)
            summary = describe_task_denial(d.tool_name, d.tool_input, t.root, others) or a.summary
            grant = grant_for_task(d.tool_name, d.tool_input, t.root)
            if grant is not None and t.engine == "cursor" and not cursor_grantable(d.tool_name, d.tool_input):
                grant = None
            rid = uuid.uuid4().hex[:12]
            card = ev("task_approval_required", task_id=t.id, request_id=rid, tool_name=d.tool_name,
                      summary=summary, risk=a.risk.value, voice_approvable=False,
                      grantable=grant is not None, detail=_detail(d.tool_input))
            group[rid] = _Pending(rid, d.tool_name, dict(d.tool_input), summary, grant, card)
            cards.append(card)
        self._groups[t.id] = group
        t.status = "needs_approval"
        t.result_text = text
        await self._changed(t)
        for card in cards:
            if self._groups.get(t.id) is not group:
                return  # cancelled while the cards were going out
            await self._emit(card)
        if self._groups.get(t.id) is group:
            self._speak(f"{ENGINE_LABELS.get(t.engine, t.engine)} task {t.title} needs your OK.")
        await self.pump()

    async def _changed(self, t: TaskRecord) -> None:
        self.store.save_task(t)
        await self._emit(ev("task_update", task=t.public()))


def open_in_file_manager(path: str) -> None:
    """Show a folder Barnaby created in Explorer (or the platform's file manager). No shell:
    ``os.startfile`` hands the path to the Windows shell as a single argument."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path],  # noqa: S603
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def describe_task_denial(tool_name: str, tool_input: dict[str, Any], workspace: str,
                         other_lanes: tuple[str, ...]) -> str | None:
    """Card text for an edit a task tried outside its folders (None: use the normal text)."""
    if tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return None
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return None
    lane = lane_of(workspace, raw, ("",))
    if lane is None:
        return f"{tool_name} {raw} (outside the project folder)"
    rel = _rel(workspace, {"file_path": raw}) or raw
    busy = lane_of(workspace, raw, other_lanes) if other_lanes else None
    note = f"; another task is working in {busy or 'this project'}" if busy is not None else ""
    return f"{tool_name} {rel} (outside this task's folders{note})"


_EDIT_RULE = re.compile(r"^(Edit|Write|MultiEdit)(\(|$)")


def settings_warning(workspace: str) -> str:
    """Claude Code merges the user's and the project's own settings with Barnaby's per-turn
    file, so a saved Edit allow rule there widens every lane. Say so on the task."""
    home = Path.home() / ".claude"
    found: list[str] = []
    for path in (Path(workspace) / ".claude" / "settings.json", Path(workspace) / ".claude" / "settings.local.json",
                 home / "settings.json", home / "settings.local.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        allow = ((data or {}).get("permissions") or {}).get("allow") or []
        rules = [r for r in allow if isinstance(r, str) and _EDIT_RULE.match(r)]
        if rules:
            found.append(f"{', '.join(rules[:3])} in {path}")
    if not found:
        return ""
    return ("Your Claude settings allow edits (" + "; ".join(found) + "), so this task can change files "
            "outside its folders without asking.")


def _rel(workspace: str, tool_input: dict[str, Any]) -> str | None:
    raw = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return Path(raw).resolve().relative_to(Path(workspace).resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _detail(tool_input: dict[str, Any]) -> str:
    for key in ("command", "file_path", "notebook_path", "url"):
        if tool_input.get(key):
            return str(tool_input[key])[:2000]
    return ""
