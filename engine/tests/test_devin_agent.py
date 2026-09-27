"""DevinAgent against a scripted fake of Devin's v1 API (shapes captured from the real API)."""
import asyncio
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from barnaby_engine.agent import AgentError, SessionStarted, TextDelta, ToolEnd, ToolStart, TurnResult
from barnaby_engine.devin_agent import DevinAgent, DevinApiError, DevinClient, build_prompt, repo_hint

SID = "devin-0123456789abcdef0123456789abcdef"
URL = "https://app.devin.ai/sessions/0123456789abcdef0123456789abcdef"


def msg(eid, text, kind="devin_message"):
    return {"event_id": eid, "message": text, "origin": "web", "timestamp": "2026-09-27T16:00:00Z",
            "type": kind, "user_id": None, "username": None}


def detail(status, messages, status_enum=None, pr=None):
    return {"session_id": SID, "status": status, "status_enum": status_enum or status, "messages": messages,
            "created_at": "", "updated_at": "", "pull_request": pr, "structured_output": None,
            "playbook_id": None, "snapshot_id": None, "tags": ["barnaby"], "title": "t"}


class FakeClient:
    """Pops one scripted reply per (method, path-kind) call; records every call."""

    def __init__(self, polls, *, created=None, current=None, message_reply=None):
        self.polls = list(polls)
        self.created = created or {"session_id": SID, "url": URL, "is_new_session": True}
        self.current = current
        self.message_reply = message_reply
        self.calls = []

    def has_key(self):
        return True

    async def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == "/sessions":
            return self.created
        if method == "POST" and path.endswith("/message"):
            return self.message_reply
        if method == "GET" and self.current is not None and not any(c[0] == "POST" for c in self.calls):
            cur = self.current
            if isinstance(cur, Exception):
                raise cur
            return cur
        item = self.polls.pop(0) if self.polls else detail("working", [])
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


async def collect(agent, prompt="fix the login bug", session_id=None, workspace=""):
    return [e async for e in agent.run(prompt, workspace=workspace, session_id=session_id)]


def texts(events):
    return "".join(e.text for e in events if isinstance(e, TextDelta))


def result(events) -> TurnResult:
    return [e for e in events if isinstance(e, TurnResult)][-1]


class NewSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_creates_streams_and_finishes_when_blocked(self):
        client = FakeClient([
            detail("working", []),
            detail("working", [msg("e1", "Looking at the login flow.", "initial_user_message") | {"type": "initial_user_message"},
                               msg("e2", "Looking at the login flow.")]),
            detail("blocked", [msg("e2", "Looking at the login flow."), msg("e3", "Fixed. Want a PR?")]),
        ])
        agent = DevinAgent(client, poll_s=0, clock=Clock())
        events = await collect(agent)
        self.assertEqual(events[0], SessionStarted(session_id=SID, model=None))
        self.assertIsInstance(events[1], ToolStart)
        self.assertEqual(events[1].input["url"], URL)
        self.assertEqual(texts(events), "Looking at the login flow.\n\nFixed. Want a PR?\n\n")
        res = result(events)
        self.assertEqual(res.text, "Looking at the login flow.\n\nFixed. Want a PR?")
        self.assertEqual(res.session_id, SID)
        self.assertFalse(res.is_error)
        method, path, body = client.calls[0]
        self.assertEqual((method, path), ("POST", "/sessions"))
        self.assertTrue(body["prompt"].startswith("fix the login bug\n\n---\nSent from Barnaby"))
        self.assertEqual(body["tags"], ["barnaby"])

    async def test_user_messages_are_not_echoed(self):
        client = FakeClient([detail("finished", [msg("u1", "fix the login bug", "user_message"), msg("d1", "Done.")])])
        events = await collect(DevinAgent(client, poll_s=0, clock=Clock()))
        self.assertEqual(texts(events), "Done.\n\n")

    async def test_pull_request_is_reported(self):
        pr = {"url": "https://github.com/o/r/pull/7"}
        client = FakeClient([detail("finished", [msg("d1", "Opened a PR.")], pr=pr)])
        events = await collect(DevinAgent(client, poll_s=0, clock=Clock()))
        self.assertIn("Pull request: https://github.com/o/r/pull/7", result(events).text)
        self.assertEqual([e for e in events if isinstance(e, ToolEnd)][0].preview, pr["url"])

    async def test_bad_create_response(self):
        client = FakeClient([], created={"session_id": "../../etc", "url": URL})
        with self.assertRaises(AgentError):
            await collect(DevinAgent(client, poll_s=0, clock=Clock()))


class FollowUpTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_blocked_status_is_not_the_answer(self):
        """After a follow-up, the session still says blocked until Devin picks it up."""
        old = [msg("d1", "Fixed. Want a PR?")]
        client = FakeClient(
            [detail("blocked", old),                                   # stale: must not end the turn
             detail("blocked", old + [msg("u2", "yes open one", "user_message")]),
             detail("working", old + [msg("u2", "yes open one", "user_message")]),
             detail("finished", old + [msg("u2", "x", "user_message"), msg("d2", "PR opened.")])],
            current=detail("blocked", old))
        events = await collect(DevinAgent(client, poll_s=0, clock=Clock()), "yes open one", session_id=SID)
        self.assertEqual(texts(events), "PR opened.\n\n")  # d1 was already seen
        self.assertEqual(client.calls[1], ("POST", f"/sessions/{SID}/message", {"message": "yes open one"}))
        self.assertFalse(any(c[1] == "/sessions" for c in client.calls))  # no new session

    async def test_expired_session_starts_a_new_one(self):
        client = FakeClient([detail("finished", [msg("d1", "Done.")])],
                            current=detail("expired", [], status_enum="expired"))
        events = await collect(DevinAgent(client, poll_s=0, clock=Clock()), session_id=SID)
        self.assertIn(("POST", "/sessions"), [(c[0], c[1]) for c in client.calls])
        self.assertIn("expired, so I started a new one", texts(events))

    async def test_missing_session_starts_a_new_one(self):
        client = FakeClient([detail("finished", [msg("d1", "Done.")])],
                            current=DevinApiError("gone", 404))
        events = await collect(DevinAgent(client, poll_s=0, clock=Clock()), session_id=SID)
        self.assertIn("gone, so I started a new one", texts(events))

    async def test_non_devin_session_id_is_ignored(self):
        client = FakeClient([detail("finished", [msg("d1", "Done.")])])
        await collect(DevinAgent(client, poll_s=0, clock=Clock()), session_id="../x/../y")
        self.assertEqual(client.calls[0][:2], ("POST", "/sessions"))


