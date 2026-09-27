"""Cursor engine: parser pinned to a real captured run, permission translation
(semantics verified live against Cursor CLI 2026.09.26), the guard that
enforces what Cursor's headless config doesn't, and the process adapter."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from barnaby_engine.agent import AgentError, Denial, SessionStarted, TextDelta, ToolEnd, ToolStart, TurnResult
from barnaby_engine.cursor_agent import (CursorAgent, CursorGuard, CursorStreamParser, build_config,
                                         cursor_grantable, exact_shell_rule, translate_rules)
from barnaby_engine.orchestrator import route_engine
from barnaby_engine.safety import Grant
from tests.helpers import FIXTURES, pid_alive

INJECTION = 'fix it & calc.exe | whoami > pwned.txt "; --force'


def parse_fixture(name):
    parser, events = CursorStreamParser(), []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            events += parser.feed(json.loads(line))
    return events


class ParserTest(unittest.TestCase):
    def test_real_run_text_once_tools_and_rejection(self):
        events = parse_fixture("cursor_rejected_shell.jsonl")
        result = events[-1]
        self.assertIsInstance(events[0], SessionStarted)
        self.assertIsInstance(result, TurnResult)
        # --stream-partial-output emits every sentence up to three times; exactly once must survive.
        self.assertEqual("".join(e.text for e in events if isinstance(e, TextDelta)), result.text)
        starts = {e.tool_id: e for e in events if isinstance(e, ToolStart)}
        ends = {e.tool_id: e for e in events if isinstance(e, ToolEnd)}
        self.assertEqual(sorted(s.name for s in starts.values()), ["Bash", "Bash", "Edit", "Edit", "Read"])
        self.assertEqual(set(starts), set(ends))
        by_cmd = {s.input.get("command"): ends[t] for t, s in starts.items() if s.name == "Bash"}
        self.assertTrue(by_cmd["python --version"].ok)
        self.assertIn("Python 3.11", by_cmd["python --version"].preview)
        self.assertFalse(by_cmd["whoami"].ok)
        self.assertEqual(result.denials, (Denial(next(t for t, s in starts.items()
                                                      if s.input.get("command") == "whoami"),
                                                 "Bash", {"command": "whoami"}),))
        self.assertIsNone(result.cost_usd)

    def test_failure_shows_exit_and_stderr(self):
        p = CursorStreamParser()
        p.feed({"type": "tool_call", "subtype": "started", "call_id": "c1",
                "tool_call": {"shellToolCall": {"args": {"command": "git log -1"}}, "toolCallId": "c1"}})
        (end,) = p.feed({"type": "tool_call", "subtype": "completed", "call_id": "c1", "tool_call": {
            "shellToolCall": {"result": {"failure": {"exitCode": 128, "stderr": "fatal: no commits"}}},
            "startedAtMs": "1"}})
        self.assertFalse(end.ok)
        self.assertIn("exit 128", end.preview)
        self.assertIn("fatal: no commits", end.preview)

    def test_paragraph_break_between_text_around_tools(self):
        p = CursorStreamParser()
        a = p.feed({"type": "assistant", "timestamp_ms": 1, "message": {"content": [{"type": "text", "text": "Checking."}]}})
        p.feed({"type": "tool_call", "subtype": "started", "call_id": "t",
                "tool_call": {"readToolCall": {"args": {"path": "a.py"}}}})
        b = p.feed({"type": "assistant", "timestamp_ms": 2, "message": {"content": [{"type": "text", "text": "Done."}]}})
        dup = p.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking.Done."}]}})
        self.assertEqual([a[0].text, b[0].text], ["Checking.", "\n\nDone."])
        self.assertEqual(dup, [])


class RulesTest(unittest.TestCase):
    def test_exact_rule_shape(self):
        self.assertEqual(exact_shell_rule("git push origin main"), "Shell(git:push origin main)")
        self.assertEqual(exact_shell_rule("hostname"), "Shell(hostname:)")
        self.assertEqual(exact_shell_rule("  git   status "), "Shell(git:status)")
        for bad in ("ls *.py", "echo (x)", "a:b c", "", "rm -rf [ab]"):
            with self.subTest(bad=bad):
                self.assertIsNone(exact_shell_rule(bad))

    def test_translate_safe_list_and_grant(self):
        grant = Grant(allow=("Bash(git push origin main)", "WebFetch(domain:docs.python.org)"), add_dirs=())
        allow = translate_rules(["git status", "git diff:*", "pwd"], grant)
        self.assertEqual(allow, ["Read(**)", "Shell(git:status)", "Shell(git diff)", "Shell(pwd:)",
                                 "Shell(git:push origin main)", "WebFetch(docs.python.org)"])
        cfg = build_config(allow)
        self.assertEqual(cfg["approvalMode"], "allowlist")
        self.assertEqual(cfg["permissions"]["deny"], [])

    def test_grantability(self):
        self.assertTrue(cursor_grantable("Bash", {"command": "npm install lodash"}))
        self.assertFalse(cursor_grantable("Bash", {"command": "rm build/*.o"}))
        self.assertTrue(cursor_grantable("Edit", {"file_path": "/x"}))


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "proj"
        self.ws.mkdir()
        self.other = Path(self._tmp.name) / "other"
        self.guard = CursorGuard(str(self.ws), ["git status", "git diff:*", "ls:*"], None)

    def tearDown(self):
        self._tmp.cleanup()

    def test_edits_fenced_to_project(self):
        self.assertIsNone(self.guard.check("Edit", {"file_path": str(self.ws / "src" / "a.py")}))
        self.assertIsNone(self.guard.check("Write", {"file_path": "src/a.py"}))  # relative = inside
        for name in ("Edit", "Write", "Delete", "Read"):
            with self.subTest(name=name):
                self.assertIn("outside your project", self.guard.check(name, {"file_path": str(self.other / "x")}))
        self.assertIsNotNone(self.guard.check("Edit", {"file_path": str(self.ws / ".." / "other" / "x")}))

    def test_approved_dir_is_inside(self):
        g = CursorGuard(str(self.ws), [], Grant(allow=(), add_dirs=(str(self.other),)))
        self.assertIsNone(g.check("Edit", {"file_path": str(self.other / "cfg.py")}))

    def test_only_fires_where_cursor_would_be_looser(self):
        ok = [("ls -la", None), ("git status", None), ("git diff --stat", None),
              ("whoami", None),                      # Cursor rejects it -> approval card, not a kill
              ("git status && whoami", None),        # Cursor rejects the chain -> approval card
              ("git push origin main --force", None)]  # no rule matches -> Cursor rejects
        for cmd, expected in ok:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.guard.check("Bash", {"command": cmd}), expected)
        for cmd in ("lsblk", "git diff > /tmp/leak.txt", "git diff $(whoami)", "ls `id`"):
            with self.subTest(cmd=cmd):
                self.assertIn("goes beyond", self.guard.check("Bash", {"command": cmd}))

    def test_grant_is_exact(self):
        g = CursorGuard(str(self.ws), [], Grant(allow=("Bash(git diff > out.txt)",), add_dirs=()))
        self.assertIsNone(g.check("Bash", {"command": "git diff > out.txt"}))


class RouteTest(unittest.TestCase):
    def test_routes(self):
        cases = {
            "Cursor, fix the build": ("cursor", "fix the build"),
            "have cursor run the tests": ("cursor", "run the tests"),
            "ask Claude to explain this": ("claude", "explain this"),
            "use cloud to refactor": ("claude", "refactor"),
            "use cursor": ("cursor", ""),
            "Claude: why is it failing": ("claude", "why is it failing"),
            "hey cursor, add tests": ("cursor", "add tests"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(route_engine(text), expected)

    def test_ordinary_instructions_untouched(self):
        for text in ("add a cursor to the table", "cursor position is wrong in the editor",
                     "cloud functions are timing out", "make the claude.md shorter"):
            with self.subTest(text=text):
                self.assertEqual(route_engine(text), (None, text))


FAKE = r'''
import json, os, sys, time
argv = sys.argv[1:]
cfg_dir = os.environ.get("CURSOR_CONFIG_DIR", "")
cfg = json.load(open(os.path.join(cfg_dir, "cli-config.json"))) if cfg_dir else None
json.dump({"argv": argv, "cfg": cfg, "cfg_dir": cfg_dir, "pid": os.getpid(), "cwd": os.getcwd(),
           "stdin_closed": sys.stdin is None or not sys.stdin.readable() or sys.stdin.read() == ""},
          open(os.environ["FAKE_RECORD"], "w"))
mode = os.environ["FAKE_MODE"]
def out(o): print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "session_id": "eec2672e-02b9-4e24-8c11-d5f2f93358cf", "model": "Auto"})
if mode == "replay":
    # The fixture was captured in C:\\...\\cursor-probe; re-home its paths into this
    # test's project so the guard sees in-project work (as it did on the real run).
    captured = json.dumps("C:\\Users\\Ray\\AppData\\Local\\Temp\\cursor-probe")[1:-1]
    here = json.dumps(os.environ["FAKE_WS"])[1:-1]
    for line in open(os.environ["FAKE_FIXTURE"], encoding="utf-8"):
        line = line.replace(captured, here)
        if os.sep == "/":  # the captured separators after the prefix are Windows-style
            line = line.replace(here + "\\\\", here + "/")
        sys.stdout.write(line); sys.stdout.flush()
elif mode == "outside_edit":
    out({"type": "tool_call", "subtype": "started", "call_id": "e1",
         "tool_call": {"editToolCall": {"args": {"path": os.environ["FAKE_OUTSIDE"], "streamContent": "x"}}}})
    time.sleep(60)
elif mode == "no_result":
    sys.stderr.write("Error: Not logged in. Run agent login\n"); sys.exit(1)
'''


class CursorProcessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ws = self.tmp / "proj"
        self.ws.mkdir()
        self.script = self.tmp / "fake_cursor.py"
        self.script.write_text(FAKE)
        self.record = self.tmp / "record.json"
        self.patch = mock.patch("barnaby_engine.cursor_agent.resolve_cursor",
                                return_value=[sys.executable, str(self.script)])
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self._tmp.cleanup()

    def agent(self, **kw):
        return CursorAgent(config_dir=self.tmp / "cursor-cfg", safe_bash=["git status", "npm test:*"],
                           scratch_dir=self.tmp / "run", **kw)

    async def collect(self, agent, mode, prompt="hello", **kw):
        kw.setdefault("session_id", None)
        kw.setdefault("grant", None)
        env = {"FAKE_MODE": mode, "FAKE_RECORD": str(self.record),
               "FAKE_FIXTURE": str(FIXTURES / "cursor_rejected_shell.jsonl"),
               "FAKE_OUTSIDE": str(self.tmp / "outside.txt"), "FAKE_WS": str(self.ws)}
        with mock.patch.dict(os.environ, env):
            return [e async for e in agent.run(prompt, workspace=str(self.ws), **kw)]

    def rec(self):
        return json.loads(self.record.read_text())

    async def test_argv_config_and_prompt_position(self):
        events = await self.collect(self.agent(), "replay", INJECTION)
        rec = self.rec()
        argv = rec["argv"]
        for flag in ("-p", "--trust", "--stream-partial-output"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--workspace") + 1], str(self.ws))
        # Prompt is ONE argv element after "--" (no shell anywhere in the path).
        self.assertEqual(argv[-2], "--")
        self.assertTrue(argv[-1].endswith(INJECTION))
        self.assertIn("barnaby_voice_interface", argv[-1])  # voice guidance on a new chat
        self.assertTrue(rec["stdin_closed"])
        self.assertEqual(Path(rec["cfg_dir"]), self.tmp / "cursor-cfg")
        self.assertEqual(rec["cfg"]["approvalMode"], "allowlist")
        self.assertIn("Shell(git:status)", rec["cfg"]["permissions"]["allow"])
        self.assertIn("Shell(npm test)", rec["cfg"]["permissions"]["allow"])
        self.assertIsInstance(events[-1], TurnResult)
        self.assertEqual(events[-1].denials[0].tool_input, {"command": "whoami"})

    async def test_resume_with_grant_no_voice_preamble(self):
        grant = Grant(allow=("Bash(whoami)",), add_dirs=())
        await self.collect(self.agent(), "replay", "run it", session_id="eec2672e-02b9-4e24-8c11-d5f2f93358cf",
                           grant=grant)
        rec = self.rec()
        self.assertEqual(rec["argv"][rec["argv"].index("--resume") + 1], "eec2672e-02b9-4e24-8c11-d5f2f93358cf")
        self.assertEqual(rec["argv"][-1], "run it")
        self.assertIn("Shell(whoami:)", rec["cfg"]["permissions"]["allow"])
        # The config dir persists (Cursor keeps chat history there for --resume).
        self.assertTrue((self.tmp / "cursor-cfg" / "cli-config.json").exists())

    async def test_guard_kills_outside_edit_immediately(self):
        agent = self.agent()
        seen = []
        with self.assertRaises(AgentError) as ctx:
            async with asyncio.timeout(20):
                with mock.patch.dict(os.environ, {"FAKE_MODE": "outside_edit", "FAKE_RECORD": str(self.record),
                                                  "FAKE_OUTSIDE": str(self.tmp / "outside.txt")}):
                    async for e in agent.run("x", workspace=str(self.ws), session_id=None, grant=None):
                        seen.append(e)
        self.assertIn("outside your project", str(ctx.exception))
        self.assertIsInstance(seen[-1], ToolEnd)
        self.assertEqual(seen[-1].preview, "Blocked by Barnaby")
        self.assertFalse(pid_alive(self.rec()["pid"]), "Cursor must be killed, not left running")

    async def test_missing_result_surfaces_stderr(self):
        with self.assertRaises(AgentError) as ctx:
            await self.collect(self.agent(), "no_result")
        self.assertIn("agent login", str(ctx.exception))

    async def test_refusals_before_spawn(self):
        with self.assertRaises(AgentError):
            await self.collect(self.agent(), "replay", session_id="x; rm -rf /")
        with self.assertRaises(AgentError):
            await self.collect(self.agent(model="auto && calc"), "replay")
        with self.assertRaises(AgentError):
            await self.collect(self.agent(), "replay", "x" * 30_000)
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
