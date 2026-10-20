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
import json
import logging
import re
import sqlite3
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
                    ToolStart, TurnResult, needs_sign_in)
from .claude_auth import ClaudeAuth
from .config import Config, data_dir
from .editors import EditorError, open_in_editor
from .protocol import (Approve, Cancel, Command, DeleteVocab, Deny, DiscardDraft,
                       GetVoiceProfile, ListDevices, ListSessions, LoadSession,
                       ListTemplates, NewSession, OpenInEditor, Ping, PttStart, PttStop, Risk, RunTemplate,
                       AudioClip, DeleteSecret, SetConfig, SetSecret, State, StopSpeaking, TabletPairStart,
                       TabletRevoke, TaskCancel, TaskClear, TaskCreate, TaskFollowUp, TaskList,
                       TextSubmit, UpdateCorrection,
                       ClaudeAuthCheck, ClaudeLogin, ClaudeLoginCancel,
                       ev, ev_error, ev_state,
                       ev_continuous_mode_changed,
                       ev_command_chain_start, ev_command_chain_end, ev_command_chain_step,
                       ev_ide_connected, ev_ide_context_update,
                       ev_workflow_started, ev_workflow_step_complete,
                       ev_workflow_error)
from .command_chain import split_chain
from .continuous import VoiceSegmenter
from .cursor_agent import cursor_grantable
from .remote import TabletBlocked
from .safety import Grant, Scope, assess, grant_for, lane_of, redact_for_speech
from .tasks import LaneError, TaskManager, parse_task_command, resolve_lane_words
from .speech import SpeechComposer
from .store import Store
from .templates import TemplateManager, match_template, template_steps
from .vault import MemoryCredentials, Vault, VaultError
from .voice import STT_RATE, Recorder, Speaker, Transcriber, list_input_devices, resample

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]

MIN_AUDIO_S = 0.35
MIN_RMS = 0.004
STT_TIMEOUT_S = 60.0  # generous for CPU + long clips; a stall past this is a fault, not work
LEVEL_INTERVAL_S = 0.04
_YES_CORE = frozenset({"yes", "yeah", "yep", "approve", "approved", "confirm", "confirmed", "proceed", "ahead"})
_NO_CORE = frozenset({"no", "nope", "deny", "denied", "don't", "dont", "reject", "cancel", "stop"})
_FILLER = frozenset({"go", "do", "it", "please", "ok", "okay", "that", "sure", "thanks", "and", "just"})


ENGINE_LABELS = {"claude": "Claude", "cursor": "Cursor", "devin": "Devin"}
_ENGINE_WORDS = {"claude": "claude", "clod": "claude", "cloud": "claude", "cursor": "cursor", "curser": "cursor",
                 "devin": "devin", "devon": "devin", "deven": "devin"}
_ROUTE_RE = re.compile(
    r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:(?:have|ask|use|let|get|tell)\s+)?"
    r"(claude|clod|cloud|cursor|curser|devin|devon|deven)\b(?:\s*[,:.\-]\s*|\s+(?:to\s+)?|\s*$)(.*)$",
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


_SEND = frozenset({"send", "send it", "send that", "submit", "submit it", "go", "go ahead", "do it"})
_DISCARD = frozenset({"scratch that", "discard", "discard it", "discard that", "cancel that", "never mind",
                      "nevermind", "delete that", "forget it", "forget that"})


def classify_draft_command(text: str) -> str | None:
    """Hands-free control of a pending review draft: "send it" / "scratch that".
    Only the whole short phrase counts, so "send the report to finance" is an
    ordinary instruction."""
    words = re.sub(r"[^a-z' ]+", " ", text.lower()).split()
    words = [w for w in words if w not in ("please", "ok", "okay", "barnaby", "now", "yes")]
    phrase = " ".join(words)
    if phrase in _SEND:
        return "send"
    if phrase in _DISCARD:
        return "discard"
    return None


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
class ChainRun:
    """A multi-step request in flight: steps run as sequential turns in one session."""
    id: str
    steps: list[str]
    name: str | None = None      # template/workflow name, if it came from one
    context: str | None = None   # "for the cart total"
    index: int = 0               # step currently running (0-based)
    declined: bool = False       # user declined an approval during the current step
    interrupted: bool = False    # the first step interrupted a running turn


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
    if name == "Devin":
        return f"Devin session {tool_input.get('url') or tool_input.get('session_id', '')}".strip()[:300], None
    return name, rel


