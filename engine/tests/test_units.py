import json
import tempfile
import unittest
from pathlib import Path

from barnaby_engine import lexicon
from barnaby_engine.adaptive import PROMOTE_AT, VoiceProfile, extract_substitutions
from barnaby_engine.config import Config
from barnaby_engine.editors import EditorError, resolve_in_workspace
from barnaby_engine.speech import OVERFLOW_NOTE, SpeechComposer
from barnaby_engine.store import Store


class SpeechComposerTest(unittest.TestCase):
    def speak(self, chunks, max_chars=700):
        c = SpeechComposer(max_chars)
        out = []
        for ch in chunks:
            out += c.feed(ch)
        return out + c.flush()

    def test_sentences_emitted_as_they_complete(self):
        c = SpeechComposer()
        self.assertEqual(c.feed("Tests pass now. The fi"), ["Tests pass now."])
        self.assertEqual(c.flush(), ["The fi"])

    def test_code_fence_split_across_deltas_never_spoken(self):
        out = self.speak(["Fixed it.\n\n``", "`ts\nconst secret = 1;\n`", "``\nAll ", "done."])
        joined = " ".join(out)
        self.assertNotIn("const", joined)
        self.assertIn("Fixed it.", joined)
        self.assertIn("All done.", joined)

    def test_markdown_stripped(self):
        out = self.speak(["## Summary\n\n- **Updated** `cart.ts` per [docs](https://x.y).\n\n"])
        self.assertEqual(out, ["Summary", "Updated cart.ts per docs."])

    def test_tables_and_long_inline_code_skipped(self):
        out = self.speak(["| a | b |\n|---|---|\n\nRun `" + "x" * 60 + "` now."])
        self.assertEqual(out, ["Run the code shown now."])

    def test_budget_overflow(self):
        out = self.speak(["First sentence here. " * 10], max_chars=50)
        self.assertEqual(out[-1], OVERFLOW_NOTE)
        self.assertLessEqual(sum(len(s) for s in out[:-1]), 50)

    def test_secrets_redacted(self):
        out = self.speak(["Your key is sk-ant-abcdefghijklmnopqrstu. Done."])
        self.assertNotIn("sk-ant", " ".join(out))


class LexiconTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(lexicon.normalize("open app dot tsx and fix the use effect in zoo stand"),
                         "open app .tsx and fix the useEffect in Zustand")
        self.assertEqual(lexicon.normalize("add sequel alchemy to fast api"), "add SQLAlchemy to FastAPI")

    def test_normalize_leaves_ordinary_words(self):
        text = "use the state machine and effect a change"
        self.assertEqual(lexicon.normalize(text), text)

    def test_prompt_budget_and_project_terms_first(self):
        prompt = lexicon.build_prompt(["tanstack-query"] * 3 + [f"term{i}" for i in range(500)])
        self.assertTrue(prompt.startswith("tanstack-query, term0"))
        self.assertLessEqual(len(prompt), 701)

    def test_project_terms_from_manifests(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(json.dumps({"dependencies": {"zustand": "^5", "@tanstack/react-query": "5"},
                                                           "devDependencies": {"vitest": "3"}}))
            (root / "requirements.txt").write_text("Django>=5.1\n# comment\ncelery[redis]==5.4\n-r base.txt\n")
            (root / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi>=0.115", "sqlalchemy"]\n')
            terms = lexicon.project_terms(root)
            for expected in ("zustand", "react-query", "vitest", "Django", "celery", "fastapi", "sqlalchemy"):
                self.assertIn(expected, terms)

    def test_broken_manifest_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text("{not json")
            self.assertEqual(lexicon.project_terms(d), [])


class AdaptiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "t.db")
        self.profile = VoiceProfile(self.store)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_extract(self):
        self.assertEqual(extract_substitutions("add a zoo stand store for the cart",
                                               "add a Zustand store for the cart"),
                         [("zoo stand", "Zustand")])

    def test_ignores_rewrites_case_and_stopwords(self):
        self.assertEqual(extract_substitutions("run the tests", "deploy everything to production now"), [])
        self.assertEqual(extract_substitutions("open react app", "open React app"), [])
        self.assertEqual(extract_substitutions("fix the bug", "fix a bug"), [])

    def test_promotion_after_repeat_then_applies(self):
        self.assertEqual(self.profile.learn("wire up barn a bee engine", "wire up Barnaby engine"), [])
        self.assertEqual(self.profile.apply("the barn a bee engine"), "the barn a bee engine")
        promoted = self.profile.learn("restart barn a bee now", "restart Barnaby now")
        self.assertEqual(promoted, [("barn a bee", "Barnaby")])
        self.assertEqual(self.profile.apply("Barn A Bee is up"), "Barnaby is up")
        self.assertIn("Barnaby", self.profile.vocabulary())

    def test_disabled_rule_not_applied(self):
        for _ in range(PROMOTE_AT):
            self.profile.learn("open doc ops", "open DBOps")
        self.assertEqual(self.profile.apply("doc ops"), "DBOps")
        self.store.set_correction_disabled("doc ops", "DBOps", True)
        self.profile.reload()
        self.assertEqual(self.profile.apply("doc ops"), "doc ops")

    def test_rule_does_not_hit_inside_words(self):
        for _ in range(PROMOTE_AT):
            self.profile.learn("check the rig hand logs", "check the RigHand logs")
        self.assertEqual(self.profile.apply("the brig handle"), "the brig handle")

    def test_identifier_usage_builds_vocab(self):
        for _ in range(3):
            self.profile.learn("call useCartStore", "call useCartStore")
        self.assertIn("useCartStore", self.profile.vocabulary())


class StoreTest(unittest.TestCase):
    def test_migrates_pre_engine_database(self):
        import sqlite3
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "old.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, claude_session_id TEXT, title TEXT NOT NULL "
                    "DEFAULT 'New session', workspace TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        con.execute("INSERT INTO sessions VALUES ('s1', 'c1', 'Old', '/w', 1, 1)")
        con.commit(); con.close()
        store = Store(path)
        try:
            self.assertEqual(store.list_sessions()[0]["engine"], "claude")
            sid = store.create_session("/w", "cursor")
            self.assertEqual(store.get_session(sid)["engine"], "cursor")
        finally:
            store.close()

    def test_sessions_and_turns(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "s.db")
            sid = store.create_session("/w")
            store.set_title_if_default(sid, "Fix the cart")
            store.set_title_if_default(sid, "ignored")
            store.add_turn(sid, user_text="u", assistant_text="a", tools=[{"name": "Bash"}], status="ok", cost_usd=0.1)
            s = store.get_session(sid)
            self.assertEqual(s["title"], "Fix the cart")
            self.assertEqual(s["turns"][0]["tools"], [{"name": "Bash"}])
            self.assertEqual(store.list_sessions()[0]["turn_count"], 1)
            self.assertIsNone(store.get_session("nope"))
            store.close()


class ConfigTest(unittest.TestCase):
    def test_ui_cannot_widen_auto_approve_or_set_binary(self):
        c = Config()
        rejected = c.apply_ui_update({"safe_bash": ["rm:*"], "claude_bin": "/tmp/evil"})
        self.assertEqual(set(rejected), {"safe_bash", "claude_bin"})
        self.assertNotIn("rm:*", c.safe_bash)

    def test_validation(self):
        c = Config()
        self.assertEqual(c.apply_ui_update({"tts_speed": 1}), [])
        self.assertEqual(c.tts_speed, 1.0)
        self.assertEqual(set(c.apply_ui_update({
            "tts_speed": 9, "tts_enabled": "yes", "workspace": "/definitely/not/here",
            "claude_model": "opus;calc", "whisper_model": "../evil", "input_device": True,
        })), {"tts_speed", "tts_enabled", "workspace", "claude_model", "whisper_model", "input_device"})

    def test_roundtrip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            Config(tts_voice="af_heart").save(p)
            self.assertEqual(Config.load(p).tts_voice, "af_heart")
            p.write_text("{broken")
            self.assertEqual(Config.load(p).tts_voice, Config().tts_voice)


class EditorsTest(unittest.TestCase):
    def test_path_confinement(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "src").mkdir()
            self.assertEqual(resolve_in_workspace(d, "src/a.ts"), (Path(d) / "src/a.ts").resolve())
            for bad in ("../outside.txt", "/etc/passwd", "src/../../x"):
                with self.subTest(bad=bad), self.assertRaises(EditorError):
                    resolve_in_workspace(d, bad)


if __name__ == "__main__":
    unittest.main()
