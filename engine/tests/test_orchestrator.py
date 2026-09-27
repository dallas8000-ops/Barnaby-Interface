"""Orchestrator state machine with fake audio, fake models and a scripted agent."""
import asyncio
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import (AgentError, Denial, SessionStarted, TextDelta,
                                  ToolEnd, ToolStart, TurnResult)
from barnaby_engine.config import Config
from barnaby_engine.mock import (MOCK_SESSION, FakeRecorder, FakeSpeaker,
                                 FakeTranscriber, ScriptedAgent)
from barnaby_engine.orchestrator import Orchestrator
from barnaby_engine.protocol import parse_command
from barnaby_engine.store import Store
from tests.helpers import TempDataDir


def cmd(type_, **kw):
    import json
    return parse_command(json.dumps({"type": type_, **kw}))


def simple(text="Done.", denials=()):
    return [SessionStarted(MOCK_SESSION, "m"), TextDelta(text),
            TurnResult(text, MOCK_SESSION, False, 0.01, 10, denials=tuple(denials))]


class OrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name
        self.store = Store(Path(self._tmp.name) / "db.sqlite")
        self.events: list[dict] = []

    async def asyncTearDown(self):
        await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    async def build(self, *, scripts=None, texts=None, review=False, recorder=None, step_delay=0.0,
                    speaker=None, tts=True, cursor_scripts=None, unavailable=None):
        async def emit(e):
            self.events.append(e)

        self.config = Config(workspace=self.workspace, review_transcripts=review, tts_enabled=tts)
        self.recorder = recorder or FakeRecorder()
        self.speaker = speaker or FakeSpeaker()
        self.agent = ScriptedAgent(scripts or [], step_delay=step_delay)
        self.cursor = ScriptedAgent(cursor_scripts or [], step_delay=step_delay)
        self.agent.model = self.cursor.model = ""
        self.transcriber = FakeTranscriber(texts)
        unavailable = unavailable or {}
        self.orch = Orchestrator(config=self.config, store=self.store, profile=VoiceProfile(self.store),
                                 recorder=self.recorder, transcriber=self.transcriber, speaker=self.speaker,
                                 agents={"claude": self.agent, "cursor": self.cursor},
                                 engine_available=lambda name: unavailable.get(name), emit=emit)
        await self.orch.start()

    def of(self, type_):
        return [e for e in self.events if e["type"] == type_]

    def states(self):
        return [e["state"] for e in self.of("state")]

    async def settle(self):
        for _ in range(200):
            await asyncio.sleep(0.01)
            task = self.orch._turn_task
            if (task is None or task.done()) and self.orch._speech_q.empty() and not self.orch._speaking:
                await asyncio.sleep(0.02)
                return
        self.fail("orchestrator did not settle")

    # ---------------------------------------------------------------- voice flow
    async def test_ptt_full_turn(self):
        await self.build(scripts=[simple("All tests pass. Nothing else to do.")])
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        await self.settle()
        self.assertEqual(self.states()[:3], ["listening", "transcribing", "thinking"])
        self.assertEqual(self.states()[-1], "idle")
        self.assertEqual(self.agent.calls[0]["prompt"], "run the tests and fix anything failing")
        self.assertEqual(self.speaker.spoken, ["All tests pass.", "Nothing else to do."])
        self.assertEqual(self.orch.claude_session_id, MOCK_SESSION)
        turn = self.store.get_session(self.orch.session_id)["turns"][0]
        self.assertEqual(turn["status"], "ok")

    async def test_stalled_transcription_times_out_and_recovers(self):
        await self.build()
        gate = __import__("threading").Event()
        self.transcriber.transcribe = lambda audio, prompt: (gate.wait(5), "late")[1]
        from barnaby_engine import orchestrator as orch_mod
        with unittest.mock.patch.object(orch_mod, "STT_TIMEOUT_S", 0.2):
            await self.orch.handle(cmd("ptt_start"))
            await self.orch.handle(cmd("ptt_stop"))
        gate.set()
        self.assertEqual(self.states()[-1], "idle")
        errors = self.of("error")
        self.assertEqual(errors[-1]["code"], "stt")
        self.assertIn("stopped responding", errors[-1]["message"])
        self.assertFalse(self.orch._transcribing)
        self.assertEqual(self.agent.calls, [])

    async def test_ptt_refused_while_previous_clip_transcribing(self):
        await self.build(scripts=[simple()])
        gate = __import__("threading").Event()
        self.transcriber.transcribe = lambda audio, prompt: (gate.wait(5), "run lint")[1]
        await self.orch.handle(cmd("ptt_start"))
        stop = asyncio.create_task(self.orch.handle(cmd("ptt_stop")))
        for _ in range(100):
            if self.orch._transcribing:
                break
            await asyncio.sleep(0.01)
        await self.orch.handle(cmd("ptt_start"))
        self.assertFalse(self.recorder.started, "must not open the mic while a clip is still transcribing")
        self.assertIn("Still working out your last recording.", [e["message"] for e in self.of("notice")])
        gate.set()
        await stop
        await self.settle()
        self.assertEqual(self.agent.calls[0]["prompt"], "run lint")

    # ---------------------------------------------------------------- engines
    async def test_spoken_route_switches_engine_and_strips_address(self):
        await self.build(cursor_scripts=[simple("Linted.")])
        await self.orch.handle(cmd("text_submit", text="Cursor, run the linter"))
        await self.settle()
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(self.cursor.calls[0]["prompt"], "run the linter")
        self.assertEqual(self.config.engine, "cursor")
        self.assertEqual(self.of("turn_start")[-1]["engine"], "cursor")
        self.assertEqual(self.store.list_sessions()[0]["engine"], "cursor")
        self.assertIn("Switched to Cursor.", [e["message"] for e in self.of("notice")])

    async def test_bare_route_switches_without_a_turn(self):
        await self.build()
        await self.orch.handle(cmd("text_submit", text="use cursor"))
        await self.settle()
        self.assertEqual(self.config.engine, "cursor")
        self.assertEqual(self.of("turn_start"), [])

    async def test_sessions_are_engine_bound(self):
        await self.build(scripts=[simple()], cursor_scripts=[simple()])
        await self.orch.handle(cmd("text_submit", text="one"))
        await self.settle()
        claude_session = self.orch.session_id
        await self.orch.handle(cmd("set_config", values={"engine": "cursor"}))
        await self.orch.handle(cmd("text_submit", text="two"))
        await self.settle()
        self.assertNotEqual(self.orch.session_id, claude_session)
        self.assertIsNone(self.cursor.calls[0]["session_id"], "a Claude session id must never reach Cursor")
        engines = {s["id"]: s["engine"] for s in self.store.list_sessions()}
        self.assertEqual(engines[claude_session], "claude")
        self.assertEqual(engines[self.orch.session_id], "cursor")
        await self.orch.handle(cmd("load_session", session_id=claude_session))
        self.assertEqual(self.config.engine, "claude")

    async def test_unavailable_engine_refused(self):
        await self.build(unavailable={"cursor": "Cursor's command-line agent was not found."})
        await self.orch.handle(cmd("text_submit", text="have cursor fix it"))
        await self.settle()
        self.assertEqual(self.config.engine, "claude")
        self.assertEqual(self.cursor.calls, [])
        self.assertEqual(self.of("error")[-1]["code"], "engine")
        self.assertEqual([e["name"] for e in (await self.orch.hello())["engines"]], ["claude", "cursor"])

    async def test_cursor_approval_not_grantable_when_rule_would_widen(self):
        denial = Denial("t1", "Bash", {"command": "rm build/*.o"})
        await self.build(cursor_scripts=[simple("Need OK.", denials=[denial])])
        await self.orch.handle(cmd("set_config", values={"engine": "cursor"}))
        await self.orch.handle(cmd("text_submit", text="clean the build"))
        await self.settle()
        self.assertFalse(self.of("approval_required")[-1]["grantable"])

    async def test_model_setting_reaches_live_agents(self):
        await self.build()
        await self.orch.handle(cmd("set_config", values={"claude_model": "opus", "cursor_model": "gpt-5"}))
        self.assertEqual(self.agent.model, "opus")
        self.assertEqual(self.cursor.model, "gpt-5")

    async def test_session_resumed_on_second_turn(self):
        await self.build(scripts=[simple(), simple()])
        await self.orch.handle(cmd("text_submit", text="one"))
        await self.settle()
        await self.orch.handle(cmd("text_submit", text="two"))
        await self.settle()
        self.assertIsNone(self.agent.calls[0]["session_id"])
        self.assertEqual(self.agent.calls[1]["session_id"], MOCK_SESSION)

    async def test_silence_is_ignored(self):
        await self.build(recorder=FakeRecorder(amplitude=0.0))
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(self.of("notice")[0]["message"], "I didn't hear anything.")
        self.assertEqual(self.states()[-1], "idle")

    async def test_too_short_is_ignored(self):
        await self.build(recorder=FakeRecorder(seconds=0.1))
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        self.assertEqual(self.agent.calls, [])

    async def test_vocabulary_prompt_includes_project_terms(self):
        (Path(self.workspace) / "package.json").write_text('{"dependencies": {"zustand": "5"}}')
        await self.build(scripts=[simple()])
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        await self.settle()
        self.assertTrue(self.transcriber.prompts[0].startswith("zustand"))

    # ---------------------------------------------------------------- review + learning
    async def test_review_mode_waits_and_learns_from_correction(self):
        await self.build(scripts=[simple(), simple()], review=True,
                         texts=["add a zoo stand slice"])
        for _ in range(2):
            self.events.clear()
            await self.orch.handle(cmd("ptt_start"))
            await self.orch.handle(cmd("ptt_stop"))
            draft = self.of("transcript")[0]
            self.assertTrue(draft["review"])
            self.assertEqual(self.agent.calls.__len__(), _, "must not send before review")
            await self.orch.handle(cmd("text_submit", text="add a Zustand slice", draft_id=draft["draft_id"]))
            await self.settle()
        # "zoo stand" is already normalized by the built-in lexicon, so craft an unknown term instead
        self.assertEqual(len(self.agent.calls), 2)

    async def test_learning_promotes_custom_term(self):
        await self.build(scripts=[simple(), simple(), simple()], review=True,
                         texts=["open doc ops dashboard"])
        for _ in range(2):
            await self.orch.handle(cmd("ptt_start"))
            await self.orch.handle(cmd("ptt_stop"))
            draft = self.of("transcript")[-1]
            await self.orch.handle(cmd("text_submit", text="open DBOps dashboard", draft_id=draft["draft_id"]))
            await self.settle()
        self.assertEqual(self.of("learned"), [{"type": "learned", "heard": "doc ops", "meant": "DBOps"}])
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        self.assertEqual(self.of("transcript")[-1]["text"], "open DBOps dashboard")

    async def test_discard_draft(self):
        await self.build(review=True)
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        draft = self.of("transcript")[0]
        await self.orch.handle(cmd("discard_draft", draft_id=draft["draft_id"]))
        self.assertEqual(self.orch._drafts, {})
        self.assertEqual(self.agent.calls, [])

    # ---------------------------------------------------------------- tools + approvals
    async def test_tool_events_carry_relative_paths(self):
        f = str(Path(self.workspace) / "src" / "a.ts")
        await self.build(scripts=[[SessionStarted(MOCK_SESSION, "m"), ToolStart("t1", "Edit", {"file_path": f}),
                                   ToolEnd("t1", True, "ok"), TurnResult("ok", MOCK_SESSION, False, None, None)]])
        await self.orch.handle(cmd("text_submit", text="edit"))
        await self.settle()
        start = self.of("tool_start")[0]
        self.assertEqual(start["path"], str(Path("src") / "a.ts"))
        self.assertTrue(self.of("tool_end")[0]["ok"])

    async def test_denial_opens_approval_and_approve_resumes_with_exact_grant(self):
        denial = Denial("t5", "Bash", {"command": "git push origin main", "description": "Push fix"})
        await self.build(scripts=[simple("Need to push.", [denial]), simple("Pushed.")])
        await self.orch.handle(cmd("text_submit", text="push it"))
        await self.settle()
        req = self.of("approval_required")[0]
        self.assertEqual(req["risk"], "high")
        self.assertTrue(req["voice_approvable"])
        self.assertEqual(self.states()[-1], "awaiting_approval")
        await self.orch.handle(cmd("approve", request_id=req["request_id"]))
        await self.settle()
        second = self.agent.calls[1]
        self.assertEqual(second["grant"].allow, ("Bash(git push origin main)",))
        self.assertEqual(second["session_id"], MOCK_SESSION)
        self.assertIn("approved", second["prompt"])
        self.assertEqual(self.states()[-1], "idle")

    async def test_deny_resumes_without_grant(self):
        denial = Denial("t5", "Bash", {"command": "git push origin main"})
        await self.build(scripts=[simple("x", [denial]), simple("ok")])
        await self.orch.handle(cmd("text_submit", text="push"))
        await self.settle()
        await self.orch.handle(cmd("deny", request_id=self.of("approval_required")[0]["request_id"]))
        await self.settle()
        self.assertIsNone(self.agent.calls[1]["grant"])
        self.assertIn("declined", self.agent.calls[1]["prompt"])

    async def test_voice_yes_cannot_approve_critical(self):
        denials = [Denial("a", "Bash", {"command": "git push origin main"}),
                   Denial("b", "Bash", {"command": "git push --force origin main"})]
        await self.build(scripts=[simple("x", denials), simple("ok")], texts=["yes go ahead"])
        await self.orch.handle(cmd("text_submit", text="push"))
        await self.settle()
        reqs = {r["risk"]: r for r in self.of("approval_required")}
        self.assertFalse(reqs["critical"]["voice_approvable"])
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        decided = self.of("approval_decided")
        self.assertEqual(decided, [{"type": "approval_decided", "request_id": reqs["high"]["request_id"], "approved": True}])
        self.assertEqual(len(self.agent.calls), 1, "must still wait for the critical item")
        self.assertEqual(self.states()[-1], "awaiting_approval")
        await self.orch.handle(cmd("deny", request_id=reqs["critical"]["request_id"]))
        await self.settle()
        grant = self.agent.calls[1]["grant"]
        self.assertEqual(grant.allow, ("Bash(git push origin main)",))

    async def test_ungrantable_approval_is_forced_to_deny(self):
        denial = Denial("t", "Bash", {"command": "echo $(whoami)"})
        await self.build(scripts=[simple("x", [denial]), simple("ok")])
        await self.orch.handle(cmd("text_submit", text="go"))
        await self.settle()
        req = self.of("approval_required")[0]
        self.assertFalse(req["grantable"])
        await self.orch.handle(cmd("approve", request_id=req["request_id"]))
        await self.settle()
        self.assertEqual(self.of("approval_decided")[0]["approved"], False)
        self.assertIsNone(self.agent.calls[1]["grant"])

    async def test_new_instruction_supersedes_pending_approvals(self):
        denial = Denial("t", "Bash", {"command": "git push"})
        await self.build(scripts=[simple("x", [denial]), simple("ok")])
        await self.orch.handle(cmd("text_submit", text="push"))
        await self.settle()
        await self.orch.handle(cmd("text_submit", text="never mind, run lint"))
        await self.settle()
        self.assertTrue(self.of("approvals_cleared"))
        self.assertEqual(self.agent.calls[1]["prompt"], "never mind, run lint")
        self.assertIsNone(self.agent.calls[1]["grant"])

    async def test_unknown_request_id_ignored(self):
        await self.build()
        await self.orch.handle(cmd("approve", request_id="nope"))
        self.assertEqual(self.agent.calls, [])

    # ---------------------------------------------------------------- interruption
    async def test_barge_in_stops_speech(self):
        await self.build(scripts=[simple("One. Two. Three.")], speaker=FakeSpeaker(delay=0.3))
        await self.orch.handle(cmd("text_submit", text="go"))
        await asyncio.sleep(0.1)
        await self.orch.handle(cmd("ptt_start"))
        self.assertGreaterEqual(self.speaker.stops, 1)
        self.assertTrue(self.orch._speech_q.empty())
        await self.orch.handle(cmd("cancel"))

    async def test_new_submit_interrupts_running_turn_and_resumes_session(self):
        slow = [SessionStarted(MOCK_SESSION, "m")] + [TextDelta("x ") for _ in range(50)] + \
               [TurnResult("x", MOCK_SESSION, False, None, None)]
        await self.build(scripts=[slow, simple("ok")], step_delay=0.05)
        await self.orch.handle(cmd("text_submit", text="long task"))
        await asyncio.sleep(0.15)
        await self.orch.handle(cmd("text_submit", text="actually stop and do this"))
        await self.settle()
        self.assertEqual(self.agent.cancelled, 1)
        self.assertTrue(self.agent.calls[1]["prompt"].startswith("(The user interrupted"))
        self.assertEqual(self.agent.calls[1]["session_id"], MOCK_SESSION)
        statuses = [e["status"] for e in self.of("turn_end")]
        self.assertEqual(statuses, ["cancelled", "ok"])

    async def test_cancel_returns_to_idle(self):
        slow = [SessionStarted(MOCK_SESSION, "m")] + [TextDelta("x ") for _ in range(50)]
        await self.build(scripts=[slow], step_delay=0.05)
        await self.orch.handle(cmd("text_submit", text="go"))
        await asyncio.sleep(0.1)
        await self.orch.handle(cmd("cancel"))
        self.assertEqual(self.states()[-1], "idle")
        self.assertTrue(self.orch._turn_task is None or self.orch._turn_task.done())

    async def test_hello_replays_component_status_loaded_before_connect(self):
        await self.build()
        for _ in range(100):  # preload finishes with no client attached
            if all(c["status"] == "ready" for c in self.orch._components.values()):
                break
            await asyncio.sleep(0.02)
        hello = await self.orch.hello()
        self.assertEqual(hello["components"], {"stt": {"status": "ready"}, "tts": {"status": "ready"}})

    # ---------------------------------------------------------------- errors / config
    async def test_agent_error_surfaces_and_returns_idle(self):
        def boom(prompt, grant):
            raise AgentError("Claude Code was not found.")
        await self.build(scripts=[boom])
        await self.orch.handle(cmd("text_submit", text="go"))
        await self.settle()
        self.assertEqual(self.of("error")[0]["code"], "agent")
        self.assertEqual(self.states()[-1], "idle")
        self.assertEqual(self.of("turn_end")[-1]["status"], "error")

    async def test_no_workspace(self):
        await self.build()
        self.config.workspace = ""
        await self.orch.handle(cmd("text_submit", text="go"))
        self.assertEqual(self.of("error")[0]["code"], "workspace")
        self.assertEqual(self.agent.calls, [])

    async def test_tts_disabled(self):
        await self.build(scripts=[simple("Hello there.")], tts=False)
        await self.orch.handle(cmd("text_submit", text="go"))
        await self.settle()
        self.assertEqual(self.speaker.spoken, [])

    async def test_set_config_rejects_safe_bash(self):
        await self.build()
        await self.orch.handle(cmd("set_config", values={"safe_bash": ["rm:*"], "tts_speed": 1.2}))
        cfg = self.of("config")[-1]
        self.assertEqual(cfg["rejected"], ["safe_bash"])
        self.assertEqual(cfg["config"]["tts_speed"], 1.2)
        self.assertNotIn("claude_bin", cfg["config"])

    async def test_load_session_restores_claude_session(self):
        await self.build(scripts=[simple()])
        await self.orch.handle(cmd("text_submit", text="go"))
        await self.settle()
        sid = self.orch.session_id
        await self.orch.handle(cmd("new_session"))
        self.assertIsNone(self.orch.claude_session_id)
        await self.orch.handle(cmd("load_session", session_id=sid))
        self.assertEqual(self.orch.claude_session_id, MOCK_SESSION)
        self.assertEqual(self.of("session_loaded")[-1]["session"]["turns"][0]["user_text"], "go")


class SpokenDecisionTest(unittest.TestCase):
    def test_classify(self):
        from barnaby_engine.orchestrator import classify_spoken_decision as c
        for yes in ("yes", "Yes, go ahead.", "yeah do it", "go ahead", "approved", "okay proceed please"):
            self.assertIs(c(yes), True, yes)
        for no in ("no", "Nope.", "don't do that", "cancel it", "deny"):
            self.assertIs(c(no), False, no)
        for other in ("yes and also delete the logs", "no wait, push to staging instead", "yes no",
                      "run the tests", "", "okay"):
            self.assertIsNone(c(other), other)


if __name__ == "__main__":
    unittest.main()
