"""A git worktree per local task: created from HEAD on its own branch, the agent and
its checks run there, follow-ups and restarts reuse it, Discard and Clear clean it up
(Clear only when nothing would be lost), and a non-git project falls back to the
shared folder with a warning. Uses real temporary git repositories."""
import asyncio
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from barnaby_engine import worktrees
from barnaby_engine.agent import SessionStarted, TextDelta, TurnResult
from barnaby_engine.tasks import TaskRecord, pick_next
from tests.test_task_manager import SID, GateAgent, _Base, cmd

HAVE_GIT = shutil.which("git") is not None


def git(cwd, *args):
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {out.stdout}{out.stderr}")
    return out.stdout.strip()


def _force_rmtree(path):
    def onerror(func, p, _exc):  # git object files are read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=onerror)


class WritingAgent(GateAgent):
    """Like GateAgent, but writes ``api/<task>.py`` in the folder it was given before finishing."""

    async def run(self, prompt, *, workspace, session_id, grant, scope=None):
        async for event in super().run(prompt, workspace=workspace, session_id=session_id, grant=grant, scope=scope):
            if isinstance(event, TurnResult) and not event.denials:
                (Path(workspace) / "api" / f"{self.task_id}.py").write_text(f"# turn {len(self.calls)}\n")
            yield event


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class _GitBase(_Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        (self.ws / "api" / "a.py").write_text("x = 1\n")
        (self.ws / "app" / "b.ts").write_text("export {}\n")
        (self.ws / "README.md").write_text("hi\n")
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "config", "user.email", "test@example.com")
        git(self.ws, "config", "user.name", "Test")
        git(self.ws, "config", "commit.gpgsign", "false")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "init")

    async def asyncTearDown(self):
        if self.orch:
            await self.orch.shutdown()
            self.orch = None
        base = Path(os.environ["BARNABY_DATA_DIR"]) / "worktrees"
        if base.exists():
            _force_rmtree(base)
        _force_rmtree(self.ws)
        await super().asyncTearDown()

    async def build(self, convo_scripts=None, **config):
        await super().build(convo_scripts, **config)
        outer = self.orch.tasks._factory

        def factory(engine, task_id):
            agent = outer(engine, task_id)
            writing = WritingAgent(engine, task_id, agent.script)
            self.agents[self.agents.index(agent)] = writing
            return writing
        self.orch.tasks._factory = factory

    def status_of_main(self):
        return git(self.ws, "status", "--porcelain")

    async def wait_for(self, pred, seconds=30):
        for _ in range(int(seconds / 0.02)):
            if pred():
                return
            await asyncio.sleep(0.02)
        self.fail("timed out waiting")

    def _agents_of(self, title):
        tid = self.task(title).id
        return [a for a in self.agents if a.task_id == tid]

    async def create(self, title, engine="claude", lanes=(), instruction="do the thing"):
        # Creating a worktree is several git calls: wait until the agent is running (or the
        # task is queued behind another / failed) instead of a fixed settle.
        await super().create(title, engine, lanes, instruction)
        await self.wait_for(lambda: self.task(title).status != "running"
                            or any(a.started.is_set() for a in self._agents_of(title)))

    async def follow(self, title, text):
        before = len(self.calls_for(title))
        await self.orch.handle(cmd("task_follow_up", task_id=self.task(title).id, text=text))
        await self.wait_for(lambda: len(self.calls_for(title)) > before and self.agent_for(title).started.is_set())

    async def finish(self, title):
        agent = self.agent_for(title)
        before = len(self.calls_for(title))
        agent.gate.set()
        await self.wait_for(lambda: self.task(title).status not in ("running", "verifying")
                            or len(self.calls_for(title)) > before)
        await self.settle()


