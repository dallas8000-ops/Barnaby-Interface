"""TEST DOUBLES ONLY: never imported by the shipped engine.

Deterministic stand-ins for hardware/model/agent dependencies, used by the unit tests
and by ``tests/e2e_engine.py`` (the Playwright harness): no microphone, no models, no API spend.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator, Callable

import numpy as np

from barnaby_engine.agent import (AgentEvent, Denial, SessionStarted, TextDelta, ToolEnd,
                    ToolStart, TurnResult)
from barnaby_engine.safety import Grant

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

    # hands-free: tests push blocks through ``stream_cb``; E2E only toggles it
    stream_cb = None

    def start_stream(self, on_block: Callable[[np.ndarray], None]) -> int:
        self.stream_cb = on_block
        return 16_000

    def stop_stream(self) -> None:
        self.stream_cb = None


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

    def __init__(self, scripts: list[Any] | None = None, step_delay: float = 0.0,
                 default: Callable[[str, Grant | None], list[AgentEvent]] | None = None) -> None:
        self.scripts = list(scripts or [])
        self.default = default
        self.step_delay = step_delay
        self.calls: list[dict[str, Any]] = []
        self.cancelled = 0
        self._cancel = asyncio.Event()

    async def run(self, prompt: str, *, workspace: str, session_id: str | None,
                  grant: Grant | None, scope: Any = None) -> AsyncIterator[AgentEvent]:
        self.calls.append({"prompt": prompt, "session_id": session_id, "grant": grant, "workspace": workspace,
                           "scope": scope})
        self._cancel = asyncio.Event()
        script = self.scripts.pop(0) if self.scripts else (self.default or demo_script)
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


MOCK_DEVIN_SESSION = "devin-mock0000000000000000000000000000"


def devin_demo_script(prompt: str, grant: Grant | None) -> list[AgentEvent]:
    """What a Devin turn looks like to the orchestrator: a session card, Devin's messages, a result."""
    url = "https://app.devin.ai/sessions/mock"
    first = "I've started on it in my cloud workspace and cloned the repository."
    second = "Done. I opened a pull request with the change and passing tests."
    return [SessionStarted(MOCK_DEVIN_SESSION, None),
            ToolStart("devin-session", "Devin", {"session_id": MOCK_DEVIN_SESSION, "url": url}),
            TextDelta(first + "\n\n"), TextDelta(second + "\n\n"),
            ToolEnd("devin-session", True, url),
            TurnResult(f"{first}\n\n{second}", MOCK_DEVIN_SESSION, False, None, 1200)]


def task_demo_script(prompt: str, grant: Grant | None) -> list[AgentEvent]:
    """A background task for demos/E2E. An instruction mentioning "install" first asks to
    run `npm install zod`; everything else finishes with a one-line summary."""
    instruction = prompt.split("\n\n", 1)[-1].strip()
    if "install" in instruction.lower() and not grant and "The user" not in prompt:
        return [SessionStarted(MOCK_SESSION, "mock"),
                ToolStart("k1", "Bash", {"command": "npm install zod"}),
                ToolEnd("k1", False, "Permission required"),
                TurnResult("I need to install zod first.", MOCK_SESSION, False, 0.01, 800, denials=(
                    Denial("k1", "Bash", {"command": "npm install zod", "description": "Install zod"}),))]
    text = "Finished the task and all tests pass. I kept my changes inside my folders."
    return [SessionStarted(MOCK_SESSION, "mock"),
            ToolStart("k2", "Read", {"file_path": "README.md"}), ToolEnd("k2", True, "…"),
            TextDelta(text), TurnResult(text, MOCK_SESSION, False, 0.02, 1500)]


class FakeClaudeAuth:
    """Stands in for ClaudeAuth: a sign-in that never opens a browser."""

    def __init__(self, *, signed_in: bool = False, login_ok: bool = True, hold: bool = False,
                 error: str | None = None, had_token: bool = False, delay: float = 0.0) -> None:
        self.signed_in = signed_in
        self.login_ok = login_ok
        self.hold = hold          # keep the sign-in "waiting on the browser" until cancelled
        self.error = error
        self.had_token = had_token
        self.delay = delay
        self.logins = 0
        self.cancels = 0

    async def status(self):
        from barnaby_engine.claude_auth import AuthStatus
        return AuthStatus(self.signed_in, "claude.ai" if self.signed_in else None, self.error)

    async def login(self) -> bool:
        self.logins += 1
        if self.hold:
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay)
        if self.login_ok:
            self.signed_in = True
        return self.login_ok

    async def cancel(self) -> None:
        self.cancels += 1

    def drop_stale_token(self) -> bool:
        return self.had_token
