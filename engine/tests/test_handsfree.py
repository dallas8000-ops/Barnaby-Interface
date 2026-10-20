"""Hands-free mode through the orchestrator, fed synthetic audio via a fake mic stream."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import SessionStarted, TextDelta, TurnResult
from barnaby_engine.config import Config
from tests.doubles import MOCK_SESSION, FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
from barnaby_engine.orchestrator import Orchestrator, classify_draft_command
from barnaby_engine.protocol import parse_command
from barnaby_engine.store import Store
from tests.helpers import TempDataDir
from tests.test_segmenter import Audio, B


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


def simple(text="Done."):
    return [SessionStarted(MOCK_SESSION, "m"), TextDelta(text), TurnResult(text, MOCK_SESSION, False, 0.0, 1)]


class HandsFreeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "db.sqlite")
        self.events, self.orch = [], None
        self.audio = Audio(seed=3)

    async def asyncTearDown(self):
        if self.orch:
            await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    async def build(self, texts=None, scripts=None, **config):
        async def emit(e):
            self.events.append(e)
        config.setdefault("continuous_mode_enabled", True)
        self.config = Config(workspace=self._tmp.name, tts_enabled=False, **config)
        self.recorder = FakeRecorder()
        self.agent = ScriptedAgent(scripts or [])
        self.orch = Orchestrator(config=self.config, store=self.store, profile=VoiceProfile(self.store),
                                 recorder=self.recorder, transcriber=FakeTranscriber(texts), speaker=FakeSpeaker(),
                                 agent=self.agent, emit=emit)
        await self.orch.start()

    def of(self, t):
        return [e for e in self.events if e["type"] == t]

    async def speak(self, seconds=1.2, lead=0.6, tail=None):
        """Push lead-in room noise, speech, then enough quiet to end the utterance."""
        tail = tail if tail is not None else self.config.continuous_mode_timeout + 0.6
        sig = np.concatenate([self.audio.noise(lead), self.audio.speech(seconds), self.audio.noise(tail)])
        for i in range(0, len(sig), B):
            self.recorder.stream_cb(sig[i:i + B])
            await asyncio.sleep(0)
        for _ in range(100):  # let transcription + dispatch finish
            await asyncio.sleep(0.01)
            task = self.orch._turn_task
            if not self.orch._transcribing and (task is None or task.done()):
                break

    async def test_starts_with_config_and_toggles(self):
        await self.build()
        self.assertIsNotNone(self.recorder.stream_cb)
        self.assertEqual(self.of("continuous_mode_changed")[-1]["active"], True)
        self.assertTrue((await self.orch.hello())["handsfree"], "a UI connecting later must learn the state")
        await self.orch.handle(cmd("set_config", values={"continuous_mode_enabled": False}))
        self.assertIsNone(self.recorder.stream_cb)
        self.assertEqual(self.of("continuous_mode_changed")[-1]["active"], False)

    async def test_utterance_becomes_a_review_draft(self):
        await self.build(texts=["run the tests"])
        await self.speak()
        states = [e["state"] for e in self.of("state")]
        self.assertIn("listening", states)
        self.assertIn("transcribing", states)
        t = self.of("transcript")[-1]
        self.assertEqual((t["text"], t["review"]), ("run the tests", True))
        self.assertEqual(self.agent.calls, [])

    async def test_without_review_it_submits(self):
        await self.build(texts=["run the tests"], scripts=[simple()], review_transcripts=False)
        await self.speak()
        self.assertEqual(self.agent.calls[0]["prompt"], "run the tests")

    async def test_send_it_and_scratch_that_control_the_draft(self):
        await self.build(texts=["run the tests", "send it", "fix the lint", "scratch that"])
        await self.speak()
        await self.speak()
        self.assertEqual(self.of("draft_command")[-1]["action"], "send")
        await self.speak()
        await self.speak()
        self.assertEqual(self.of("draft_command")[-1]["action"], "discard")
        self.assertEqual(self.orch._drafts, {})
        self.assertEqual(self.agent.calls, [], "the UI submits the draft, so edits are kept")

    async def test_does_not_listen_while_barnaby_speaks(self):
        await self.build(texts=["echo of my own voice"])
        self.orch._speaking = True
        await self.speak()
        self.assertEqual(self.of("transcript"), [])
        self.orch._speaking = False
        self.orch._speech_ended_at = 0.0
        await self.speak()
        self.assertEqual(len(self.of("transcript")), 1)

    async def test_push_to_talk_suspends_then_resumes(self):
        await self.build(texts=["via push to talk"])
        await self.orch.handle(cmd("ptt_start"))
        self.assertIsNone(self.recorder.stream_cb, "one mic owner at a time")
        await self.orch.handle(cmd("ptt_stop"))
        self.assertIsNotNone(self.recorder.stream_cb)
        self.assertEqual(self.of("continuous_mode_changed")[-1]["active"], True, "no off/on flicker")

    async def test_noise_is_ignored_silently(self):
        await self.build(texts=[""])
        sig = np.concatenate([self.audio.noise(2), self.audio.noise(10, 0.02)])  # a fan switching on
        for i in range(0, len(sig), B):
            self.recorder.stream_cb(sig[i:i + B])
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        self.assertEqual(self.of("transcript"), [])
        self.assertEqual(self.of("notice"), [], "no 'didn't catch that' spam in hands-free")
        self.assertEqual(self.orch.state.value, "idle")

    async def test_mic_failure_reports_and_stays_off(self):
        class BrokenMic(FakeRecorder):
            def start_stream(self, on_block):
                raise RuntimeError("device busy")
        async def emit(e):
            self.events.append(e)
        self.config = Config(workspace=self._tmp.name, continuous_mode_enabled=True)
        self.orch = Orchestrator(config=self.config, store=self.store, profile=VoiceProfile(self.store),
                                 recorder=BrokenMic(), transcriber=FakeTranscriber(), speaker=FakeSpeaker(),
                                 agent=ScriptedAgent([]), emit=emit)
        await self.orch.start()
        self.assertIn("device busy", self.of("error")[-1]["message"])
        self.assertEqual(self.of("continuous_mode_changed")[-1]["active"], False)

    async def test_pause_length_setting_reaches_segmenter(self):
        await self.build()
        await self.orch.handle(cmd("set_config", values={"continuous_mode_timeout": 2.5}))
        self.assertEqual(self.orch._hf.silence_s, 2.5)
        await self.orch.handle(cmd("set_config", values={"continuous_mode_timeout": 9}))
        self.assertIn("continuous_mode_timeout", self.of("config")[-1]["rejected"])


class DraftCommandTest(unittest.TestCase):
    def test_phrases(self):
        for text, want in {"Send it.": "send", "okay send it please": "send", "go ahead": "send",
                           "Scratch that": "discard", "never mind": "discard",
                           "send the report to finance": None, "go to the settings page": None,
                           "scratch that test and write a new one": None}.items():
            with self.subTest(text=text):
                self.assertEqual(classify_draft_command(text), want)


if __name__ == "__main__":
    unittest.main()
