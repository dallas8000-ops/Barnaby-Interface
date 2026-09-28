"""Bridge as a hub: several clients with roles, broadcast, per-device replacement, role policy."""
import asyncio
import json
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from barnaby_engine.protocol import parse_command
from barnaby_engine.server import MAX_REMOTE_CLIENTS, Bridge, ClientInfo, permit

TOKEN = "t" * 40


class FakeOrch:
    def __init__(self):
        self.handled = []
        self.remote_counts = []
        self._recording = False

    async def hello(self):
        return {"type": "hello", "state": "idle"}

    async def handle(self, cmd):
        self.handled.append(cmd.type)

    def set_remote_listeners(self, n):
        self.remote_counts.append(n)


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


class _HubBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = Bridge(TOKEN)
        self.orch = FakeOrch()
        self.bridge.orchestrator = self.orch
        self.local = await self.bridge.serve(port=0)
        self.local_url = f"ws://127.0.0.1:{self.local.sockets[0].getsockname()[1]}"

        # Stand-in for the TLS tablet listener: authentication is its job; here the
        # device id is taken from the path so tests can connect as different devices.
        async def remote_handler(ws):
            await self.bridge.run_client(ws, ClientInfo(role="remote", device_id=ws.request.path.strip("/")))
        self.remote = await serve(remote_handler, "127.0.0.1", 0)
        self.remote_base = f"ws://127.0.0.1:{self.remote.sockets[0].getsockname()[1]}"

    async def asyncTearDown(self):
        for s in (self.local, self.remote):
            s.close()
            await s.wait_closed()

    async def desktop(self):
        ws = await connect(self.local_url)
        await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        hello = json.loads(await ws.recv())
        return ws, hello

    async def tablet(self, device="d1"):
        ws = await connect(f"{self.remote_base}/{device}")
        hello = json.loads(await ws.recv())
        return ws, hello

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0.01)

class HubTest(_HubBase):
    async def test_hello_goes_to_the_newcomer_only_and_carries_role(self):
        d, dh = await self.desktop()
        self.assertEqual(dh["role"], "desktop")
        t, th = await self.tablet()
        self.assertEqual(th["role"], "remote")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(d.recv(), 0.2)  # the desktop didn't get the tablet's hello
        await d.close(); await t.close()

    async def test_events_broadcast_to_every_client(self):
        d, _ = await self.desktop()
        t, _ = await self.tablet()
        await self.bridge.emit({"type": "notice", "message": "hi"})
        self.assertEqual(json.loads(await d.recv())["message"], "hi")
        self.assertEqual(json.loads(await t.recv())["message"], "hi")
        await d.close(); await t.close()

    async def test_desktop_and_tablet_coexist_and_both_dispatch(self):
        d, _ = await self.desktop()
        t, _ = await self.tablet()
        await d.send(json.dumps({"type": "ping"}))
        await t.send(json.dumps({"type": "ping"}))
        await self.settle()
        self.assertEqual(self.orch.handled, ["ping", "ping"])
        await d.close(); await t.close()

    async def test_emit_desktop_skips_tablets(self):
        d, _ = await self.desktop()
        t, _ = await self.tablet()
        await self.bridge.emit_desktop({"type": "tablet_pairing", "url": "https://x/#pair=CODE"})
        self.assertEqual(json.loads(await d.recv())["type"], "tablet_pairing")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(t.recv(), 0.2)
        await d.close(); await t.close()

    async def test_same_device_is_replaced_other_devices_coexist(self):
        a, _ = await self.tablet("d1")
        b, _ = await self.tablet("d2")
        a2, _ = await self.tablet("d1")
        with self.assertRaises(ConnectionClosed) as ctx:
            await a.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4000)
        await self.bridge.emit({"type": "notice", "message": "x"})
        self.assertEqual(json.loads(await b.recv())["type"], "notice")
        self.assertEqual(json.loads(await a2.recv())["type"], "notice")
        await b.close(); await a2.close()

    async def test_remote_connection_cap(self):
        socks = [ (await self.tablet(f"d{i}"))[0] for i in range(MAX_REMOTE_CLIENTS)]
        extra = await connect(f"{self.remote_base}/dX")
        with self.assertRaises(ConnectionClosed) as ctx:
            await extra.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4430)  # 4429 means locked out; different meaning
        for s in socks:
            await s.close()

    async def test_orchestrator_told_how_many_tablets_are_connected(self):
        t1, _ = await self.tablet("d1")
        t2, _ = await self.tablet("d2")
        await t1.close()
        await self.settle()
        await t2.close()
        await self.settle()
        self.assertEqual(self.orch.remote_counts, [1, 2, 1, 0])

    async def test_disconnect_device_closes_it(self):
        t, _ = await self.tablet("d1")
        other, _ = await self.tablet("d2")
        self.assertTrue(await self.bridge.disconnect_device("d1"))
        with self.assertRaises(ConnectionClosed) as ctx:
            await t.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4403)
        self.assertFalse(await self.bridge.disconnect_device("nope"))
        self.assertEqual(self.bridge.connected_devices(), {"d2"})
        await other.close()

    async def test_refused_remote_command_reports_error_and_is_not_dispatched(self):
        t, _ = await self.tablet()
        await t.send(json.dumps({"type": "set_secret", "name": "devin", "value": "apk_user_" + "x" * 30}))
        err = json.loads(await t.recv())
        self.assertEqual((err["type"], err["code"]), ("error", "not_allowed"))
        self.assertNotIn("apk_user_", json.dumps(err))
        await self.settle()
        self.assertEqual(self.orch.handled, [])
        await t.close()


