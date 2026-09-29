"""Task board plumbing: commands and their bounds, tablet permissions, desktop-only
settings, and the tasks table (including interrupted-on-start)."""
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from barnaby_engine.config import Config
from barnaby_engine.protocol import SetConfig, parse_command
from barnaby_engine.server import permit
from barnaby_engine.store import Store
from barnaby_engine.tasks import TaskRecord


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


class TaskCommandsTest(unittest.TestCase):
    def test_valid_commands(self):
        c = cmd("task_create", engine="devin", instruction="add orders", lanes=["engine", "api"], title="Orders")
        self.assertEqual((c.engine, c.lanes, c.title), ("devin", ["engine", "api"], "Orders"))
        self.assertEqual(cmd("task_create", engine="claude", instruction="x").lanes, [])
        self.assertEqual(cmd("task_cancel", task_id="a1b2c3").task_id, "a1b2c3")
        self.assertEqual(cmd("task_follow_up", task_id="a1", text="also add tests").text, "also add tests")
        cmd("task_clear")
        cmd("task_list")

    def test_bounds(self):
        bad = [
            dict(type="task_create", engine="gpt", instruction="x"),
            dict(type="task_create", engine="claude", instruction=""),
            dict(type="task_create", engine="claude", instruction="x" * 20_001),
            dict(type="task_create", engine="claude", instruction="x", lanes=["a"] * 9),
            dict(type="task_create", engine="claude", instruction="x", lanes=["a" * 201]),
            dict(type="task_create", engine="claude", instruction="x", title="t" * 121),
            dict(type="task_create", engine="claude", instruction="x", extra=1),
            dict(type="task_cancel", task_id="../../x"),
            dict(type="task_cancel", task_id="a" * 33),
            dict(type="task_follow_up", task_id="a1", text=""),
        ]
        for frame in bad:
            with self.subTest(frame=frame), self.assertRaises(ValidationError):
                parse_command(json.dumps(frame))


class TaskPermissionsTest(unittest.TestCase):
    def test_tablet_may_run_tasks(self):
        for c in (cmd("task_create", engine="claude", instruction="x"), cmd("task_cancel", task_id="a"),
                  cmd("task_follow_up", task_id="a", text="x"), cmd("task_clear"), cmd("task_list")):
            with self.subTest(c=c.type):
                self.assertIsNone(permit("remote", c))
                self.assertIsNone(permit("desktop", c))

    def test_task_settings_are_pc_only(self):
        for key, value in (("max_parallel_tasks", 6), ("task_timeout_s", 7200),
                           ("task_lanes", {"/p": {"backend": ["engine"]}})):
            with self.subTest(key=key):
                self.assertIsNotNone(permit("remote", SetConfig(type="set_config", values={key: value})))


class TaskConfigTest(unittest.TestCase):
    def test_defaults(self):
        c = Config()
        self.assertEqual((c.max_parallel_tasks, c.task_timeout_s, c.task_lanes), (3, 3600, {}))

    def test_validation(self):
        with tempfile.TemporaryDirectory() as d:
            ok = {"max_parallel_tasks": 6, "task_timeout_s": 300,
                  "task_lanes": {d: {"backend": ["engine"], "front end": ["app/src", "app/public"]}}}
            c = Config()
            self.assertEqual(c.apply_ui_update(ok), [])
            self.assertEqual(c.task_lanes[d]["front end"], ["app/src", "app/public"])
        bad = [("max_parallel_tasks", 0), ("max_parallel_tasks", 7), ("max_parallel_tasks", True),
               ("task_timeout_s", 60), ("task_timeout_s", 20_000),
               ("task_lanes", []), ("task_lanes", {"/p": {"x": "engine"}}), ("task_lanes", {"/p": {"x": ["a*"]}}),
               ("task_lanes", {"/p": {"x": ["../up"]}}), ("task_lanes", {"/p": {"": ["a"]}}),
               ("task_lanes", {"/p": {"n" * 33: ["a"]}}), ("task_lanes", {"/p": {"x": ["a"] * 9}}),
               ("task_lanes", {"/p": {f"n{i}": ["a"] for i in range(21)}}),
               ("task_lanes", {"/p": {"x": ["."]}}), ("task_lanes", {"/p": {"x": ["./"]}}),
               ("task_lanes", {"/p": {"Backend": ["a"], "backend": ["b"]}})]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                self.assertEqual(Config().apply_ui_update({key: value}), [key])


class TaskStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "b.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def rec(self, tid, status="queued", created=1.0, **kw):
        return TaskRecord(id=tid, workspace="/p", engine="claude", title=tid, instruction="do it",
                          lanes=("api", "app"), status=status, created_at=created, **kw)

    def test_save_update_and_list_newest_first(self):
        self.store.save_task(self.rec("a", created=1.0))
        self.store.save_task(self.rec("b", created=2.0))
        a = self.rec("a", status="done", created=1.0, summary="ok", cost_usd=0.5, cli_session_id="s1", turns=2)
        self.store.save_task(a)
        got = self.store.list_tasks()
        self.assertEqual([t.id for t in got], ["b", "a"])
        self.assertEqual(got[1], a)
        self.assertEqual(got[1].lanes, ("api", "app"))

    def test_result_text_is_capped(self):
        self.store.save_task(self.rec("a", result_text="x" * 50_000))
        self.assertEqual(len(self.store.list_tasks()[0].result_text), 20_000)

    def test_active_tasks_become_interrupted_on_start(self):
        for tid, st in (("q", "queued"), ("r", "running"), ("w", "needs_approval"), ("d", "done")):
            self.store.save_task(self.rec(tid, status=st))
        self.assertEqual(self.store.interrupt_active_tasks(), 3)
        got = {t.id: t.status for t in self.store.list_tasks()}
        self.assertEqual(got, {"q": "interrupted", "r": "interrupted", "w": "interrupted", "d": "done"})

    def test_delete(self):
        for tid in ("a", "b", "c"):
            self.store.save_task(self.rec(tid))
        self.store.delete_tasks(["a", "c"])
        self.assertEqual([t.id for t in self.store.list_tasks()], ["b"])


if __name__ == "__main__":
    unittest.main()
