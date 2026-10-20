"""Folder lanes inside the agents: Claude permission settings for tasks and for the
conversation, exact-file grants for approved out-of-lane edits, and Cursor's guard."""
import inspect
import tempfile
import unittest
from pathlib import Path

from barnaby_engine.agent import ClaudeCodeAgent, build_argv, build_settings
from barnaby_engine.cursor_agent import CursorGuard
from barnaby_engine.devin_agent import DevinAgent
from tests.doubles import ScriptedAgent
from barnaby_engine.safety import Grant, Scope, grant_for_task, lane_of
from tests.helpers import make_fake_claude, record_of


class ClaudeSettingsTest(unittest.TestCase):
    def test_no_scope_is_unchanged(self):
        s = build_settings(["git status"], None)
        self.assertEqual(s, {"permissions": {"allow": ["Bash(git status)"], "defaultMode": "acceptEdits"}})
        self.assertEqual(build_settings(["git status"], None, Scope()), s)

    def test_task_lanes_switch_to_default_mode_with_lane_allows(self):
        s = build_settings(["git status"], None, Scope(allow_lanes=("engine", "app/src")))["permissions"]
        self.assertEqual(s["defaultMode"], "default")
        self.assertEqual(s["allow"], ["Bash(git status)", "Edit(./engine/**)", "Edit(./app/src/**)"])
        self.assertNotIn("ask", s)

    def test_task_grant_adds_exact_file(self):
        s = build_settings([], Grant(allow=("Edit(./docs/notes.md)",)), Scope(allow_lanes=("api",)))
        self.assertIn("Edit(./docs/notes.md)", s["permissions"]["allow"])

    def test_conversation_asks_for_busy_lanes_except_opened_ones(self):
        s = build_settings([], None, Scope(ask_lanes=("api", "app")))["permissions"]
        self.assertEqual(s["defaultMode"], "acceptEdits")
        self.assertEqual(s["ask"], ["Edit(./api/**)", "Edit(./app/**)"])
        s = build_settings([], Grant(open_lanes=("api",)), Scope(ask_lanes=("api", "app")))["permissions"]
        self.assertEqual(s["ask"], ["Edit(./app/**)"])
        s = build_settings([], Grant(open_lanes=("api", "app")), Scope(ask_lanes=("api", "app")))["permissions"]
        self.assertNotIn("ask", s)

    def test_whole_project_busy_lane_asks_for_every_edit(self):
        s = build_settings([], None, Scope(ask_lanes=("",)))["permissions"]
        self.assertEqual(s["ask"], ["Edit(./**)"])

    def test_argv_permission_mode(self):
        self.assertIn("acceptEdits", build_argv("c", settings_path="s", prompt_path="p", session_id=None, model=""))
        argv = build_argv("c", settings_path="s", prompt_path="p", session_id=None, model="", permission_mode="default")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "default")
        with self.assertRaises(ValueError):
            build_argv("c", settings_path="s", prompt_path="p", session_id=None, model="",
                       permission_mode="bypassPermissions")


class ClaudeProcessScopeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ws = self.tmp / "proj"
        (self.ws / "api").mkdir(parents=True)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def run_with(self, scope):
        agent = ClaudeCodeAgent(claude_bin=str(make_fake_claude(self.tmp)), scratch_dir=self.tmp / "run")
        return [e async for e in agent.run("hi", workspace=str(self.ws), session_id=None, grant=None, scope=scope)]

    async def test_task_scope_reaches_the_cli(self):
        await self.run_with(Scope(allow_lanes=("api",)))
        rec = record_of(self.tmp)
        self.assertEqual(rec["argv"][rec["argv"].index("--permission-mode") + 1], "default")
        self.assertIn("Edit(./api/**)", rec["settings"]["permissions"]["allow"])

    async def test_no_scope_keeps_accept_edits(self):
        await self.run_with(None)
        rec = record_of(self.tmp)
        self.assertEqual(rec["argv"][rec["argv"].index("--permission-mode") + 1], "acceptEdits")


class TaskGrantTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "proj"
        (self.ws / "docs").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_edit_inside_project_is_one_exact_file(self):
        for tool in ("Edit", "Write", "MultiEdit"):
            g = grant_for_task(tool, {"file_path": str(self.ws / "docs" / "notes.md")}, str(self.ws))
            self.assertEqual(g, Grant(allow=("Edit(./docs/notes.md)",)), tool)
        g = grant_for_task("NotebookEdit", {"notebook_path": str(self.ws / "a.ipynb")}, str(self.ws))
        self.assertEqual(g, Grant(allow=("Edit(./a.ipynb)",)))

    def test_edits_that_cannot_be_granted_narrowly(self):
        outside = str(Path(self._tmp.name) / "elsewhere.txt")
        for inp in ({"file_path": outside}, {"file_path": "docs/relative.md"}, {"file_path": ""}, {},
                    {"file_path": str(self.ws / "docs" / "a*.md")}, {"file_path": str(self.ws / "d(x).md")},
                    {"file_path": str(self.ws / "docs" / ".." / ".." / "x")}, {"file_path": str(self.ws)}):
            with self.subTest(inp=inp):
                self.assertIsNone(grant_for_task("Write", inp, str(self.ws)))

    def test_other_tools_use_the_normal_rules(self):
        self.assertEqual(grant_for_task("Bash", {"command": "npm install"}, str(self.ws)),
                         Grant(allow=("Bash(npm install)",)))
        self.assertEqual(grant_for_task("WebFetch", {"url": "https://docs.x.io/a"}, str(self.ws)),
                         Grant(allow=("WebFetch(domain:docs.x.io)",)))

    def test_lane_of(self):
        f = str(self.ws / "docs" / "sub" / "x.md")
        self.assertEqual(lane_of(str(self.ws), f, ("api", "docs")), "docs")
        self.assertIsNone(lane_of(str(self.ws), f, ("api",)))
        self.assertIsNone(lane_of(str(self.ws), str(Path(self._tmp.name) / "x"), ("docs",)))
        self.assertEqual(lane_of(str(self.ws), "docs/y.md", ("docs",)), "docs")  # relative to project
        self.assertEqual(lane_of(str(self.ws), f, ("",)), "")  # whole project


class CursorLaneGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "proj"
        for d in ("api", "app"):
            (self.ws / d).mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_task_edit_outside_lane_is_stopped(self):
        g = CursorGuard(str(self.ws), [], None, Scope(allow_lanes=("api",)))
        self.assertIsNone(g.check("Edit", {"file_path": str(self.ws / "api" / "a.py")}))
        self.assertIsNone(g.check("Read", {"file_path": str(self.ws / "app" / "b.ts")}))
        for tool in ("Edit", "Write", "Delete"):
            msg = g.check(tool, {"file_path": str(self.ws / "app" / "b.ts")})
            self.assertIn("outside its folders", msg or "", tool)
            self.assertIn("api", msg)

    def test_conversation_edit_in_busy_lane_is_stopped_unless_opened(self):
        g = CursorGuard(str(self.ws), [], None, Scope(ask_lanes=("api",)))
        self.assertIn("task", g.check("Write", {"file_path": str(self.ws / "api" / "a.py")}) or "")
        self.assertIsNone(g.check("Write", {"file_path": str(self.ws / "app" / "a.py")}))
        g = CursorGuard(str(self.ws), [], Grant(open_lanes=("api",)), Scope(ask_lanes=("api",)))
        self.assertIsNone(g.check("Write", {"file_path": str(self.ws / "api" / "a.py")}))

    def test_whole_project_task_stops_every_conversation_edit(self):
        g = CursorGuard(str(self.ws), [], None, Scope(ask_lanes=("",)))
        self.assertIn("background task", g.check("Write", {"file_path": str(self.ws / "app" / "a.py")}) or "")

    def test_outside_project_message_still_wins(self):
        g = CursorGuard(str(self.ws), [], None, Scope(allow_lanes=("api",)))
        self.assertIn("outside your project", g.check("Write", {"file_path": "/etc/passwd"}) or "")


class OtherAgentsAcceptScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_scripted_agent_records_scope(self):
        a = ScriptedAgent(scripts=[[]])
        scope = Scope(allow_lanes=("api",))
        _ = [e async for e in a.run("x", workspace="/w", session_id=None, grant=None, scope=scope)]
        self.assertEqual(a.calls[0]["scope"], scope)

    def test_devin_run_takes_scope(self):
        self.assertIn("scope", inspect.signature(DevinAgent.run).parameters)


if __name__ == "__main__":
    unittest.main()
