"""RemoteServer end to end over real TLS: static files, CA download, pairing, auth,
Origin and peer checks, lockout, revoke. The client trusts only Barnaby's own CA, so
these also prove the name-constrained chain validates."""
import asyncio
import json
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from barnaby_engine.remote import Lockout, RemoteServer
from barnaby_engine.server import Bridge

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


class _ServerBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        static = self.tmp / "dist"
        (static / "assets").mkdir(parents=True)
        (static / "index.html").write_text("<!doctype html><title>Barnaby</title>")
        (static / "assets" / "app.js").write_text("console.log('hi')")
        (self.tmp / "secret.txt").write_text("outside dist")
        self.bridge = Bridge(TOKEN)
        self.orch = FakeOrch()
        self.bridge.orchestrator = self.orch
        self.changes = 0
        self.peer_ok = True

        def on_change():
            self.changes += 1

        self.server = RemoteServer(self.bridge, data_dir=self.tmp / "tablet", port=0, static_dir=static,
                                   host="127.0.0.1", ips=lambda: [], on_change=on_change,
                                   peer_allowed=lambda ip: self.peer_ok,
                                   network_check=lambda: None)  # never depend on this machine's Windows network
        await self.server.start()
        self.port = self.server.port
        self.base = f"https://127.0.0.1:{self.port}"
        self.ctx = ssl.create_default_context(cafile=str(self.tmp / "tablet" / "ca.pem"))

    async def asyncTearDown(self):
        await self.server.stop()

    # ---- helpers
    def get(self, path):
        def run():
            try:
                with urllib.request.urlopen(self.base + path, context=self.ctx, timeout=5) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read()
        return asyncio.to_thread(run)

    def ws(self, origin=None):
        return connect(f"wss://127.0.0.1:{self.port}/ws", ssl=self.ctx,
                       origin=origin if origin is not None else self.base)

    async def pair(self, name="Galaxy Tab S10"):
        code, _ = self.server.pairing.start()
        ws = await self.ws()
        await ws.send(json.dumps({"type": "pair", "code": code, "name": name}))
        paired = json.loads(await ws.recv())
        return ws, paired

    # ---- static and CA
