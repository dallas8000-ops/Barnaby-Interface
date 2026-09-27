import asyncio
import tempfile
import unittest
from pathlib import Path

from barnaby_engine.agent import (AgentError, ClaudeCodeAgent, SessionStarted, TurnResult,
                                  build_argv)
from barnaby_engine.safety import Grant
from tests.helpers import make_fake_claude, pid_alive, record_of

INJECTION = 'fix the build & calc.exe | whoami > pwned.txt "; rm -rf /'


class AgentProcessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.workspace = self.tmp / "proj"
        self.workspace.mkdir()

    async def asyncTearDown(self):
        self._tmp.cleanup()

    def agent(self, mode="replay", **kw):
        return ClaudeCodeAgent(claude_bin=str(make_fake_claude(self.tmp, mode=mode)),
                               safe_bash=["git status"], scratch_dir=self.tmp / "run", **kw)

    async def collect(self, agent, prompt="hello", **kw):
        kw.setdefault("session_id", None)
        kw.setdefault("grant", None)
        return [e async for e in agent.run(prompt, workspace=str(self.workspace), **kw)]

    async def test_prompt_goes_over_stdin_never_argv(self):
        await self.collect(self.agent(), INJECTION)
        rec = record_of(self.tmp)
        self.assertEqual(rec["stdin"], INJECTION)
        self.assertFalse(any(INJECTION in a or "calc.exe" in a for a in rec["argv"]))

    async def test_runs_in_workspace_with_expected_flags(self):
        events = await self.collect(self.agent())
        rec = record_of(self.tmp)
        self.assertEqual(Path(rec["cwd"]).resolve(), self.workspace.resolve())
        for flag in ("-p", "--include-partial-messages", "--verbose"):
            self.assertIn(flag, rec["argv"])
        self.assertEqual(rec["argv"][rec["argv"].index("--output-format") + 1], "stream-json")
        self.assertEqual(rec["argv"][rec["argv"].index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("speech-to-text", rec["sysprompt"])
        self.assertIsInstance(events[0], SessionStarted)
        self.assertIsInstance(events[-1], TurnResult)

    async def test_grant_is_scoped_to_settings_file_and_file_is_removed(self):
        grant = Grant(allow=("Bash(git push origin main)",), add_dirs=("/tmp/elsewhere",))
        await self.collect(self.agent(), grant=grant)
        perms = record_of(self.tmp)["settings"]["permissions"]
        self.assertIn("Bash(git status)", perms["allow"])
        self.assertIn("Bash(git push origin main)", perms["allow"])
        self.assertEqual(perms["additionalDirectories"], ["/tmp/elsewhere"])
        self.assertEqual(list((self.tmp / "run").iterdir()), [], "temp settings/prompt files must be deleted")

    async def test_resume_passes_validated_session_id(self):
        sid = "0cb78c70-3072-55f8-ad03-91da1569e1a9"
        await self.collect(self.agent(), session_id=sid)
        argv = record_of(self.tmp)["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], sid)

    async def test_malformed_session_id_refused_before_spawn(self):
        with self.assertRaises(AgentError):
            await self.collect(self.agent(), session_id="abc; rm -rf /")
        self.assertFalse((self.tmp / "record.json").exists())

    async def test_non_json_lines_skipped(self):
        events = await self.collect(self.agent("noise"))
        self.assertEqual(events[-1].text, "ok")

    async def test_line_larger_than_default_stream_limit(self):
        # 200 KiB single line would raise LimitOverrunError with asyncio's 64 KiB default.
        events = await self.collect(self.agent("huge"))
        self.assertEqual(events[-1].text, "done")

    async def test_missing_result_surfaces_stderr(self):
        with self.assertRaises(AgentError) as ctx:
            await self.collect(self.agent("no_result"))
        self.assertIn("/login", str(ctx.exception))

    async def test_cancel_kills_process(self):
        agent = self.agent("hang")
        seen = []

        async def consume():
            async for e in agent.run("x", workspace=str(self.workspace), session_id=None, grant=None):
                seen.append(e)

        task = asyncio.create_task(consume())
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(seen, "init event should arrive before cancel")
        pid = record_of(self.tmp)["pid"]
        await agent.cancel()
        await asyncio.wait_for(task, timeout=5)
        self.assertFalse(pid_alive(pid), "claude process tree must be killed on cancel")

    async def test_timeout_stops_turn(self):
        with self.assertRaises(AgentError) as ctx:
            await self.collect(self.agent("hang", timeout_s=1))
        self.assertIn("exceeded", str(ctx.exception))

    async def test_missing_workspace(self):
        agent = self.agent()
        with self.assertRaises(AgentError):
            _ = [e async for e in agent.run("x", workspace=str(self.tmp / "nope"), session_id=None, grant=None)]


class BuildArgvTest(unittest.TestCase):
    def test_model_validated(self):
        with self.assertRaises(AgentError):
            build_argv("claude", settings_path="s", prompt_path="p", session_id=None, model="opus && calc")
        argv = build_argv("claude", settings_path="s", prompt_path="p", session_id=None, model="claude-opus-5-5")
        self.assertEqual(argv[-2:], ["--model", "claude-opus-5-5"])


if __name__ == "__main__":
    unittest.main()
