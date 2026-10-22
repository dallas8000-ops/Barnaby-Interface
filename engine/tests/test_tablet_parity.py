"""PC and tablet share one Barnaby: every project the PC has opened, the full task board,
live activity with a snapshot for late joiners, and one decision per approval or draft
whichever device acts first. Folder choice, keys and pairing stay on the PC."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import Denial, SessionStarted, TextDelta, ToolStart, TurnResult
from barnaby_engine.config import Config
from barnaby_engine.orchestrator import Orchestrator
from barnaby_engine.protocol import parse_command
from barnaby_engine.server import Bridge, ClientInfo, permit
from barnaby_engine.store import Store
from tests.doubles import FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
from tests.helpers import TempDataDir
from tests.test_task_manager import GateAgent

SID = "11111111-2222-3333-4444-555555555555"
TOKEN = "t" * 40


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


def denied(path, text="I need to write a file."):
    return [SessionStarted(SID, None), TextDelta(text),
            TurnResult(text, SID, False, 0.01, 10, (Denial("d1", "Write", {"file_path": path, "content": "x"}),))]


def two_denials(ws):
    text = "I need two files."
    return [SessionStarted(SID, None), TextDelta(text),
            TurnResult(text, SID, False, 0.01, 10,
                       (Denial("d1", "Write", {"file_path": str(Path(ws) / "a.txt"), "content": "x"}),
                        Denial("d2", "Write", {"file_path": str(Path(ws) / "b.txt"), "content": "y"})))]


def ok(text="Done."):
    return [SessionStarted(SID, None), TextDelta(text), TurnResult(text, SID, False, 0.01, 10)]


class HangingAgent:
    """Streams a little, starts a tool, then waits until cancelled: a turn caught mid-flight."""

    def __init__(self):
        self.calls = []
        self._stop = asyncio.Event()

    async def run(self, prompt, *, workspace, session_id, grant, scope=None):
        self.calls.append({"prompt": prompt, "workspace": workspace})
        self._stop = asyncio.Event()
        yield SessionStarted(SID, None)
        yield TextDelta("Looking at the tests. ")
        yield ToolStart("t1", "Bash", {"command": "npm test"})
        await self._stop.wait()

    async def cancel(self):
        self._stop.set()


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ws = root / "shop"
        self.other = root / "blog"
        for p in (self.ws / "api", self.ws / "app", self.other / "posts"):
            p.mkdir(parents=True)
        self.store = Store(root / "db.sqlite")
        self.events = []
        self.task_agents = []
        self.task_script = []  # outcomes for the next task turns ("ok", "deny:<path>")
        self.orch = None

    async def asyncTearDown(self):
        if self.orch:
            await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    async def build(self, scripts=None, agent=None, emit=None, emit_desktop=None, **config):
        async def record(e):
            self.events.append(e)

        def factory(engine, task_id):
            a = GateAgent(engine, task_id, self.task_script)
            self.task_agents.append(a)
            return a

        config.setdefault("review_transcripts", False)
        self.config = Config(workspace=str(self.ws), **config)
        self.orch = Orchestrator(
            config=self.config, store=self.store, profile=VoiceProfile(self.store), recorder=FakeRecorder(),
            transcriber=FakeTranscriber(["send the report"]), speaker=FakeSpeaker(),
            emit=emit or record, emit_desktop=emit_desktop,
            agents={"claude": agent or ScriptedAgent(scripts or []), "cursor": ScriptedAgent([]),
                    "devin": ScriptedAgent([])},
            task_agent_factory=factory)
        await self.orch.start()

    def of(self, t):
        return [e for e in self.events if e["type"] == t]

    async def settle(self, n=40):
        for _ in range(n):
            await asyncio.sleep(0.005)

    def other_known(self):
        """The blog project, opened on the PC earlier (it has a session in history)."""
        self.store.create_session(str(self.other), "claude")


class ProjectsTest(_Base):
    async def test_known_projects_are_current_recent_and_history_and_only_real_folders(self):
        gone = Path(self._tmp.name) / "deleted"
        await self.build(recent_projects=[str(gone)])
        self.other_known()
        names = [p["name"] for p in self.orch.projects()]
        self.assertEqual(names[0], "shop")
        self.assertIn("blog", names)
        self.assertNotIn("deleted", names)
        shop = self.orch.projects()[0]
        self.assertTrue(shop["current"])
        self.assertEqual(shop["folders"], ["api", "app"])

    async def test_hello_carries_the_project_list(self):
        await self.build()
        self.other_known()
        hello = await self.orch.hello()
        self.assertEqual({p["name"] for p in hello["projects"]}, {"shop", "blog"})

    async def test_select_a_known_project_switches_the_conversation(self):
        await self.build()
        self.other_known()
        await self.orch.handle(cmd("select_project", workspace=str(self.other)))
        self.assertEqual(self.config.workspace, str(self.other))
        self.assertTrue(next(p for p in self.of("projects")[-1]["items"] if p["name"] == "blog")["current"])
        self.assertIsNone(self.of("session_loaded")[-1]["session"])

    async def test_a_different_spelling_resolves_to_the_pcs_own_path(self):
        await self.build()
        self.other_known()
        spelled = str(self.other).upper() + os.sep if os.name == "nt" else str(self.other) + os.sep
        await self.orch.handle(cmd("select_project", workspace=spelled))
        self.assertEqual(self.config.workspace, str(self.other))

    async def test_a_folder_the_pc_never_opened_is_refused(self):
        await self.build()
        stranger = Path(self._tmp.name) / "stranger"
        stranger.mkdir()
        await self.orch.handle(cmd("select_project", workspace=str(stranger)))
        self.assertEqual(self.config.workspace, str(self.ws))
        self.assertEqual(self.of("error")[-1]["code"], "not_allowed")
        self.assertIn("PC", self.orch.remote_check(cmd("select_project", workspace=str(stranger))))
        self.assertIn("PC", self.orch.remote_check(cmd("select_project", workspace="C:/Windows/../Windows")))
        self.assertNotIn(str(stranger), self.config.recent_projects)

    async def test_choosing_a_folder_on_the_pc_adds_it_to_the_list(self):
        await self.build()
        fresh = Path(self._tmp.name) / "fresh"
        fresh.mkdir()
        await self.orch.handle(cmd("set_config", values={"workspace": str(fresh)}))
        self.assertEqual(self.config.recent_projects[0], str(fresh))
        self.assertIn("fresh", [p["name"] for p in self.of("projects")[-1]["items"]])
        self.assertEqual(Config.load().recent_projects[0], str(fresh))  # persisted

    async def test_the_project_list_itself_cant_be_set_over_the_socket(self):
        await self.build()
        await self.orch.handle(cmd("set_config", values={"recent_projects": ["C:/evil"]}))
        self.assertNotIn("C:/evil", self.config.recent_projects)
        self.assertIn("recent_projects", self.of("config")[-1]["rejected"])

    async def test_sessions_of_any_known_project_can_be_listed_and_opened(self):
        await self.build()
        mine = self.store.create_session(str(self.ws), "claude")
        theirs = self.store.create_session(str(self.other), "claude")
        await self.orch.handle(cmd("list_sessions", workspace=str(self.other)))
        listed = self.of("sessions")[-1]
        self.assertEqual(listed["workspace"], str(self.other))
        self.assertEqual([s["id"] for s in listed["items"]], [theirs])
        self.assertNotIn(mine, [s["id"] for s in listed["items"]])
        # A tablet may open it: its project is one the PC opened.
        self.assertIsNone(self.orch.remote_check(cmd("load_session", session_id=theirs)))
        await self.orch.handle(cmd("load_session", session_id=theirs))
        self.assertEqual(self.config.workspace, str(self.other))
        self.assertEqual(self.config.recent_projects[0], str(self.other))

    async def test_listing_sessions_of_an_unknown_folder_is_refused(self):
        await self.build()
        await self.orch.handle(cmd("list_sessions", workspace="C:/Users"))
        self.assertEqual(self.of("error")[-1]["code"], "not_allowed")
        self.assertEqual(self.of("sessions"), [])


class TabletPrivilegeTest(_Base):
    """What stays on the PC, enforced by the engine whatever the tablet's UI shows."""

    def test_policy_keeps_folders_keys_mic_and_pairing_on_the_pc(self):
        for c in (cmd("set_config", values={"workspace": "C:/x"}),
                  cmd("set_config", values={"recent_projects": ["C:/x"]}),
                  cmd("set_config", values={"task_lanes": {}}),
                  cmd("set_config", values={"tablet_access": False}),
                  cmd("set_config", values={"continuous_mode_enabled": True}),
                  cmd("set_secret", name="devin", value="sk-123"), cmd("delete_secret", name="devin"),
                  cmd("tablet_pair_start"), cmd("tablet_revoke", device_id="d1"),
                  cmd("ptt_start"), cmd("list_devices"), cmd("claude_login"),
                  cmd("open_in_editor", editor="vscode", path="a.py")):
            self.assertIsNotNone(permit("remote", c), c.type)
        for c in (cmd("list_projects"), cmd("select_project", workspace="C:/x"), cmd("resync"),
                  cmd("list_sessions", workspace="C:/x"),
                  cmd("task_create", engine="claude", instruction="x", workspace="C:/x")):
            self.assertIsNone(permit("remote", c), c.type)

    async def test_snapshot_shows_whether_keys_exist_never_their_value(self):
        await self.build()
        await self.orch.handle(cmd("set_secret", name="devin", value="sk-very-secret-123"))
        hello = await self.orch.hello()
        self.assertEqual(hello["secrets"], {"devin": True})
        self.assertNotIn("sk-very-secret-123", json.dumps(hello, default=str))
        self.assertNotIn("sk-very-secret-123", json.dumps(self.events, default=str))

    async def test_tablet_cannot_start_a_task_in_a_folder_the_pc_never_opened(self):
        await self.build()
        stranger = Path(self._tmp.name) / "stranger"
        (stranger / "src").mkdir(parents=True)
        reason = self.orch.remote_check(cmd("task_create", engine="claude", instruction="x",
                                            workspace=str(stranger)))
        self.assertIn("PC", reason)
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", workspace=str(stranger)))
        self.assertEqual(self.orch.tasks.tasks, {})


