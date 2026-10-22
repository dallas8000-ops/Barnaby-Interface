"""Wire protocol between the engine and the UI.

Engine -> UI: plain dicts produced by the ``ev_*`` helpers (always carry ``type``).
UI -> Engine: validated by the pydantic ``Command`` discriminated union, so a
malformed or hostile frame is rejected before it reaches the orchestrator.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

PROTOCOL_VERSION = 1


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    AWAITING_APPROVAL = "awaiting_approval"
    ERROR = "error"


class Risk(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------- commands
class _Cmd(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Auth(_Cmd):
    type: Literal["auth"]
    token: str = Field(max_length=256)


class PttStart(_Cmd):
    type: Literal["ptt_start"]


class PttStop(_Cmd):
    type: Literal["ptt_stop"]


class TextSubmit(_Cmd):
    type: Literal["text_submit"]
    text: str = Field(min_length=1, max_length=20_000)
    draft_id: str | None = Field(default=None, max_length=64)  # links a reviewed voice draft


class Cancel(_Cmd):
    type: Literal["cancel"]


class StopSpeaking(_Cmd):
    type: Literal["stop_speaking"]


class Approve(_Cmd):
    type: Literal["approve"]
    request_id: str = Field(max_length=64)


class Deny(_Cmd):
    type: Literal["deny"]
    request_id: str = Field(max_length=64)


class NewSession(_Cmd):
    type: Literal["new_session"]


class LoadSession(_Cmd):
    type: Literal["load_session"]
    session_id: str = Field(max_length=64)


class ListSessions(_Cmd):
    """All recent sessions, or (``workspace`` set) the sessions of one project Barnaby knows."""
    type: Literal["list_sessions"]
    workspace: str | None = Field(default=None, min_length=1, max_length=1024)


class ListProjects(_Cmd):
    """The projects the PC has opened. A tablet picks from these; it can't name a new folder."""
    type: Literal["list_projects"]


class SelectProject(_Cmd):
    """Point the conversation at one of the projects the PC has already opened."""
    type: Literal["select_project"]
    workspace: str = Field(min_length=1, max_length=1024)


class Resync(_Cmd):
    """Ask for a fresh snapshot (a tablet waking up). Answered to the sender only, with ``hello``."""
    type: Literal["resync"]


class SetConfig(_Cmd):
    type: Literal["set_config"]
    values: dict[str, Any]


class Ping(_Cmd):
    type: Literal["ping"]


class DiscardDraft(_Cmd):
    type: Literal["discard_draft"]
    draft_id: str = Field(max_length=64)


class GetVoiceProfile(_Cmd):
    type: Literal["get_voice_profile"]


class UpdateCorrection(_Cmd):
    type: Literal["update_correction"]
    heard: str = Field(max_length=200)
    meant: str = Field(max_length=200)
    action: Literal["enable", "disable", "delete"]


class DeleteVocab(_Cmd):
    type: Literal["delete_vocab"]
    term: str = Field(max_length=100)


class OpenInEditor(_Cmd):
    type: Literal["open_in_editor"]
    editor: Literal["vscode", "cursor"]
    path: str = Field(min_length=1, max_length=1000)
    line: int | None = Field(default=None, ge=1, le=10_000_000)


class ListDevices(_Cmd):
    type: Literal["list_devices"]


class ClaudeLogin(_Cmd):
    """Run Claude Code's own sign-in (browser). Desktop only."""
    type: Literal["claude_login"]


class ClaudeLoginCancel(_Cmd):
    type: Literal["claude_login_cancel"]


class ClaudeAuthCheck(_Cmd):
    type: Literal["claude_auth_check"]


class ListTemplates(_Cmd):
    type: Literal["list_templates"]


class RunTemplate(_Cmd):
    type: Literal["run_template"]
    template_id: str = Field(min_length=1, max_length=128)
    context: str | None = Field(default=None, max_length=2_000)  # what to apply it to


class SetSecret(_Cmd):
    """Save a cloud agent's API key to Windows Credential Manager. SecretStr keeps
    the value out of reprs, logs and validation-error messages; the engine never
    sends it back, only whether one is saved."""
    type: Literal["set_secret"]
    name: Literal["devin"]
    value: SecretStr = Field(min_length=1, max_length=600)


class DeleteSecret(_Cmd):
    type: Literal["delete_secret"]
    name: Literal["devin"]


MAX_CLIP_S = 60
MAX_CLIP_B64 = 4 * ((MAX_CLIP_S * 16_000 * 2 + 2) // 3)  # 16 kHz mono int16, base64


class AudioClip(_Cmd):
    """A hold-to-talk recording from a paired tablet (16 kHz mono int16 LE, base64)."""
    type: Literal["audio_clip"]
    pcm_b64: str = Field(min_length=4, max_length=MAX_CLIP_B64)


class TabletPairStart(_Cmd):
    type: Literal["tablet_pair_start"]


class TabletRevoke(_Cmd):
    type: Literal["tablet_revoke"]
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class Pair(_Cmd):
    """First frame from a tablet that isn't paired yet. Only the tablet listener accepts it."""
    type: Literal["pair"]
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=60, pattern=r"^[\w .()'-]+$")