class RemoteServerTest(_ServerBase):
    async def test_serves_the_app_with_security_headers(self):
        status, headers, body = await self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Barnaby", body)
        csp = headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("wss:", csp.replace("'self'", ""))  # no wildcard socket hosts
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, headers, body = await self.get("/assets/app.js")
        self.assertEqual((status, body), (200, b"console.log('hi')"))
        self.assertIn("javascript", headers["Content-Type"])

    async def test_spa_fallback_and_traversal_blocked(self):
        status, _, body = await self.get("/settings")
        self.assertEqual(status, 200)
        self.assertIn(b"Barnaby", body)
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt",
                     "/..%2fsecret.txt", "/assets/missing.js"):
            with self.subTest(path=path):
                status, _, body = await self.get(path)
                self.assertNotIn(b"outside dist", body)
                self.assertIn(status, (400, 404))

    async def test_ca_certificate_download(self):
        status, headers, body = await self.get("/barnaby-ca.crt")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"-----BEGIN CERTIFICATE-----"))
        self.assertIn("x509", headers["Content-Type"])
        self.assertNotIn(b"PRIVATE KEY", body)

    async def test_non_private_peer_refused(self):
        self.peer_ok = False
        status, _, _ = await self.get("/")
        self.assertEqual(status, 403)
        with self.assertRaises(InvalidStatus) as ctx:
            async with self.ws():
                pass
        self.assertEqual(ctx.exception.response.status_code, 403)

    # ---- sockets
    async def test_cross_origin_socket_refused(self):
        for origin in ("https://evil.example", f"http://127.0.0.1:{self.port}", "null"):
            with self.subTest(origin=origin):
                with self.assertRaises(InvalidStatus) as ctx:
                    async with self.ws(origin=origin):
                        pass
                self.assertEqual(ctx.exception.response.status_code, 403)

    async def test_pairing_then_token_auth(self):
        ws, paired = await self.pair()
        self.assertEqual(paired["type"], "paired")
        token = paired["token"]
        hello = json.loads(await ws.recv())
        self.assertEqual((hello["type"], hello["role"]), ("hello", "remote"))
        self.assertEqual(self.changes, 1)
        self.assertEqual([d["name"] for d in self.server.devices()], ["Galaxy Tab S10"])
        self.assertTrue(self.server.devices()[0]["connected"])
        await ws.close()

        # Next visit: the stored token alone.
        ws = await self.ws()
        await ws.send(json.dumps({"type": "auth", "token": token}))
        self.assertEqual(json.loads(await ws.recv())["role"], "remote")
        await ws.send(json.dumps({"type": "ping"}))
        await asyncio.sleep(0.05)
        self.assertIn("ping", self.orch.handled)
        await ws.close()

    async def test_code_is_single_use(self):
        code, _ = self.server.pairing.start()
        ws = await self.ws()
        await ws.send(json.dumps({"type": "pair", "code": code, "name": "Tab"}))
        await ws.recv()
        await ws.close()
        ws2 = await self.ws()
        await ws2.send(json.dumps({"type": "pair", "code": code, "name": "Thief"}))
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws2.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4401)
        self.assertEqual(len(self.server.devices()), 1)

    async def test_bad_tokens_lock_the_address_out(self):
        for _ in range(Lockout.LIMIT):
            ws = await self.ws()
            await ws.send(json.dumps({"type": "auth", "token": "x" * 43}))
            with self.assertRaises(ConnectionClosed):
                await ws.recv()
        with self.assertRaises(InvalidStatus) as ctx:
            async with self.ws():
                pass
        self.assertEqual(ctx.exception.response.status_code, 429)

    async def test_garbage_first_frame_and_timeout(self):
        ws = await self.ws()
        await ws.send("not json")
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4401)

    async def test_revoke_closes_live_socket_and_token_stops_working(self):
        ws, paired = await self.pair()
        await ws.recv()  # hello
        device_id = paired["device_id"]
        self.assertTrue(await self.server.revoke(device_id))
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4403)
        ws2 = await self.ws()
        await ws2.send(json.dumps({"type": "auth", "token": paired["token"]}))
        with self.assertRaises(ConnectionClosed):
            await ws2.recv()

    async def test_tablet_is_bound_by_role_policy(self):
        ws, _ = await self.pair()
        await ws.recv()  # hello
        await ws.send(json.dumps({"type": "set_config", "values": {"workspace": "C:/evil"}}))
        err = json.loads(await ws.recv())
        self.assertEqual(err["code"], "not_allowed")
        await ws.close()
        self.assertNotIn("set_config", self.orch.handled)

    async def test_pairing_url(self):
        url, expires = self.server.start_pairing(host="192.168.1.20")
        self.assertRegex(url, rf"^https://192\.168\.1\.20:{self.port}/#pair=[A-Za-z0-9_-]{{16,}}$")
        self.assertGreater(expires, 0)

    async def test_stop_closes_everything(self):
        ws, _ = await self.pair()
        await ws.recv()
        await self.server.stop()
        self.assertFalse(self.server.running)
        with self.assertRaises(ConnectionClosed):
            await ws.recv()


class StartFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_port_in_use_raises_clean_error(self):
        tmp = Path(tempfile.mkdtemp())
        bridge = Bridge(TOKEN)
        bridge.orchestrator = FakeOrch()
        first = RemoteServer(bridge, data_dir=tmp / "t", port=0, static_dir=tmp, host="127.0.0.1", ips=lambda: [],
                             network_check=lambda: None)
        await first.start()
        second = RemoteServer(bridge, data_dir=tmp / "t", port=first.port, static_dir=tmp, host="127.0.0.1",
                              ips=lambda: [], network_check=lambda: None)
        with self.assertRaises(OSError):
            await second.start()
        self.assertFalse(second.running)
        await first.stop()


if __name__ == "__main__":
    unittest.main()


