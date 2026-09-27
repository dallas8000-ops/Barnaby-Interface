"""IDE bridge security: runs a real server and attacks it the way a web page or
another local process would."""
import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from barnaby_engine.ide_bridge import IdeBridge

TOKEN = "t" * 40


class TestIdeBridge(unittest.TestCase):
    def test_initialization(self):
        bridge = IdeBridge(port=8765, token=TOKEN)
        self.assertEqual(bridge.port, 8765)
        self.assertFalse(bridge.is_running)
        self.assertEqual(bridge.get_client_count(), 0)

    def test_generates_strong_token_and_rejects_weak(self):
        self.assertGreaterEqual(len(IdeBridge().token), 32)
        with self.assertRaises(ValueError):
            IdeBridge(token="short")


class TestIdeBridgeServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.discovery = Path(self._tmp.name) / "ide-bridge.json"
        self.bridge = IdeBridge(port=0, token=TOKEN, discovery_path=self.discovery)
        await self.bridge.start()
        self.url = f"ws://127.0.0.1:{self.bridge.port}"

    async def asyncTearDown(self):
        await self.bridge.stop()
        self._tmp.cleanup()

    async def expect_closed_unauthorized(self, ws):
        with self.assertRaises(ConnectionClosed) as ctx:
            await asyncio.wait_for(ws.recv(), timeout=7)
        self.assertEqual(ctx.exception.rcvd.code, 4401)

    async def test_browser_origin_refused_at_handshake(self):
        for origin in ("https://evil.example", "http://localhost:1420", "null"):
            with self.subTest(origin=origin):
                with self.assertRaises(InvalidStatus) as ctx:
                    await connect(self.url, origin=origin)
                self.assertEqual(ctx.exception.response.status_code, 403)

    async def test_no_auth_wrong_token_and_garbage_are_dropped(self):
        for first in (json.dumps({"type": "auth", "token": "x" * 40}), "not json",
                      json.dumps({"type": "hello"}), json.dumps(["auth", TOKEN])):
            with self.subTest(first=first[:20]):
                async with connect(self.url) as ws:
                    await ws.send(first)
                    await self.expect_closed_unauthorized(ws)
        self.assertEqual(self.bridge.get_client_count(), 0)

    async def test_unauthenticated_client_never_receives_broadcasts(self):
        async with connect(self.url) as ws:  # connected, but hasn't authenticated
            await self.bridge.broadcast({"type": "agent_text", "delta": "secret transcript"})
            await self.expect_closed_unauthorized(ws)  # times out into 4401, receiving nothing first

    async def test_authenticated_client_receives_broadcast(self):
        async with connect(self.url) as ws:
            await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
            for _ in range(50):
                if self.bridge.get_client_count():
                    break
                await asyncio.sleep(0.02)
            await self.bridge.broadcast({"type": "ide_context_update", "file_path": "a.py"})
            self.assertEqual(json.loads(await asyncio.wait_for(ws.recv(), 3))["file_path"], "a.py")

    async def test_discovery_file_published_private_and_removed(self):
        data = json.loads(self.discovery.read_text())
        self.assertEqual(data, {"port": self.bridge.port, "token": TOKEN, "pid": os.getpid()})
        if sys.platform != "win32":
            self.assertEqual(self.discovery.stat().st_mode & 0o777, 0o600)
        await self.bridge.stop()
        self.assertFalse(self.discovery.exists())

    async def test_stop_with_connected_clients(self):
        clients = []
        for _ in range(3):
            ws = await connect(self.url)
            await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
            clients.append(ws)
        for _ in range(50):
            if self.bridge.get_client_count() == 3:
                break
            await asyncio.sleep(0.02)
        await self.bridge.stop()  # used to raise "Set changed size during iteration"
        self.assertEqual(self.bridge.get_client_count(), 0)


class TestPortFallback(unittest.IsolatedAsyncioTestCase):
    async def test_taken_port_falls_back(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        bridge = IdeBridge(port=taken, token=TOKEN)
        try:
            await bridge.start()
            self.assertNotEqual(bridge.port, taken)
            self.assertTrue(bridge.is_running)
        finally:
            await bridge.stop()
            busy.close()


if __name__ == "__main__":
    unittest.main()