class WorktreeLifecycleTest(_GitBase):
    async def test_task_runs_in_its_own_worktree_and_branch_and_commits_there(self):
        await self.build()
        await self.create("Orders API!", lanes=["api"])
        t = self.task("Orders API!")
        self.assertEqual(t.isolation, "worktree")
        self.assertTrue(t.branch.startswith(f"barnaby/task-{t.id[:8]}-orders-api"), t.branch)
        wt = Path(t.worktree)
        self.assertTrue(wt.is_dir())
        self.assertNotIn(self.ws.resolve(), wt.resolve().parents)  # outside the project folder
        call = self.agent_for("Orders API!").calls[0]
        self.assertEqual(Path(call["workspace"]).resolve(), wt.resolve())
        self.assertIn(t.branch, call["prompt"])
        self.assertIn("own git worktree", call["prompt"])
        self.assertIn("started from", t.notice)
        await self.finish("Orders API!")
        t = self.task("Orders API!")
        self.assertEqual(t.status, "done")
        self.assertIn("Committed", t.notice)
        self.assertEqual(self.status_of_main(), "")                      # the user's checkout is untouched
        self.assertFalse((self.ws / "api" / f"{t.id}.py").exists())
        self.assertIn(f"api/{t.id}.py", git(self.ws, "show", "--name-only", "--format=", t.branch))
        self.assertEqual(git(self.ws, "log", "-1", "--format=%s", t.branch), "Orders API!")
        self.assertEqual(git(wt, "status", "--porcelain"), "")
        self.assertEqual(self.task("Orders API!").public()["run_dir"], str(wt))

    async def test_follow_up_and_restart_reuse_the_same_worktree(self):
        await self.build()
        await self.create("a", lanes=["api"])
        t = self.task("a")
        wt = t.worktree
        await self.finish("a")
        await self.follow("a", "more")
        self.assertEqual(Path(self.calls_for("a")[-1]["workspace"]).resolve(), Path(wt).resolve())
        self.assertEqual(self.calls_for("a")[-1]["session_id"], SID)
        # Barnaby closes mid-turn, the worktree folder even disappears; a follow-up after the
        # restart checks the same branch out again at the same path, so the agent session resumes.
        await self.orch.shutdown()
        self.orch = None
        self.assertEqual(self.store.list_tasks()[0].status, "interrupted")
        git(self.ws, "worktree", "remove", "--force", wt)
        await self.build()
        await self.follow("a", "carry on")
        self.assertEqual(self.task("a").status, "running")
        self.assertEqual(Path(self.calls_for("a")[-1]["workspace"]).resolve(), Path(wt).resolve())
        self.assertTrue((Path(wt) / "api" / f"{t.id}.py").exists())  # the committed first turn is there

    async def test_discard_removes_worktree_and_branch(self):
        await self.build()
        await self.create("a", lanes=["api"])
        t = self.task("a")
        await self.orch.handle(cmd("task_discard", task_id=t.id))
        self.assertIn("Cancel the task", self.of("error")[-1]["message"])  # not while it runs
        await self.finish("a")
        await self.orch.handle(cmd("task_discard", task_id=t.id))
        self.assertFalse(Path(t.worktree).exists())
        self.assertEqual(git(self.ws, "branch", "--list", t.branch), "")
        self.assertNotIn(t.id, self.orch.tasks.tasks)
        self.assertEqual(self.store.list_tasks(), [])
        self.assertIn("Discarded", self.of("notice")[-1]["message"])
        self.assertEqual(git(self.ws, "worktree", "list", "--porcelain").count("worktree "), 1)

    async def test_clear_keeps_unmerged_work_and_removes_merged(self):
        await self.build()
        await self.create("kept", lanes=["api"])
        await self.create("merged", lanes=["app"])
        await self.finish("kept")
        await self.finish("merged")
        merged = self.task("merged")
        self.assertEqual((self.task("kept").status, merged.status), ("done", "done"))
        git(self.ws, "merge", "-q", "--no-edit", merged.branch)
        await self.orch.handle(cmd("task_clear"))
        self.assertEqual([t.title for t in self.orch.tasks.tasks.values()], ["kept"])
        self.assertIn("isn't merged", self.of("notice")[-1]["message"])
        self.assertIn("1 commit", self.of("notice")[-1]["message"])
        self.assertFalse(Path(merged.worktree).exists())
        self.assertEqual(git(self.ws, "branch", "--list", merged.branch), "")
        self.assertTrue(Path(self.task("kept").worktree).exists())

    async def test_uncommitted_changes_also_keep_the_worktree(self):
        await self.build()
        await self.create("a", lanes=["api"])
        t = self.task("a")
        await self.orch.handle(cmd("task_cancel", task_id=t.id))
        (Path(t.worktree) / "api" / "wip.py").write_text("half done\n")
        await self.orch.handle(cmd("task_clear"))
        self.assertIn(t.id, self.orch.tasks.tasks)
        self.assertIn("uncommitted", self.of("notice")[-1]["message"])

    async def test_clean_unchanged_task_is_cleared_with_its_worktree(self):
        await self.build()
        await self.create("a", lanes=["api"])
        t = self.task("a")
        await self.orch.handle(cmd("task_cancel", task_id=t.id))
        await self.orch.handle(cmd("task_clear"))
        self.assertEqual(self.orch.tasks.tasks, {})
        self.assertFalse(Path(t.worktree).exists())
        self.assertEqual(git(self.ws, "branch", "--list", "barnaby/*"), "")

    async def test_a_follow_up_during_clear_keeps_the_task(self):
        await self.build()
        await self.create("a", lanes=["api"])
        t = self.task("a")
        await self.orch.handle(cmd("task_cancel", task_id=t.id))
        real = self.orch.tasks._worktree_keep_reason

        async def slow_check(task):  # the user follows up while Clear is still asking git
            why = await real(task)
            await self.orch.handle(cmd("task_follow_up", task_id=task.id, text="actually, keep going"))
            return why
        self.orch.tasks._worktree_keep_reason = slow_check
        await self.orch.handle(cmd("task_clear"))
        self.assertIn(t.id, self.orch.tasks.tasks)
        self.assertEqual([x.id for x in self.store.list_tasks()], [t.id])
        await self.wait_for(lambda: self.agent_for("a").started.is_set() and len(self.calls_for("a")) == 2)
        self.assertTrue(Path(t.worktree).exists())

    async def test_open_folder_shows_the_worktree(self):
        opened = []
        await self.build()
        self.orch.tasks._open_folder = opened.append
        await self.create("a", lanes=["api"])
        await self.orch.handle(cmd("task_open_folder", task_id=self.task("a").id))
        self.assertEqual([Path(p).resolve() for p in opened], [Path(self.task("a").worktree).resolve()])

    async def test_main_checkout_changes_are_noted(self):
        (self.ws / "README.md").write_text("edited by the user\n")
        await self.build()
        await self.create("a", lanes=["api"])
        self.assertIn("uncommitted change", self.task("a").notice)
        self.assertEqual((Path(self.task("a").worktree) / "README.md").read_text(), "hi\n")


