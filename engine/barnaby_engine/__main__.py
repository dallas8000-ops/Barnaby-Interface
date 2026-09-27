"""Entry point: ``python -m barnaby_engine [--mock] [--dev]``.

Contract with the Tauri shell:
* token arrives in env ``BARNABY_TOKEN`` (never argv: argv is visible to every
  process on the machine via the process list);
* the engine prints exactly one ``{"event":"ready","port":N}`` line to stdout;
* with ``BARNABY_WATCH_STDIN=1`` the engine exits when stdin closes, so it can
  never outlive a crashed shell and keep the microphone open.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import __version__
from .adaptive import VoiceProfile
from .agent import AgentError, ClaudeCodeAgent, resolve_claude
from .cursor_agent import CursorAgent, resolve_cursor
from .config import resolve_workflows_dir, Config, data_dir
from .orchestrator import Orchestrator
from .server import Bridge, allowed_origins_from_env
from .store import Store
from .wake_word import WakeWordDetector
from .continuous import ContinuousMode
from .command_chain import CommandChain
from .ide_bridge import IdeBridge
from .workflows import WorkflowEngine
from .macro_recorder import MacroRecorder
from .templates import TemplateManager


def _setup_logging() -> None:
    handler = RotatingFileHandler(data_dir() / "engine.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler(sys.stderr)])


def _watch_stdin(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def run() -> None:
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except (OSError, ValueError):
            pass
        loop.call_soon_threadsafe(stop.set)
    threading.Thread(target=run, name="stdin-watch", daemon=True).start()


async def amain(args: argparse.Namespace) -> None:
    token = os.environ.get("BARNABY_TOKEN") or ""
    if not token:
        if not args.dev:
            print("BARNABY_TOKEN is required (use --dev for a printed dev token).", file=sys.stderr)
            sys.exit(2)
        token = secrets.token_urlsafe(32)
        print(f"dev token: {token}", file=sys.stderr)

    config = Config.load()
    store = Store(data_dir() / "barnaby.db")
    profile = VoiceProfile(store)
    bridge = Bridge(token, allowed_origins_from_env())
    
    # Initialize new subsystems
    wake_word_detector = WakeWordDetector(
        keyword=config.wake_word_keyword,
        sensitivity=config.wake_word_sensitivity,
    )
    
    continuous_mode = ContinuousMode(timeout=config.continuous_mode_timeout)
    
    command_chain = CommandChain(max_depth=config.command_chain_max_depth)
    
    ide_bridge = IdeBridge(port=config.ide_bridge_port)
    
    workflow_engine = WorkflowEngine(workflows_dir=str(resolve_workflows_dir(config.workflows_directory)))
    
    macro_recorder = MacroRecorder()
    
    template_manager = TemplateManager()

    if args.mock:
        from .mock import FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
        recorder, transcriber, speaker = FakeRecorder(), FakeTranscriber(), FakeSpeaker(delay=0.4)
        agents = {"claude": ScriptedAgent(step_delay=0.35), "cursor": ScriptedAgent(step_delay=0.35)}
        available = lambda name: None  # noqa: E731
        tfactory = sfactory = None
        if not config.workspace:
            config.workspace = str(data_dir())
    else:
        from .voice import KokoroSpeaker, MicRecorder, WhisperTranscriber
        recorder = MicRecorder(config.input_device)
        tfactory = lambda c: WhisperTranscriber(c.whisper_model, c.whisper_device)  # noqa: E731
        sfactory = lambda c: KokoroSpeaker(c.tts_voice, c.tts_speed)  # noqa: E731
        transcriber, speaker = tfactory(config), sfactory(config)
        agents = {
            "claude": ClaudeCodeAgent(claude_bin=config.claude_bin, model=config.claude_model,
                                      safe_bash=config.safe_bash, timeout_s=config.turn_timeout_s,
                                      scratch_dir=data_dir() / "run"),
            "cursor": CursorAgent(config_dir=data_dir() / "cursor", cursor_bin=config.cursor_bin,
                                  model=config.cursor_model, safe_bash=config.safe_bash,
                                  timeout_s=config.turn_timeout_s, scratch_dir=data_dir() / "run"),
        }

        def available(name: str) -> str | None:
            try:
                if name == "claude":
                    resolve_claude(config.claude_bin)
                elif name == "cursor":
                    resolve_cursor(config.cursor_bin)
            except AgentError as exc:
                return str(exc)
            return None

    orch = Orchestrator(config=config, store=store, profile=profile, recorder=recorder,
                        transcriber=transcriber, speaker=speaker, agents=agents, engine_available=available,
                        templates=template_manager, workflows=workflow_engine,
                        emit=bridge.emit,
                        transcriber_factory=tfactory, speaker_factory=sfactory)
    bridge.orchestrator = orch

    server = await bridge.serve(port=args.port)
    port = server.sockets[0].getsockname()[1]
    await orch.start()
    print(json.dumps({"event": "ready", "port": port, "version": __version__, "mock": args.mock}), flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.environ.get("BARNABY_WATCH_STDIN") == "1":
        _watch_stdin(loop, stop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: Ctrl+C still raises KeyboardInterrupt
    try:
        await stop.wait()
    finally:
        await orch.shutdown()
        server.close()
        await server.wait_closed()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="barnaby-engine")
    parser.add_argument("--mock", action="store_true", help="scripted agent + fake audio (UI dev / E2E)")
    parser.add_argument("--dev", action="store_true", help="generate and print a token if none is provided")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    _setup_logging()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