class ReviewRegressionServerTest(_ServerBase):
    """Findings from the independent review (2026-09-28)."""

    async def test_big_frames_only_after_authentication(self):
        # F4: 3 MB frames were accepted from anyone on the network before auth.
        ws = await self.ws()
        await ws.send(json.dumps({"type": "auth", "token": "x" * 8000}))
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws.recv()
        self.assertEqual(ctx.exception.rcvd.code, 1009)  # message too big
        ws, _ = await self.pair()
        await ws.recv()  # hello
        await ws.send(json.dumps({"type": "text_submit", "text": "y" * 19_000}))
        await asyncio.sleep(0.05)
        self.assertIn("text_submit", self.orch.handled)
        await ws.close()

    async def test_unauthenticated_connections_are_capped(self):
        # F4: unlimited pending sockets per address.
        from barnaby_engine.remote import MAX_PENDING_PER_IP
        pending = [await self.ws() for _ in range(MAX_PENDING_PER_IP)]
        extra = await self.ws()
        with self.assertRaises(ConnectionClosed) as ctx:
            await asyncio.wait_for(extra.recv(), 2)
        self.assertEqual(ctx.exception.rcvd.code, 1013)
        for p in pending:
            await p.close()

    async def test_non_object_first_frame_counts_as_failure(self):
        # F5: `[1]` crashed the handler (1011) and recorded no failure.
        for frame in ("[1]", "1", "null", '"auth"'):
            with self.subTest(frame=frame):
                ws = await self.ws()
                await ws.send(frame)
                with self.assertRaises(ConnectionClosed) as ctx:
                    await ws.recv()
                self.assertEqual(ctx.exception.rcvd.code, 4401)
        self.assertTrue(self.server.lockout._fails.get("127.0.0.1"))

    async def test_certificate_follows_an_ip_change_while_running(self):
        # F6: the cert was only (re)issued at start(); a DHCP change broke the QR link.
        self.server._ips = lambda: ["10.9.9.9"]
        self.server.tls._ips = self.server._ips
        url, _ = self.server.start_pairing()
        self.assertIn("10.9.9.9", url)

        def peer_sans():
            import socket as s
            with s.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
                with self.ctx.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                    return [v for k, v in tls.getpeercert()["subjectAltName"] if k == "IP Address"]
        self.assertIn("10.9.9.9", await asyncio.to_thread(peer_sans))

    async def test_full_registry_explains_and_keeps_the_code(self):
        # F7a: a full registry burned the code, counted a lockout failure and said only "unauthorized".
        from barnaby_engine.remote import DeviceRegistry
        for i in range(DeviceRegistry.MAX_DEVICES):
            self.server.registry.pair(f"t{i}")
        code, _ = self.server.pairing.start()
        ws = await self.ws()
        await ws.send(json.dumps({"type": "pair", "code": code, "name": "One more"}))
        msg = json.loads(await ws.recv())
        self.assertEqual(msg["type"], "pair_error")
        self.assertIn("Remove one", msg["message"])
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4409)
        self.assertFalse(self.server.lockout._fails.get("127.0.0.1"))
        self.server.registry.revoke(self.server.registry.list()[0]["id"])
        ws, paired = await self.pair_with(code, "One more")
        self.assertEqual(paired["type"], "paired")
        await ws.close()

    async def pair_with(self, code, name):
        ws = await self.ws()
        await ws.send(json.dumps({"type": "pair", "code": code, "name": name}))
        return ws, json.loads(await ws.recv())

    async def test_failed_token_delivery_rolls_back_the_pairing(self):
        # F7b: device saved but the tablet never got its token: an orphan filling a slot.
        from websockets.exceptions import ConnectionClosedError
        code, _ = self.server.pairing.start()

        class Fake:
            remote_address = ("127.0.0.1", 5555)
            closed_with = None
            async def recv(self):
                return json.dumps({"type": "pair", "code": code, "name": "Flaky"})
            async def send(self, _):
                raise ConnectionClosedError(None, None)
            async def close(self, code=1000, reason=""):
                Fake.closed_with = code
        await self.server._handler(Fake())
        self.assertEqual(self.server.registry.list(), [])


