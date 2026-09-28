"""Tablet access inside the orchestrator: lifecycle from settings, pairing events that
go only to the PC window, speech routed to the tablet, and tablet audio clips."""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import SessionStarted, TextDelta, TurnResult
from barnaby_engine.config import Config
from barnaby_engine.mock import MOCK_SESSION, FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
from barnaby_engine.orchestrator import Orchestrator
from barnaby_engine.protocol import parse_command
from barnaby_engine.store import Store
from tests.helpers import TempDataDir


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


def clip(seconds=1.0, amp=0.2, rate=16000):
    t = np.arange(int(seconds * rate)) / rate
    wave = amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    return base64.b64encode((wave * 32767).astype("<i2").tobytes()).decode()


class FakeTablet:
    instances = []

    def __init__(self, port, on_change, fail=None):
        self.port_requested = port
        self.on_change = on_change
        self.fail = fail
        self.running = False
        self.port = None
        self.revoked = []
        self.pairings = 0
        FakeTablet.instances.append(self)

    async def start(self):
        if self.fail:
            raise self.fail
        self.running, self.port = True, self.port_requested

    async def stop(self):
        self.running = False

    def urls(self):
        return [f"https://192.168.1.20:{self.port}/"] if self.running else []

    def start_pairing(self, host=None):
        self.pairings += 1
        return f"https://192.168.1.20:{self.port}/#pair=SECRETCODE{self.pairings}", 1_800_000_300.0

    def devices(self):
        return [{"id": "dev_1", "name": "Galaxy Tab S10", "created": 1.0, "last_seen": 2.0, "connected": True}]

    async def revoke(self, device_id):
        self.revoked.append(device_id)
        return True

    def ca_fingerprint(self):
        return "AB:" * 31 + "CD"


class _OrchBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeTablet.instances.clear()
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "db.sqlite")
        self.events, self.desktop_events = [], []
        self.fail = None

    async def asyncTearDown(self):
        await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    async def build(self, texts=None, scripts=None, **config):
        async def emit(e):
            self.events.append(e)

        async def emit_desktop(e):
            self.desktop_events.append(e)

        self.config = Config(workspace=self._tmp.name, review_transcripts=False, **config)
        self.speaker = FakeSpeaker()
        self.transcriber = FakeTranscriber(texts)
        self.orch = Orchestrator(
            config=self.config, store=self.store, profile=VoiceProfile(self.store), recorder=FakeRecorder(),
            transcriber=self.transcriber, speaker=self.speaker, emit=emit, emit_desktop=emit_desktop,
            agent=ScriptedAgent(scripts or []),
            tablet_factory=lambda port, on_change: FakeTablet(port, on_change, self.fail))
        await self.orch.start()

    def of(self, t, events=None):
        return [e for e in (self.events if events is None else events) if e["type"] == t]

    async def settle(self, n=60):
        for _ in range(n):
            await asyncio.sleep(0.01)

    # ---- lifecycle
