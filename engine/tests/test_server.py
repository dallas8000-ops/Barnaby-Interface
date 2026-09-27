"""Bridge security: token auth, origin allowlist, schema validation."""
import asyncio
import json
import unittest

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from barnaby_engine.server import Bridge

TOKEN = "t" * 40


class FakeOrch:
    def __init__(self):
        self.handled = []
        self._recording = False

    async def hello(self):
        return {"type": "hello", "state": "idle"}

    async def handle(self, cmd):
        self.handled.append(cmd)


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = Bridge(TOKEN)
        self.orch = FakeOrch()
        self.bridge.orchestrator = self.orch
        self.server = await self.bridge.serve(port=0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_valid_auth_gets_hello_and_commands_dispatch(self):
        async with connect(self.url, origin="tauri://localhost") as ws:
            await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
            hello = json.loads(await ws.recv())
            self.assertEqual(hello["type"], "hello")
            self.assertEqual(hello["protocol"], 1)
            await ws.send(json.dumps({"type": "ptt_start"}))
            await asyncio.sleep(0.05)
        self.assertEqual(self.orch.handled[0].type, "ptt_start")

    async def test_wrong_token_closed_4401(self):
        async with connect(self.url) as ws:
            await ws.send(json.dumps({"type": "auth", "token": "x" * 40}))
            with self.assertRaises(ConnectionClosed) as ctx:
                await ws.recv()
            self.assertEqual(ctx.exception.rcvd.code, 4401)

    async def test_command_before_auth_rejected(self):
        async with connect(self.url) as ws:
            await ws.send(json.dumps({"type": "ptt_start"}))
            with self.assertRaises(ConnectionClosed):
                await ws.recv()
        self.assertEqual(self.orch.handled, [])

    async def test_foreign_origin_rejected_at_handshake(self):
        with self.assertRaises(InvalidStatus) as ctx:
            async with connect(self.url, origin="https://evil.example"):
                pass
        self.assertEqual(ctx.exception.response.status_code, 403)

    async def test_invalid_command_reports_error_but_keeps_socket(self):
        async with connect(self.url) as ws:
            await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
            await ws.recv()
            await ws.send(json.dumps({"type": "set_config", "values": {}, "extra": 1}))
            err = json.loads(await ws.recv())
            self.assertEqual(err["code"], "bad_command")
            await ws.send(json.dumps({"type": "rm_rf"}))
            self.assertEqual(json.loads(await ws.recv())["code"], "bad_command")
            await ws.send(json.dumps({"type": "ping"}))
            await asyncio.sleep(0.05)
        self.assertEqual([c.type for c in self.orch.handled], ["ping"])

    async def test_newer_client_replaces_older(self):
        async with connect(self.url) as a:
            await a.send(json.dumps({"type": "auth", "token": TOKEN}))
            await a.recv()
            async with connect(self.url) as b:
                await b.send(json.dumps({"type": "auth", "token": TOKEN}))
                await b.recv()
                with self.assertRaises(ConnectionClosed) as ctx:
                    await a.recv()
                self.assertEqual(ctx.exception.rcvd.code, 4000)

    async def test_plain_http_gets_health_response(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", int(self.url.rsplit(":", 1)[1]))
        writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        head = await asyncio.wait_for(reader.readline(), 2)
        writer.close()
        self.assertIn(b"200", head)

    async def test_short_token_refused(self):
        with self.assertRaises(ValueError):
            Bridge("short")


if __name__ == "__main__":
    unittest.main()