class TaskBoardAllProjectsTest(_Base):
    async def test_a_task_can_be_started_in_another_known_project(self):
        await self.build()
        self.other_known()
        await self.orch.handle(cmd("task_create", engine="claude", instruction="fix the post list",
                                   lanes=["posts"], title="posts", workspace=str(self.other)))
        await self.settle()
        t = next(iter(self.orch.tasks.tasks.values()))
        self.assertEqual((t.workspace, t.lanes), (str(self.other), ("posts",)))
        self.assertEqual(self.task_agents[-1].calls[0]["workspace"], str(self.other))
        self.assertEqual(self.config.workspace, str(self.ws))  # the conversation stays where it was
        blog = next(p for p in self.orch.projects() if p["name"] == "blog")
        self.assertEqual(blog["active_tasks"], 1)

    async def test_lanes_are_checked_against_the_chosen_project(self):
        await self.build()
        self.other_known()
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["api"],
                                   workspace=str(self.other)))  # api/ is in shop, not blog
        self.assertEqual(self.orch.tasks.tasks, {})
        self.assertEqual(self.of("error")[-1]["code"], "tasks")

    async def test_tablet_can_follow_up_and_approve_tasks_of_other_known_projects(self):
        await self.build()
        self.other_known()
        self.task_script.append("deny:outside.txt")
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["posts"],
                                   title="p", workspace=str(self.other)))
        await self.settle()
        tid = next(iter(self.orch.tasks.tasks))
        self.task_agents[-1].gate.set()
        await self.settle()
        rid = self.of("task_approval_required")[-1]["request_id"]
        self.assertIsNone(self.orch.remote_check(cmd("approve", request_id=rid)))
        self.assertIsNone(self.orch.remote_check(cmd("task_follow_up", task_id=tid, text="more")))
        hello = await self.orch.hello()
        self.assertEqual([c["request_id"] for c in hello["tasks"]["approvals"]], [rid])

    async def test_task_records_pass_extra_fields_through_untouched(self):
        # Verified tasks (branch, check results) add fields to TaskRecord.public(); the
        # snapshot and updates carry whatever is there.
        await self.build()
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["api"], title="a"))
        t = next(iter(self.orch.tasks.tasks.values()))
        public = t.public()
        self.assertEqual(set(public) - {"pending_prompt"},
                         set(next(iter((await self.orch.hello())["tasks"]["items"]))))


