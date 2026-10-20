"""Background tasks end to end inside the orchestrator: parallel runs, lane blocking,
per-task approvals, completion updates, follow-ups, cancel, restart, and the
conversation's busy-lane guard."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import AgentError, Denial, SessionStarted, TextDelta, ToolStart, TurnResult
from barnaby_engine.config import Config
from tests.doubles import FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
from barnaby_engine.orchestrator import Orchestrator
from barnaby_engine.protocol import parse_command
from barnaby_engine.safety import Grant, Scope
from barnaby_engine.store import Store
from barnaby_engine.tasks import TaskRecord
from tests.helpers import TempDataDir

SID = "11111111-2222-3333-4444-555555555555"


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


class GateAgent:
    """A task agent the test releases by hand. ``script`` is a list of turn outcomes:
    "ok", "deny:<path>", "error", or "hang"."""

    def __init__(self, engine, task_id, script):
        self.engine, self.task_id = engine, task_id
        self.script = script  # shared across a task's turns (a new agent is built per turn)
        self.calls = []
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.cancelled = False

    async def run(self, prompt, *, workspace, session_id, grant, scope=None):
        self.calls.append({"prompt": prompt, "session_id": session_id, "grant": grant, "scope": scope,
                           "workspace": workspace})
        outcome = self.script.pop(0) if self.script else "ok"
        self.started.set()
        yield SessionStarted(SID, None)
        yield ToolStart("t1", "Read", {"file_path": str(Path(workspace) / "api" / "a.py")})
        if outcome == "hang":
            await asyncio.Event().wait()
        # Like CliAgent: a cancel ends the stream cleanly, with no result and no error.
        stop = asyncio.ensure_future(self.stopped.wait())
        gate = asyncio.ensure_future(self.gate.wait())
        await asyncio.wait({stop, gate}, return_when=asyncio.FIRST_COMPLETED)
        stop.cancel(), gate.cancel()
        if self.stopped.is_set():
            return
        self.gate = asyncio.Event()
        if outcome == "error":
            raise AgentError("the CLI crashed")
        if outcome.startswith("deny:"):
            path = str(Path(workspace) / outcome[5:])
            yield TurnResult("I need to edit a file outside my folders.", SID, False, 0.1, 10,
                             (Denial("d1", "Write", {"file_path": path, "content": "x"}),))
            return
        yield TextDelta("Added the orders endpoint and its tests. All 12 tests pass.")
        yield TurnResult("Added the orders endpoint and its tests. All 12 tests pass.", SID, False, 0.25, 1000)

    async def cancel(self):
        self.cancelled = True
        self.stopped.set()
        await asyncio.sleep(0.02)  # taskkill takes a moment; the stream ends before cancel() returns


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "proj"
        for d in ("api", "app", "engine", "docs"):
            (self.ws / d).mkdir(parents=True)
        self.store = Store(Path(self._tmp.name) / "db.sqlite")
        self.events = []
        self.agents = []
        self.scripts = {}          # title -> outcomes
        self.unavailable = {}
        self._remaining = {}
        self.orch = None

    async def asyncTearDown(self):
        if self.orch:
            await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    async def build(self, convo_scripts=None, **config):
        async def emit(e):
            self.events.append(e)

        def factory(engine, task_id):
            title = next((t.title for t in self.orch.tasks.tasks.values() if t.id == task_id), "")
            remaining = self._remaining.setdefault(task_id, list(self.scripts.get(title, ["ok"])))
            agent = GateAgent(engine, task_id, remaining)
            self.agents.append(agent)
            return agent

        config.setdefault("review_transcripts", False)
        self.config = Config(workspace=str(self.ws), **config)
        self.speaker = FakeSpeaker()
        self.convo = ScriptedAgent(convo_scripts or [])
        self.orch = Orchestrator(
            config=self.config, store=self.store, profile=VoiceProfile(self.store), recorder=FakeRecorder(),
            transcriber=FakeTranscriber([]), speaker=self.speaker, emit=emit,
            agents={"claude": self.convo, "cursor": ScriptedAgent([]), "devin": ScriptedAgent([])},
            engine_available=lambda name: self.unavailable.get(name),
            task_agent_factory=factory)
        await self.orch.start()

    def of(self, t):
        return [e for e in self.events if e["type"] == t]

    def task(self, title):
        return next(t for t in self.orch.tasks.tasks.values() if t.title == title)

    def agent_for(self, title):
        tid = self.task(title).id
        return [a for a in self.agents if a.task_id == tid][-1]

    def calls_for(self, title):
        tid = self.task(title).id
        return [c for a in self.agents if a.task_id == tid for c in a.calls]

    async def settle(self, n=30):
        for _ in range(n):
            await asyncio.sleep(0.005)

    async def create(self, title, engine="claude", lanes=(), instruction="do the thing"):
        await self.orch.handle(cmd("task_create", engine=engine, instruction=instruction, lanes=list(lanes),
                                   title=title))
        await self.settle()

    async def finish(self, title):
        self.agent_for(title).gate.set()
        await self.settle()


class ParallelTest(_Base):
    async def test_disjoint_lanes_run_at_the_same_time_with_their_own_agents(self):
        await self.build()
        await self.create("orders api", lanes=["api"])
        await self.create("login page", engine="cursor", lanes=["app"])
        self.assertEqual(self.task("orders api").status, "running")
        self.assertEqual(self.task("login page").status, "running")
        a, b = self.agent_for("orders api"), self.agent_for("login page")
        self.assertIsNot(a, b)
        self.assertEqual((a.engine, b.engine), ("claude", "cursor"))
        self.assertEqual(a.calls[0]["scope"], Scope(allow_lanes=("api",)))
        self.assertEqual(a.calls[0]["workspace"], str(self.ws))
        self.assertIn("api/", a.calls[0]["prompt"])
        self.assertIn("do the thing", a.calls[0]["prompt"])

    async def test_overlapping_lane_waits_then_runs(self):
        await self.build()
        await self.create("first", lanes=["engine"])
        await self.create("second", lanes=["engine"])
        await self.create("third", lanes=["docs"])
        self.assertEqual([self.task(x).status for x in ("first", "second", "third")],
                         ["running", "queued", "running"])
        await self.finish("first")
        self.assertEqual(self.task("first").status, "done")
        self.assertEqual(self.task("second").status, "running")

    async def test_parallel_limit(self):
        await self.build(max_parallel_tasks=2)
        for name, lane in (("a", "api"), ("b", "app"), ("c", "docs")):
            await self.create(name, lanes=[lane])
        self.assertEqual([self.task(x).status for x in "abc"], ["running", "running", "queued"])
        await self.finish("b")
        self.assertEqual(self.task("c").status, "running")

    async def test_devin_task_gets_lanes_as_guidance_and_holds_no_local_lane(self):
        await self.build()
        await self.create("remote", engine="devin", lanes=["api"])
        await self.create("local", lanes=["api"])
        self.assertEqual(self.task("remote").status, "running")
        self.assertEqual(self.task("local").status, "running")
        self.assertIn("api/", self.agent_for("remote").calls[0]["prompt"])

    async def test_whole_project_task_waits_for_everything_local(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.create("everything")
        self.assertEqual(self.task("everything").status, "queued")
        await self.finish("a")
        self.assertEqual(self.task("everything").status, "running")
        self.assertIsNone(self.agent_for("everything").calls[0]["scope"])


class CompletionTest(_Base):
    async def test_done_updates_board_speaks_and_persists(self):
        await self.build()
        await self.create("orders endpoint", engine="devin", lanes=["api"])
        await self.finish("orders endpoint")
        t = self.task("orders endpoint")
        self.assertEqual(t.status, "done")
        self.assertEqual(t.summary, "Added the orders endpoint and its tests. All 12 tests pass.")
        self.assertEqual((t.cost_usd, t.cli_session_id, t.turns), (0.25, SID, 1))
        self.assertIsNotNone(t.finished_at)
        done = self.of("task_done")
        self.assertEqual(done[-1]["task_id"], t.id)
        self.assertEqual(done[-1]["status"], "done")
        await self.settle(40)
        spoken = " ".join(self.speaker.spoken)
        self.assertIn("Devin finished orders endpoint.", spoken)
        self.assertIn("Added the orders endpoint", spoken)
        self.assertEqual(self.store.list_tasks()[0].status, "done")
        self.assertTrue(any(e["type"] == "task_text" and e["task_id"] == t.id for e in self.events))

    async def test_failure_is_reported(self):
        await self.build()
        self.scripts["broken"] = ["error"]
        await self.create("broken", lanes=["api"])
        await self.finish("broken")
        t = self.task("broken")
        self.assertEqual(t.status, "failed")
        self.assertIn("the CLI crashed", t.error)
        await self.settle(40)
        self.assertIn("failed", " ".join(self.speaker.spoken))

    async def test_timeout_fails_the_task(self):
        await self.build()
        self.config.task_timeout_s = 0.05
        self.scripts["slow"] = ["hang"]
        await self.create("slow", lanes=["api"])
        await asyncio.sleep(0.2)
        await self.settle()
        t = self.task("slow")
        self.assertEqual(t.status, "failed")
        self.assertIn("longer than", t.error)


class TaskApprovalTest(_Base):
    async def test_out_of_lane_edit_becomes_a_task_card_and_approval_resumes_with_one_file(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/notes.md", "ok"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        t = self.task("t")
        self.assertEqual(t.status, "needs_approval")
        card = self.of("task_approval_required")[-1]
        self.assertEqual((card["task_id"], card["tool_name"], card["grantable"]), (t.id, "Write", True))
        self.assertFalse(card["voice_approvable"])
        self.assertEqual(self.of("approval_required"), [])  # never mixed into the conversation's cards
        await self.orch.handle(cmd("approve", request_id=card["request_id"]))
        await self.settle()
        call = self.calls_for("t")[-1]
        self.assertEqual(call["grant"], Grant(allow=("Edit(./docs/notes.md)",)))
        self.assertEqual(call["session_id"], SID)
        self.assertIn("approved", call["prompt"])
        await self.finish("t")
        self.assertEqual(self.task("t").status, "done")
        self.assertEqual(self.task("t").turns, 2)

    async def test_deny_resumes_with_the_decline(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/notes.md", "ok"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        card = self.of("task_approval_required")[-1]
        await self.orch.handle(cmd("deny", request_id=card["request_id"]))
        await self.settle()
        call = self.calls_for("t")[-1]
        self.assertIsNone(call["grant"])
        self.assertIn("declined", call["prompt"])

    async def test_edit_outside_the_project_cannot_be_approved(self):
        await self.build()
        self.scripts["t"] = ["deny:../elsewhere.txt"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        self.assertFalse(self.of("task_approval_required")[-1]["grantable"])

    async def test_waiting_task_keeps_its_lane_but_frees_its_slot(self):
        await self.build(max_parallel_tasks=1)
        self.scripts["w"] = ["deny:docs/x.md"]
        await self.create("w", lanes=["api"])
        await self.create("same lane", lanes=["api"])
        await self.create("other lane", lanes=["app"])
        await self.finish("w")
        self.assertEqual(self.task("w").status, "needs_approval")
        self.assertEqual(self.task("same lane").status, "queued")
        self.assertEqual(self.task("other lane").status, "running")

    async def test_spoken_yes_does_not_approve_a_background_task(self):
        await self.build(convo_scripts=[[TurnResult("ok", SID, False, None, None)]])
        self.scripts["t"] = ["deny:docs/x.md"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        await self.orch.handle(cmd("text_submit", text="yes"))
        await self.settle()
        self.assertEqual(self.task("t").status, "needs_approval")

    async def test_speaks_once_when_a_task_needs_approval(self):
        await self.build()
        self.scripts["orders"] = ["deny:docs/x.md"]
        await self.create("orders", lanes=["api"])
        await self.finish("orders")
        await self.settle(40)
        self.assertIn("Claude task orders needs your OK.", self.speaker.spoken)


class ControlTest(_Base):
    async def test_cancel_stops_only_that_task(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.create("b", lanes=["app"])
        await self.orch.handle(cmd("task_cancel", task_id=self.task("a").id))
        await self.settle()
        self.assertEqual(self.task("a").status, "cancelled")
        self.assertTrue(self.agent_for("a").cancelled)
        self.assertEqual(self.task("b").status, "running")

    async def test_conversation_stop_leaves_tasks_running(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.orch.handle(cmd("cancel"))
        await self.orch.handle(cmd("new_session"))
        await self.settle()
        self.assertEqual(self.task("a").status, "running")

    async def test_follow_up_resumes_the_same_agent_session(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.finish("a")
        await self.orch.handle(cmd("task_follow_up", task_id=self.task("a").id, text="also add pagination"))
        await self.settle()
        self.assertEqual(self.task("a").status, "running")
        call = self.calls_for("a")[-1]
        self.assertEqual(call["session_id"], SID)
        self.assertIn("also add pagination", call["prompt"])
        self.assertEqual(call["scope"], Scope(allow_lanes=("api",)))

    async def test_follow_up_on_a_running_task_is_refused(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.orch.handle(cmd("task_follow_up", task_id=self.task("a").id, text="more"))
        self.assertIn("still", self.of("error")[-1]["message"])

    async def test_clear_finished(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.create("b", lanes=["app"])
        await self.finish("a")
        await self.orch.handle(cmd("task_clear"))
        self.assertEqual([t.title for t in self.orch.tasks.tasks.values()], ["b"])
        self.assertEqual([t.title for t in self.store.list_tasks()], ["b"])
        self.assertEqual([t["title"] for t in self.of("tasks")[-1]["items"]], ["b"])

    async def test_shutdown_interrupts_running_tasks(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.orch.shutdown()
        self.orch = None
        self.assertEqual(self.store.list_tasks()[0].status, "interrupted")


class ValidationTest(_Base):
    async def test_bad_lane_unavailable_engine_and_queue_cap(self):
        await self.build()
        await self.create("x", lanes=["../outside"])
        self.assertIn("outside the project", self.of("error")[-1]["message"])
        self.unavailable["devin"] = "Add your Devin API key in Settings first."
        await self.create("y", engine="devin")
        self.assertIn("Devin API key", self.of("error")[-1]["message"])
        self.assertEqual(self.orch.tasks.tasks, {})
        self.config.max_parallel_tasks = 1
        for i in range(20):
            await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["api"], title=f"t{i}"))
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["api"], title="t20"))
        self.assertIn("20", self.of("error")[-1]["message"])

    async def test_needs_a_project_folder(self):
        await self.build()
        self.config.workspace = ""
        await self.create("x")
        self.assertIn("project folder", self.of("error")[-1]["message"])

    async def test_default_title_is_the_first_line(self):
        await self.build()
        await self.orch.handle(cmd("task_create", engine="claude", instruction="Add caching to the API\nmore", lanes=[]))
        await self.settle()
        self.assertEqual(next(iter(self.orch.tasks.tasks.values())).title, "Add caching to the API")


class RestartTest(_Base):
    async def test_active_tasks_become_interrupted_and_show_in_hello(self):
        self.store.save_task(TaskRecord(id="old1", workspace=str(self.ws), engine="claude", title="old",
                                        instruction="x", lanes=("api",), status="running", created_at=1.0))
        await self.build()
        hello = await self.orch.hello()
        self.assertEqual(hello["tasks"]["items"][0]["status"], "interrupted")
        self.assertEqual(hello["tasks"]["folders"], ["api", "app", "docs", "engine"])
        await self.orch.handle(cmd("task_follow_up", task_id="old1", text="carry on"))
        await self.settle()
        self.assertEqual(self.task("old").status, "running")


class VoiceTaskTest(_Base):
    async def test_spoken_task_command_uses_saved_lanes(self):
        await self.build(task_lanes={str(self.ws): {"backend": ["engine"]}})
        await self.orch.handle(cmd("text_submit", text="task for Devin in backend: add the orders endpoint"))
        await self.settle()
        t = next(iter(self.orch.tasks.tasks.values()))
        self.assertEqual((t.engine, t.lanes, t.instruction), ("devin", ("engine",), "add the orders endpoint"))
        self.assertEqual(self.convo.calls, [])  # not sent to the conversation
        await self.settle(40)
        self.assertTrue(any("Devin" in s and "orders endpoint" in s for s in self.speaker.spoken))

    async def test_unknown_spoken_lane_is_explained(self):
        await self.build()
        await self.orch.handle(cmd("text_submit", text="task for claude in billing: fix it"))
        await self.settle()
        self.assertIn("billing", self.of("error")[-1]["message"])
        self.assertEqual(self.orch.tasks.tasks, {})


class ConversationGuardTest(_Base):
    async def test_conversation_asks_before_editing_busy_lanes(self):
        await self.build(convo_scripts=[[TurnResult("ok", SID, False, None, None)],
                                        [TurnResult("ok", SID, False, None, None)]])
        await self.create("a", lanes=["api"])
        await self.orch.handle(cmd("text_submit", text="rename the helper"))
        await self.settle()
        self.assertEqual(self.convo.calls[-1]["scope"], Scope(ask_lanes=("api",)))
        await self.finish("a")
        await self.orch.handle(cmd("text_submit", text="and the other one"))
        await self.settle()
        self.assertIsNone(self.convo.calls[-1]["scope"])

    async def test_whole_project_task_makes_the_conversation_ask_everywhere(self):
        await self.build(convo_scripts=[[TurnResult("ok", SID, False, None, None)]])
        await self.create("all")
        await self.orch.handle(cmd("text_submit", text="rename the helper"))
        await self.settle()
        self.assertEqual(self.convo.calls[-1]["scope"], Scope(ask_lanes=("",)))

    async def test_approving_a_busy_lane_edit_opens_that_lane_for_one_turn(self):
        denial = Denial("d", "Write", {"file_path": str(self.ws / "api" / "a.py"), "content": "x"})
        await self.build(convo_scripts=[[TurnResult("need ok", SID, False, None, None, (denial,))],
                                        [TurnResult("done", SID, False, None, None)]])
        await self.create("a", lanes=["api"])
        await self.orch.handle(cmd("text_submit", text="edit api/a.py"))
        await self.settle()
        card = self.of("approval_required")[-1]
        self.assertIn("background task", card["summary"])
        await self.orch.handle(cmd("approve", request_id=card["request_id"]))
        await self.settle()
        self.assertEqual(self.convo.calls[-1]["grant"].open_lanes, ("api",))


class ReviewRegressionTest(_Base):
    """Findings from the independent engine review (2026-09-29)."""

    async def test_cancel_is_not_reported_as_a_failure(self):
        await self.build()
        await self.create("long job", lanes=["api"])
        await self.orch.handle(cmd("task_cancel", task_id=self.task("long job").id))
        await self.settle(40)
        self.assertEqual(self.task("long job").status, "cancelled")
        self.assertEqual([e["status"] for e in self.of("task_done")], [])
        self.assertFalse(any("failed" in s for s in self.speaker.spoken))
        self.assertEqual(self.orch.tasks._runs, {})

    async def test_shutdown_marks_interrupted_without_speaking_a_failure(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.orch.shutdown()
        self.orch = None
        self.assertEqual(self.store.list_tasks()[0].status, "interrupted")
        self.assertEqual(self.of("task_done"), [])

    async def test_concurrent_pumps_start_each_task_once(self):
        await self.build(max_parallel_tasks=2)
        for name, lane in (("r1", "api"), ("r2", "app"), ("q1", "docs"), ("q2", "engine")):
            await self.create(name, lanes=[lane])
        original = self.orch.tasks._emit

        async def yielding_emit(e):  # a real broadcast awaits sockets; let other tasks run
            await asyncio.sleep(0.01)
            await original(e)
        self.orch.tasks._emit = yielding_emit
        self.agent_for("r1").gate.set()
        self.agent_for("r2").gate.set()
        await self.settle(60)
        for name in ("q1", "q2"):
            tid = self.task(name).id
            self.assertEqual(len([a for a in self.agents if a.task_id == tid]), 1, name)
        self.assertLessEqual(sum(1 for t in self.orch.tasks.tasks.values() if t.status == "running"), 2)

    async def test_cancel_while_cards_are_being_sent_stays_cancelled(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/x.md"]
        await self.create("t", lanes=["api"])
        slow = asyncio.Event()
        original = self.orch.tasks._emit

        async def emit(e):
            if e["type"] == "task_approval_required":
                slow.set()
                await asyncio.sleep(0.05)
            await original(e)
        self.orch.tasks._emit = emit
        self.agent_for("t").gate.set()
        await slow.wait()
        await self.orch.handle(cmd("task_cancel", task_id=self.task("t").id))
        await self.settle(40)
        self.assertEqual(self.task("t").status, "cancelled")
        self.assertEqual(self.orch.tasks.busy_lanes(str(self.ws)), ())
        self.assertEqual(self.orch.tasks._groups, {})

    async def test_two_clients_deciding_at_once(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/x.md", "ok"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        rid = self.of("task_approval_required")[-1]["request_id"]
        results = await asyncio.gather(self.orch.tasks.decide(rid, True), self.orch.tasks.decide(rid, True),
                                       return_exceptions=True)
        self.assertFalse(any(isinstance(r, Exception) for r in results), results)
        await self.settle()
        self.assertEqual(len(self.calls_for("t")), 2)

    async def test_blank_instruction_is_refused(self):
        await self.build()
        await self.orch.handle(cmd("task_create", engine="claude", instruction="   \n  ", lanes=[]))
        self.assertIn("instruction", self.of("error")[-1]["message"].lower())
        self.assertEqual(self.orch.tasks.tasks, {})

    async def test_follow_up_without_a_session_keeps_the_original_instruction(self):
        self.store.save_task(TaskRecord(id="old1", workspace=str(self.ws), engine="claude", title="old",
                                        instruction="build the orders api", lanes=("api",), status="queued",
                                        created_at=1.0))
        await self.build()
        await self.orch.handle(cmd("task_follow_up", task_id="old1", text="and add pagination"))
        await self.settle()
        prompt = self.calls_for("old")[-1]["prompt"]
        self.assertIn("build the orders api", prompt)
        self.assertIn("and add pagination", prompt)

    async def test_tablet_cannot_follow_up_a_task_from_another_project(self):
        other = Path(self._tmp.name) / "other"
        (other / "api").mkdir(parents=True)
        self.store.save_task(TaskRecord(id="x1", workspace=str(other), engine="claude", title="x",
                                        instruction="x", lanes=("api",), status="done", created_at=1.0))
        await self.build()
        reason = self.orch.remote_check(cmd("task_follow_up", task_id="x1", text="go"))
        self.assertIn("another project", reason or "")
        self.assertIsNone(self.orch.remote_check(cmd("task_cancel", task_id="x1")))

    async def test_task_card_describes_an_in_project_edit_honestly(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/x.md"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        summary = self.of("task_approval_required")[-1]["summary"]
        self.assertNotIn("outside the project", summary)
        self.assertIn("docs/x.md", summary)
        self.assertIn("outside this task's folders", summary)

    async def test_titles_are_redacted_when_spoken(self):
        await self.build()
        await self.create("use token=abcd1234secret", lanes=["api"])
        await self.finish("use token=abcd1234secret")
        await self.settle(40)
        self.assertFalse(any("abcd1234secret" in s for s in self.speaker.spoken), self.speaker.spoken)

    async def test_streamed_text_is_throttled_but_complete(self):
        class Chatty(GateAgent):
            async def run(self, prompt, *, workspace, session_id, grant, scope=None):
                self.calls.append({"prompt": prompt})
                for i in range(200):
                    yield TextDelta(f"w{i} ")
                yield TurnResult("done.", SID, False, None, None)
        await self.build()
        self.orch.tasks._factory = lambda engine, tid: Chatty(engine, tid, [])
        await self.create("chatty", lanes=["api"])
        await self.settle(40)
        texts = [e["delta"] for e in self.events if e["type"] == "task_text"]
        self.assertLess(len(texts), 20)
        self.assertEqual("".join(texts), "".join(f"w{i} " for i in range(200)))

    async def test_clearing_forgets_per_task_files(self):
        await self.build()
        forgotten = []
        self.orch.tasks._on_forget = forgotten.append
        await self.create("a", lanes=["api"])
        tid = self.task("a").id
        await self.finish("a")
        await self.orch.handle(cmd("task_clear"))
        self.assertEqual(forgotten, [tid])


class FullReviewRegressionTest(_Base):
    """Findings from the independent full-feature review (2026-09-29)."""

    async def test_snapshot_carries_undecided_cards_for_clients_that_reconnect(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/x.md"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        rid = self.of("task_approval_required")[-1]["request_id"]
        hello = await self.orch.hello()
        self.assertEqual([c["request_id"] for c in hello["tasks"]["approvals"]], [rid])
        await self.orch.handle(cmd("deny", request_id=rid))
        await self.settle()
        self.assertEqual(self.orch.tasks.snapshot()["approvals"], [])

    async def test_tablet_cannot_approve_another_projects_task(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/x.md"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        rid = self.of("task_approval_required")[-1]["request_id"]
        self.assertIsNone(self.orch.remote_check(cmd("approve", request_id=rid)))
        other = Path(self._tmp.name) / "other"
        other.mkdir()
        self.config.workspace = str(other)
        self.assertIn("another project", self.orch.remote_check(cmd("approve", request_id=rid)) or "")
        self.assertIn("another project", self.orch.remote_check(cmd("deny", request_id=rid)) or "")

    async def test_follow_up_restarts_the_clock(self):
        await self.build()
        await self.create("a", lanes=["api"])
        await self.finish("a")
        first = self.task("a").started_at
        self.orch.tasks._clock = lambda: first + 86_400
        await self.orch.handle(cmd("task_follow_up", task_id=self.task("a").id, text="more"))
        await self.settle()
        self.assertEqual(self.task("a").started_at, first + 86_400)


class SettingsWarningTest(_Base):
    async def test_project_edit_allow_rules_are_flagged(self):
        (self.ws / ".claude").mkdir()
        (self.ws / ".claude" / "settings.local.json").write_text(json.dumps(
            {"permissions": {"allow": ["Edit(./**)", "mcp__claude_ai_Railway__list-projects"]}}))
        await self.build()
        await self.create("a", lanes=["api"])
        t = self.task("a")
        self.assertIn("Edit(./**)", t.warning)
        self.assertIn("settings.local.json", t.warning)

    async def test_no_warning_for_devin_or_clean_settings(self):
        await self.build()
        await self.create("a", lanes=["api"])
        self.assertEqual(self.task("a").warning, "")


class TaskFactoryTest(unittest.TestCase):
    def test_fresh_agent_per_turn_and_private_cursor_config(self):
        from barnaby_engine.__main__ import make_task_factory
        from barnaby_engine.agent import ClaudeCodeAgent
        from barnaby_engine.cursor_agent import CursorAgent
        from barnaby_engine.devin_agent import DevinAgent
        with tempfile.TemporaryDirectory() as d:
            config = Config(task_timeout_s=1234, claude_model="opus")
            f = make_task_factory(config, lambda: None, Path(d))
            a, b = f("claude", "t1"), f("claude", "t1")
            self.assertIsInstance(a, ClaudeCodeAgent)
            self.assertIsNot(a, b)
            self.assertEqual((a.timeout_s, a.model), (1234, "opus"))
            c1, c2 = f("cursor", "t1"), f("cursor", "t2")
            self.assertIsInstance(c1, CursorAgent)
            self.assertNotEqual(c1.config_dir, c2.config_dir)
            self.assertEqual(c1.config_dir, Path(d) / "cursor-tasks" / "t1")
            dv = f("devin", "t3")
            self.assertIsInstance(dv, DevinAgent)
            self.assertEqual(dv.watch_timeout_s, 1234.0)


if __name__ == "__main__":
    unittest.main()
