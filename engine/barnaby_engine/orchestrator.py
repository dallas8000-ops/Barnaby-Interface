"""The conversation state machine.

All mutation happens on the asyncio loop thread. Audio callbacks and blocking
model calls hop threads explicitly (``call_soon_threadsafe`` / executor), so
there is no shared mutable state touched from two threads.

Turn lifecycle:
  ptt_start -> LISTENING -> ptt_stop -> TRANSCRIBING -> (review draft) ->
  submit -> THINKING (+ speaking sentences as they stream) ->
  [denials -> AWAITING_APPROVAL -> approve/deny -> resumed turn] -> IDLE
Barge-in: ptt_start while speaking stops speech; a submit while a turn is
running cancels that turn and resumes the same Claude session with the new
instruction.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from . import lexicon
from .adaptive import VoiceProfile
from .agent import (Agent, AgentError, SessionStarted, TextDelta, ToolEnd,
                    ToolStart, TurnResult)
from .config import Config, data_dir
from .editors import EditorError, open_in_editor
from .protocol import (Approve, Cancel, Command, DeleteVocab, Deny, DiscardDraft,
                       GetVoiceProfile, ListDevices, ListSessions, LoadSession,
                       NewSession, OpenInEditor, Ping, PttStart, PttStop, Risk,
                       SetConfig, State, StopSpeaking, TextSubmit, UpdateCorrection,
                       ev, ev_error, ev_state)
from .cursor_agent import cursor_grantable
from .safety import Grant, assess, grant_for
from .speech import SpeechComposer
from .store import Store
from .voice import STT_RATE, Recorder, Speaker, Transcriber, list_input_devices

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]

MIN_AUDIO_S = 0.35
MIN_RMS = 0.004
STT_TIMEOUT_S = 60.0  # generous for CPU + long clips; a stall past this is a fault, not work
LEVEL_INTERVAL_S = 0.04
_YES_CORE = frozenset({"yes", "yeah", "yep", "approve", "approved", "confirm", "confirmed", "proceed", "ahead"})
_NO_CORE = frozenset({"no", "nope", "deny", "denied", "don't", "dont", "reject", "cancel", "stop"})
_FILLER = frozenset({"go", "do", "it", "please", "ok", "okay", "that", "sure", "thanks", "and", "just"})


ENGINE_LABELS = {"claude": "Claude", "cursor": "Cursor"}
_ENGINE_WORDS = {"claude": "claude", "clod": "claude", "cloud": "claude", "cursor": "cursor", "curser": "cursor"}
_ROUTE_RE = re.compile(
    r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:(?:have|ask|use|let|get|tell)\s+)?"
    r"(claude|clod|cloud|cursor|curser)\b(?:\s*[,:.\-]\s*|\s+(?:to\s+)?|\s*$)(.*)$",
    re.IGNORECASE | re.DOTALL)


def route_engine(text: str) -> tuple[str | None, str]:
    """"Cursor, fix the build" / "have Claude run the tests" / "use Cursor" ->
    (engine, remaining instruction). Only a leading address counts, so
    "add a cursor to the table" stays an ordinary instruction.

    "cloud" is accepted only with a routing verb ("use cloud ...") because
    Whisper often hears "Claude" that way, but "cloud functions ..." is real.
    """
    m = _ROUTE_RE.match(text)
    if not m:
        return None, text
    word = m.group(1).lower()
    has_verb = bool(re.match(r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:have|ask|use|let|get|tell)\s", text, re.IGNORECASE))
    has_sep = bool(re.match(r"^\s*(?:hey\s+|ok(?:ay)?\s+)?\w+\s*[,:.\-]", text))
    if word in ("cloud", "clod") and not has_verb:
        return None, text
    if not has_verb and not has_sep and m.group(2).strip():
        return None, text  # "cursor position is wrong" is not an address
    return _ENGINE_WORDS[word], m.group(2).strip()


def classify_spoken_decision(text: str) -> bool | None:
    """True/False for a short spoken yes/no; None if it's anything else.

    Every word must be a decision word or filler, so "yes, and also delete the
    logs" is NOT an approval: it's a new instruction and must go to the agent.
    """
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 6:
        return None
    yes = any(w in _YES_CORE for w in words) or words in (["do", "it"], ["go", "ahead"])
    no = any(w in _NO_CORE for w in words)
    if yes == no or any(w not in _YES_CORE | _NO_CORE | _FILLER for w in words):
        return None
    return yes


@dataclass
class Draft:
    id: str
    shown_text: str
    audio: np.ndarray


@dataclass
class PendingApproval:
    request_id: str
    tool_name: str
    tool_input: dict[str, Any]
    summary: str
    risk: Risk
    voice_approvable: bool
    grant: Grant | None
    decision: bool | None = None


@dataclass
class ApprovalGroup:
    items: dict[str, PendingApproval] = field(default_factory=dict)

    def done(self) -> bool:
        return all(p.decision is not None for p in self.items.values())


def tool_summary(name: str, tool_input: dict[str, Any], workspace: str) -> tuple[str, str | None]:
    """Human summary + workspace-relative file path (for editor buttons)."""
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    rel: str | None = None
    if isinstance(path, str) and path:
        try:
            rel = str(Path(path).resolve().relative_to(Path(workspace).resolve()))
        except (ValueError, OSError):
            rel = path
    if name == "Bash":
        return str(tool_input.get("command", ""))[:300], None
    if name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        return f"{name} {rel or ''}".strip(), rel
    if name in ("Grep", "Glob"):
        return f"{name} {tool_input.get('pattern', '')}"[:200], rel
    if name == "WebFetch":
        return f"Fetch {tool_input.get('url', '')}"[:200], None
    if name == "WebSearch":
        return f"Search {tool_input.get('query', '')}"[:200], None
    if name in ("Task", "Agent"):
        return f"Subagent: {tool_input.get('description', '')}"[:200], None
    if name == "TodoWrite":
        return "Update task list", None
    return name, rel


class Orchestrator:
    def __init__(self, *, config: Config, store: Store, profile: VoiceProfile,
                 recorder: Recorder, transcriber: Transcriber, speaker: Speaker,
                 agent: Agent | None = None, emit: Emit,
                 agents: dict[str, Agent] | None = None,
                 engine_available: Callable[[str], str | None] | None = None,
                 transcriber_factory: Callable[[Config], Transcriber] | None = None,
                 speaker_factory: Callable[[Config], Speaker] | None = None) -> None:
        self.config = config
        self.store = store
        self.profile = profile
        self.recorder = recorder
        self.transcriber = transcriber
        self.speaker = speaker
        if agents is None:
            if agent is None:
                raise ValueError("Orchestrator needs an agent")
            agents = {"claude": agent}
        self.agents = agents
        # engine name -> None if usable, else a human reason (e.g. "not installed")
        self._engine_available = engine_available or (lambda name: None)
        self._running_agent: Agent | None = None
        self.emit = emit
        self._transcriber_factory = transcriber_factory
        self._speaker_factory = speaker_factory

        self.state = State.IDLE
        self.session_id: str | None = None
        self.claude_session_id: str | None = None  # the agent CLI's own session id (Claude or Cursor)
        self.session_engine: str | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._drafts: dict[str, Draft] = {}
        self._approvals: ApprovalGroup | None = None
        self._speech_q: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._speech_gen = 0
        self._speaking = False
        self._speech_task: asyncio.Task[None] | None = None
        self._last_level = 0.0
        self._project_terms: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recording = False
        self._transcribing = False
        self._cancel_requested = False
        # Last-known component status, replayed in `hello`: load events fire at
        # startup, usually before any UI has connected to receive them.
        self._components: dict[str, dict[str, Any]] = {
            "stt": {"status": "loading"}, "tts": {"status": "loading"}}

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._speech_task = asyncio.create_task(self._speech_worker())
        self._refresh_project_terms()
        asyncio.create_task(self._preload())

    async def shutdown(self) -> None:
        await self._cancel_turn()
        self.speaker.stop()
        if self._speech_task:
            self._speech_task.cancel()

    async def hello(self) -> dict[str, Any]:
        return ev("hello", state=self.state.value, config=self.config.public(), engines=self.engines(),
                  session_id=self.session_id, speaking=self._speaking,
                  sessions=self.store.list_sessions(), components=self._components)

    def engines(self) -> list[dict[str, Any]]:
        out = []
        for name in self.agents:
            reason = self._engine_available(name)
            out.append({"name": name, "available": reason is None, **({"reason": reason} if reason else {})})
        return out

    @property
    def agent(self) -> Agent:
        """The agent for the currently selected engine."""
        return self.agents.get(self.config.engine) or self.agents["claude"]

    async def _component(self, name: str, status: str, detail: str | None = None) -> None:
        self._components[name] = {"status": status, **({"detail": detail} if detail else {})}
        await self.emit(ev("component", name=name, status=status, **({"detail": detail} if detail else {})))

    async def _preload(self) -> None:
        loop = asyncio.get_running_loop()
        for name, component in (("stt", self.transcriber), ("tts", self.speaker)):
            loader = getattr(component, "load", None)
            if loader is None:
                continue
            await self._component(name, "loading")
            try:
                await loop.run_in_executor(None, loader)
                await self._component(name, "ready", getattr(component, "status_detail", None))
            except Exception as exc:  # noqa: BLE001
                log.exception("%s failed to load", name)
                await self._component(name, "error", _component_hint(name, exc))

    # ------------------------------------------------------------ dispatch
    async def handle(self, cmd: Command) -> None:
        match cmd:
            case PttStart():
                await self._ptt_start()
            case PttStop():
                await self._ptt_stop()
            case TextSubmit(text=text, draft_id=draft_id):
                await self._submit(text, draft_id)
            case DiscardDraft(draft_id=draft_id):
                self._drafts.pop(draft_id, None)
                await self._set_state(State.IDLE)
            case Cancel():
                await self._cancel_all()
            case StopSpeaking():
                self._stop_speaking()
            case Approve(request_id=rid):
                await self._decide(rid, True)
            case Deny(request_id=rid):
                await self._decide(rid, False)
            case NewSession():
                await self._cancel_all()
                self.session_id = self.claude_session_id = self.session_engine = None
                await self.emit(ev("session_loaded", session=None))
            case LoadSession(session_id=sid):
                await self._load_session(sid)
            case ListSessions():
                await self.emit(ev("sessions", items=self.store.list_sessions()))
            case SetConfig(values=values):
                await self._set_config(values)
            case GetVoiceProfile():
                await self._emit_voice_profile()
            case UpdateCorrection(heard=h, meant=m, action=action):
                if action == "delete":
                    self.store.delete_correction(h, m)
                else:
                    self.store.set_correction_disabled(h, m, action == "disable")
                self.profile.reload()
                await self._emit_voice_profile()
            case DeleteVocab(term=term):
                self.store.delete_vocab(term)
                await self._emit_voice_profile()
            case OpenInEditor(editor=editor, path=path, line=line):
                try:
                    open_in_editor(editor, self.config.workspace, path, line)
                except EditorError as exc:
                    await self.emit(ev_error("editor", str(exc)))
            case ListDevices():
                devices = await asyncio.get_running_loop().run_in_executor(None, list_input_devices)
                await self.emit(ev("devices", items=devices))
            case Ping():
                await self.emit(ev("pong", t=time.time()))

    # ------------------------------------------------------------ voice input
    async def _ptt_start(self) -> None:
        if self._recording:
            return
        if self._transcribing:
            await self.emit(ev("notice", message="Still working out your last recording."))
            return
        self._stop_speaking()  # barge-in
        loop = asyncio.get_running_loop()

        def on_level(rms: float) -> None:  # PortAudio thread
            loop.call_soon_threadsafe(self._on_level, rms)

        try:
            self.recorder.start(on_level)
        except Exception as exc:  # noqa: BLE001
            await self.emit(ev_error("mic", str(exc)))
            return
        self._recording = True
        await self._set_state(State.LISTENING)

    def _on_level(self, rms: float) -> None:
        now = time.monotonic()
        if now - self._last_level >= LEVEL_INTERVAL_S:
            self._last_level = now
            asyncio.ensure_future(self.emit(ev("level", rms=round(min(rms * 8, 1.0), 3))))

    async def _ptt_stop(self) -> None:
        if not self._recording:
            return
        self._recording = False
        audio = self.recorder.stop()
        busy = self._turn_task is not None and not self._turn_task.done()
        resting = State.THINKING if busy else (State.AWAITING_APPROVAL if self._approvals else State.IDLE)
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        if audio.size < MIN_AUDIO_S * STT_RATE or rms < MIN_RMS:
            await self.emit(ev("notice", message="I didn't hear anything."))
            await self._set_state(resting)
            return

        await self._set_state(State.TRANSCRIBING)
        prompt = lexicon.build_prompt(self._project_terms + self.profile.vocabulary())
        self._transcribing = True
        try:
            raw = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, self.transcriber.transcribe, audio, prompt),
                timeout=STT_TIMEOUT_S)
        except TimeoutError:  # asyncio.TimeoutError is an alias of this on 3.11+
            log.error("transcription exceeded %.0fs", STT_TIMEOUT_S)
            await self.emit(ev_error("stt", "Speech recognition stopped responding. Restart Barnaby; if it "
                                            "keeps happening, pick a smaller recognition model in Settings."))
            await self._set_state(resting)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("transcription failed")
            await self.emit(ev_error("stt", _component_hint("stt", exc)))
            await self._set_state(resting)
            return
        finally:
            self._transcribing = False
        shown = self.profile.apply(lexicon.normalize(raw))
        if not shown:
            await self.emit(ev("notice", message="I didn't catch that."))
            await self._set_state(resting)
            return

        # Spoken approval while approvals are pending.
        if self._approvals and not self._approvals.done():
            decision = classify_spoken_decision(shown)
            if decision is not None:
                await self.emit(ev("transcript", draft_id=None, heard=raw, text=shown, review=False))
                await self._voice_decision(decision)
                return

        draft = Draft(id=uuid.uuid4().hex[:12], shown_text=shown, audio=audio)
        self._drafts = {draft.id: draft}  # only the latest draft is live
        review = self.config.review_transcripts
        await self.emit(ev("transcript", draft_id=draft.id, heard=raw, text=shown, review=review))
        if review:
            await self._set_state(resting if busy else State.IDLE)
        else:
            await self._submit(shown, draft.id)

    # ------------------------------------------------------------ turns
    async def _submit(self, text: str, draft_id: str | None) -> None:
        text = text.strip()
        if not text:
            return
        routed, rest = route_engine(text)
        if routed and routed in self.agents:
            if routed != self.config.engine:
                if not await self._switch_engine(routed):
                    return
            if not rest:
                self._drafts.pop(draft_id, None) if draft_id else None
                await self._set_state(State.IDLE)
                return
            text = rest
        draft = self._drafts.pop(draft_id, None) if draft_id else None
        if draft is not None:
            promoted = self.profile.learn(draft.shown_text, text)
            for heard, meant in promoted:
                await self.emit(ev("learned", heard=heard, meant=meant))
            if self.config.save_voice_samples:
                self._save_sample(draft, text)
        if self._approvals and not self._approvals.done():
            # A new instruction supersedes pending approvals: treat them as denied.
            self._approvals = None
            await self.emit(ev("approvals_cleared"))
        interrupting = await self._cancel_turn()
        prompt = f"(The user interrupted your previous step.) {text}" if interrupting else text
        await self._start_turn(prompt, display_text=text, grant=None)

    async def _start_turn(self, prompt: str, *, display_text: str, grant: Grant | None,
                          system_note: bool = False) -> None:
        if not self.config.workspace:
            await self.emit(ev_error("workspace", "Pick a project folder in Settings first."))
            return
        engine = self.config.engine if self.config.engine in self.agents else "claude"
        reason = self._engine_available(engine)
        if reason:
            await self.emit(ev_error("engine", reason))
            await self._set_state(State.IDLE)
            return
        if self.session_id is not None and self.session_engine not in (None, engine):
            # Sessions are engine-bound: a Claude session can't be resumed by Cursor.
            self.session_id = self.claude_session_id = None
            await self.emit(ev("session_loaded", session=None))
        if self.session_id is None:
            self.session_id = self.store.create_session(self.config.workspace, engine)
            self.store.set_title_if_default(self.session_id, display_text)
            await self.emit(ev("sessions", items=self.store.list_sessions()))
        self.session_engine = engine
        turn_id = uuid.uuid4().hex[:12]
        await self.emit(ev("turn_start", turn_id=turn_id, text=display_text,
                           session_id=self.session_id, system=system_note, engine=engine))
        self._turn_task = asyncio.create_task(self._run_turn(turn_id, prompt, display_text, grant))

    async def _run_turn(self, turn_id: str, prompt: str, display_text: str, grant: Grant | None) -> None:
        await self._set_state(State.THINKING)
        self._cancel_requested = False
        composer = SpeechComposer(self.config.max_spoken_chars)
        tools: list[dict[str, Any]] = []
        streamed: list[str] = []
        result: TurnResult | None = None
        status = "ok"
        gen = self._speech_gen
        try:
            # aclosing(): on CancelledError the generator's finally (which kills the
            # claude process tree) must run now, not whenever the GC gets to it.
            agent = self._running_agent = self.agent
            async with contextlib.aclosing(agent.run(
                    prompt, workspace=self.config.workspace,
                    session_id=self.claude_session_id, grant=grant)) as stream:
                async for event in stream:
                    match event:
                        case SessionStarted(session_id=csid):
                            self.claude_session_id = csid
                            if self.session_id:
                                self.store.set_claude_session(self.session_id, csid)
                        case TextDelta(text=delta):
                            streamed.append(delta)
                            await self.emit(ev("agent_text", turn_id=turn_id, delta=delta))
                            self._enqueue_speech(composer.feed(delta), gen)
                        case ToolStart(tool_id=tid, name=name, input=tin):
                            summary, rel = tool_summary(name, tin, self.config.workspace)
                            tools.append({"id": tid, "name": name, "summary": summary, "path": rel, "ok": None})
                            await self.emit(ev("tool_start", turn_id=turn_id, tool_id=tid, name=name,
                                               summary=summary, path=rel))
                        case ToolEnd(tool_id=tid, ok=ok, preview=preview):
                            for t in tools:
                                if t["id"] == tid:
                                    t["ok"] = ok
                            await self.emit(ev("tool_end", turn_id=turn_id, tool_id=tid, ok=ok, preview=preview))
                        case TurnResult() as res:
                            result = res
            if self._cancel_requested:
                raise asyncio.CancelledError  # agent stopped cleanly because we asked it to
            self._enqueue_speech(composer.flush(), gen)
        except asyncio.CancelledError:
            status = "cancelled"
            await self.emit(ev("turn_end", turn_id=turn_id, status=status))
            self._persist(display_text, "".join(streamed), tools, status, None)
            if self._cancel_requested and asyncio.current_task() and not asyncio.current_task().cancelling():
                return
            raise
        except AgentError as exc:
            status = "error"
            await self.emit(ev_error("agent", str(exc)))
        except Exception as exc:  # noqa: BLE001 - never let a turn kill the engine
            log.exception("turn failed")
            status = "error"
            await self.emit(ev_error("internal", f"Unexpected error: {exc}"))

        text = result.text if result else "".join(streamed)
        if result and result.is_error:
            status = "error"
        needs_approval = bool(result and result.denials)
        if needs_approval:
            status = "needs_approval"
        await self.emit(ev("turn_end", turn_id=turn_id, status=status, text=text,
                           cost_usd=result.cost_usd if result else None,
                           duration_ms=result.duration_ms if result else None))
        self._persist(display_text, text, tools, status, result.cost_usd if result else None)

        if needs_approval and result:
            await self._open_approvals(turn_id, result)
        else:
            await self._set_state(State.IDLE)

    def _persist(self, user_text: str, assistant_text: str, tools: list[dict[str, Any]],
                 status: str, cost: float | None) -> None:
        if self.session_id:
            self.store.add_turn(self.session_id, user_text=user_text, assistant_text=assistant_text,
                                tools=tools, status=status, cost_usd=cost)
            asyncio.ensure_future(self.emit(ev("sessions", items=self.store.list_sessions())))

    async def _cancel_turn(self) -> bool:
        task = self._turn_task
        if not task or task.done():
            return False
        self._cancel_requested = True
        await (self._running_agent or self.agent).cancel()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._turn_task = None
        return True

    async def _cancel_all(self) -> None:
        if self._recording:
            self._recording = False
            self.recorder.abort()
        self._stop_speaking()
        await self._cancel_turn()
        if self._approvals:
            self._approvals = None
            await self.emit(ev("approvals_cleared"))
        await self._set_state(State.IDLE)

    # ------------------------------------------------------------ approvals
    async def _open_approvals(self, turn_id: str, result: TurnResult) -> None:
        group = ApprovalGroup()
        for denial in result.denials:
            a = assess(denial.tool_name, denial.tool_input)
            grant = grant_for(denial.tool_name, denial.tool_input)
            if grant is not None and self.session_engine == "cursor" and not cursor_grantable(
                    denial.tool_name, denial.tool_input):
                grant = None  # can't be expressed as one exact Cursor rule
            rid = uuid.uuid4().hex[:12]
            group.items[rid] = PendingApproval(rid, denial.tool_name, denial.tool_input, a.summary,
                                               a.risk, a.voice_approvable, grant)
            await self.emit(ev("approval_required", turn_id=turn_id, request_id=rid,
                               tool_name=denial.tool_name, summary=a.summary, risk=a.risk.value,
                               voice_approvable=a.voice_approvable and grant is not None,
                               grantable=grant is not None,
                               detail=_approval_detail(denial.tool_name, denial.tool_input)))
        self._approvals = group
        await self._set_state(State.AWAITING_APPROVAL)
        n = len(group.items)
        self._enqueue_speech([f"I need your OK for {n} action{'s' if n > 1 else ''}. It's on screen."],
                             self._speech_gen)

    async def _voice_decision(self, approve: bool) -> None:
        group = self._approvals
        if not group:
            return
        for p in group.items.values():
            if p.decision is None:
                if approve and not (p.voice_approvable and p.grant):
                    continue  # critical items need a deliberate click
                await self._decide(p.request_id, approve)
        if group is self._approvals and not group.done():
            self._enqueue_speech(["Some items need you to confirm on screen."], self._speech_gen)
            await self._set_state(State.AWAITING_APPROVAL)

    async def _decide(self, request_id: str, approve: bool) -> None:
        group = self._approvals
        if not group or request_id not in group.items:
            return
        item = group.items[request_id]
        if approve and item.grant is None:
            await self.emit(ev_error("approval", "That action can't be approved narrowly; ask me to do it a different way."))
            approve = False
        item.decision = approve
        await self.emit(ev("approval_decided", request_id=request_id, approved=approve))
        if not group.done():
            return
        self._approvals = None
        approved = [p for p in group.items.values() if p.decision]
        denied = [p for p in group.items.values() if not p.decision]
        grant = Grant(
            allow=tuple(r for p in approved if p.grant for r in p.grant.allow),
            add_dirs=tuple(d for p in approved if p.grant for d in p.grant.add_dirs),
        ) if approved else None
        parts = []
        if approved:
            parts.append("The user approved: " + "; ".join(p.summary for p in approved)
                         + ". Perform exactly those actions now and continue the task.")
        if denied:
            parts.append("The user declined: " + "; ".join(p.summary for p in denied)
                         + ". Do not attempt those; briefly say what you'll do instead.")
        await self._start_turn(" ".join(parts), display_text="Approval decision", grant=grant, system_note=True)

    # ------------------------------------------------------------ speech output
    def _enqueue_speech(self, sentences: list[str], gen: int) -> None:
        if not self.config.tts_enabled:
            return
        for s in sentences:
            self._speech_q.put_nowait((gen, s))

    def _stop_speaking(self) -> None:
        self._speech_gen += 1
        while not self._speech_q.empty():
            self._speech_q.get_nowait()
        self.speaker.stop()

    async def _speech_worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            gen, sentence = await self._speech_q.get()
            if gen != self._speech_gen:
                continue
            if not self._speaking:
                self._speaking = True
                await self.emit(ev("speaking", active=True))
            try:
                await loop.run_in_executor(None, self.speaker.say, sentence)
            except Exception as exc:  # noqa: BLE001
                await self.emit(ev_error("tts", _component_hint("tts", exc)))
            if self._speech_q.empty() and self._speaking:
                self._speaking = False
                await self.emit(ev("speaking", active=False))

    # ------------------------------------------------------------ misc
    async def _set_state(self, state: State, detail: str | None = None) -> None:
        self.state = state
        await self.emit(ev_state(state, detail))

    async def _load_session(self, sid: str) -> None:
        session = self.store.get_session(sid)
        if not session:
            await self.emit(ev_error("session", "Session not found."))
            return
        await self._cancel_all()
        self.session_id = session["id"]
        self.claude_session_id = session.get("claude_session_id")
        self.session_engine = session.get("engine") or "claude"
        if self.session_engine in self.agents and self.session_engine != self.config.engine:
            self.config.engine = self.session_engine
            self.config.save()
            await self.emit(ev("config", config=self.config.public()))
        if session["workspace"] and Path(session["workspace"]).is_dir() and session["workspace"] != self.config.workspace:
            self.config.workspace = session["workspace"]
            self.config.save()
            self._refresh_project_terms()
            await self.emit(ev("config", config=self.config.public()))
        await self.emit(ev("session_loaded", session=session))

    async def _set_config(self, values: dict[str, Any]) -> None:
        before = self.config.public()
        rejected = self.config.apply_ui_update(values)
        self.config.save()
        after = self.config.public()
        self._apply_models()
        if before["engine"] != after["engine"] and before["workspace"] == after["workspace"]:
            # Pending approvals and the running turn belong to the old engine.
            await self._cancel_all()
            await self.emit(ev("notice", message=f"Switched to {ENGINE_LABELS.get(after['engine'], after['engine'])}."))
        if before["workspace"] != after["workspace"]:
            await self._cancel_all()
            self.session_id = self.claude_session_id = self.session_engine = None
            self._refresh_project_terms()
            await self.emit(ev("session_loaded", session=None))
        if self._transcriber_factory and (before["whisper_model"], before["whisper_device"]) != (after["whisper_model"], after["whisper_device"]):
            self.transcriber = self._transcriber_factory(self.config)
            asyncio.create_task(self._preload_one("stt", self.transcriber))
        if self._speaker_factory and (before["tts_voice"], before["tts_speed"]) != (after["tts_voice"], after["tts_speed"]):
            self._stop_speaking()
            self.speaker = self._speaker_factory(self.config)
        if hasattr(self.recorder, "device"):
            self.recorder.device = self.config.input_device  # type: ignore[attr-defined]
        await self.emit(ev("config", config=after, rejected=rejected))

    def _apply_models(self) -> None:
        # Settings changes must reach the live agents (previously the model was
        # captured once at startup and changing it did nothing until restart).
        for name, model in (("claude", self.config.claude_model), ("cursor", self.config.cursor_model)):
            agent = self.agents.get(name)
            if agent is not None and hasattr(agent, "model"):
                agent.model = model  # type: ignore[attr-defined]

    async def _switch_engine(self, engine: str) -> bool:
        reason = self._engine_available(engine)
        if reason:
            await self.emit(ev_error("engine", reason))
            await self._set_state(State.IDLE)
            return False
        await self._set_config({"engine": engine})
        return True

    async def _preload_one(self, name: str, component: Any) -> None:
        await self._component(name, "loading")
        try:
            await asyncio.get_running_loop().run_in_executor(None, component.load)
            await self._component(name, "ready", getattr(component, "status_detail", None))
        except Exception as exc:  # noqa: BLE001
            await self._component(name, "error", _component_hint(name, exc))

    async def _emit_voice_profile(self) -> None:
        await self.emit(ev("voice_profile",
                           corrections=self.store.corrections(),
                           vocabulary=self.profile.vocabulary(),
                           project_terms=self._project_terms,
                           samples=self.store.voice_sample_count()))

    def _refresh_project_terms(self) -> None:
        self._project_terms = lexicon.project_terms(self.config.workspace) if self.config.workspace else []

    def _save_sample(self, draft: Draft, final_text: str) -> None:
        folder = data_dir() / "voice_samples"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{draft.id}.wav"
        pcm = (np.clip(draft.audio, -1, 1) * 32767).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(STT_RATE)
            w.writeframes(pcm)
        path.write_bytes(buf.getvalue())
        self.store.add_voice_sample(str(path), draft.shown_text, final_text)


def _approval_detail(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))
    if tool_name in ("Edit", "Write", "MultiEdit"):
        return str(tool_input.get("file_path", ""))
    return ", ".join(f"{k}={str(v)[:120]}" for k, v in list(tool_input.items())[:4])


def _component_hint(name: str, exc: BaseException) -> str:
    if isinstance(exc, ImportError):
        pkg = "faster-whisper" if name == "stt" else "kokoro soundfile"
        return f"Missing package ({exc.name or exc}). Run: pip install {pkg}"
    return f"{type(exc).__name__}: {exc}"