class SnapshotTest(_Base):
    async def test_a_client_joining_mid_turn_sees_the_conversation_and_the_turn_so_far(self):
        agent = HangingAgent()
        await self.build(scripts=None, agent=agent)
        await self.orch.handle(cmd("text_submit", text="run the tests"))
        await self.settle()
        live = (await self.orch.hello())["live"]
        self.assertEqual(live["turn"]["text"], "run the tests")
        self.assertEqual(live["turn"]["assistant"], "Looking at the tests. ")
        self.assertEqual(live["turn"]["tools"][0]["summary"], "npm test")
        self.assertEqual(live["session"]["id"], self.orch.session_id)
        await self.orch.handle(cmd("cancel"))
        live = (await self.orch.hello())["live"]
        self.assertIsNone(live["turn"])
        self.assertEqual(live["session"]["turns"][-1]["status"], "cancelled")  # now in history

    async def test_pending_approvals_survive_a_reconnect_until_decided(self):
        await self.build(scripts=[denied(str(self.ws / "x.txt")), ok()])
        await self.orch.handle(cmd("text_submit", text="write x"))
        await self.settle()
        rid = self.of("approval_required")[-1]["request_id"]
        live = (await self.orch.hello())["live"]
        self.assertEqual([a["request_id"] for a in live["approvals"]], [rid])
        self.assertEqual(live["session"]["turns"][-1]["status"], "needs_approval")
        await self.orch.handle(cmd("deny", request_id=rid))
        await self.settle()
        self.assertEqual((await self.orch.hello())["live"]["approvals"], [])

    async def test_chain_progress_and_review_draft_are_in_the_snapshot(self):
        await self.build(agent=HangingAgent(), review_transcripts=True)
        await self.orch.handle(cmd("text_submit", text="run the tests, then fix the lint"))
        await self.settle()
        chain = (await self.orch.hello())["live"]["chain"]
        self.assertEqual((chain["index"], len(chain["steps"])), (0, 2))
        await self.orch.handle(cmd("cancel"))
        self.assertIsNone((await self.orch.hello())["live"]["chain"])

    async def test_recent_notifications_and_task_activity_are_replayed(self):
        await self.build()
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["api"], title="a"))
        await self.settle()
        tid = next(iter(self.orch.tasks.tasks))
        live = (await self.orch.hello())["live"]
        self.assertEqual(live["task_live"][tid]["tool"], "Read")
        self.task_agents[-1].gate.set()
        await self.settle()
        live = (await self.orch.hello())["live"]
        self.assertNotIn(tid, live["task_live"])
        done = [r for r in live["recent"] if r["type"] == "task_done"]
        self.assertEqual(done[-1]["task_id"], tid)
        self.assertGreaterEqual(done[-1]["age_s"], 0)
        self.assertEqual(done[-1]["nid"], self.of("task_done")[-1]["nid"])  # same id as the live event

    async def test_review_draft_is_restored_for_a_late_device(self):
        await self.build(review_transcripts=True)
        from tests.test_tablet_orchestrator import clip
        await self.orch.handle(cmd("audio_clip", pcm_b64=clip(1.0)))
        await self.settle()
        draft = (await self.orch.hello())["live"]["draft"]
        self.assertEqual(draft["text"], self.of("transcript")[-1]["text"])