class PolicyTest(unittest.TestCase):
    def test_desktop_may_do_everything_existing(self):
        for c in (cmd("set_secret", name="devin", value="k" * 20), cmd("ptt_start"),
                  cmd("set_config", values={"workspace": "C:/x"}), cmd("list_devices")):
            self.assertIsNone(permit("desktop", c), c.type)

    def test_remote_denials(self):
        for c in (cmd("set_secret", name="devin", value="k" * 20), cmd("delete_secret", name="devin"),
                  cmd("ptt_start"), cmd("ptt_stop"), cmd("list_devices"),
                  cmd("open_in_editor", editor="vscode", path="a.py")):
            with self.subTest(c=c.type):
                self.assertIsNotNone(permit("remote", c))

    def test_remote_set_config_allowlist_is_all_or_nothing(self):
        self.assertIsNone(permit("remote", cmd("set_config", values={"engine": "cursor"})))
        self.assertIsNone(permit("remote", cmd("set_config", values={"review_transcripts": False,
                                                                   "claude_model": "opus"})))
        for bad in ({"workspace": "C:/evil"}, {"engine": "cursor", "workspace": "C:/evil"},
                    {"input_device": 2}, {"continuous_mode_enabled": True}, {"tts_enabled": True}):
            with self.subTest(values=bad):
                self.assertIsNotNone(permit("remote", cmd("set_config", values=bad)))

    def test_remote_ordinary_work_allowed(self):
        for c in (cmd("text_submit", text="run the tests"), cmd("approve", request_id="r1"),
                  cmd("deny", request_id="r1"), cmd("cancel"), cmd("stop_speaking"), cmd("new_session"),
                  cmd("load_session", session_id="s1"), cmd("list_sessions"), cmd("ping"),
                  cmd("discard_draft", draft_id="d"), cmd("run_template", template_id="tdd")):
            with self.subTest(c=c.type):
                self.assertIsNone(permit("remote", c))


if __name__ == "__main__":
    unittest.main()


class ReviewRegressionTest(_HubBase):
    """Findings from the independent review (2026-09-28)."""

    async def test_revoke_during_reconnect_cannot_leave_the_device_connected(self):
        # F1: the old socket's close used to be awaited before the new one was registered,
        # so a revoke in that window found nothing to close.
        old, _ = await self.tablet("d1")
        (old_server_ws,) = [w for w, i in self.bridge._clients.items() if i.device_id == "d1"]
        real_close = old_server_ws.close

        async def slow_close(*a, **k):
            await asyncio.sleep(0.5)
            await real_close(*a, **k)
        old_server_ws.close = slow_close
        connecting = asyncio.create_task(self.tablet("d1"))
        await asyncio.sleep(0.15)
        self.assertTrue(await self.bridge.disconnect_device("d1"))
        new, _ = await connecting
        with self.assertRaises(ConnectionClosed):
            await asyncio.wait_for(new.recv(), 2)
        await asyncio.sleep(0.6)
        self.assertEqual(self.bridge.connected_devices(), set())

    async def test_admit_is_rechecked_after_registration(self):
        async def handler(ws):
            await self.bridge.run_client(ws, ClientInfo(role="remote", device_id="gone"), admit=lambda: False)
        srv = await serve(handler, "127.0.0.1", 0)
        ws = await connect(f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}")
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4403)
        self.assertEqual(self.bridge.connected_devices(), set())
        srv.close()
        await srv.wait_closed()

    async def test_stalled_client_is_dropped_instead_of_freezing_everyone(self):
        # F2: a client that stops reading made every emit hang forever.
        import barnaby_engine.server as server_mod

        class Stalled:
            aborted = False
            async def send(self, _):
                await asyncio.sleep(3600)
            async def close(self, *a, **k):
                pass
            class transport:
                @staticmethod
                def abort():
                    Stalled.aborted = True

        d, _ = await self.desktop()
        stalled = Stalled()
        self.bridge._clients[stalled] = ClientInfo(role="remote", device_id="frozen")
        old = server_mod.SEND_TIMEOUT_S
        server_mod.SEND_TIMEOUT_S = 0.1
        try:
            await asyncio.wait_for(self.bridge.emit({"type": "notice", "message": "still alive"}), 2)
        finally:
            server_mod.SEND_TIMEOUT_S = old
        self.assertEqual(json.loads(await d.recv())["message"], "still alive")
        self.assertNotIn(stalled, self.bridge._clients)
        self.assertTrue(Stalled.aborted)
        await d.close()

    async def test_orchestrator_can_veto_a_remote_command(self):
        # F3: remote load_session could switch the PC's project folder.
        self.orch.remote_check = lambda c: "Open that session on the PC." if c.type == "load_session" else None
        t, _ = await self.tablet()
        await t.send(json.dumps({"type": "load_session", "session_id": "other-project"}))
        err = json.loads(await asyncio.wait_for(t.recv(), 2))
        self.assertEqual((err["code"], err["message"]), ("not_allowed", "Open that session on the PC."))
        await self.settle()
        self.assertEqual(self.orch.handled, [])
        await t.close()
