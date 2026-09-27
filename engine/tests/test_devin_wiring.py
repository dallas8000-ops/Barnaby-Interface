"""Devin inside the orchestrator: saving the key, availability, routing, and that
the key never appears in anything the engine emits."""
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import SessionStarted, TextDelta, TurnResult
from barnaby_engine.config import Config
from barnaby_engine.mock import FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
from barnaby_engine.orchestrator import Orchestrator, route_engine, tool_summary
from barnaby_engine.protocol import parse_command
from barnaby_engine.store import Store
from barnaby_engine.vault import MemoryCredentials, Vault
from tests.helpers import TempDataDir

KEY = "apk_user_" + "Zq9/+=" * 20
DSID = "devin-0123456789abcdef0123456789abcdef"


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


class DevinWiringTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._data = TempDataDir()
        self._data.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "db.sqlite")
        self.events = []
        self.backend = MemoryCredentials()
        self.vault = Vault(self.backend)
        self.devin = ScriptedAgent([[SessionStarted(DSID, None), TextDelta("On it."),
                                     TurnResult("On it.", DSID, False, None, 5)]])

        async def emit(e):
            self.events.append(e)

        self.config = Config(workspace=self._tmp.name, tts_enabled=False)
        self.orch = Orchestrator(
            config=self.config, store=self.store, profile=VoiceProfile(self.store), recorder=FakeRecorder(),
            transcriber=FakeTranscriber([]), speaker=FakeSpeaker(), emit=emit, vault=self.vault,
            agents={"claude": ScriptedAgent([]), "devin": self.devin},
            engine_available=lambda n: "Add your Devin API key in Settings → Devin first."
            if n == "devin" and not self.vault.has("devin") else None)
        await self.orch.start()

    async def asyncTearDown(self):
        await self.orch.shutdown()
        self.store.close()
        self._tmp.cleanup()
        self._data.__exit__()

    def of(self, t):
        return [e for e in self.events if e["type"] == t]

    def assert_key_never_emitted(self):
        self.assertNotIn(KEY, json.dumps(self.events, default=str))
        self.assertNotIn(KEY[9:40], json.dumps(self.events, default=str))

    async def test_hello_reports_whether_a_key_is_saved(self):
        self.assertEqual((await self.orch.hello())["secrets"], {"devin": False})
        self.backend.items["Barnaby-devin"] = ("devin", KEY)
        hello = await self.orch.hello()
        self.assertEqual(hello["secrets"], {"devin": True})
        self.assertNotIn(KEY, json.dumps(hello, default=str))

    async def test_saving_the_key_makes_devin_available_and_is_never_echoed(self):
        engines = {e["name"]: e for e in self.orch.engines()}
        self.assertFalse(engines["devin"]["available"])
        await self.orch.handle(cmd("set_secret", name="devin", value=KEY))
        self.assertEqual(self.backend.items["Barnaby-devin"], ("devin", KEY))
        self.assertEqual(self.of("secrets")[-1]["items"], {"devin": True})
        self.assertTrue({e["name"]: e for e in self.of("engines")[-1]["items"]}["devin"]["available"])
        self.assertIn("saved to Windows Credential Manager", self.of("notice")[-1]["message"])
        self.assert_key_never_emitted()

    async def test_bad_key_is_refused_without_echo(self):
        bad = "this is not a key at all but long"
        await self.orch.handle(cmd("set_secret", name="devin", value=bad))
        self.assertEqual(self.backend.items, {})
        self.assertEqual(self.of("error")[-1]["code"], "secret")
        self.assertNotIn(bad, json.dumps(self.events))

    async def test_removing_the_key_while_on_devin_falls_back_to_claude(self):
        await self.orch.handle(cmd("set_secret", name="devin", value=KEY))
        await self.orch.handle(cmd("set_config", values={"engine": "devin"}))
        self.assertEqual(self.config.engine, "devin")
        await self.orch.handle(cmd("delete_secret", name="devin"))
        self.assertEqual(self.backend.items, {})
        self.assertEqual(self.config.engine, "claude")
        self.assertEqual(self.of("secrets")[-1]["items"], {"devin": False})

    async def test_spoken_devin_routes_the_turn(self):
        await self.orch.handle(cmd("set_secret", name="devin", value=KEY))
        await self.orch.handle(cmd("text_submit", text="Devin, add a health check endpoint"))
        await self.orch._turn_task
        self.assertEqual(self.config.engine, "devin")
        sent = self.devin.calls[-1]
        self.assertIn("add a health check endpoint", sent["prompt"])
        self.assertNotIn("Devin,", sent["prompt"])  # the address is stripped before Devin sees it
        self.assertIsNone(sent["session_id"])       # first Devin turn: new Devin session
        start = self.of("turn_start")[-1]
        self.assertEqual(start["engine"], "devin")
        self.assertEqual(self.of("turn_end")[-1]["text"], "On it.")
        self.assertEqual(self.orch.claude_session_id, DSID)

    async def test_devin_without_key_says_how_to_fix_it(self):
        await self.orch.handle(cmd("text_submit", text="Devin, add a health check endpoint"))
        self.assertEqual(self.config.engine, "claude")
        self.assertIn("Settings → Devin", self.of("error")[-1]["message"])


class ProtocolSecretTest(unittest.TestCase):
    def test_value_is_hidden_in_repr_and_validation_errors(self):
        c = cmd("set_secret", name="devin", value=KEY)
        self.assertNotIn(KEY, repr(c))
        self.assertNotIn(KEY, str(c))
        with self.assertRaises(ValidationError) as ctx:
            cmd("set_secret", name="devin", value=KEY * 10)
        self.assertNotIn(KEY, ctx.exception.errors(include_url=False)[0]["msg"])

    def test_only_known_names(self):
        for name in ("openai", "../devin", "Barnaby-devin"):
            with self.assertRaises(ValidationError):
                cmd("set_secret", name=name, value=KEY)


class RoutingTest(unittest.TestCase):
    def test_devin_addressing(self):
        for text, want in (("Devin, fix the build", ("devin", "fix the build")),
                           ("have Devon run the tests", ("devin", "run the tests")),
                           ("ask devin to open a PR", ("devin", "open a PR")),
                           ("use Devin", ("devin", ""))):
            with self.subTest(text=text):
                self.assertEqual(route_engine(text), want)
        # Not an address: a name in the middle, or "Devin" as the subject of a sentence.
        for text in ("email Devin the report", "Devin said the tests pass"):
            with self.subTest(text=text):
                self.assertEqual(route_engine(text), (None, text))

    def test_tool_summary(self):
        self.assertEqual(tool_summary("Devin", {"session_id": DSID, "url": "https://app.devin.ai/sessions/x"}, "/w"),
                         ("Devin session https://app.devin.ai/sessions/x", None))


if __name__ == "__main__":
    unittest.main()
