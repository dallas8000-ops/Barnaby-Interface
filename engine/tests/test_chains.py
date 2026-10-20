"""Chained instructions and templates, end to end through the orchestrator:
sequencing, approval pause/resume, and every way a chain can stop."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import AgentError, Denial, SessionStarted, TextDelta, TurnResult
from barnaby_engine.config import Config
from tests.doubles import MOCK_SESSION, FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
from barnaby_engine.orchestrator import Orchestrator
from barnaby_engine.protocol import parse_command
from barnaby_engine.store import Store
from barnaby_engine.templates import match_template, template_steps, TemplateManager
from barnaby_engine.workflows import WorkflowEngine
from tests.helpers import TempDataDir


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


def simple(text="Done.", denials=()):
    return [SessionStarted(MOCK_SESSION, "m"), TextDelta(text),
            TurnResult(text, MOCK_SESSION, False, 0.01, 10, denials=tuple(denials))]


PUSH = Denial("t1", "Bash", {"command": "git push origin main"})


class ChainTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "db.sqlite")
        self.events = []
        self.orch = None

    async def asyncTearDown(self):
        if self.orch:
            await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    async def build(self, scripts, step_delay=0.0, workflows=None, **config):
        async def emit(e):
            self.events.append(e)
        self.config = Config(workspace=self._tmp.name, review_transcripts=False, **config)
        self.speaker = FakeSpeaker()
        self.agent = ScriptedAgent(scripts, step_delay=step_delay)
        self.orch = Orchestrator(config=self.config, store=self.store, profile=VoiceProfile(self.store),
                                 recorder=FakeRecorder(), transcriber=FakeTranscriber(), speaker=self.speaker,
                                 agent=self.agent, emit=emit, workflows=workflows)
        await self.orch.start()

    def of(self, t):
        return [e for e in self.events if e["type"] == t]

    async def settle(self):
        for _ in range(400):
            await asyncio.sleep(0.01)
            task = self.orch._turn_task
            if (task is None or task.done()) and self.orch._speech_q.empty() and not self.orch._speaking:
                await asyncio.sleep(0.03)
                task = self.orch._turn_task
                if task is None or task.done():
                    return
        self.fail("did not settle")

    def prompts(self):
        return [c["prompt"] for c in self.agent.calls]

    # ------------------------------------------------------------ sequencing
    async def test_steps_run_in_order_in_one_session(self):
        await self.build([simple("Tests ran."), simple("Fixed.")])
        await self.orch.handle(cmd("text_submit", text="run the tests, then fix anything failing"))
        await self.settle()
        p = self.prompts()
        self.assertEqual(len(p), 2)
        self.assertIn("Step 1 of 2", p[0])
        self.assertTrue(p[0].endswith("Run the tests"))
        self.assertIn("Final step 2 of 2", p[1])
        self.assertTrue(p[1].endswith("Fix anything failing"))
        self.assertIsNone(self.agent.calls[0]["session_id"])
        self.assertEqual(self.agent.calls[1]["session_id"], MOCK_SESSION, "step 2 must resume step 1's session")
        self.assertEqual(self.of("command_chain_start")[0]["steps"], ["Run the tests", "Fix anything failing"])
        self.assertEqual([e["index"] for e in self.of("command_chain_step")], [0, 1])
        end = self.of("command_chain_end")[-1]
        self.assertEqual((end["success"], end["reason"], end["completed"], end["total"]), (True, "completed", 2, 2))
        self.assertEqual([e["text"] for e in self.of("turn_start")], ["Run the tests", "Fix anything failing"])
        self.assertIn("All 2 steps are done.", self.speaker.spoken)

    async def test_failed_step_stops_the_chain(self):
        def boom(prompt, grant):
            raise AgentError("broke")
        await self.build([boom, simple("never")])
        await self.orch.handle(cmd("text_submit", text="build it then deploy it"))
        await self.settle()
        self.assertEqual(len(self.agent.calls), 1)
        end = self.of("command_chain_end")[-1]
        self.assertEqual((end["success"], end["reason"], end["completed"]), (False, "failed", 0))
        self.assertIn("I stopped after step 1 of 2, because that step didn't finish.", self.speaker.spoken)

    # ------------------------------------------------------------ approvals
    async def test_approval_pauses_then_approve_continues(self):
        await self.build([simple("Need OK.", denials=[PUSH]), simple("Pushed."), simple("Tagged.")])
        await self.orch.handle(cmd("text_submit", text="push the fix, then tag a release"))
        await self.settle()
        self.assertEqual(len(self.agent.calls), 1, "chain must wait for the decision")
        self.assertEqual(self.of("command_chain_end"), [])
        rid = self.of("approval_required")[-1]["request_id"]
        await self.orch.handle(cmd("approve", request_id=rid))
        await self.settle()
        self.assertEqual(len(self.agent.calls), 3)
        self.assertIsNotNone(self.agent.calls[1]["grant"])  # resumed step 1 with the exact grant
        self.assertIn("Final step 2 of 2", self.prompts()[2])
        self.assertEqual(self.of("command_chain_end")[-1]["reason"], "completed")

    async def test_decline_finishes_the_step_then_stops(self):
        await self.build([simple("Need OK.", denials=[PUSH]), simple("Okay, I won't push."), simple("never")])
        await self.orch.handle(cmd("text_submit", text="push the fix, then tag a release"))
        await self.settle()
        rid = self.of("approval_required")[-1]["request_id"]
        await self.orch.handle(cmd("deny", request_id=rid))
        await self.settle()
        self.assertEqual(len(self.agent.calls), 2, "step 2 must not run after a decline")
        self.assertIn("declined", self.prompts()[1])
        end = self.of("command_chain_end")[-1]
        self.assertEqual((end["reason"], end["completed"]), ("declined", 0))

    # ------------------------------------------------------------ interruption
    async def test_cancel_aborts_the_chain(self):
        await self.build([simple("A"), simple("B")], step_delay=0.3)
        await self.orch.handle(cmd("text_submit", text="lint the code then format it"))
        await asyncio.sleep(0.1)
        await self.orch.handle(cmd("cancel"))
        await self.settle()
        self.assertEqual(len(self.agent.calls), 1)
        self.assertEqual(self.of("command_chain_end")[-1]["reason"], "cancelled")

    async def test_new_instruction_interrupts_the_chain(self):
        await self.build([simple("A"), simple("Other.")], step_delay=0.3)
        await self.orch.handle(cmd("text_submit", text="lint the code then format it"))
        await asyncio.sleep(0.1)
        await self.orch.handle(cmd("text_submit", text="actually just explain the linter config"))
        await self.settle()
        self.assertEqual(self.of("command_chain_end")[-1]["reason"], "interrupted")
        self.assertEqual(len(self.agent.calls), 2)
        self.assertIn("interrupted", self.prompts()[1])
        self.assertTrue(self.prompts()[1].endswith("actually just explain the linter config"))

    async def test_step_limit_refuses(self):
        await self.build([], command_chain_max_depth=2)
        await self.orch.handle(cmd("text_submit", text="lint then test then build"))
        await self.settle()
        self.assertEqual(self.agent.calls, [])
        self.assertIn("the limit is 2", self.of("notice")[-1]["message"])

    async def test_chaining_can_be_disabled(self):
        await self.build([simple()], command_chain_enabled=False)
        await self.orch.handle(cmd("text_submit", text="lint then test"))
        await self.settle()
        self.assertEqual(self.prompts(), ["lint then test"])
        self.assertEqual(self.of("command_chain_start"), [])

    # ------------------------------------------------------------ templates
    async def test_template_by_voice_with_context(self):
        await self.build([simple(f"s{i}") for i in range(4)])
        await self.orch.handle(cmd("text_submit", text="run the TDD template for the cart total"))
        await self.settle()
        p = self.prompts()
        self.assertEqual(len(p), 4)
        for prompt in p:
            self.assertIn('"TDD Cycle" template', prompt)
            self.assertIn("applied to: the cart total", prompt)
        self.assertTrue(p[0].endswith("Write a failing test for the feature"))
        start = self.of("command_chain_start")[0]
        self.assertEqual(start["name"], "TDD Cycle")
        self.assertEqual(self.of("command_chain_end")[-1]["completed"], 4)

    async def test_run_template_command_and_unknown(self):
        await self.build([simple(f"s{i}") for i in range(5)])
        await self.orch.handle(cmd("run_template", template_id="nope"))
        self.assertEqual(self.of("error")[-1]["code"], "template")
        await self.orch.handle(cmd("run_template", template_id="debugging", context="the login crash"))
        await self.settle()
        self.assertEqual(len(self.agent.calls), 5)
        self.assertIn("applied to: the login crash", self.prompts()[0])

    async def test_saved_workflows_listed_and_runnable(self):
        engine = WorkflowEngine(workflows_dir=str(Path(self._tmp.name) / "wf"))
        wf = engine.create_workflow("Release prep", [{"instruction": "Bump the version"},
                                                     {"instruction": "Update the changelog", "extra": "ignored"}])
        (Path(self._tmp.name) / "wf" / "broken.json").write_text("{ not json")
        await self.build([simple(), simple()], workflows=engine)
        hello = await self.orch.hello()
        names = {t["name"]: t for t in hello["templates"]}
        self.assertEqual(names["Release prep"]["step_count"], 2)
        self.assertTrue(names["Release prep"]["custom"])
        self.assertIn("TDD Cycle", names)
        await self.orch.handle(cmd("text_submit", text="run the release prep workflow"))
        await self.settle()
        self.assertEqual([p.split(") ", 1)[1] for p in self.prompts()], ["Bump the version", "Update the changelog"])
        self.assertIn(wf.id, [t["id"] for t in hello["templates"]])


class MatchTemplateTest(unittest.TestCase):
    def test_matching(self):
        T = TemplateManager().list_templates()
        cases = {
            "run the TDD template for the cart total": ("tdd-cycle", "the cart total"),
            "start the debugging workflow": ("debugging", None),
            "run the debug workflow": ("debugging", None),
            "do the code review checklist on the auth module": ("code-review", "the auth module"),
            "use the tdd cycle template": ("tdd-cycle", None),
        }
        for text, (tid, ctx) in cases.items():
            with self.subTest(text=text):
                tpl, got_ctx = match_template(text, T)
                self.assertEqual((tpl["id"], got_ctx), (tid, ctx))

    def test_ordinary_instructions_not_hijacked(self):
        T = TemplateManager().list_templates()
        for text in ("update the email template", "run the deploy template", "fix the tdd tests",
                     "make the workflow file shorter"):
            with self.subTest(text=text):
                self.assertIsNone(match_template(text, T))

    def test_template_steps_tolerates_junk(self):
        self.assertEqual(template_steps({"steps": [{"instruction": " a  b "}, {"x": 1}, "c", 5, {"instruction": ""}]}),
                         ["a b", "c"])


if __name__ == "__main__":
    unittest.main()