class WorktreeScopeTest(_GitBase):
    async def test_lanes_and_approvals_are_relative_to_the_worktree(self):
        await self.build()
        self.scripts["t"] = ["deny:docs/notes.md", "ok"]
        await self.create("t", lanes=["api"])
        await self.finish("t")
        card = self.of("task_approval_required")[-1]
        self.assertTrue(card["grantable"])
        self.assertIn("docs/notes.md", card["summary"])
        before = len(self.calls_for("t"))
        await self.orch.handle(cmd("approve", request_id=card["request_id"]))
        await self.wait_for(lambda: len(self.calls_for("t")) > before)
        call = self.calls_for("t")[-1]
        self.assertEqual(call["grant"].allow, ("Edit(./docs/notes.md)",))
        self.assertEqual(Path(call["workspace"]).resolve(), Path(self.task("t").worktree).resolve())

    async def test_an_edit_in_the_users_checkout_cannot_be_approved(self):
        await self.build()
        await self.create("t", lanes=["api"])
        wt = self.task("t").worktree
        agent = self.agent_for("t")
        # The agent reaches for the main checkout instead of its worktree.
        agent.script.insert(0, "ok")
        self.scripts.clear()
        from barnaby_engine.agent import Denial
        denial = TurnResult("x", SID, False, None, None,
                            (Denial("d", "Write", {"file_path": str(self.ws / "docs" / "x.md"), "content": "x"}),))
        await self.orch.tasks._open_approvals(self.task("t"), denial, "x")
        card = self.of("task_approval_required")[-1]
        self.assertFalse(card["grantable"])
        self.assertIn("outside the project", card["summary"])
        self.assertTrue(Path(wt).exists())

    async def test_worktree_tasks_do_not_make_the_conversation_ask(self):
        await self.build(convo_scripts=[[TurnResult("ok", SID, False, None, None)]])
        await self.create("a", lanes=["api"])
        self.assertEqual(self.orch.tasks.busy_lanes(str(self.ws)), ())
        await self.orch.handle(cmd("text_submit", text="rename the helper"))
        await self.settle()
        self.assertIsNone(self.convo.calls[-1]["scope"])

    async def test_overlapping_lanes_wait_by_default_and_run_together_when_allowed(self):
        await self.build()
        await self.create("one", lanes=["api"])
        await self.create("two", lanes=["api"])
        self.assertEqual((self.task("one").status, self.task("two").status), ("running", "queued"))
        await self.orch.shutdown()
        self.orch = None
        self._remaining.clear()
        await self.build(task_worktree_parallel=True)
        await self.create("three", lanes=["api"])
        await self.create("four", lanes=["api"])
        await self.create("everything")
        self.assertEqual([self.task(x).status for x in ("three", "four", "everything")], ["running"] * 3)
        self.assertNotEqual(self.task("three").worktree, self.task("four").worktree)

    async def test_project_in_a_subfolder_of_the_repo(self):
        sub = self.ws / "app"
        (sub / "src").mkdir()
        (sub / "src" / "c.ts").write_text("x\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "app src")
        await self.build()
        self.config.workspace = str(sub)
        await self.create("sub", lanes=["src"])
        t = self.task("sub")
        run = Path(self.calls_for("sub")[0]["workspace"])
        self.assertEqual(run.resolve(), (Path(t.worktree) / "app").resolve())
        self.assertTrue((run / "src" / "c.ts").exists())


class SharedFallbackTest(_Base):
    async def test_non_git_project_uses_the_shared_folder_and_says_so(self):
        await self.build()
        await self.create("a", lanes=["api"])
        t = self.task("a")
        self.assertEqual((t.isolation, t.worktree, t.branch), ("shared", "", ""))
        self.assertIn("No worktree", t.notice)
        self.assertEqual(self.agent_for("a").calls[0]["workspace"], str(self.ws))
        self.assertEqual(self.orch.tasks.busy_lanes(str(self.ws)), ("api",))
        await self.finish("a")
        await self.orch.handle(cmd("task_discard", task_id=t.id))
        self.assertIn("no worktree", self.of("error")[-1]["message"])

    @unittest.skipUnless(HAVE_GIT, "git is not installed")
    async def test_worktrees_turned_off_and_legacy_tasks_stay_shared(self):
        git(self.ws, "init", "-q")
        (self.ws / "f").write_text("x")
        git(self.ws, "add", "-A")
        git(self.ws, "-c", "user.email=t@e", "-c", "user.name=T", "-c", "commit.gpgsign=false", "commit", "-q",
            "-m", "i")
        self.store.save_task(TaskRecord(id="old1", workspace=str(self.ws), engine="claude", title="old",
                                        instruction="x", lanes=("api",), status="done", created_at=1.0,
                                        cli_session_id=SID))
        await self.build(task_worktrees=False)
        await self.create("off", lanes=["app"])
        self.assertEqual((self.task("off").isolation, self.task("off").notice), ("shared", ""))
        await self.orch.handle(cmd("task_follow_up", task_id="old1", text="go on"))
        await self.settle()
        self.assertEqual(self.task("old").isolation, "shared")
        self.assertEqual(self.calls_for("old")[-1]["workspace"], str(self.ws))
        _force_rmtree(self.ws / ".git")


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class WorktreeUnitsTest(unittest.IsolatedAsyncioTestCase):
    def test_branch_names_are_safe(self):
        self.assertEqual(worktrees.branch_name("abcdef123456", "Fix: the *login* form!!"),
                         "barnaby/task-abcdef12-fix-the-login-form")
        self.assertEqual(worktrees.branch_name("abcdef123456", "日本"), "barnaby/task-abcdef12")
        name = worktrees.branch_name("abcdef123456", "x" * 100)
        self.assertLessEqual(len(name), 60)

    async def test_repo_of_and_failed_worktree_reasons(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            plain = Path(d) / "plain dir"
            plain.mkdir()
            self.assertIsNone(await worktrees.repo_of(plain))
            git(plain, "init", "-q")
            self.assertIsNone(await worktrees.repo_of(plain))  # no commits yet
            (plain / "x").write_text("x")
            git(plain, "add", "-A")
            git(plain, "-c", "user.email=t@e", "-c", "user.name=T", "-c", "commit.gpgsign=false", "commit", "-q",
                "-m", "i")
            repo = await worktrees.repo_of(plain)
            self.assertEqual((repo.top.resolve(), repo.prefix), (plain.resolve(), ""))
            blocker = Path(d) / "wt"
            blocker.mkdir()
            (blocker / "something").write_text("x")
            with self.assertRaises(worktrees.WorktreeError):
                await worktrees.ensure(repo.top, blocker, "barnaby/task-x")
            _force_rmtree(plain / ".git")


if __name__ == "__main__":
    unittest.main()
