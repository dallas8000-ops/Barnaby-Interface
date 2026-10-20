"""Engine for the Playwright suite ONLY: ``python -m tests.e2e_engine --port N`` (cwd: engine/).

Runs the real engine, server, orchestrator and tablet TLS server, with the microphone, speech
models and agents replaced by the scripted doubles in tests/doubles.py. The shipped engine
(``python -m barnaby_engine``) has no such mode.
"""
from __future__ import annotations

import asyncio

from barnaby_engine.__main__ import DEVIN_NEEDS_KEY, Backends, _setup_logging, amain, parse_args
from barnaby_engine.config import Config, data_dir
from barnaby_engine.vault import MemoryCredentials, Vault
from tests.doubles import (FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent, devin_demo_script,
                           task_demo_script)


def e2e_backends(config: Config) -> Backends:
    vault = Vault(MemoryCredentials())  # never touch the real Credential Manager
    if not config.workspace:
        config.workspace = str(data_dir())

    def available(name: str) -> str | None:
        return DEVIN_NEEDS_KEY if name == "devin" and not vault.has("devin") else None

    return Backends(
        recorder=FakeRecorder(), transcriber=FakeTranscriber(), speaker=FakeSpeaker(delay=0.4),
        agents={"claude": ScriptedAgent(step_delay=0.35), "cursor": ScriptedAgent(step_delay=0.35),
                "devin": ScriptedAgent(step_delay=0.35, default=devin_demo_script)},
        vault=vault, available=available,
        task_factory=lambda engine, task_id: ScriptedAgent(step_delay=0.6, default=task_demo_script),
        # The test machine's Windows network category must not decide whether tablet access starts.
        tablet_kwargs={"network_check": lambda: None},
    )


if __name__ == "__main__":
    _setup_logging()
    try:
        asyncio.run(amain(parse_args(), e2e_backends))
    except KeyboardInterrupt:
        pass
