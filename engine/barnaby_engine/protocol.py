"""Wire protocol between the engine and the UI.

Engine -> UI: plain dicts produced by the ``ev_*`` helpers (always carry ``type``).
UI -> Engine: validated by the pydantic ``Command`` discriminated union, so a
malformed or hostile frame is rejected before it reaches the orchestrator.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

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
    type: Literal["list_sessions"]


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


Command = Annotated[
    Union[
        Auth, PttStart, PttStop, TextSubmit, Cancel, StopSpeaking, Approve, Deny,
        NewSession, LoadSession, ListSessions, SetConfig, Ping, DiscardDraft,
        GetVoiceProfile, UpdateCorrection, DeleteVocab, OpenInEditor, ListDevices,
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
def ev_wake_word_detected(timestamp: float) -> dict[str, Any]:
    return ev("wake_word_detected", timestamp=timestamp)


def ev_continuous_mode_changed(active: bool) -> dict[str, Any]:
    return ev("continuous_mode_changed", active=active)


def ev_command_chain_start(chain_id: str) -> dict[str, Any]:
    return ev("command_chain_start", chain_id=chain_id)


def ev_command_chain_end(chain_id: str, success: bool) -> dict[str, Any]:
    return ev("command_chain_end", chain_id=chain_id, success=success)


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


def ev_macro_recorded(macro_id: str, macro_name: str, step_count: int) -> dict[str, Any]:
    return ev("macro_recorded", macro_id=macro_id, macro_name=macro_name, step_count=step_count)