class ConflictTest(_Base):
    async def test_same_approval_decided_on_two_devices_at_once_counts_once(self):
        await self.build(scripts=[denied(str(self.ws / "x.txt")), ok(), ok()])
        await self.orch.handle(cmd("text_submit", text="write x"))
        await self.settle()
        rid = self.of("approval_required")[-1]["request_id"]
        await asyncio.gather(self.orch.handle(cmd("approve", request_id=rid)),
                             self.orch.handle(cmd("deny", request_id=rid)))
        await self.settle()
        self.assertEqual(len(self.of("approval_decided")), 1)
        self.assertEqual(self.of("error")[-1]["code"], "already_decided")
        follow_ups = [e for e in self.of("turn_start") if e["system"]]
        self.assertEqual(len(follow_ups), 1)

    async def test_a_decided_item_cant_be_flipped_while_its_group_is_open(self):
        await self.build(scripts=[two_denials(str(self.ws)), ok()])
        await self.orch.handle(cmd("text_submit", text="write both"))
        await self.settle()
        a, b = [e["request_id"] for e in self.of("approval_required")]
        await self.orch.handle(cmd("deny", request_id=a))
        await self.orch.handle(cmd("approve", request_id=a))  # the other device, a moment later
        self.assertEqual([e["approved"] for e in self.of("approval_decided")], [False])
        self.assertEqual(self.of("error")[-1]["code"], "already_decided")
        await self.orch.handle(cmd("deny", request_id=b))
        await self.settle()
        self.assertEqual(len(self.of("approval_decided")), 2)

    async def test_a_late_decision_gets_a_fresh_snapshot(self):
        await self.build(scripts=[denied(str(self.ws / "x.txt")), ok()])
        await self.orch.handle(cmd("text_submit", text="write x"))
        await self.settle()
        rid = self.of("approval_required")[-1]["request_id"]
        await self.orch.handle(cmd("deny", request_id=rid))
        await self.settle()
        hellos = len(self.of("hello"))
        await self.orch.handle(cmd("approve", request_id=rid))
        self.assertEqual(len(self.of("hello")), hellos + 1)
        self.assertEqual(self.of("hello")[-1]["live"]["approvals"], [])

    async def test_task_approval_decided_once_across_devices(self):
        await self.build()
        self.task_script.append("deny:outside.txt")
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["api"], title="a"))
        await self.settle()
        self.task_agents[-1].gate.set()
        await self.settle()
        rid = self.of("task_approval_required")[-1]["request_id"]
        await asyncio.gather(self.orch.handle(cmd("deny", request_id=rid)),
                             self.orch.handle(cmd("approve", request_id=rid)))
        await self.settle()
        self.assertEqual([e["approved"] for e in self.of("task_approval_decided")], [False])
        self.assertEqual(self.of("error")[-1]["code"], "already_decided")

    async def test_a_review_draft_is_sent_once(self):
        await self.build(scripts=[ok(), ok()], review_transcripts=True)
        from tests.test_tablet_orchestrator import clip
        await self.orch.handle(cmd("audio_clip", pcm_b64=clip(1.0)))
        await self.settle()
        draft_id = self.of("transcript")[-1]["draft_id"]
        await asyncio.gather(self.orch.handle(cmd("text_submit", text="send the report", draft_id=draft_id)),
                             self.orch.handle(cmd("text_submit", text="send the report!", draft_id=draft_id)))
        await self.settle()
        self.assertEqual(len(self.of("turn_start")), 1)
        self.assertEqual(self.of("draft_closed")[-1]["draft_id"], draft_id)
        self.assertIn("already sent", self.of("notice")[-1]["message"])

    async def test_discarding_on_one_device_closes_the_draft_everywhere(self):
        await self.build(review_transcripts=True)
        from tests.test_tablet_orchestrator import clip
        await self.orch.handle(cmd("audio_clip", pcm_b64=clip(1.0)))
        await self.settle()
        draft_id = self.of("transcript")[-1]["draft_id"]
        await self.orch.handle(cmd("discard_draft", draft_id=draft_id))
        self.assertEqual(self.of("draft_closed")[-1]["draft_id"], draft_id)
        await self.orch.handle(cmd("text_submit", text="send the report", draft_id=draft_id))
        self.assertEqual(self.of("turn_start"), [])


