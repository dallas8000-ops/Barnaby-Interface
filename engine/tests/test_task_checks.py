"""Deterministic checks gate task success: Barnaby runs the project's check commands
itself after a local task's turn, retries the agent with the failing output, and only
then marks the task done or failed. Plus the store migration for the new task fields."""
import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from barnaby_engine.checks import (CheckError, CheckSpec, allowed_by, failure_report, problem_with, run_check,
                                   specs_from, split_command)
from barnaby_engine.config import DEFAULT_SAFE_BASH, Config
from barnaby_engine.store import Store
from barnaby_engine.tasks import TaskRecord, pick_next
from tests.test_task_manager import _Base, cmd

PY = f'"{sys.executable}"'
SAFE = ["python:*"]


def script(folder: Path, name: str, body: str) -> str:
    (folder / name).write_text(body, encoding="utf-8")
    return name


class SplitAndAllowTest(unittest.TestCase):
    def test_split_groups_quotes_and_refuses_shell_syntax(self):
        self.assertEqual(split_command('npm test -- --run'), ["npm", "test", "--", "--run"])
        self.assertEqual(split_command('"C:\\Program Files\\py\\python.exe" -m pytest -q'),
                         ["C:\\Program Files\\py\\python.exe", "-m", "pytest", "-q"])
        for bad in ("npm test && rm -rf /", "pytest | tee x", "pytest > out.txt", "echo %PATH%",
                    "pytest; calc", "a `b`", "x\ny", 'pytest "unclosed', "echo $(id)", "py -c 'x'"):
            with self.assertRaises(CheckError, msg=bad):
                split_command(bad)

    def test_matches_the_auto_approve_rules(self):
        safe = list(DEFAULT_SAFE_BASH)
        for ok in ("npm test", "npm test -- --run", "pytest -q", "python -m pytest -q", "npx tsc --noEmit",
                   "cargo test --all", "go vet ./...", "git status"):
            self.assertTrue(allowed_by(split_command(ok), safe), ok)
        for no in ("npm install", "npm run build", "python setup.py", "pytestx", "git push", "rm -rf x",
                   "git statusx", "npx tsc"):
            self.assertFalse(allowed_by(split_command(no), safe), no)

    def test_a_program_path_is_matched_by_its_name(self):
        safe = ["python -m unittest:*"]
        self.assertTrue(allowed_by(split_command(".venv/Scripts/python.exe -m unittest discover"), safe))
        self.assertTrue(allowed_by(split_command('"C:\\Py 3\\python.exe" -m unittest'), safe))
        self.assertFalse(allowed_by(split_command(".venv/Scripts/python.exe -m pip install x"), safe))

    def test_problem_with_explains(self):
        safe = list(DEFAULT_SAFE_BASH)
        self.assertIsNone(problem_with(specs_from([{"run": "pytest -q", "dir": "engine"}]), safe))
        self.assertIn("auto-approve", problem_with(specs_from(["npm run build"]), safe))
        self.assertIn("inside the project", problem_with((CheckSpec("pytest", "../x"),), safe))
        self.assertIn("characters", problem_with(specs_from(["pytest && calc"]), safe))
        self.assertIsNone(problem_with(specs_from(["npm ci"]), None))  # setup commands skip the list

    def test_specs_from_normalizes(self):
        self.assertEqual(specs_from(["pytest", {"run": " npm test ", "dir": "./app/"}, {"run": ""}, 3]),
                         (CheckSpec("pytest", ""), CheckSpec("npm test", "app")))


class RunCheckTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "my project"   # a space, like most Windows paths
        (self.root / "sub dir").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_pass_fail_output_tail_and_folder(self):
        ok = script(self.root / "sub dir", "ok.py", "import os\nprint('cwd', os.path.basename(os.getcwd()))\n")
        r = await run_check(CheckSpec(f"{PY} {ok}", "sub dir"), self.root, safe_bash=SAFE, timeout_s=30)
        self.assertTrue(r.passed, r)
        self.assertEqual(r.exit_code, 0)
        self.assertIn("cwd sub dir", r.output)
        bad = script(self.root, "bad.py", "import sys\nprint('x' * 9000)\nprint('assert 1 == 2 failed', file=sys.stderr)\n"
                                          "sys.exit(3)\n")
        r = await run_check(CheckSpec(f"{PY} {bad}"), self.root, safe_bash=SAFE, timeout_s=30)
        self.assertFalse(r.passed)
        self.assertEqual(r.exit_code, 3)
        self.assertLessEqual(len(r.output), 4000)
        self.assertIn("assert 1 == 2 failed", r.output)  # stderr kept, the tail is what matters
        self.assertGreater(r.duration_s, 0)

    async def test_refused_missing_and_timeout(self):
        r = await run_check(CheckSpec("npm run build"), self.root, safe_bash=SAFE, timeout_s=30)
        self.assertFalse(r.passed)
        self.assertIn("auto-approve", r.error)
        r = await run_check(CheckSpec("python -m unittest", "nope"), self.root, safe_bash=SAFE, timeout_s=30)
        self.assertIn("no folder", r.error)
        r = await run_check(CheckSpec("python-does-not-exist-xyz"), self.root, safe_bash=["python-does-not-exist-xyz"],
                            timeout_s=30)
        self.assertIn("wasn't found", r.error)
        slow = script(self.root, "slow.py", "import time\nprint('started', flush=True)\ntime.sleep(30)\n")
        r = await run_check(CheckSpec(f"{PY} {slow}"), self.root, safe_bash=SAFE, timeout_s=1)
        self.assertFalse(r.passed)
        self.assertTrue(r.timed_out)
        self.assertIsNone(r.exit_code)
        self.assertIn("stopped this check", r.output)

    async def test_cancel_kills_the_check(self):
        slow = script(self.root, "slow.py", "import time\ntime.sleep(30)\n")
        run = asyncio.ensure_future(run_check(CheckSpec(f"{PY} {slow}"), self.root, safe_bash=SAFE, timeout_s=60))
        await asyncio.sleep(0.5)
        run.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(run, 10)

    def test_failure_report_lists_only_failures(self):
        from barnaby_engine.checks import CheckResult
        results = [CheckResult("pytest -q", "engine", 1, 2.5, "E   assert 1 == 2", False),
                   CheckResult("npm test", "app", 0, 1.0, "ok", True)]
        report = failure_report(results)
        self.assertIn("$ pytest -q (in engine/)", report)
        self.assertIn("exit code 1", report)
        self.assertIn("assert 1 == 2", report)
        self.assertNotIn("npm test", report)


class _CheckBase(_Base):
    async def build_checked(self, checks, **config):
        await self.build(safe_bash=SAFE, task_checks={str(self.ws): checks}, **config)

    def flag_check(self, name="check.py"):
        """A check that passes once the file ``fixed`` exists in the project."""
        body = "import os, sys\nprint('checking')\nsys.exit(0 if os.path.exists('fixed') else 1)\n"
        return {"run": f"{PY} {script(self.ws, name, body)}"}

    async def wait_for(self, pred, seconds=15):
        for _ in range(int(seconds / 0.02)):
            if pred():
                return
            await asyncio.sleep(0.02)
        self.fail("timed out waiting")