class ResilienceTest(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_and_blips_are_retried(self):
        client = FakeClient([DevinApiError("slow down", 429, 0), DevinApiError("HTTP 502", 502),
                             detail("finished", [msg("d1", "Done.")])])
        agent = DevinAgent(client, poll_s=0, clock=Clock())
        agent._sleep = lambda s: asyncio.sleep(0)  # don't wait out the backoff
        events = await collect(agent)
        self.assertEqual(result(events).text, "Done.")

    async def test_gives_up_after_repeated_errors(self):
        client = FakeClient([DevinApiError("HTTP 500", 500)] * 5)
        agent = DevinAgent(client, poll_s=0, clock=Clock(), max_errors=5)
        agent._sleep = lambda s: asyncio.sleep(0)
        with self.assertRaises(AgentError) as ctx:
            await collect(agent)
        self.assertIn("may still be running", str(ctx.exception))

    async def test_auth_failure_is_immediate(self):
        client = FakeClient([DevinApiError("Devin rejected the API key.", 401)])
        with self.assertRaises(DevinApiError):
            await collect(DevinAgent(client, poll_s=0, clock=Clock()))

    async def test_long_task_times_out_without_error(self):
        client = FakeClient([detail("working", [])] * 50)
        events = await collect(DevinAgent(client, poll_s=0, clock=Clock(), watch_timeout_s=600))
        res = result(events)
        self.assertFalse(res.is_error)
        self.assertIn("still working", res.text)
        self.assertIn(URL, res.text)

    async def test_cancel_stops_watching(self):
        client = FakeClient([detail("working", [])] * 1000)
        agent = DevinAgent(client, poll_s=0.01, clock=Clock(), watch_timeout_s=10_000)
        gen = agent.run("x", workspace="", session_id=None)
        events = [await gen.__anext__(), await gen.__anext__()]  # SessionStarted, ToolStart
        await agent.cancel()
        events += [e async for e in gen]
        self.assertIn("keeps running in the cloud", result(events).text)

    async def test_expired_mid_turn(self):
        client = FakeClient([detail("expired", [], status_enum="expired")])
        res = result(await collect(DevinAgent(client, poll_s=0, clock=Clock())))
        self.assertTrue(res.is_error)
        self.assertIn("expired", res.text)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class ClientTest(unittest.TestCase):
    def run_call(self, opener, key="apk_user_" + "k" * 30):
        client = DevinClient(lambda: key, opener=opener)
        return asyncio.run(client.call("POST", "/sessions", {"prompt": "p"}))

    def test_sends_key_only_in_header_to_fixed_host(self):
        seen = {}

        def opener(req, timeout):
            seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), req.data
            return FakeResponse(json.dumps({"session_id": SID, "url": URL}).encode())

        self.assertEqual(self.run_call(opener)["session_id"], SID)
        self.assertEqual(seen["url"], "https://api.devin.ai/v1/sessions")
        self.assertEqual(seen["auth"], "Bearer apk_user_" + "k" * 30)
        self.assertNotIn(b"apk_user_", seen["body"])

    def test_errors_are_mapped_and_never_contain_the_key(self):
        key = "apk_user_" + "s" * 30
        for code, expect in ((401, "rejected the API key"), (403, "rejected the API key"),
                             (404, "couldn't find"), (429, "rate limiting"), (500, "HTTP 500")):
            def opener(req, timeout, code=code):
                raise urllib.error.HTTPError(req.full_url, code, "x", {"Retry-After": "7"}, io.BytesIO(key.encode()))
            with self.subTest(code=code):
                with self.assertRaises(DevinApiError) as ctx:
                    self.run_call(opener, key)
                self.assertIn(expect, str(ctx.exception))
                self.assertNotIn(key, str(ctx.exception))
                self.assertEqual(ctx.exception.status, code)
                if code == 429:
                    self.assertEqual(ctx.exception.retry_after, 7.0)

    def test_no_key(self):
        with self.assertRaises(DevinApiError) as ctx:
            self.run_call(lambda *a, **k: None, key=None)
        self.assertIn("Settings", str(ctx.exception))

    def test_refuses_plain_http_base(self):
        with self.assertRaises(ValueError):
            DevinClient(lambda: "k", base="http://api.devin.ai/v1")

    def test_oversized_and_non_json(self):
        with self.assertRaises(DevinApiError):
            self.run_call(lambda req, timeout: FakeResponse(b"x" * (5 * 1024 * 1024 + 2)))
        with self.assertRaises(DevinApiError):
            self.run_call(lambda req, timeout: FakeResponse(b"<html>"))
        self.assertIsNone(self.run_call(lambda req, timeout: FakeResponse(b"")))


class RepoHintTest(unittest.TestCase):
    def workspace(self, url):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / ".git").mkdir()
        (Path(tmp) / ".git" / "config").write_text(
            f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
        return tmp

    def test_credentials_are_stripped(self):
        for url, want in (
            ("https://ghp_secret123@github.com/dallas8000-ops/barnaby.git", "https://github.com/dallas8000-ops/barnaby.git"),
            ("https://ray:ghp_secret@github.com/o/r.git", "https://github.com/o/r.git"),
            ("ssh://git:hunter2@example.com:2222/o/r.git", "ssh://example.com:2222/o/r.git"),
            ("ssh://git@example.com/o/r.git", "ssh://git@example.com/o/r.git"),
            ("git@github.com:o/r.git", "git@github.com:o/r.git"),
        ):
            with self.subTest(url=url):
                self.assertEqual(repo_hint(self.workspace(url)), want)

    def test_unusable_remotes(self):
        for url in ("file:///C:/repos/x", "C:\\repos\\x", "https://", "https://a b/c"):
            with self.subTest(url=url):
                self.assertIsNone(repo_hint(self.workspace(url)))
        self.assertIsNone(repo_hint(tempfile.mkdtemp()))

    def test_prompt_includes_clean_repo(self):
        ws = self.workspace("https://tok@github.com/o/r.git")
        prompt = build_prompt("add tests", ws)
        self.assertIn("Repository: https://github.com/o/r.git", prompt)
        self.assertNotIn("tok@", prompt)


if __name__ == "__main__":
    unittest.main()