class Orchestrator:
    def __init__(self, *, config: Config, store: Store, profile: VoiceProfile,
                 recorder: Recorder, transcriber: Transcriber, speaker: Speaker,
                 agent: Agent | None = None, emit: Emit,
                 agents: dict[str, Agent] | None = None,
                 engine_available: Callable[[str], str | None] | None = None,
                 templates: TemplateManager | None = None,
                 workflows: Any = None,
                 transcriber_factory: Callable[[Config], Transcriber] | None = None,
                 speaker_factory: Callable[[Config], Speaker] | None = None,
                 vault: Vault | None = None,
                 emit_desktop: Emit | None = None,
                 tablet_factory: Callable[[int, Callable[[], None]], Any] | None = None,
                 task_agent_factory: Callable[[str, str], Agent] | None = None,
                 task_forget: Callable[[str], None] | None = None,
                 claude_auth: ClaudeAuth | None = None) -> None:
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
        self._templates = templates or TemplateManager()
        self._workflows = workflows  # WorkflowEngine: user-saved workflows run like templates
        self._chain: ChainRun | None = None
        self.emit = emit
        self._transcriber_factory = transcriber_factory
        self._speaker_factory = speaker_factory
        # Cloud agents' API keys. Tests get an in-memory vault, never the real Windows store.
        self.vault = vault or Vault(MemoryCredentials())
        # Tablet access: events only the PC window may see (pairing links), and the
        # LAN server, created on demand from settings.
        self._emit_desktop = emit_desktop or emit
        self._tablet_factory = tablet_factory
        self._tablet: Any = None
        self._tablet_error: str | None = None
        self._remote_listeners = 0
        # Background tasks (task board): each gets its own agent from task_agent_factory.
        self.tasks = TaskManager(store=store, config=config, agent_factory=task_agent_factory,
                                 engine_available=self._engine_available, emit=emit,
                                 speak=lambda text: self._enqueue_speech([text], self._speech_gen),
                                 on_forget=task_forget)

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
        # Hands-free (continuous dictation): an always-open mic stream cut into utterances.
        self._hf: VoiceSegmenter | None = None
        self._hf_rate = STT_RATE
        self._hf_resume = False          # push-to-talk suspended the stream; restart after
        self._speech_ended_at = 0.0      # echo guard: don't listen right after Barnaby speaks
        self._cancel_requested = False
        self._notices: list[str] = []
        # Claude sign-in (None in tests that don't exercise it: nothing then touches the real CLI).
        self._claude_auth = claude_auth
        self._claude_state: dict[str, Any] = {"state": "unknown"}
        self._claude_prev: dict[str, Any] = self._claude_state
        self._claude_login_task: asyncio.Task[None] | None = None
        # Last-known component status, replayed in `hello`: load events fire at
        # startup, usually before any UI has connected to receive them.
        self._components: dict[str, dict[str, Any]] = {
            "stt": {"status": "loading"}, "tts": {"status": "loading"}}

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        damaged = getattr(self.store, "recovered_to", None)
        if damaged:
            self._notices.append(f"Your history database was damaged, so Barnaby started a fresh one. "
                                 f"The old file is kept at {damaged}.")
        self._speech_task = asyncio.create_task(self._speech_worker())
        self.tasks.load()
        self._refresh_project_terms()
        asyncio.create_task(self._preload())
        asyncio.create_task(self._claude_check())
        await self._apply_handsfree()
        await self._apply_tablet(announce=False)

    async def shutdown(self) -> None:
        if self._claude_login_task and not self._claude_login_task.done():
            await self._claude_login_cancel()
        self._stop_handsfree()
        await self._stop_tablet()
        await self.tasks.shutdown()
        await self._cancel_turn()
        self.speaker.stop()
        if self._speech_task:
            self._speech_task.cancel()

    async def hello(self) -> dict[str, Any]:
        return ev("hello", state=self.state.value, config=self.config.public(), engines=self.engines(),
                  templates=self._template_summaries(), handsfree=self._hf is not None, secrets=self.vault.status(), tablet=self._tablet_status(),
                  session_id=self.session_id, speaking=self._speaking,
                  sessions=self.store.list_sessions(), components=self._components,
                  tasks=self.tasks.snapshot(), claude_auth=self._claude_state)

    def take_notices(self) -> list[str]:
        """Startup notices (e.g. a repaired database) to show once, to the first window that connects."""
        notices, self._notices = self._notices, []
        return notices

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
                if not await self.tasks.decide(rid, True):
                    await self._decide(rid, True)
            case Deny(request_id=rid):
                if not await self.tasks.decide(rid, False):
                    await self._decide(rid, False)
            case TaskCreate(engine=engine, instruction=instruction, lanes=lanes, title=title):
                await self.tasks.create(engine, instruction, list(lanes), title)
            case TaskCancel(task_id=tid):
                await self.tasks.cancel(tid)
            case TaskFollowUp(task_id=tid, text=text):
                await self.tasks.follow_up(tid, text)
            case TaskClear():
                await self.tasks.clear_finished()
            case TaskList():
                await self.emit(self.tasks.snapshot())
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
            case ListTemplates():
                await self.emit(ev("templates", items=self._template_summaries()))
            case RunTemplate(template_id=tid, context=ctx):
                await self._run_template(tid, ctx)
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
            case AudioClip(pcm_b64=pcm):
                await self._audio_clip(pcm)
            case TabletPairStart():
                await self._tablet_pair_start()
            case TabletRevoke(device_id=device_id):
                if self._tablet is not None:
                    await self._tablet.revoke(device_id)
                await self._emit_tablet_status()
            case SetSecret(name=name, value=value):
                await self._set_secret(name, value.get_secret_value())
            case DeleteSecret(name=name):
                await self._set_secret(name, None)
            case ClaudeLogin():
                await self._claude_login()
            case ClaudeLoginCancel():
                await self._claude_login_cancel()
            case ClaudeAuthCheck():
                await self._claude_check()
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
        if self._hf is not None:
            # Push-to-talk takes over the mic; hands-free resumes after this recording.
            self._stop_handsfree()
            self._hf_resume = True
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
        try:
            await self._process_audio(audio)
        finally:
            if self._hf_resume:
                self._hf_resume = False
                await self._apply_handsfree(announce=False)

    def _resting_state(self) -> State:
        busy = self._turn_task is not None and not self._turn_task.done()
        return State.THINKING if busy else (State.AWAITING_APPROVAL if self._approvals else State.IDLE)

    async def _process_audio(self, audio: np.ndarray, *, handsfree: bool = False) -> None:
        """Shared by push-to-talk and hands-free: transcribe, correct, then approve /
        draft / submit exactly the same way."""
        busy = self._turn_task is not None and not self._turn_task.done()
        resting = self._resting_state()
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        if audio.size < MIN_AUDIO_S * STT_RATE or rms < MIN_RMS:
            if not handsfree:  # hands-free drops quiet blips silently
                await self.emit(ev("notice", message="I didn't hear anything."))
            await self._set_state(resting)
            return

        # Claim the transcriber before the first await: callers check _transcribing and
        # then call here with no await in between, so two clips can't both get through.
        self._transcribing = True
        try:
            await self._set_state(State.TRANSCRIBING)
            prompt = lexicon.build_prompt(self._project_terms + self.profile.vocabulary())
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
            if not handsfree:
                await self.emit(ev("notice", message="I didn't catch that."))
            await self._set_state(resting)
            return

        # Hands-free: "send it" / "scratch that" act on a pending review draft. The UI
        # submits it, so edits typed into the draft box are kept.
        if handsfree and self._drafts and self.config.review_transcripts:
            action = classify_draft_command(shown)
            if action:
                if action == "discard":
                    self._drafts.clear()
                await self.emit(ev("draft_command", action=action))
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
        task = parse_task_command(text)
        if task is not None:
            self._drafts.pop(draft_id, None) if draft_id else None
            await self._voice_task(*task)
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
        interrupting = await self._supersede()
        tmatch = match_template(text, self._template_list())
        if tmatch:
            tpl, ctx = tmatch
            await self._start_chain(template_steps(tpl), name=str(tpl.get("name") or tpl.get("id")),
                                    context=ctx, interrupted=interrupting)
            return
        if self.config.command_chain_enabled:
            steps = split_chain(text)
            if len(steps) > 1:
                limit = self.config.command_chain_max_depth
                if len(steps) > limit:
                    await self.emit(ev("notice", message=f"That's {len(steps)} steps; the limit is {limit}. "
                                                         "Give me fewer steps at a time."))
                    await self._set_state(State.IDLE)
                    return
                await self._start_chain(steps, interrupted=interrupting)
                return
        prompt = f"(The user interrupted your previous step.) {text}" if interrupting else text
        await self._start_turn(prompt, display_text=text, grant=None)

    async def _voice_task(self, engine: str, words: list[str], instruction: str) -> None:
        """"task for Devin in backend: ..." -> a background task; the conversation carries on."""
        saved = self.config.task_lanes.get(self.config.workspace, {})
        try:
            lanes = resolve_lane_words(self.config.workspace, words, saved) if words else []
        except LaneError as exc:
            await self.emit(ev_error("tasks", str(exc)))
            self._enqueue_speech([str(exc)], self._speech_gen)
            await self._set_state(self._resting_state())
            return
        t = await self.tasks.create(engine, instruction, lanes, None)
        if t is not None:
            who = ENGINE_LABELS.get(engine, engine)
            verb = "Started" if t.status == "running" else "Queued"
            self._enqueue_speech([redact_for_speech(f"{verb} {who} on {t.title}.")], self._speech_gen)
        await self._set_state(self._resting_state())

    async def _supersede(self) -> bool:
        """A new instruction replaces whatever was in flight. Returns True if a turn was interrupted."""
        await self._end_chain("interrupted")
        if self._approvals and not self._approvals.done():
            # A new instruction supersedes pending approvals: treat them as denied.
            self._approvals = None
            await self.emit(ev("approvals_cleared"))
        return await self._cancel_turn()

    # ------------------------------------------------------------ chains & templates
    def _template_list(self) -> list[dict[str, Any]]:
        items = [dict(t) for t in self._templates.list_templates()]
        if self._workflows is not None:
            try:
                for wf in self._workflows.list_workflows():
                    items.append({"id": wf.id, "name": wf.name, "description": wf.description,
                                  "steps": wf.steps, "custom": True})
            except Exception:  # noqa: BLE001 - one bad file must not hide the built-ins
                log.exception("could not load saved workflows")
        return items

    def _template_summaries(self) -> list[dict[str, Any]]:
        return [{"id": t.get("id"), "name": t.get("name"), "description": t.get("description", ""),
                 "step_count": len(template_steps(t)), "custom": bool(t.get("custom"))}
                for t in self._template_list() if template_steps(t)]

    async def _run_template(self, template_id: str, context: str | None) -> None:
        tpl = next((t for t in self._template_list() if t.get("id") == template_id), None)
        steps = template_steps(tpl) if tpl else []
        if not steps:
            await self.emit(ev_error("template", "That template wasn't found or has no steps."))
            return
        interrupting = await self._supersede()
        await self._start_chain(steps, name=str(tpl.get("name") or template_id),
                                context=(context or "").strip() or None, interrupted=interrupting)

    async def _start_chain(self, steps: list[str], *, name: str | None = None, context: str | None = None,
                           interrupted: bool = False) -> None:
        chain = ChainRun(id=uuid.uuid4().hex[:12], steps=list(steps), name=name, context=context,
                         interrupted=interrupted)
        self._chain = chain
        await self.emit(ev_command_chain_start(chain.id, chain.steps, name))
        await self._run_chain_step()

    async def _run_chain_step(self) -> None:
        chain = self._chain
        if chain is None:
            return
        i, n = chain.index, len(chain.steps)
        step = chain.steps[i]
        label = f"the \"{chain.name}\" template" if chain.name else "a multi-step request"
        applied = f", applied to: {chain.context}" if chain.context else ""
        prefix = f"(Step {i + 1} of {n} of {label}{applied}. Do only this step now; the next step follows.)"
        if i + 1 == n:
            prefix = f"(Final step {i + 1} of {n} of {label}{applied}.)"
        if i == 0 and chain.interrupted:
            prefix = "(The user interrupted your previous step.) " + prefix
        chain.declined = False
        await self.emit(ev_command_chain_step(chain.id, i, n, step))
        if not await self._start_turn(f"{prefix} {step}", display_text=step, grant=None):
            await self._end_chain("failed")

    async def _advance_chain(self, status: str) -> None:
        chain = self._chain
        if chain is None:
            return
        if status != "ok":
            await self._end_chain("failed")
        elif chain.declined:
            await self._end_chain("declined")
        elif chain.index + 1 >= len(chain.steps):
            await self._end_chain("completed")
        else:
            chain.index += 1
            await self._run_chain_step()

    async def _end_chain(self, reason: str) -> None:
        chain, self._chain = self._chain, None
        if chain is None:
            return
        n = len(chain.steps)
        done = n if reason == "completed" else chain.index
        await self.emit(ev_command_chain_end(chain.id, reason == "completed", reason, done, n))
        if reason == "completed":
            self._enqueue_speech([f"All {n} steps are done."], self._speech_gen)
        elif reason in ("failed", "declined"):
            why = "you declined an action" if reason == "declined" else "that step didn't finish"
            self._enqueue_speech([f"I stopped after step {chain.index + 1} of {n}, because {why}."],
                                 self._speech_gen)

    async def _start_turn(self, prompt: str, *, display_text: str, grant: Grant | None,
                          system_note: bool = False) -> bool:
        if not self.config.workspace:
            await self.emit(ev_error("workspace", "Pick a project folder in Settings first."))
            return False
        engine = self.config.engine if self.config.engine in self.agents else "claude"
        reason = self._engine_available(engine)
        if reason:
            await self.emit(ev_error("engine", reason))
            await self._set_state(State.IDLE)
            return False
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
        return True

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
            # Folders a background task is working in: this turn must ask before editing them.
            busy = self.tasks.busy_lanes(self.config.workspace)
            extra = {"scope": Scope(ask_lanes=busy)} if busy else {}
            async with contextlib.aclosing(agent.run(
                    prompt, workspace=self.config.workspace,
                    session_id=self.claude_session_id, grant=grant, **extra)) as stream:
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
        if self._claude_auth is not None and self.session_engine == "claude" and result:
            if result.is_error and needs_sign_in(text):
                await self._claude_set("expired", "Claude rejected your sign-in. Sign in again.")
            elif not result.is_error and self._claude_state["state"] in ("expired", "signed_out"):
                await self._claude_set("signed_in")

        if needs_approval and result:
            await self._open_approvals(turn_id, result)  # a chain stays paused until the decision
        else:
            await self._set_state(State.IDLE)
            await self._advance_chain(status)

    def _persist(self, user_text: str, assistant_text: str, tools: list[dict[str, Any]],
                 status: str, cost: float | None) -> None:
        if self.session_id:
            try:
                self.store.add_turn(self.session_id, user_text=user_text, assistant_text=assistant_text,
                                    tools=tools, status=status, cost_usd=cost)
                asyncio.ensure_future(self.emit(ev("sessions", items=self.store.list_sessions())))
            except sqlite3.Error:
                # History is secondary: a failed save must never leave the turn (and the UI) stuck.
                log.exception("couldn't save the turn to history")
                asyncio.ensure_future(self.emit(ev_error(
                    "history", "This conversation couldn't be saved to history. Restart Barnaby to repair it.")))

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
        await self._end_chain("cancelled")
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
        busy = self.tasks.busy_lanes(self.config.workspace)
        seen: set[str] = set()
        for denial in result.denials:
            # Agents retry a blocked command; one card per distinct action is enough, since
            # approving it grants exactly that action.
            key = f"{denial.tool_name}\0{json.dumps(denial.tool_input, sort_keys=True, default=str)}"
            if key in seen:
                continue
            seen.add(key)
            a = assess(denial.tool_name, denial.tool_input)
            summary = a.summary
            grant = grant_for(denial.tool_name, denial.tool_input)
            if grant is not None and self.session_engine == "cursor" and not cursor_grantable(
                    denial.tool_name, denial.tool_input):
                grant = None  # can't be expressed as one exact Cursor rule
            path = denial.tool_input.get("file_path") or denial.tool_input.get("notebook_path")
            lane = lane_of(self.config.workspace, path, busy) if busy and isinstance(path, str) and path else None
            if lane is not None:
                # The ask rule for this lane stops the edit; approving opens the lane for one turn.
                rel = Path(path).resolve().relative_to(Path(self.config.workspace).resolve()).as_posix()
                summary = f"{denial.tool_name} {rel} (a background task is working in {lane or 'this project'})"
                if grant is not None:
                    grant = Grant(allow=grant.allow, add_dirs=grant.add_dirs, open_lanes=(lane,))
            rid = uuid.uuid4().hex[:12]
            group.items[rid] = PendingApproval(rid, denial.tool_name, denial.tool_input, summary,
                                               a.risk, a.voice_approvable, grant)
            await self.emit(ev("approval_required", turn_id=turn_id, request_id=rid,
                               tool_name=denial.tool_name, summary=summary, risk=a.risk.value,
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
        if denied and self._chain is not None:
            self._chain.declined = True  # finish this step's reply, then stop the chain
        grant = Grant(
            allow=tuple(r for p in approved if p.grant for r in p.grant.allow),
            add_dirs=tuple(d for p in approved if p.grant for d in p.grant.add_dirs),
            open_lanes=tuple(x for p in approved if p.grant for x in p.grant.open_lanes),
        ) if approved else None
        parts = []
        if approved:
            parts.append("The user approved: " + "; ".join(p.summary for p in approved)
                         + ". Perform exactly those actions now and continue the task.")
        if denied:
            parts.append("The user declined: " + "; ".join(p.summary for p in denied)
                         + ". Do not attempt those; briefly say what you'll do instead.")
        await self._start_turn(" ".join(parts), display_text="Approval decision", grant=grant, system_note=True)

    # ------------------------------------------------------------ hands-free
    async def _apply_handsfree(self, announce: bool = True) -> None:
        """Start or stop the continuous mic stream to match config (idempotent)."""
        want = bool(self.config.continuous_mode_enabled) and hasattr(self.recorder, "start_stream") \
            and not self._recording
        if want and self._hf is None:
            loop = asyncio.get_running_loop()

            def on_block(block: np.ndarray) -> None:  # PortAudio thread
                loop.call_soon_threadsafe(self._feed_handsfree, block)

            try:
                self._hf_rate = self.recorder.start_stream(on_block)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                await self.emit(ev_error("mic", f"Hands-free couldn't open the microphone: {exc}"))
                await self.emit(ev_continuous_mode_changed(False))
                return
            self._hf = VoiceSegmenter(rate=self._hf_rate, silence_s=float(self.config.continuous_mode_timeout))
            if announce:
                await self.emit(ev_continuous_mode_changed(True))
        elif not want and self._hf is not None:
            self._stop_handsfree()
            if announce:
                await self.emit(ev_continuous_mode_changed(False))
        elif self._hf is not None:
            self._hf.silence_s = float(self.config.continuous_mode_timeout)

    def _stop_handsfree(self) -> None:
        if self._hf is None:
            return
        self._hf = None
        stop = getattr(self.recorder, "stop_stream", None)
        if stop:
            stop()

    def _feed_handsfree(self, block: np.ndarray) -> None:
        seg = self._hf
        if seg is None:
            return
        # Don't listen while Barnaby talks (it would transcribe itself through the
        # speakers), just after it stops (room echo), or while busy with a recording.
        if (self._speaking or self._transcribing or self._recording
                or time.monotonic() - self._speech_ended_at < 0.5):
            if not seg.paused:
                was_capturing = seg.capturing
                seg.pause()
                if was_capturing:
                    asyncio.ensure_future(self._set_state(self._resting_state()))
            return
        if seg.paused:
            seg.resume()
        was = seg.capturing
        utterances = seg.feed(block)
        if seg.capturing:
            self._on_level(float(np.sqrt(np.mean(block * block))) if block.size else 0.0)
            if not was:
                asyncio.ensure_future(self._set_state(State.LISTENING))
        for audio in utterances:
            asyncio.ensure_future(self._process_audio(resample(audio, self._hf_rate, STT_RATE), handsfree=True))
        if was and not seg.capturing and not utterances:
            asyncio.ensure_future(self._set_state(self._resting_state()))  # it was noise

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
        if self._remote_listeners and self._loop is not None:
            self._loop.create_task(self.emit(ev("say_stop")))

    async def _speech_worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            gen, sentence = await self._speech_q.get()
            if gen != self._speech_gen:
                continue
            if self._remote_listeners:
                # A tablet is connected: it speaks with its own voice. It gets the same
                # capped, secret-redacted sentences the PC would have spoken.
                await self.emit(ev("say", text=sentence))
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
                self._speech_ended_at = time.monotonic()
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
        await self._apply_handsfree()
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
        task_keys = ("workspace", "max_parallel_tasks", "task_lanes")
        if any(before.get(k) != after.get(k) for k in task_keys):
            await self.emit(self.tasks.snapshot())
            await self.tasks.pump()  # a higher limit may let queued tasks start
        if (before.get("tablet_access"), before.get("tablet_port")) != (after.get("tablet_access"), after.get("tablet_port")):
            await self._apply_tablet()

    def _apply_models(self) -> None:
        # Settings changes must reach the live agents (previously the model was
        # captured once at startup and changing it did nothing until restart).
        for name, model in (("claude", self.config.claude_model), ("cursor", self.config.cursor_model)):
            agent = self.agents.get(name)
            if agent is not None and hasattr(agent, "model"):
                agent.model = model  # type: ignore[attr-defined]

    # ------------------------------------------------------------ tablet access
    def remote_check(self, cmd: Command) -> str | None:
        """Extra checks for tablet commands that need engine state (the hub has already
        applied the role policy). Opening a session switches to its project folder,
        which only the PC may do, so a tablet can open sessions of the current project only."""
        # Tasks of another project: the tablet can see and cancel them, but only the PC
        # follows them up or approves their actions (same rule as opening its sessions).
        if isinstance(cmd, TaskFollowUp):
            t = self.tasks.tasks.get(cmd.task_id)
            if t is not None and t.workspace != self.config.workspace:
                return "That task belongs to another project. Follow it up on the PC."
        if isinstance(cmd, (Approve, Deny)):
            t = self.tasks.task_of_request(cmd.request_id)
            if t is not None and t.workspace != self.config.workspace:
                return "That task belongs to another project. Approve it on the PC."
        if isinstance(cmd, LoadSession):
            session = self.store.get_session(cmd.session_id)
            if session and session.get("workspace") and session["workspace"] != self.config.workspace:
                return "That session belongs to another project. Open it on the PC."
        return None

    def set_remote_listeners(self, count: int) -> None:
        """Called by the hub as tablets connect and leave."""
        self._remote_listeners = count

    def _tablet_status(self) -> dict[str, Any]:
        t = self._tablet
        running = bool(t is not None and t.running)
        status: dict[str, Any] = {
            "enabled": self.config.tablet_access, "running": running,
            "port": t.port if running else self.config.tablet_port,
            "urls": t.urls() if running else [],
            "devices": t.devices() if t is not None else [],
        }
        if running:
            try:
                status["ca_fingerprint"] = t.ca_fingerprint()
            except Exception:  # noqa: BLE001 - informational only
                pass
        if self._tablet_error:
            status["error"] = self._tablet_error
        return status

    async def _emit_tablet_status(self) -> None:
        await self.emit(ev("tablet_status", **self._tablet_status()))

    async def _stop_tablet(self) -> None:
        tablet, self._tablet = self._tablet, None
        if tablet is not None:
            await tablet.stop()

    async def _apply_tablet(self, announce: bool = True) -> None:
        """Start, stop or restart the LAN server to match settings."""
        want = self.config.tablet_access and self._tablet_factory is not None
        current = self._tablet
        if current is not None and (not want or current.port_requested != self.config.tablet_port):
            await self._stop_tablet()
        self._tablet_error = None
        if want and self._tablet is None:
            loop = asyncio.get_running_loop()

            def changed() -> None:
                loop.call_soon_threadsafe(lambda: loop.create_task(self._emit_tablet_status()))

            tablet = self._tablet_factory(self.config.tablet_port, changed)
            try:
                await tablet.start()
                self._tablet = tablet
            except TabletBlocked as exc:
                self._tablet_error = f"Tablet access couldn't start: {exc}"
                log.warning("tablet access is off: %s", exc)
            except OSError as exc:
                in_use = getattr(exc, "errno", None) in (48, 98, 10048) or "in use" in str(exc).lower()
                self._tablet_error = (f"Port {self.config.tablet_port} is already in use. Pick another port."
                                      if in_use else f"Tablet access couldn't start: {exc}")
                log.warning("tablet access failed to start: %s", exc)
            except ImportError:
                self._tablet_error = ("Tablet access needs the 'cryptography' package. In the engine folder run: "
                                      ".venv\\Scripts\\python -m pip install cryptography")
            except Exception as exc:  # noqa: BLE001 - e.g. certificate generation
                self._tablet_error = f"Tablet access couldn't start: {exc}"
                log.exception("tablet access failed to start")
        if announce or self._tablet_error:
            await self._emit_tablet_status()

    async def _tablet_pair_start(self) -> None:
        if self._tablet is None or not self._tablet.running:
            await self.emit(ev_error("tablet", "Turn on Tablet access in Settings first."))
            return
        url, expires = self._tablet.start_pairing()
        # The link carries a one-time code: it goes to the PC window only, never to tablets.
        await self._emit_desktop(ev("tablet_pairing", url=url, expires_at=expires))

    async def _audio_clip(self, pcm_b64: str) -> None:
        """A hold-to-talk recording from a paired tablet: 16 kHz mono int16 LE."""
        import base64
        import binascii
        try:
            raw = base64.b64decode(pcm_b64, validate=True)
        except (binascii.Error, ValueError):
            await self.emit(ev_error("audio", "The tablet sent audio I couldn't read."))
            return
        if len(raw) % 2:
            await self.emit(ev_error("audio", "The tablet sent audio I couldn't read."))
            return
        if self._recording or self._transcribing:
            await self.emit(ev("notice", message="Still working out your last recording."))
            return
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        await self._process_audio(audio)

    async def _claude_set(self, state: str, detail: str | None = None, method: str | None = None) -> None:
        self._claude_state = {"state": state, **({"detail": detail} if detail else {}),
                              **({"method": method} if method else {})}
        await self.emit(ev("claude_auth", **self._claude_state))

    async def _claude_check(self) -> None:
        """Read the CLI's sign-in status. Leaves an in-progress sign-in and a known-expired login alone."""
        if self._claude_auth is None or self._claude_state["state"] in ("signing_in", "expired"):
            return
        status = await self._claude_auth.status()
        if status.error:
            await self._claude_set("unavailable", status.error)
        elif status.signed_in:
            await self._claude_set("signed_in", method=status.method)
        else:
            await self._claude_set("signed_out")

    async def _claude_login(self) -> None:
        if self._claude_auth is None:
            await self.emit(ev_error("claude_auth", "Signing in from Barnaby isn't available here."))
            return
        if self._claude_login_task and not self._claude_login_task.done():
            return  # already waiting on the browser
        self._claude_prev = self._claude_state
        await self._claude_set("signing_in", "Finish signing in in your browser. A console window is open too, in case it asks you to paste a code.")
        self._claude_login_task = asyncio.create_task(self._claude_login_flow())

    async def _claude_login_flow(self) -> None:
        auth = self._claude_auth
        assert auth is not None
        try:
            finished = await auth.login()
        except asyncio.CancelledError:
            raise
        except AgentError as exc:
            await self._claude_set("failed", str(exc))
            return
        except OSError as exc:
            await self._claude_set("failed", f"Couldn't start Claude Code's sign-in: {exc}")
            return
        if not finished:
            await self._claude_set("failed", "The sign-in window closed before it finished. Try again.")
            return
        # The old token would outrank the login we just made, so retire it for this session first.
        dropped = auth.drop_stale_token()
        status = await auth.status()
        if not status.signed_in:
            await self._claude_set("failed", "Sign-in finished, but Claude Code still reports you're signed out.")
            return
        await self._claude_set("signed_in", method=status.method)
        await self.emit(ev("notice", message="Signed in to Claude." + (
            " The old CLAUDE_CODE_OAUTH_TOKEN is ignored from now on; remove it from Windows settings too." if dropped else "")))

    async def _claude_login_cancel(self) -> None:
        task = self._claude_login_task
        if self._claude_auth is None or task is None or task.done():
            return
        await self._claude_auth.cancel()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._claude_state = self._claude_prev
        await self.emit(ev("claude_auth", **self._claude_state))

    async def _set_secret(self, name: str, value: str | None) -> None:
        """Save or remove a key. Only the outcome is reported, never the value."""
        loop = asyncio.get_running_loop()
        try:
            if value is None:
                await loop.run_in_executor(None, self.vault.delete, name)
            else:
                await loop.run_in_executor(None, self.vault.set, name, value)
        except VaultError as exc:
            await self.emit(ev_error("secret", str(exc)))
            return
        label = ENGINE_LABELS.get(name, name)
        await self.emit(ev("notice", message=f"{label} key saved to Windows Credential Manager." if value is not None
                           else f"{label} key removed."))
        await self.emit(ev("secrets", items=self.vault.status()))
        await self.emit(ev("engines", items=self.engines()))
        if value is None and self.config.engine == name:
            await self._set_config({"engine": "claude"})

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
