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

from . import languages

MAX_LANES = 8
MAX_LANE_CHARS = 200
LOCAL_ENGINES = frozenset({"claude", "cursor"})
ACTIVE = frozenset({"queued", "running", "needs_approval"})
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
    warning: str = ""                                            # shown on the card; not persisted
    pending_prompt: str | None = field(default=None, repr=False)  # next turn to run (not shown)

    @property
    def local(self) -> bool:
        return self.engine in LOCAL_ENGINES

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("pending_prompt", None)
        d["lanes"] = list(self.lanes)
        return d


def pick_next(tasks: list[TaskRecord], max_parallel: int) -> list[TaskRecord]:
    """Queued tasks (in creation order) that may start now.

    Running tasks use a slot and hold their lanes. Tasks waiting for approval hold
    their lanes but not a slot. A blocked task doesn't stop later tasks whose lanes
    are free.
    """
    slots = max_parallel - sum(1 for t in tasks if t.status == "running")
    held = [t for t in tasks if t.local and t.status in ("running", "needs_approval")]
    started: list[TaskRecord] = []
    for t in tasks:
        if slots <= 0:
            break
        if t.status != "queued":
            continue
        if t.local and any(h.workspace == t.workspace and lanes_overlap(h.lanes, t.lanes) for h in held):
            continue
        started.append(t)
        slots -= 1
        if t.local:
            held.append(t)
    return started


# ---------------------------------------------------------------- voice grammar
def _lane_words(text: str | None, g: languages.Grammar) -> list[str]:
    if not text:
        return []
    words = []
    for chunk in g.lane_split.split(text) if g.lane_split else [text]:
        kept = [w for w in chunk.split() if w.lower() not in g.lane_filler]
        if kept:
            words.append(" ".join(kept))
    return words


def parse_task_command(text: str, lang: str = "en") -> tuple[str, list[str], str] | None:
    """"task for Devin in backend: add orders" -> ("devin", ["backend"], "add orders").
    Phrasing per language comes from ``languages.GRAMMARS``; English always works."""
    for g in languages.grammars(lang):
        for rx in g.task_patterns:
            m = rx.match(text)
            if m:
                rest = m.group("rest").strip()
                if not rest:
                    return None
                return g.engines[m.group("engine").lower()], _lane_words(m.group("lanes"), g), rest
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
import time  # noqa: E402
import uuid  # noqa: E402
from typing import Awaitable, Callable  # noqa: E402