class PublicNetworkTest(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_to_start_on_a_public_network(self):
        tmp = Path(tempfile.mkdtemp())
        bridge = Bridge(TOKEN)
        bridge.orchestrator = FakeOrch()
        srv = RemoteServer(bridge, data_dir=tmp / "t", port=0, static_dir=tmp, host="127.0.0.1", ips=lambda: [],
                           network_check=lambda: "Your network is set to Public in Windows.")
        with self.assertRaises(RuntimeError) as ctx:
            await srv.start()
        self.assertIn("Public", str(ctx.exception))
        self.assertFalse(srv.running)

    def test_windows_category_parsing(self):
        from barnaby_engine.remote import network_problem
        self.assertIsNone(network_problem(lambda: {"Private"}))
        self.assertIsNone(network_problem(lambda: {"Public", "DomainAuthenticated"}))
        self.assertIn("Public", network_problem(lambda: {"Public"}))
        self.assertIsNone(network_problem(lambda: None))  # couldn't tell: don't block (firewall still applies)


class SecondReviewServerTest(_ServerBase):
    """Findings from the second independent review (2026-09-28)."""

    async def test_stopping_tablet_access_is_not_a_removal(self):
        # 1: stop() closed tablets with 4403 "device removed", so the tablet forgot its token
        # every time Barnaby closed on the PC.
        ws, paired = await self.pair()
        await ws.recv()  # hello
        await self.server.stop()
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws.recv()
        self.assertEqual(ctx.exception.rcvd.code, 1001)
        self.assertTrue(self.server.registry.has(paired["device_id"]))

    async def test_revoke_still_says_removed(self):
        ws, paired = await self.pair()
        await ws.recv()
        await self.server.revoke(paired["device_id"])
        with self.assertRaises(ConnectionClosed) as ctx:
            await ws.recv()
        self.assertEqual(ctx.exception.rcvd.code, 4403)


class AddressOrderTest(unittest.TestCase):
    def test_default_route_address_comes_first(self):
        # 6: VirtualBox's 192.168.56.1 sorted ahead of the real 192.168.68.x LAN.
        from barnaby_engine.remote import order_addresses
        self.assertEqual(order_addresses("192.168.68.20", {"192.168.56.1", "192.168.68.20", "10.0.0.5"}),
                         ["192.168.68.20", "192.168.56.1", "10.0.0.5"])
        self.assertEqual(order_addresses(None, {"10.0.0.5", "192.168.1.9"}), ["192.168.1.9", "10.0.0.5"])
        self.assertEqual(order_addresses("192.0.2.2", {"192.168.1.9"}), ["192.168.1.9"])  # not RFC 1918


class VpnAddressOrderTest(unittest.TestCase):
    """Found live on the user's PC: Proton VPN's tunnel (10.2.0.2) is the default route, so
    it was listed first and the QR code pointed the tablet at an unreachable address."""

    def test_physical_adapters_win_over_the_default_route(self):
        from barnaby_engine.remote import order_addresses
        self.assertEqual(order_addresses("10.2.0.2", {"10.2.0.2", "192.168.100.4"}, physical={"192.168.100.4"}),
                         ["192.168.100.4", "10.2.0.2"])

    def test_route_still_breaks_ties_among_physical_adapters(self):
        from barnaby_engine.remote import order_addresses
        self.assertEqual(order_addresses("192.168.68.20", {"192.168.56.1", "192.168.68.20", "10.2.0.2"},
                                         physical={"192.168.68.20", "192.168.56.1"}),
                         ["192.168.68.20", "192.168.56.1", "10.2.0.2"])

    def test_unknown_physical_set_keeps_the_old_order(self):
        from barnaby_engine.remote import order_addresses
        self.assertEqual(order_addresses("192.168.1.9", {"192.168.1.9", "10.0.0.5"}, physical=None),
                         ["192.168.1.9", "10.0.0.5"])

    def test_windows_query_parsing(self):
        from barnaby_engine import remote
        self.assertEqual(remote._parse_ip_lines("192.168.100.4\r\n\r\n10.0.0.3\r\nnot-an-ip\r\n"),
                         {"192.168.100.4", "10.0.0.3"})