class TabletOrchestratorTest(_OrchBase):
    async def test_off_by_default(self):
        await self.build()
        status = (await self.orch.hello())["tablet"]
        self.assertEqual((status["enabled"], status["running"]), (False, False))
        self.assertEqual(FakeTablet.instances, [])

    async def test_turning_on_and_off_from_settings(self):
        await self.build()
        await self.orch.handle(cmd("set_config", values={"tablet_access": True}))
        tablet = FakeTablet.instances[-1]
        self.assertTrue(tablet.running)
        self.assertEqual(tablet.port_requested, 8766)
        status = self.of("tablet_status")[-1]
        self.assertEqual((status["enabled"], status["running"]), (True, True))
        self.assertEqual(status["urls"], ["https://192.168.1.20:8766/"])
        self.assertEqual(status["devices"][0]["name"], "Galaxy Tab S10")
        self.assertEqual(status["ca_fingerprint"], "AB:" * 31 + "CD")
        await self.orch.handle(cmd("set_config", values={"tablet_access": False}))
        self.assertFalse(tablet.running)
        self.assertEqual(self.of("tablet_status")[-1]["running"], False)

    async def test_starts_at_launch_when_enabled(self):
        await self.build(tablet_access=True)
        self.assertTrue(FakeTablet.instances[-1].running)

    async def test_start_failure_is_reported_and_harmless(self):
        self.fail = OSError(10048, "address already in use")
        await self.build()
        await self.orch.handle(cmd("set_config", values={"tablet_access": True}))
        status = self.of("tablet_status")[-1]
        self.assertFalse(status["running"])
        self.assertIn("8766", status["error"])
        await self.orch.handle(cmd("ping"))  # everything else still works
        self.assertTrue(self.of("pong"))

    async def test_port_change_restarts(self):
        await self.build(tablet_access=True)
        first = FakeTablet.instances[-1]
        await self.orch.handle(cmd("set_config", values={"tablet_port": 9100}))
        self.assertFalse(first.running)
        self.assertEqual(FakeTablet.instances[-1].port_requested, 9100)
        self.assertTrue(FakeTablet.instances[-1].running)

    # ---- pairing and revoke
    async def test_pairing_link_goes_only_to_the_pc_window(self):
        await self.build(tablet_access=True)
        await self.orch.handle(cmd("tablet_pair_start"))
        pairing = self.of("tablet_pairing", self.desktop_events)[-1]
        self.assertIn("#pair=SECRETCODE1", pairing["url"])
        self.assertNotIn("SECRETCODE", json.dumps(self.events))  # never broadcast to tablets

    async def test_pairing_while_off_explains(self):
        await self.build()
        await self.orch.handle(cmd("tablet_pair_start"))
        self.assertIn("Tablet access", self.of("error")[-1]["message"])

    async def test_revoke(self):
        await self.build(tablet_access=True)
        await self.orch.handle(cmd("tablet_revoke", device_id="dev_1"))
        self.assertEqual(FakeTablet.instances[-1].revoked, ["dev_1"])
        self.assertTrue(self.of("tablet_status"))

    async def test_server_change_callback_publishes_status(self):
        await self.build(tablet_access=True)
        before = len(self.of("tablet_status"))
        FakeTablet.instances[-1].on_change()
        await self.settle(5)
        self.assertEqual(len(self.of("tablet_status")), before + 1)

    # ---- speech routing
    async def test_replies_go_to_the_tablet_as_say_events_while_one_is_connected(self):
        reply = [SessionStarted(MOCK_SESSION, "m"), TextDelta("All tests pass."),
                 TurnResult("All tests pass.", MOCK_SESSION, False, 0.0, 1)]
        await self.build(scripts=[reply, list(reply)])
        self.orch.set_remote_listeners(1)
        await self.orch.handle(cmd("text_submit", text="run the tests"))
        await self.orch._turn_task
        await self.settle()
        self.assertEqual([e["text"] for e in self.of("say")], ["All tests pass."])
        self.assertEqual(self.speaker.spoken, [])  # the PC stays quiet

        self.orch.set_remote_listeners(0)
        await self.orch.handle(cmd("text_submit", text="again"))
        await self.orch._turn_task
        await self.settle()
        self.assertEqual(len(self.of("say")), 1)
        self.assertEqual(self.speaker.spoken, ["All tests pass."])

    async def test_say_respects_the_speak_replies_setting(self):
        reply = [SessionStarted(MOCK_SESSION, "m"), TextDelta("Done."), TurnResult("Done.", MOCK_SESSION, False, 0.0, 1)]
        await self.build(scripts=[reply], tts_enabled=False)
        self.orch.set_remote_listeners(1)
        await self.orch.handle(cmd("text_submit", text="x"))
        await self.orch._turn_task
        await self.settle()
        self.assertEqual(self.of("say"), [])

    async def test_stop_speaking_tells_the_tablet(self):
        await self.build()
        self.orch.set_remote_listeners(1)
        await self.orch.handle(cmd("stop_speaking"))
        await self.settle(5)
        self.assertTrue(self.of("say_stop"))

    async def test_spoken_text_is_redacted_for_the_tablet_too(self):
        secret = "sk-ant-api03-" + "A" * 40
        reply = [SessionStarted(MOCK_SESSION, "m"), TextDelta(f"Your key is {secret}."),
                 TurnResult(f"Your key is {secret}.", MOCK_SESSION, False, 0.0, 1)]
        await self.build(scripts=[reply])
        self.orch.set_remote_listeners(1)
        await self.orch.handle(cmd("text_submit", text="x"))
        await self.orch._turn_task
        await self.settle()
        said = " ".join(e["text"] for e in self.of("say"))
        self.assertTrue(said)
        self.assertNotIn(secret, said)

    # ---- tablet audio
    async def test_audio_clip_is_transcribed_like_push_to_talk(self):
        await self.build(texts=["add a health check"])
        await self.orch.handle(cmd("audio_clip", pcm_b64=clip(1.2)))
        await self.settle()
        self.assertEqual(self.of("transcript")[-1]["text"], "add a health check")
        self.assertEqual(self.of("turn_start")[-1]["text"], "add a health check")

    async def test_silent_or_short_clip(self):
        await self.build()
        await self.orch.handle(cmd("audio_clip", pcm_b64=clip(1.0, amp=0.0)))
        await self.settle(10)
        self.assertIn("didn't hear", self.of("notice")[-1]["message"])
        self.assertEqual(self.of("transcript"), [])

    async def test_malformed_clip(self):
        await self.build()
        for bad in ("!!!!notbase64", base64.b64encode(b"\x00\x01\x02").decode()):
            with self.subTest(bad=bad):
                await self.orch.handle(cmd("audio_clip", pcm_b64=bad))
                self.assertEqual(self.of("error")[-1]["code"], "audio")
        self.assertEqual(self.of("transcript"), [])


