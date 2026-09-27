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