class HubParityTest(_Base):
    """The real orchestrator behind the real hub: a PC window and a tablet side by side."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bridge = Bridge(TOKEN)
        await self.build(scripts=[denied(str(self.ws / "x.txt")), ok(), ok()],
                         emit=self.bridge.emit, emit_desktop=self.bridge.emit_desktop)
        self.bridge.orchestrator = self.orch
        self.local = await self.bridge.serve(port=0)
        self.local_url = f"ws://127.0.0.1:{self.local.sockets[0].getsockname()[1]}"

        async def remote_handler(ws):
            await self.bridge.run_client(ws, ClientInfo(role="remote", device_id="tab"))
        self.remote = await serve(remote_handler, "127.0.0.1", 0)
        self.remote_url = f"ws://127.0.0.1:{self.remote.sockets[0].getsockname()[1]}/tab"

    async def asyncTearDown(self):
        for s in (self.local, self.remote):
            s.close()
            await s.wait_closed()
        await super().asyncTearDown()

    async def desktop(self):
        ws = await connect(self.local_url)
        await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        return ws, json.loads(await ws.recv())

    async def tablet(self):
        ws = await connect(self.remote_url)
        return ws, json.loads(await ws.recv())

    @staticmethod
    async def until(ws, kind, limit=50):
        for _ in range(limit):
            e = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if e["type"] == kind:
                return e
        raise AssertionError(f"no {kind}")

    async def test_tablet_joining_late_gets_the_pending_approval_then_its_decision(self):
        pc, _ = await self.desktop()
        await pc.send(json.dumps({"type": "text_submit", "text": "write x"}))
        card = await self.until(pc, "approval_required")
        tab, hello = await self.tablet()  # first frame: the snapshot, with the card in it
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["role"], "remote")
        self.assertEqual([a["request_id"] for a in hello["live"]["approvals"]], [card["request_id"]])
        await pc.send(json.dumps({"type": "deny", "request_id": card["request_id"]}))
        decided = await self.until(tab, "approval_decided")  # then the delta, on the tablet too
        self.assertEqual(decided["request_id"], card["request_id"])
        await pc.close()
        await tab.close()

    async def test_losing_device_alone_is_told_and_resynced(self):
        pc, _ = await self.desktop()
        tab, _ = await self.tablet()
        await pc.send(json.dumps({"type": "text_submit", "text": "write x"}))
        card = await self.until(tab, "approval_required")
        await pc.send(json.dumps({"type": "deny", "request_id": card["request_id"]}))
        await self.until(tab, "approval_decided")
        await tab.send(json.dumps({"type": "approve", "request_id": card["request_id"]}))
        err = await self.until(tab, "error")
        self.assertEqual(err["code"], "already_decided")
        fresh = await self.until(tab, "hello")
        self.assertEqual(fresh["live"]["approvals"], [])
        # The PC gets neither the refusal nor the resent snapshot.
        await pc.send(json.dumps({"type": "ping"}))
        e = await self.until(pc, "pong")
        self.assertEqual(e["type"], "pong")
        await pc.close()
        await tab.close()

    async def test_resync_answers_only_the_asking_device(self):
        pc, _ = await self.desktop()
        tab, _ = await self.tablet()
        await tab.send(json.dumps({"type": "resync"}))
        hello = await self.until(tab, "hello")
        self.assertEqual(hello["role"], "remote")
        await pc.send(json.dumps({"type": "ping"}))
        first = json.loads(await asyncio.wait_for(pc.recv(), 5))
        self.assertEqual(first["type"], "pong")
        await pc.close()
        await tab.close()

    async def test_project_switch_on_the_tablet_shows_on_the_pc(self):
        self.other_known()
        pc, _ = await self.desktop()
        tab, hello = await self.tablet()
        self.assertIn(str(self.other), [p["path"] for p in hello["projects"]])
        await tab.send(json.dumps({"type": "select_project", "workspace": str(self.other)}))
        config = await self.until(pc, "config")
        self.assertEqual(config["config"]["workspace"], str(self.other))
        await tab.send(json.dumps({"type": "set_config", "values": {"workspace": str(self.ws / "api")}}))
        err = await self.until(tab, "error")
        self.assertEqual(err["code"], "not_allowed")
        self.assertEqual(self.config.workspace, str(self.other))
        await pc.close()
        await tab.close()


if __name__ == "__main__":
    unittest.main()