class TaskChecksTest(_CheckBase):
    async def test_passing_checks_make_the_task_done_and_verified(self):
        (self.ws / "fixed").write_text("")
        await self.build_checked([self.flag_check()])
        await self.create("orders", lanes=["api"])
        self.assertIn("Barnaby runs these checks", self.agent_for("orders").calls[0]["prompt"])
        await self.finish("orders")
        await self.wait_for(lambda: self.task("orders").status == "done")
        t = self.task("orders")
        self.assertEqual((t.verification, t.attempts), ("passed", 1))
        self.assertEqual(t.check_results[0]["exit_code"], 0)
        self.assertIn("checking", t.check_results[0]["output"])
        self.assertIn("verifying", [e["task"]["status"] for e in self.of("task_update")])
        await self.settle(40)
        self.assertIn("checks passed", " ".join(self.speaker.spoken))

    async def test_agent_claim_does_not_count_and_failure_output_is_sent_back(self):
        await self.build_checked([self.flag_check()])
        await self.create("orders", lanes=["api"])  # the agent's reply says "All 12 tests pass"
        await self.finish("orders")
        await self.wait_for(lambda: len(self.calls_for("orders")) == 2)
        t = self.task("orders")
        self.assertEqual((t.status, t.attempts, t.verification), ("running", 1, ""))
        retry = self.calls_for("orders")[1]
        self.assertEqual(retry["session_id"], "11111111-2222-3333-4444-555555555555")
        self.assertIn("check.py", retry["prompt"])
        self.assertIn("exit code 1", retry["prompt"])
        self.assertIn("checking", retry["prompt"])
        (self.ws / "fixed").write_text("")  # the agent "fixes" it on the retry turn
        await self.finish("orders")
        await self.wait_for(lambda: self.task("orders").status == "done")
        self.assertEqual((self.task("orders").verification, self.task("orders").attempts), ("passed", 2))
        self.assertEqual(self.task("orders").turns, 2)

    async def test_exhausted_retries_fail_with_the_check_output(self):
        await self.build_checked([self.flag_check()], task_check_retries=1)
        await self.create("orders", lanes=["api"])
        await self.finish("orders")
        await self.wait_for(lambda: len(self.calls_for("orders")) == 2)
        await self.finish("orders")
        await self.wait_for(lambda: self.task("orders").status == "failed")
        t = self.task("orders")
        self.assertEqual((t.verification, t.attempts, len(self.calls_for("orders"))), ("failed", 2, 2))
        self.assertIn("exited with code 1", t.error)
        self.assertIn("checking", t.error)
        self.assertEqual(self.of("task_done")[-1]["status"], "failed")
        stored = self.store.list_tasks()[0]
        self.assertEqual((stored.verification, stored.attempts), ("failed", 2))
        self.assertEqual(stored.check_results[0]["exit_code"], 1)

    async def test_no_checks_is_unverified_and_devin_is_never_checked_locally(self):
        await self.build()
        await self.create("plain", lanes=["api"])
        await self.finish("plain")
        self.assertEqual((self.task("plain").status, self.task("plain").verification), ("done", "unverified"))
        await self.orch.shutdown()
        self.orch = None
        self._remaining.clear()
        await self.build_checked([self.flag_check()])
        await self.create("remote", engine="devin", lanes=["api"])
        self.assertEqual(self.task("remote").checks, ())
        await self.finish("remote")
        self.assertEqual((self.task("remote").status, self.task("remote").verification), ("done", "unverified"))

    async def test_per_task_override_and_refusal(self):
        (self.ws / "fixed").write_text("")
        await self.build_checked([{"run": "python -m unittest"}])
        await self.create_with_checks("own", [self.flag_check("own.py")])
        self.assertEqual([c.run for c in self.task("own").checks], [self.flag_check("own.py")["run"]])
        await self.create_with_checks("none", [])
        self.assertEqual(self.task("none").checks, ())
        await self.create_with_checks("bad", [{"run": "npm run deploy"}])
        self.assertIn("auto-approve", self.of("error")[-1]["message"])
        self.assertNotIn("bad", [t.title for t in self.orch.tasks.tasks.values()])

    async def create_with_checks(self, title, checks):
        await self.orch.handle(cmd("task_create", engine="claude", instruction="x", lanes=["api"] if title != "none"
                                   else ["app"], title=title, checks=checks))
        await self.settle()

    async def test_verifying_holds_its_lane_and_slot_and_cancel_stops_the_check(self):
        slow_py = script(self.ws, "slow.py", "import time\ntime.sleep(30)\n")
        slow = {"run": f"{PY} {slow_py}"}
        await self.build_checked([slow], max_parallel_tasks=2)
        await self.create("a", lanes=["api"])
        await self.create("same lane", lanes=["api"])
        await self.finish("a")
        await self.wait_for(lambda: self.task("a").status == "verifying")
        self.assertEqual(self.task("same lane").status, "queued")
        await self.create("other lane", lanes=["app"])
        await self.create("third", lanes=["docs"])
        self.assertEqual(self.task("other lane").status, "running")
        self.assertEqual(self.task("third").status, "queued")  # "a" still uses one of the 2 slots
        await self.orch.handle(cmd("task_cancel", task_id=self.task("a").id))
        await self.settle(40)
        self.assertEqual(self.task("a").status, "cancelled")
        self.assertEqual(self.task("same lane").status, "running")
        self.assertNotIn(self.task("a").id, self.orch.tasks._runs)

    async def test_follow_up_gets_fresh_attempts(self):
        await self.build_checked([self.flag_check()], task_check_retries=0)
        await self.create("a", lanes=["api"])
        await self.finish("a")
        await self.wait_for(lambda: self.task("a").status == "failed")
        (self.ws / "fixed").write_text("")
        await self.orch.handle(cmd("task_follow_up", task_id=self.task("a").id, text="try again"))
        await self.settle()
        self.assertEqual((self.task("a").attempts, self.task("a").verification), (0, ""))
        await self.finish("a")
        await self.wait_for(lambda: self.task("a").status == "done")
        self.assertEqual(self.task("a").verification, "passed")

    async def test_restart_during_verifying_is_interrupted(self):
        self.store.save_task(TaskRecord(id="v1", workspace=str(self.ws), engine="claude", title="v",
                                        instruction="x", status="verifying", created_at=1.0))
        await self.build()
        self.assertEqual(self.task("v").status, "interrupted")