_TASK_ID = r"^[A-Za-z0-9]{1,32}$"


class TaskCreate(_Cmd):
    """Start a background task: one engine, one instruction, the folders it may change."""
    type: Literal["task_create"]
    engine: Literal["claude", "cursor", "devin"]
    instruction: str = Field(min_length=1, max_length=20_000)
    lanes: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=8)
    title: str | None = Field(default=None, max_length=120)
    # One of the projects the PC has opened (None = the current one). Checked by the engine.
    workspace: str | None = Field(default=None, min_length=1, max_length=1024)


class TaskCancel(_Cmd):
    type: Literal["task_cancel"]
    task_id: str = Field(pattern=_TASK_ID)


class TaskFollowUp(_Cmd):
    type: Literal["task_follow_up"]
    task_id: str = Field(pattern=_TASK_ID)
    text: str = Field(min_length=1, max_length=20_000)


class TaskClear(_Cmd):
    """Remove finished tasks (done, failed, cancelled, interrupted) from the board."""
    type: Literal["task_clear"]


class TaskList(_Cmd):
    type: Literal["task_list"]


_pair_adapter: TypeAdapter[Pair] = TypeAdapter(Pair)


def parse_pair(raw: str | bytes) -> Pair:
    return _pair_adapter.validate_json(raw)


Command = Annotated[
    Union[
        Auth, PttStart, PttStop, TextSubmit, Cancel, StopSpeaking, Approve, Deny,
        NewSession, LoadSession, ListSessions, ListProjects, SelectProject, Resync, SetConfig, Ping, DiscardDraft,
        GetVoiceProfile, UpdateCorrection, DeleteVocab, OpenInEditor, ListDevices,
        ListTemplates, RunTemplate, SetSecret, DeleteSecret, AudioClip, TabletPairStart, TabletRevoke,
        TaskCreate, TaskCancel, TaskFollowUp, TaskClear, TaskList,
        ClaudeLogin, ClaudeLoginCancel, ClaudeAuthCheck,
    ],
    Field(discriminator="type"),
]
_command_adapter: TypeAdapter[Command] = TypeAdapter(Command)


def parse_command(raw: str | bytes) -> Command:
    """Parse and validate one inbound frame. Raises pydantic.ValidationError."""
    return _command_adapter.validate_json(raw)


# ---------------------------------------------------------------- events
def ev(type_: str, **fields: Any) -> dict[str, Any]:
    return {"type": type_, **fields}


def ev_state(state: State, detail: str | None = None) -> dict[str, Any]:
    return ev("state", state=state.value, detail=detail)


def ev_error(code: str, message: str) -> dict[str, Any]:
    return ev("error", code=code, message=message)


# ---------------------------------------------------------------- voice feature events
def ev_continuous_mode_changed(active: bool) -> dict[str, Any]:
    return ev("continuous_mode_changed", active=active)


def ev_command_chain_start(chain_id: str, steps: list[str] | None = None,
                           name: str | None = None) -> dict[str, Any]:
    return ev("command_chain_start", chain_id=chain_id, steps=list(steps or []), name=name)


def ev_command_chain_step(chain_id: str, index: int, total: int, text: str) -> dict[str, Any]:
    return ev("command_chain_step", chain_id=chain_id, index=index, total=total, text=text)


def ev_command_chain_end(chain_id: str, success: bool, reason: str = "completed",
                         completed: int = 0, total: int = 0) -> dict[str, Any]:
    """reason: completed | failed | declined | cancelled | interrupted | limit"""
    return ev("command_chain_end", chain_id=chain_id, success=success, reason=reason,
              completed=completed, total=total)


# ---------------------------------------------------------------- IDE bridge events
def ev_ide_connected(ide_type: str, ide_version: str) -> dict[str, Any]:
    return ev("ide_connected", ide_type=ide_type, ide_version=ide_version)


def ev_ide_context_update(file_path: str | None, selection: str | None) -> dict[str, Any]:
    return ev("ide_context_update", file_path=file_path, selection=selection)


# ---------------------------------------------------------------- workflow events
def ev_workflow_started(workflow_id: str, workflow_name: str) -> dict[str, Any]:
    return ev("workflow_started", workflow_id=workflow_id, workflow_name=workflow_name)


def ev_workflow_step_complete(workflow_id: str, step_number: int, success: bool) -> dict[str, Any]:
    return ev("workflow_step_complete", workflow_id=workflow_id, step_number=step_number, success=success)


def ev_workflow_error(workflow_id: str, error_message: str) -> dict[str, Any]:
    return ev("workflow_error", workflow_id=workflow_id, error_message=error_message)
