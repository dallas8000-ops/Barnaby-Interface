"""Deterministic stand-ins for hardware/model/agent dependencies.

Used by the unit tests and by ``python -m barnaby_engine --mock`` so the UI and
Playwright E2E suite run with no microphone, no models and no API spend.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator, Callable

import numpy as np

from .agent import (AgentEvent, Denial, SessionStarted, TextDelta, ToolEnd,
                    ToolStart, TurnResult)
from .safety import Grant

MOCK_SESSION = "11111111-2222-4333-8444-555555555555"


class FakeRecorder:
    def __init__(self, seconds: float = 1.0, amplitude: float = 0.1) -> None:
        self.seconds = seconds
        self.amplitude = amplitude
        self.started = False
        self.aborted = False

    def start(self, on_level: Callable[[float], None]) -> None:
        self.started = True
        on_level(self.amplitude)

    def stop(self) -> np.ndarray:
        self.started = False
        n = int(16_000 * self.seconds)
        return (np.sin(np.linspace(0, 440 * 2 * np.pi, n)) * self.amplitude).astype(np.float32)

    def abort(self) -> None:
        self.aborted = True
        self.started = False


class FakeTranscriber:
    def __init__(self, texts: list[str] | None = None) -> None:
        self.texts = list(texts or ["run the tests and fix anything failing"])
        self.prompts: list[str] = []

    def load(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray, vocabulary_prompt: str) -> str:
        self.prompts.append(vocabulary_prompt)
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


class FakeSpeaker:
    def __init__(self, delay: float = 0.0) -> None:
        self.spoken: list[str] = []
        self.stops = 0
        self.delay = delay
        self._stop = threading.Event()

    def load(self) -> None:
        pass

    def say(self, text: str) -> None:
        self._stop.clear()
        if self.delay:
            self._stop.wait(self.delay)
        if not self._stop.is_set():
            self.spoken.append(text)

    def stop(self) -> None:
        self.stops += 1
        self._stop.set()


class ScriptedAgent:
    """Replays scripted turns. Each script is a list of AgentEvents, or a
    callable (prompt, grant) -> list[AgentEvent]."""

    def __init__(self, scripts: list[Any] | None = None, step_delay: float = 0.0) -> None:
        self.scripts = list(scripts or [])
        self.step_delay = step_delay
        self.calls: list[dict[str, Any]] = []
        self.cancelled = 0
        self._cancel = asyncio.Event()

    async def run(self, prompt: str, *, workspace: str, session_id: str | None,
                  grant: Grant | None) -> AsyncIterator[AgentEvent]:
        self.calls.append({"prompt": prompt, "session_id": session_id, "grant": grant, "workspace": workspace})
        self._cancel = asyncio.Event()
        script = self.scripts.pop(0) if self.scripts else demo_script
        events = script(prompt, grant) if callable(script) else script
        for event in events:
            if self._cancel.is_set():
                return
            if self.step_delay:
                try:
                    await asyncio.wait_for(self._cancel.wait(), timeout=self.step_delay)
                    return
                except asyncio.TimeoutError:
                    pass
            yield event

    async def cancel(self) -> None:
        self.cancelled += 1
        self._cancel.set()


def demo_script(prompt: str, grant: Grant | None) -> list[AgentEvent]:
    """A realistic turn for demos/E2E: reads, runs tests, edits, then needs approval to push."""
    if "The user declined" in prompt and not grant:
        text = "Understood, I won't push. The fix is committed locally, so you can push it when you're ready."
        return [SessionStarted(MOCK_SESSION, "mock"), TextDelta(text),
                TurnResult(text, MOCK_SESSION, False, 0.004, 900)]
    if grant and any("git push" in r for r in grant.allow):
        return [
            SessionStarted(MOCK_SESSION, "mock"),
            ToolStart("t9", "Bash", {"command": "git push origin main", "description": "Push to origin"}),
            ToolEnd("t9", True, "main -> main"),
            TextDelta("Pushed to origin. The branch is up to date."),
            TurnResult("Pushed to origin. The branch is up to date.", MOCK_SESSION, False, 0.012, 2100),
        ]
    reply = ("All 42 tests pass now. The failure was a stale selector in the cart store, "
             "so I updated it and added a regression test.\n\n```ts\nexpect(selectTotal(state)).toBe(30)\n```\n"
             "I also tried to push the fix, which needs your OK.")
    head, tail = reply[:60], reply[60:]
    return [
        SessionStarted(MOCK_SESSION, "mock"),
        TextDelta("I'll run the test suite first. "),
        ToolStart("t1", "Bash", {"command": "npx vitest run", "description": "Run unit tests"}),
        ToolEnd("t1", False, "FAIL src/store/cart.test.ts > selectTotal\n  expected 30, received 0"),
        ToolStart("t2", "Read", {"file_path": "src/store/cart.ts"}),
        ToolEnd("t2", True, "export const useCart = create(...)"),
        ToolStart("t3", "Edit", {"file_path": "src/store/cart.ts"}),
        ToolEnd("t3", True, "The file src/store/cart.ts has been updated."),
        ToolStart("t4", "Bash", {"command": "npx vitest run", "description": "Re-run unit tests"}),
        ToolEnd("t4", True, "Test Files  9 passed (9)\n     Tests  42 passed (42)"),
        ToolStart("t5", "Bash", {"command": "git push origin main", "description": "Push the fix"}),
        ToolEnd("t5", False, "Permission required"),
        TextDelta(head),
        TextDelta(tail),
        TurnResult(reply, MOCK_SESSION, False, 0.084, 18_400, denials=(
            Denial("t5", "Bash", {"command": "git push origin main", "description": "Push the fix to origin/main"}),
        )),
    ]