from .agent import AgentError, SessionStarted, TextDelta, ToolStart, TurnResult  # noqa: E402
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
        where = (f"Only change files inside these folders: {folders}. Other agents may be changing files "
                 "elsewhere in the project right now, so don't edit, format or revert anything outside "
                 "them. If you truly must, try it and Barnaby will ask the user. ")
    else:
        where = "You have the whole project to yourself for now. "
    return (f"(Barnaby background task \"{t.title}\". You are one of several coding agents working on this "
            f"project at the same time. {where}Work on your own without asking questions; if something is "
            "ambiguous, make the most reasonable choice and say so. When you're done, start your reply with "
            "one or two plain sentences saying what you changed and how you checked it.)\n\n" + t.instruction)


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
    """

    TEXT_INTERVAL_S = 0.25

    def __init__(self, *, store: Any, config: Any, agent_factory: Callable[[str, str], Any] | None,
                 engine_available: Callable[[str], str | None], emit: Callable[[dict[str, Any]], Awaitable[None]],
                 speak: Callable[[str], None], clock: Callable[[], float] = time.time,
                 on_forget: Callable[[str], None] | None = None) -> None:
        self.store = store
        self.config = config
        self._factory = agent_factory
        self._available = engine_available
        self._emit = emit
        self._speak_raw = speak
        self._clock = clock
        self._on_forget = on_forget
        self.tasks: dict[str, TaskRecord] = {}
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._agents: dict[str, Any] = {}
        self._gen: dict[str, int] = {}
        self._grants: dict[str, Grant | None] = {}
        self._groups: dict[str, dict[str, _Pending]] = {}   # task id -> request id -> item
        self._closing = False

    def _speak(self, text: str) -> None:
        self._speak_raw(redact_for_speech(text))

    def _bump(self, tid: str) -> int:
        self._gen[tid] = self._gen.get(tid, 0) + 1
        return self._gen[tid]

    def _current(self, t: TaskRecord, gen: int) -> bool:
        return not self._closing and self._gen.get(t.id) == gen and t.status == "running"

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
                  max_parallel=self.config.max_parallel_tasks, approvals=approvals)

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
        """Lanes held by local tasks ("" = the whole project)."""
        out: list[str] = []
        for t in self.tasks.values():
            if (t.id != exclude and t.local and t.status in ("running", "needs_approval")
                    and t.workspace == workspace):
                if not t.lanes:
                    return ("",)
                out.extend(t.lanes)
        return tuple(dict.fromkeys(out))

    def owns_request(self, request_id: str) -> bool:
        return any(request_id in g for g in self._groups.values())

    # ------------------------------------------------------------ commands
    async def create(self, engine: str, instruction: str, lanes: list[str], title: str | None) -> TaskRecord | None:
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
                       instruction=instruction, lanes=canon, created_at=self._clock())
        if engine == "claude" and canon:
            t.warning = settings_warning(ws)
        t.pending_prompt = build_task_prompt(t)
        self.tasks[t.id] = t
        await self._changed(t)
        await self.pump()
        return t

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
        if t.status in ACTIVE or task_id in self._runs:
            await self._emit(ev_error("tasks", "That task is still going. Follow up when it finishes."))
            return
        reason = self._available(t.engine) if text else "Say what the follow-up should do."
        if reason:
            await self._emit(ev_error("tasks", reason))
            return
        try:
            lanes = validate_lanes(t.workspace, list(t.lanes))
        except LaneError as exc:
            await self._emit(ev_error("tasks", str(exc)))
            return
        t.lanes = lanes
        # With an agent session the task's context is already there; without one (it never
        # got going) start over with the original instruction plus the follow-up.
        t.pending_prompt = (f"(Follow-up from the user for this task.) {text}" if t.cli_session_id
                            else build_task_prompt(replace(t, instruction=f"{t.instruction}\n\nFollow-up: {text}")))
        self._grants.pop(task_id, None)
        # A follow-up is a new run: its time starts when it starts, not when the task first did.
        t.status, t.error, t.started_at, t.finished_at = "queued", "", None, None
        await self._changed(t)
        await self.pump()

    async def clear_finished(self) -> None:
        gone = [tid for tid, t in self.tasks.items() if t.status in FINISHED and tid not in self._runs]
        for tid in gone:
            del self.tasks[tid]
            self._gen.pop(tid, None)
            if self._on_forget is not None:
                with contextlib.suppress(Exception):
                    self._on_forget(tid)
        self.store.delete_tasks(gone)
        await self._emit(self.snapshot())

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
        for t in pick_next(ordered, self.config.max_parallel_tasks):
            if t.id in self._runs:  # still unwinding an earlier turn
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
                async with contextlib.aclosing(agent.run(prompt, workspace=t.workspace, session_id=t.cli_session_id,
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
                                                    path=_rel(t.workspace, event.input)))
                        elif isinstance(event, TurnResult):
                            result = event
            await flush_text()
        except asyncio.CancelledError:
            if not self._current(t, gen):
                return  # cancelled or shutting down: already recorded
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
            if self._runs.get(t.id) is asyncio.current_task():
                del self._runs[t.id]

        if not self._current(t, gen):
            return  # cancelled/shut down while the agent was unwinding
        text = result.text if result and result.text else "".join(streamed)
        if outcome is not None:
            await self._finish(t, outcome[0], text, error=outcome[1])
            return
        if result is not None and result.cost_usd is not None:
            t.cost_usd = round((t.cost_usd or 0.0) + result.cost_usd, 4)
        if result is not None and result.denials:
            await self._open_approvals(t, result, text)
            return
        if result is None or result.is_error:
            await self._finish(t, "failed", text,
                               error=_first_sentences(text, 2) or "The agent stopped without reporting a result.")
            return
        await self._finish(t, "done", text)

    async def _finish(self, t: TaskRecord, status: str, text: str, *, error: str = "") -> None:
        t.status = status
        t.turns += 1
        t.finished_at = self._clock()
        t.result_text = text
        if status == "done":
            t.summary = _first_sentences(text)
        t.error = error
        await self._changed(t)
        await self._emit(ev("task_done", task_id=t.id, status=status, title=t.title, engine=t.engine,
                            summary=t.summary, error=error))
        who = ENGINE_LABELS.get(t.engine, t.engine)
        if status == "done":
            first = _speakable_first(text)
            self._speak(f"{who} finished {t.title}." + (f" {first}" if first else ""))
        else:
            self._speak(f"{who}'s task {t.title} failed. {_speakable_first(error)}".strip())
        await self.pump()

    async def _open_approvals(self, t: TaskRecord, result: TurnResult, text: str) -> None:
        # Build the group and change status synchronously, then announce the cards.
        others = self.busy_lanes(t.workspace, exclude=t.id)
        group: dict[str, _Pending] = {}
        cards: list[dict[str, Any]] = []
        for d in result.denials:
            a = assess(d.tool_name, d.tool_input)
            summary = describe_task_denial(d.tool_name, d.tool_input, t.workspace, others) or a.summary
            grant = grant_for_task(d.tool_name, d.tool_input, t.workspace)
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
        t.turns += 1
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