class ChecksConfigTest(unittest.TestCase):
    def test_ui_can_set_checks_only_from_the_auto_approve_list(self):
        c = Config()
        self.assertEqual(c.apply_ui_update({"task_checks": {"C:/p": [{"run": "pytest -q", "dir": "engine"}]},
                                            "task_check_retries": 3, "task_worktrees": False,
                                            "task_worktree_parallel": True, "task_check_timeout_s": 120}), [])
        self.assertEqual(c.task_checks["C:/p"][0]["run"], "pytest -q")
        self.assertEqual(c.apply_ui_update({"task_checks": {"C:/p": [{"run": "npm run deploy"}]}}), ["task_checks"])
        self.assertEqual(c.apply_ui_update({"task_checks": {"C:/p": [{"run": "pytest", "dir": "../x"}]}}),
                         ["task_checks"])
        self.assertEqual(c.apply_ui_update({"task_check_retries": 9, "task_worktree_setup": {}}),
                         ["task_check_retries", "task_worktree_setup"])
        self.assertIn("auto-approve", c.checks_problem({"C:/p": [{"run": "curl x"}]}))

    def test_tablet_cannot_choose_checks(self):
        from barnaby_engine.server import permit
        self.assertIsNotNone(permit("remote", cmd("task_create", engine="claude", instruction="x",
                                                  checks=[{"run": "pytest"}])))
        self.assertIsNone(permit("remote", cmd("task_create", engine="claude", instruction="x")))
        self.assertIsNotNone(permit("remote", cmd("task_discard", task_id="abc")))
        self.assertIsNotNone(permit("remote", cmd("task_open_folder", task_id="abc")))
        self.assertIsNotNone(permit("remote", cmd("set_config", values={"task_checks": {}})))


class PickNextVerifyingTest(unittest.TestCase):
    def test_verifying_uses_a_slot_and_holds_lanes(self):
        a = TaskRecord(id="a", workspace="w", engine="claude", title="a", instruction="x", lanes=("api",),
                       status="verifying", created_at=1)
        b = TaskRecord(id="b", workspace="w", engine="claude", title="b", instruction="x", lanes=("api",),
                       created_at=2)
        c = TaskRecord(id="c", workspace="w", engine="claude", title="c", instruction="x", lanes=("app",),
                       created_at=3)
        self.assertEqual([t.id for t in pick_next([a, b, c], 2)], ["c"])
        self.assertEqual([t.id for t in pick_next([a, b, c], 1)], [])


class TaskMigrationTest(unittest.TestCase):
    def test_old_tasks_table_gains_the_new_columns_and_rows_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "db.sqlite"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE tasks (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, engine TEXT NOT NULL,
                  title TEXT NOT NULL, instruction TEXT NOT NULL, lanes_json TEXT NOT NULL DEFAULT '[]',
                  status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
                  summary TEXT NOT NULL DEFAULT '', result_text TEXT NOT NULL DEFAULT '',
                  error TEXT NOT NULL DEFAULT '', cost_usd REAL, cli_session_id TEXT,
                  turns INTEGER NOT NULL DEFAULT 0);
                INSERT INTO tasks (id, workspace, engine, title, instruction, lanes_json, status, created_at, turns)
                  VALUES ('old1', 'C:/p', 'claude', 'old', 'x', '["api"]', 'done', 1.0, 2);
            """)
            conn.close()
            store = Store(path)
            try:
                old = store.list_tasks()[0]
                self.assertEqual((old.id, old.lanes, old.turns, old.status), ("old1", ("api",), 2, "done"))
                self.assertEqual((old.checks, old.attempts, old.check_results, old.verification, old.isolation,
                                  old.worktree, old.branch), ((), 0, [], "", "", "", ""))
                old.checks = (CheckSpec("pytest -q", "engine"),)
                old.attempts, old.verification, old.isolation = 2, "passed", "worktree"
                old.worktree, old.branch, old.notice = "C:/wt/old1", "barnaby/task-old1", "Committed abc"
                old.check_results = [{"command": "pytest -q", "dir": "engine", "exit_code": 0, "duration_s": 1.5,
                                      "output": "ok", "passed": True, "timed_out": False, "error": ""}]
                store.save_task(old)
                back = store.list_tasks()[0]
                self.assertEqual(back.checks, (CheckSpec("pytest -q", "engine"),))
                self.assertEqual((back.attempts, back.verification, back.isolation, back.worktree, back.branch,
                                  back.notice), (2, "passed", "worktree", "C:/wt/old1", "barnaby/task-old1",
                                                 "Committed abc"))
                self.assertEqual(back.check_results[0]["duration_s"], 1.5)
                pub = back.public()
                self.assertEqual(pub["checks"], [{"run": "pytest -q", "dir": "engine"}])
                json.dumps(pub)
            finally:
                store.close()
            Store(path).close()  # migrating twice is a no-op


if __name__ == "__main__":
    unittest.main()
