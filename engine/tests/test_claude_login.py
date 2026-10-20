"""Signing in to Claude from Barnaby: the ClaudeAuth wrapper (fake CLI) and the orchestrator flow."""
import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import AgentError, SessionStarted, StreamParser, TurnResult, SIGN_IN_HINT
from barnaby_engine.claude_auth import ClaudeAuth, TOKEN_VAR
from barnaby_engine.config import Config
from barnaby_engine.orchestrator import Orchestrator
from barnaby_engine.protocol import parse_command
from barnaby_engine.server import permit
from barnaby_engine.store import Store
from tests.doubles import (MOCK_SESSION, FakeClaudeAuth, FakeRecorder, FakeSpeaker, FakeTranscriber,
                           ScriptedAgent)
from tests.helpers import TempDataDir


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


def make_fake_cli(tmp: Path, *, logged_in: bool, login_exit: int = 0, login_signs_in: bool = True,
                  hang_login: bool = False) -> Path:
    """An executable standing in for `claude auth ...`. Records argv and whether the stale token
    reached the login process; `auth status` reads the state file `login` writes."""
    state, record = tmp / "state.json", tmp / "record.json"
    state.write_text(json.dumps({"loggedIn": logged_in}))
    script = tmp / "fake_claude.py"
    script.write_text(f"""#!{sys.executable}
import json, os, sys, time
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    s = json.load(open({str(state)!r}))
    print(json.dumps({{"loggedIn": s["loggedIn"], "authMethod": "claude.ai" if s["loggedIn"] else "none"}}))
elif args[:2] == ["auth", "login"]:
    json.dump({{"argv": args, "token_present": {TOKEN_VAR!r} in os.environ}}, open({str(record)!r}, "w"))
    if {hang_login!r}:
        time.sleep(60)
    if {login_signs_in!r}:
        json.dump({{"loggedIn": True}}, open({str(state)!r}, "w"))
    sys.exit({login_exit})
else:
    sys.exit(9)
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    if sys.platform == "win32":
        shim = tmp / "fake_claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
        return shim
    return script


class ClaudeAuthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    async def test_status_reads_the_cli(self):
        signed_out = ClaudeAuth(str(make_fake_cli(self.tmp, logged_in=False)))
        self.assertFalse((await signed_out.status()).signed_in)
        signed_in = ClaudeAuth(str(make_fake_cli(self.tmp, logged_in=True)))
        s = await signed_in.status()
        self.assertTrue(s.signed_in)
        self.assertEqual(s.method, "claude.ai")

    async def test_status_when_claude_is_missing(self):
        with unittest.mock.patch("shutil.which", return_value=None):
            s = await ClaudeAuth("").status()
        self.assertFalse(s.signed_in)
        self.assertIn("not found", s.error)

    async def test_login_runs_fixed_argv_without_the_stale_token(self):
        cli = make_fake_cli(self.tmp, logged_in=False)
        auth = ClaudeAuth(str(cli))
        with unittest.mock.patch.dict(os.environ, {TOKEN_VAR: "stale-token-value"}):
            self.assertTrue(await auth.login())
        record = json.loads((self.tmp / "record.json").read_text())
        self.assertEqual(record["argv"], ["auth", "login", "--claudeai"])
        self.assertFalse(record["token_present"])
        self.assertTrue((await auth.status()).signed_in)

    async def test_login_that_exits_nonzero_is_not_success(self):
        auth = ClaudeAuth(str(make_fake_cli(self.tmp, logged_in=False, login_exit=1, login_signs_in=False)))
        self.assertFalse(await auth.login())

    async def test_cancel_stops_a_waiting_login(self):
        auth = ClaudeAuth(str(make_fake_cli(self.tmp, logged_in=False, hang_login=True)))
        task = asyncio.create_task(auth.login())
        for _ in range(100):
            await asyncio.sleep(0.05)
            if (self.tmp / "record.json").exists():
                break
        await auth.cancel()
        self.assertFalse(await asyncio.wait_for(task, 10))

    async def test_login_timeout_raises_and_stops(self):
        auth = ClaudeAuth(str(make_fake_cli(self.tmp, logged_in=False, hang_login=True)))
        with unittest.mock.patch("barnaby_engine.claude_auth.LOGIN_TIMEOUT_S", 0.5):
            with self.assertRaises(AgentError):
                await auth.login()

    def test_drop_stale_token_only_touches_this_process(self):
        with unittest.mock.patch.dict(os.environ, {TOKEN_VAR: "x"}):
            self.assertTrue(ClaudeAuth.drop_stale_token())
            self.assertNotIn(TOKEN_VAR, os.environ)
            self.assertFalse(ClaudeAuth.drop_stale_token())


class ClaudeLoginFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "db.sqlite")
        self.events: list[dict] = []

    async def asyncTearDown(self):
        await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    async def build(self, auth, scripts=None):
        async def emit(e):
            self.events.append(e)
        self.agent = ScriptedAgent(scripts or [])
        self.agent.model = ""
        self.orch = Orchestrator(config=Config(workspace=self._tmp.name, tts_enabled=False), store=self.store,
                                 profile=VoiceProfile(self.store), recorder=FakeRecorder(),
                                 transcriber=FakeTranscriber(), speaker=FakeSpeaker(),
                                 agents={"claude": self.agent}, emit=emit, claude_auth=auth)
        await self.orch.start()
        await self.settle()

    async def settle(self):
        for _ in range(300):
            await asyncio.sleep(0.01)
            task = self.orch._claude_login_task
            if task is None or task.done():
                await asyncio.sleep(0.02)
                return
        self.fail("login did not finish")

    def claude_states(self):
        return [e["state"] for e in self.events if e["type"] == "claude_auth"]

    async def test_startup_reports_signed_out_and_signed_in(self):
        await self.build(FakeClaudeAuth(signed_in=False))
        self.assertEqual(self.claude_states(), ["signed_out"])
        hello = await self.orch.hello()
        self.assertEqual(hello["claude_auth"]["state"], "signed_out")

    async def test_sign_in_completes_and_retires_the_stale_token(self):
        auth = FakeClaudeAuth(signed_in=False, had_token=True)
        await self.build(auth)
        await self.orch.handle(cmd("claude_login"))
        await self.settle()
        self.assertEqual(self.claude_states(), ["signed_out", "signing_in", "signed_in"])
        self.assertEqual(auth.logins, 1)
        notice = [e for e in self.events if e["type"] == "notice"][-1]["message"]
        self.assertIn("Signed in to Claude", notice)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", notice)

    async def test_failed_login_says_so_and_can_be_retried(self):
        auth = FakeClaudeAuth(signed_in=False, login_ok=False)
        await self.build(auth)
        await self.orch.handle(cmd("claude_login"))
        await self.settle()
        self.assertEqual(self.claude_states()[-1], "failed")
        auth.login_ok = True
        await self.orch.handle(cmd("claude_login"))
        await self.settle()
        self.assertEqual(self.claude_states()[-1], "signed_in")

    async def test_second_click_while_waiting_does_not_start_another(self):
        auth = FakeClaudeAuth(signed_in=False, hold=True)
        await self.build(auth)
        await self.orch.handle(cmd("claude_login"))
        await self.orch.handle(cmd("claude_login"))
        await asyncio.sleep(0.05)
        self.assertEqual(auth.logins, 1)

    async def test_cancel_restores_the_previous_state(self):
        auth = FakeClaudeAuth(signed_in=False, hold=True)
        await self.build(auth)
        await self.orch.handle(cmd("claude_login"))
        await asyncio.sleep(0.05)
        await self.orch.handle(cmd("claude_login_cancel"))
        self.assertEqual(self.claude_states()[-2:], ["signing_in", "signed_out"])
        self.assertEqual(auth.cancels, 1)

    async def test_rejected_sign_in_during_a_turn_marks_it_expired(self):
        text = "Failed to authenticate. API Error: 401 OAuth access token has been revoked." + SIGN_IN_HINT
        await self.build(FakeClaudeAuth(signed_in=True),
                         scripts=[[SessionStarted(MOCK_SESSION, "m"), TurnResult(text, MOCK_SESSION, True, 0, 5)]])
        await self.orch.handle(cmd("text_submit", text="hello"))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if self.claude_states()[-1] == "expired":
                break
        self.assertEqual(self.claude_states()[-2:], ["signed_in", "expired"])
        # a periodic re-check must not wipe the known-expired state (the CLI still says "logged in")
        await self.orch.handle(cmd("claude_auth_check"))
        self.assertEqual(self.claude_states()[-1], "expired")

    async def test_login_unavailable_without_a_claude_auth_helper(self):
        await self.build(None)
        await self.orch.handle(cmd("claude_login"))
        self.assertEqual([e["code"] for e in self.events if e["type"] == "error"], ["claude_auth"])

class SignInHintTest(unittest.TestCase):
    def test_a_tablet_cannot_start_a_sign_in(self):
        for name in ("claude_login", "claude_login_cancel", "claude_auth_check"):
            self.assertIsNotNone(permit("remote", cmd(name)))
            self.assertIsNone(permit("desktop", cmd(name)))
    def test_hint_detected_only_on_auth_failures(self):
        out = StreamParser().feed({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 401,
                                   "session_id": "s", "result": "Failed to authenticate"})
        from barnaby_engine.agent import needs_sign_in
        self.assertTrue(needs_sign_in(out[0].text))
        self.assertFalse(needs_sign_in("All tests pass."))