class FakeMatchesRealServerTest(unittest.TestCase):
    """The orchestrator is tested against FakeTablet; keep it honest against RemoteServer."""

    def test_same_interface(self):
        import inspect
        from barnaby_engine.remote import RemoteServer
        from barnaby_engine.server import Bridge
        real = RemoteServer(Bridge("t" * 40), data_dir=Path(tempfile.mkdtemp()), port=1234)
        fake = FakeTablet(1234, lambda: None)
        for name in ("start", "stop", "urls", "start_pairing", "devices", "revoke", "ca_fingerprint"):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(real, name)))
                self.assertEqual(inspect.iscoroutinefunction(getattr(real, name)),
                                 inspect.iscoroutinefunction(getattr(fake, name)), name)
        for attr in ("running", "port", "port_requested"):
            with self.subTest(attr=attr):
                self.assertTrue(hasattr(real, attr))
                self.assertTrue(hasattr(fake, attr))
        self.assertEqual(real.port_requested, 1234)


if __name__ == "__main__":
    unittest.main()


class ReviewRegressionOrchestratorTest(_OrchBase):
    async def test_remote_cannot_open_a_session_from_another_project(self):
        # F3: remote load_session switched config.workspace.
        await self.build()
        other = tempfile.mkdtemp()
        foreign = self.store.create_session(other, "claude")
        local = self.store.create_session(self.config.workspace, "claude")
        self.assertIn("PC", self.orch.remote_check(cmd("load_session", session_id=foreign)))
        self.assertIsNone(self.orch.remote_check(cmd("load_session", session_id=local)))
        self.assertIsNone(self.orch.remote_check(cmd("ping")))

    async def test_two_clips_at_once_transcribe_one_at_a_time(self):
        # F7c: busy check and _transcribing were separated by an await.
        await self.build(texts=["first"])
        await asyncio.gather(self.orch.handle(cmd("audio_clip", pcm_b64=clip(1.0))),
                             self.orch.handle(cmd("audio_clip", pcm_b64=clip(1.0))))
        await self.settle()
        self.assertEqual(len(self.of("transcript")), 1)
        self.assertTrue(any("Still working" in n["message"] for n in self.of("notice")))
