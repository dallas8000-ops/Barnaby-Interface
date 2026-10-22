"""Spoken languages: grammar tables per language, config, Whisper/Kokoro plumbing and
the orchestrator following the conversation language. No models are loaded."""
import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from barnaby_engine import languages
from barnaby_engine.adaptive import VoiceProfile
from barnaby_engine.agent import Denial, SessionStarted, TextDelta, TurnResult
from barnaby_engine.command_chain import split_chain
from barnaby_engine.config import Config
from barnaby_engine.orchestrator import (Orchestrator, classify_draft_command, classify_spoken_decision,
                                         route_engine)
from barnaby_engine.protocol import parse_command
from barnaby_engine.speech import SpeechComposer
from barnaby_engine.store import Store
from barnaby_engine.tasks import parse_task_command
from barnaby_engine.templates import TemplateManager, match_template
from barnaby_engine.voice import KokoroSpeaker, SetupError, WhisperTranscriber
from tests.doubles import MOCK_SESSION, FakeRecorder, FakeSpeaker, FakeTranscriber, ScriptedAgent
from tests.helpers import TempDataDir


class DecisionTest(unittest.TestCase):
    CASES = {
        "es": (["sí", "Sí, adelante.", "claro, hazlo", "aprobado", "confirmo, por favor"],
               ["no", "No, cancela.", "no lo hagas", "rechazado", "para"],
               ["sí, y también borra los logs", "no, espera, sube a staging", "sí no", "vale", "ejecuta los tests"]),
        "fr": (["oui", "Oui, vas-y.", "d'accord, fais-le", "approuvé", "confirmé"],
               ["non", "Non, annule.", "ne le fais pas", "refusé", "arrête"],
               ["oui et supprime aussi les logs", "non attends pousse en staging", "oui non", "d'accord", "lance les tests"]),
        "it": (["sì", "Sì, procedi.", "certo, fallo", "approvato", "va bene, confermo"],
               ["no", "No, annulla.", "non farlo", "rifiutato", "fermati"],
               ["sì e cancella anche i log", "no aspetta fai push su staging", "sì no", "va bene", "esegui i test"]),
        "pt": (["sim", "Sim, pode fazer.", "claro, manda", "aprovado", "pode"],
               ["não", "Não, cancela.", "não faça isso", "rejeitado", "pare"],
               ["sim e apaga os logs também", "não espera faz push em staging", "sim não", "beleza", "roda os testes"]),
        "hi": (["हाँ", "हाँ, कर दो।", "हां करो", "मंज़ूर है", "जी हाँ"],
               ["नहीं", "नहीं, रुको।", "मत करो", "रद्द करो", "ना"],
               ["हाँ और logs भी delete करो", "हाँ नहीं", "ठीक है", "टेस्ट चलाओ"]),
        "ja": (["はい", "はい、お願いします。", "承認します", "進めてください", "ええ、どうぞ"],
               ["いいえ", "いいえ、キャンセルしてください。", "やめて", "却下", "しないでください"],
               ["はい、それとログも削除して", "はいいいえ", "オッケー", "テストを実行して"]),
        "zh": (["是", "是的，继续吧。", "可以", "批准", "好的，确认"],
               ["不", "不要。", "取消吧", "拒绝", "别做了"],
               ["是的，然后把日志也删了", "不是", "好的", "运行测试"]),
    }

    def test_yes_no_and_everything_else_per_language(self):
        for lang, (yes, no, other) in self.CASES.items():
            for text in yes:
                with self.subTest(lang=lang, text=text):
                    self.assertIs(classify_spoken_decision(text, lang), True)
            for text in no:
                with self.subTest(lang=lang, text=text):
                    self.assertIs(classify_spoken_decision(text, lang), False)
            for text in other:
                with self.subTest(lang=lang, text=text):
                    self.assertIsNone(classify_spoken_decision(text, lang))

    def test_english_is_understood_in_every_language_but_not_the_reverse(self):
        for lang in languages.LANGUAGES:
            with self.subTest(lang=lang):
                self.assertIs(classify_spoken_decision("yes go ahead", lang), True)
                self.assertIs(classify_spoken_decision("no", lang), False)
        self.assertIsNone(classify_spoken_decision("sí"))  # English mode doesn't guess at Spanish
        self.assertIsNone(classify_spoken_decision("はい"))

    def test_long_utterances_are_never_decisions(self):
        self.assertIsNone(classify_spoken_decision("sí sí sí sí sí sí sí", "es"))


class DraftCommandTest(unittest.TestCase):
    CASES = {
        "es": {"Envíalo.": "send", "envía eso, por favor": "send", "Borra eso": "discard", "olvídalo": "discard",
               "envía el informe a finanzas": None, "borra eso del test y escribe otro": None},
        "fr": {"Envoie-le.": "send", "envoie ça s'il te plaît": "send", "Efface ça": "discard",
               "laisse tomber": "discard", "envoie le rapport à la compta": None, "oublie ça et refais le test": None},
        "it": {"Invialo.": "send", "manda per favore": "send", "Lascia perdere": "discard", "cancellalo": "discard",
               "invia il report alla contabilità": None, "cancella quel test e scrivine un altro": None},
        "pt": {"Envia.": "send", "pode mandar": "send", "Esquece isso": "discard", "apaga isso": "discard",
               "envia o relatório pro financeiro": None, "apaga isso do teste e escreve outro": None},
        "hi": {"भेज दो": "send", "send करो": "send", "रहने दो": "discard", "छोड़ दो": "discard",
               "रिपोर्ट finance को भेज दो": None},
        "ja": {"送信して": "send", "送信してください。": "send", "取り消して": "discard", "破棄してください": "discard",
               "レポートを経理に送信して": None, "そのテストを削除して書き直して": None},
        "zh": {"发送": "send", "发送吧。": "send", "算了": "discard", "删掉吧": "discard",
               "把报告发送给财务": None, "删掉那个测试然后重写": None},
    }

    def test_phrases_per_language(self):
        for lang, cases in self.CASES.items():
            for text, want in cases.items():
                with self.subTest(lang=lang, text=text):
                    self.assertEqual(classify_draft_command(text, lang), want)

    def test_english_draft_phrases_work_everywhere(self):
        for lang in languages.LANGUAGES:
            with self.subTest(lang=lang):
                self.assertEqual(classify_draft_command("send it", lang), "send")
                self.assertEqual(classify_draft_command("scratch that", lang), "discard")


class RouteTest(unittest.TestCase):
    def test_addressing_per_language(self):
        cases = {
            ("es", "Cursor, arregla el build"): ("cursor", "arregla el build"),
            ("es", "pídele a Claude que explique esto"): ("claude", "explique esto"),
            ("es", "usa Devin"): ("devin", ""),
            ("fr", "demande à Cursor de corriger le build"): ("cursor", "corriger le build"),
            ("fr", "Claude : pourquoi ça échoue"): ("claude", "pourquoi ça échoue"),
            ("it", "chiedi a Devin di aprire una PR"): ("devin", "aprire una PR"),
            ("pt", "peça ao Cursor para rodar os testes"): ("cursor", "rodar os testes"),
            ("hi", "Cursor से कहो कि बिल्ड ठीक करे"): ("cursor", "बिल्ड ठीक करे"),
            ("hi", "क्लॉड, टेस्ट चलाओ"): ("claude", "टेस्ट चलाओ"),
            ("ja", "クロード、テストを実行して"): ("claude", "テストを実行して"),
            ("ja", "Cursorに頼んでビルドを直して"): ("cursor", "ビルドを直して"),
            ("zh", "让Cursor修复构建"): ("cursor", "修复构建"),
            ("zh", "Claude，解释一下这个"): ("claude", "解释一下这个"),
        }
        for (lang, text), want in cases.items():
            with self.subTest(lang=lang, text=text):
                self.assertEqual(route_engine(text, lang), want)

    def test_ordinary_instructions_untouched(self):
        cases = [("es", "añade un cursor a la tabla"), ("es", "cloud functions están fallando"),
                 ("fr", "le curseur est mal placé"), ("it", "Devin ha detto che i test passano"),
                 ("ja", "カーソルの位置がおかしい"), ("ja", "カーソルにフォーカスを当てて"),
                 ("zh", "光标位置不对"), ("hi", "cursor की जगह गलत है")]
        for lang, text in cases:
            with self.subTest(lang=lang, text=text):
                self.assertEqual(route_engine(text, lang), (None, text))


class ChainTest(unittest.TestCase):
    def test_splits_per_language(self):
        cases = {
            ("es", "ejecuta los tests y luego arregla lo que falle"): ["Ejecuta los tests", "Arregla lo que falle"],
            ("es", "primero añade un test, luego impleméntalo y finalmente corre la suite"):
                ["Añade un test", "Impleméntalo", "Corre la suite"],
            ("fr", "lance les tests, puis corrige ce qui échoue"): ["Lance les tests", "Corrige ce qui échoue"],
            ("fr", "d'abord ajoute un test, ensuite implémente-le"): ["Ajoute un test", "Implémente-le"],
            ("it", "esegui i test e poi sistema quello che fallisce"): ["Esegui i test", "Sistema quello che fallisce"],
            ("pt", "rode os testes e depois corrija o que falhar"): ["Rode os testes", "Corrija o que falhar"],
            ("hi", "टेस्ट चलाओ और फिर जो फेल हो उसे ठीक करो"): ["टेस्ट चलाओ", "जो फेल हो उसे ठीक करो"],
            ("ja", "まずテストを実行して、それから失敗を直して"): ["テストを実行して", "失敗を直して"],
            ("zh", "首先运行测试，然后修复失败的部分"): ["运行测试", "修复失败的部分"],
            ("es", "run the tests, then commit"): ["Run the tests", "Commit"],  # English words still split
        }
        for (lang, text), want in cases.items():
            with self.subTest(lang=lang, text=text):
                self.assertEqual(split_chain(text, lang), want)

    def test_never_splits_single_instructions(self):
        cases = [
            ("es", "si los tests fallan luego revierte el cambio"),   # conditional
            ("es", "hazlo después del login"),                        # "después" needs punctuation
            ("es", "explica qué hace `a; luego b`"),                  # code span
            ("fr", "si les tests échouent, puis annule"),
            ("fr", "enfin bref, corrige le test"),
            ("it", "se fallisce poi ripristina"),
            ("pt", "se falhar depois reverte"),
            ("pt", "faz isso depois do deploy"),
            ("hi", "अगर टेस्ट फेल हो फिर बदलाव वापस लो"),
            ("ja", "ファイルの最後にコメントを追加して"),             # "最後に" = at the end, not "finally"
            ("ja", "テストが失敗したらそれから元に戻して"),
            ("zh", "删除最后一行"),                                   # "最后" = last, not "finally"
            ("zh", "如果测试失败然后回滚"),
        ]
        for lang, text in cases:
            with self.subTest(lang=lang, text=text):
                self.assertEqual(len(split_chain(text, lang)), 1)


class TaskGrammarTest(unittest.TestCase):
    def test_task_commands_per_language(self):
        cases = {
            ("es", "tarea para Devin en backend: añade el endpoint de pedidos"):
                ("devin", ["backend"], "añade el endpoint de pedidos"),
            ("es", "nueva tarea para Claude en frontend y api, arregla el login"):
                ("claude", ["frontend", "api"], "arregla el login"),
            ("es", "asigna la carpeta backend a Cursor: añade caché"): ("cursor", ["backend"], "añade caché"),
            ("fr", "tâche pour Devin dans backend : ajoute les commandes"): ("devin", ["backend"], "ajoute les commandes"),
            ("fr", "assigne backend et docs à Claude : renomme"): ("claude", ["backend", "docs"], "renomme"),
            ("it", "attività per Cursor in app: sistema gli stili"): ("cursor", ["app"], "sistema gli stili"),
            ("pt", "tarefa para o Devin no backend: adiciona pedidos"): ("devin", ["backend"], "adiciona pedidos"),
            ("hi", "Devin के लिए टास्क backend में: orders endpoint जोड़ो"):
                ("devin", ["backend"], "orders endpoint जोड़ो"),
            ("ja", "Devinにタスク（backend）で：注文エンドポイントを追加"): ("devin", ["backend"], "注文エンドポイントを追加"),
            ("ja", "backendとapiをClaudeに割り当て：キャッシュを追加"): ("claude", ["backend", "api"], "キャッシュを追加"),
            ("zh", "给Devin一个任务，在backend里：添加订单接口"): ("devin", ["backend"], "添加订单接口"),
            ("zh", "把backend和api分配给Claude：加缓存"): ("claude", ["backend", "api"], "加缓存"),
            ("es", "task for Devin in backend: add orders"): ("devin", ["backend"], "add orders"),  # English too
        }
        for (lang, text), want in cases.items():
            with self.subTest(lang=lang, text=text):
                self.assertEqual(parse_task_command(text, lang), want)

    def test_ordinary_instructions_are_not_tasks(self):
        cases = [("es", "añade una lista de tareas a la página"), ("es", "Devin, arregla el build"),
                 ("es", "tarea para bob en api: x"), ("es", "asigna el ticket a Claude"),
                 ("es", "tarea para claude en api:"), ("fr", "ajoute une tâche à la liste"),
                 ("it", "attività per oggi: niente"), ("pt", "tarefa para amanhã: revisar"),
                 ("ja", "タスク一覧を追加して"), ("zh", "添加一个任务列表"), ("hi", "टास्क लिस्ट जोड़ो")]
        for lang, text in cases:
            with self.subTest(lang=lang, text=text):
                self.assertIsNone(parse_task_command(text, lang))


class TemplateGrammarTest(unittest.TestCase):
    def test_matching_per_language(self):
        T = TemplateManager().list_templates()
        cases = {
            ("es", "ejecuta la plantilla TDD para el total del carrito"): ("tdd-cycle", "el total del carrito"),
            ("es", "inicia el flujo de trabajo de depuración"): ("debugging", None),
            ("es", "haz la lista de verificación de revisión de código en el módulo auth"):
                ("code-review", "el módulo auth"),
            ("fr", "lance le workflow de débogage"): ("debugging", None),
            ("it", "esegui il template TDD per il carrello"): ("tdd-cycle", "il carrello"),
            ("pt", "rode o fluxo de depuração"): ("debugging", None),
            ("hi", "cart total के लिए TDD टेम्पलेट चलाओ"): ("tdd-cycle", "cart total"),
            ("ja", "デバッグのワークフローを実行して"): ("debugging", None),
            ("ja", "カートの合計に対してTDDテンプレートを実行"): ("tdd-cycle", "カートの合計"),
            ("zh", "运行调试工作流"): ("debugging", None),
            ("zh", "运行代码审查检查清单，针对认证模块"): ("code-review", "认证模块"),
        }
        for (lang, text), (tid, ctx) in cases.items():
            with self.subTest(lang=lang, text=text):
                found = match_template(text, T, lang)
                self.assertIsNotNone(found)
                self.assertEqual((found[0]["id"], found[1]), (tid, ctx))

    def test_ordinary_instructions_not_hijacked(self):
        T = TemplateManager().list_templates()
        for lang, text in [("es", "actualiza la plantilla de correo"), ("es", "usa la plantilla de correo"),
                           ("fr", "modifie le modèle d'email"), ("pt", "corrige os testes de tdd"),
                           ("ja", "メールのテンプレートを更新して"), ("zh", "修改邮件模板")]:
            with self.subTest(lang=lang, text=text):
                self.assertIsNone(match_template(text, T, lang))


class SpeechTest(unittest.TestCase):
    def test_non_latin_sentences_are_split_and_spoken(self):
        c = SpeechComposer()
        self.assertEqual(c.feed("テストを実行しました。三件失敗しています！確認"), ["テストを実行しました。", "三件失敗しています！"])
        self.assertEqual(c.flush(), ["確認"])
        c = SpeechComposer()
        self.assertEqual(c.feed("मैंने टेस्ट चलाए। दो फ़ेल हुए। "), ["मैंने टेस्ट चलाए।", "दो फ़ेल हुए।"])
        c = SpeechComposer()
        self.assertEqual(c.feed("Listo. ¿Lo subo? "), ["Listo."])  # "¿" starts a sentence
        self.assertEqual(c.flush(), ["¿Lo subo?"])

    def test_cjk_overflow_cut_and_localized_note(self):
        c = SpeechComposer(10, overflow_note="其余内容在屏幕上。")
        self.assertEqual(c.feed("这是一个非常长的句子没有空格。"), ["这是一个非常长的句子。", "其余内容在屏幕上。"])

    def test_secrets_still_redacted_in_other_scripts(self):
        c = SpeechComposer()
        out = c.feed("キーは sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 です。")
        self.assertTrue(out)
        self.assertNotIn("sk-ant", " ".join(out))


class LanguageTablesTest(unittest.TestCase):
    def test_every_language_has_voices_and_messages(self):
        for code, lang in languages.LANGUAGES.items():
            with self.subTest(code=code):
                self.assertTrue(lang.voices)
                self.assertEqual(languages.voice_language(lang.default_voice), code)
                self.assertIn(code, languages.GRAMMARS)
                for key, args in (("approvals", (2,)), ("confirm", ()), ("done", (3,)), ("stopped", (1, 3, True)),
                                  ("task", (True, "Devin", "x")), ("overflow", ())):
                    self.assertTrue(languages.message(code, key, *args))

    def test_english_messages_unchanged(self):
        self.assertEqual(languages.message("en", "approvals", 1), "I need your OK for 1 action. It's on screen.")
        self.assertEqual(languages.message("en", "stopped", 2, 3, False),
                         "I stopped after step 2 of 3, because that step didn't finish.")
        self.assertEqual(languages.reply_instruction("en"), "")

    def test_voice_for_language(self):
        self.assertEqual(languages.voice_for("es", "am_michael"), "ef_dora")
        self.assertEqual(languages.voice_for("es", "em_alex"), "em_alex")
        self.assertEqual(languages.voice_for("en", "bf_emma"), "bf_emma")

    def test_app_voice_list_matches_the_engine(self):
        ts = (Path(__file__).parents[2] / "app" / "src" / "lib" / "languages.ts").read_text(encoding="utf-8")
        for code, lang in languages.LANGUAGES.items():
            with self.subTest(code=code):
                self.assertIn(f'"{lang.bcp47}"', ts)
                for v in lang.voices:
                    self.assertIn(f'"{v.id}"', ts)
        for model in languages.WHISPER_MODELS:
            self.assertIn(f'"{model}"', ts)

    def test_model_mapping(self):
        self.assertEqual(languages.stt_model_for("small.en", "es"), "small")
        self.assertEqual(languages.stt_model_for("small.en", "en"), "small.en")
        self.assertEqual(languages.stt_model_for("distil-large-v3", "auto"), "large-v3-turbo")
        self.assertEqual(languages.stt_model_for("medium", "ja"), "medium")
        self.assertEqual(languages.whisper_language("small.en", "auto"), "en")
        self.assertEqual(languages.whisper_language("small", "auto"), None)
        self.assertEqual(languages.whisper_language("small", "hi"), "hi")

    def test_script_detection(self):
        self.assertEqual(languages.script_language("टेस्ट चलाओ"), "hi")
        self.assertEqual(languages.script_language("テストを実行"), "ja")
        self.assertEqual(languages.script_language("运行测试"), "zh")
        self.assertIsNone(languages.script_language("ejecuta los tests"))


class ConfigTest(unittest.TestCase):
    def test_spoken_language_and_models_validated(self):
        c = Config()
        self.assertEqual((c.spoken_language, c.whisper_model), ("en", "small.en"))
        self.assertEqual(c.apply_ui_update({"spoken_language": "ja", "whisper_model": "large-v3-turbo"}), [])
        self.assertEqual((c.spoken_language, c.whisper_model), ("ja", "large-v3-turbo"))
        self.assertEqual(c.apply_ui_update({"spoken_language": "auto"}), [])
        rejected = c.apply_ui_update({"spoken_language": "de", "whisper_model": "huge"})
        self.assertEqual(sorted(rejected), ["spoken_language", "whisper_model"])
        self.assertEqual(sorted(c.apply_ui_update({"spoken_language": ["en"], "whisper_model": {}})),
                         ["spoken_language", "whisper_model"])
        self.assertEqual(c.spoken_language, "auto")

    def test_bad_values_on_disk_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"spoken_language": "klingon", "whisper_model": "x", "tts_speed": 1.2}))
            c = Config.load(path)
            self.assertEqual((c.spoken_language, c.whisper_model, c.tts_speed), ("en", "small.en", 1.2))
            path.write_text(json.dumps({"spoken_language": "pt", "whisper_model": "medium"}))
            self.assertEqual(Config.load(path).spoken_language, "pt")


class _FakeWhisperModel:
    def __init__(self, ranked=None, fail_detect=False):
        self.ranked = ranked or [("en", 0.9)]
        self.fail_detect = fail_detect
        self.calls = []

    def detect_language(self, audio=None, vad_filter=False, **_kw):
        if self.fail_detect:
            raise RuntimeError("no speech")
        return self.ranked[0][0], self.ranked[0][1], self.ranked

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return iter([types.SimpleNamespace(text=" hola ")]), types.SimpleNamespace(language="es")


class TranscriberLanguageTest(unittest.TestCase):
    AUDIO = np.zeros(16_000, np.float32)

    def make(self, model, language, fake):
        t = WhisperTranscriber(model, "cpu", language)
        t._model = fake  # load() is a no-op once a model is set
        return t

    def test_fixed_language_is_passed_to_whisper(self):
        fake = _FakeWhisperModel()
        t = self.make("small", "es", fake)
        self.assertEqual(t.transcribe(self.AUDIO, "pytest"), "hola")
        self.assertEqual(fake.calls[0]["language"], "es")
        self.assertEqual(t.last_language, "es")

    def test_english_only_model_is_swapped_outside_english(self):
        self.assertEqual(WhisperTranscriber("small.en", "cpu", "fr").model_name, "small")
        self.assertEqual(WhisperTranscriber("small.en", "cpu", "en").model_name, "small.en")
        fake = _FakeWhisperModel()
        t = self.make("small.en", "en", fake)
        t.transcribe(self.AUDIO, "")
        self.assertEqual(fake.calls[0]["language"], "en")

    def test_auto_picks_the_most_likely_supported_language(self):
        fake = _FakeWhisperModel(ranked=[("nl", 0.5), ("de", 0.2), ("en", 0.15), ("ja", 0.1)])
        t = self.make("small", "auto", fake)
        t.transcribe(self.AUDIO, "")
        self.assertEqual(fake.calls[0]["language"], "en")  # Dutch isn't supported: best supported wins
        self.assertEqual(t.last_language, "en")

    def test_auto_falls_back_to_whispers_own_detection(self):
        fake = _FakeWhisperModel(fail_detect=True)
        t = self.make("small", "auto", fake)
        t.transcribe(self.AUDIO, "")
        self.assertIsNone(fake.calls[0]["language"])
        self.assertEqual(t.last_language, "es")  # from the transcription info

    def test_missing_model_says_so_clearly(self):
        utils = types.ModuleType("faster_whisper.utils")

        def download_model(name, local_files_only=False):
            raise OSError("offline" if not local_files_only else "not cached")
        utils.download_model = download_model
        fw = types.ModuleType("faster_whisper")
        fw.__path__ = []
        fw.utils = utils
        fw.WhisperModel = mock.Mock(side_effect=AssertionError("must not build a model it doesn't have"))
        with mock.patch.dict(sys.modules, {"faster_whisper": fw, "faster_whisper.utils": utils}):
            t = WhisperTranscriber("medium", "cpu", "it")
            self.assertIn("Downloading the medium speech model (about 1.5 GB)", t.pending_download())
            with self.assertRaises(SetupError) as ctx:
                t.load()
        self.assertIn("medium speech model isn't installed", str(ctx.exception))
        self.assertIn("pick an installed model", str(ctx.exception))

    def test_installed_model_needs_no_download(self):
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = lambda name, local_files_only=False: "/cache/" + name
        fw = types.ModuleType("faster_whisper")
        fw.__path__ = []
        fw.utils = utils
        with mock.patch.dict(sys.modules, {"faster_whisper": fw, "faster_whisper.utils": utils}):
            self.assertIsNone(WhisperTranscriber("small", "cpu", "it").pending_download())


class KokoroLanguageTest(unittest.TestCase):
    def test_voice_follows_language_and_returns_to_the_users_choice(self):
        s = KokoroSpeaker("bf_emma")
        s.set_language("ja")
        self.assertEqual(s.voice, "jf_alpha")
        s.set_language("en")
        self.assertEqual(s.voice, "bf_emma")
        s.set_language("auto")
        self.assertEqual(s.voice, "bf_emma")

    def test_missing_japanese_package_is_explained(self):
        kokoro = types.ModuleType("kokoro")

        def pipeline(lang_code):
            if lang_code == "j":
                raise ImportError("No module named 'pyopenjtalk'")
            return object()
        kokoro.KPipeline = pipeline
        with mock.patch.dict(sys.modules, {"kokoro": kokoro}):
            s = KokoroSpeaker("jf_alpha")
            with self.assertRaises(SetupError) as ctx:
                s.load()
        self.assertIn('pip install "misaki[ja]"', str(ctx.exception))


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


def reply(text="Listo."):
    return [SessionStarted(MOCK_SESSION, "m"), TextDelta(text), TurnResult(text, MOCK_SESSION, False, 0.0, 1)]


class OrchestratorLanguageTest(unittest.IsolatedAsyncioTestCase):
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

    async def build(self, *, language="auto", scripts=None, texts=None, heard=None, factory=None):
        async def emit(e):
            self.events.append(e)
        self.config = Config(workspace=self._tmp.name, review_transcripts=False, spoken_language=language)
        self.speaker = FakeSpeaker()
        self.agent = ScriptedAgent(scripts or [])
        self.transcriber = FakeTranscriber(texts, heard)
        self.orch = Orchestrator(config=self.config, store=self.store, profile=VoiceProfile(self.store),
                                 recorder=FakeRecorder(), transcriber=self.transcriber, speaker=self.speaker,
                                 agent=self.agent, emit=emit, transcriber_factory=factory)
        await self.orch.start()

    def of(self, type_):
        return [e for e in self.events if e["type"] == type_]

    async def settle(self):
        for _ in range(300):
            await asyncio.sleep(0.01)
            task = self.orch._turn_task
            if (task is None or task.done()) and self.orch._speech_q.empty() and not self.orch._speaking:
                await asyncio.sleep(0.02)
                return
        self.fail("did not settle")

    async def speak(self):
        await self.orch.handle(cmd("ptt_start"))
        await self.orch.handle(cmd("ptt_stop"))
        await self.settle()

    async def test_auto_follows_the_spoken_language_end_to_end(self):
        await self.build(scripts=[reply("Listo. Todo pasa.")], texts=["ejecuta los tests"], heard=["es"])
        await self.speak()
        self.assertEqual(self.orch.language, "es")
        prompt = self.agent.calls[0]["prompt"]
        self.assertTrue(prompt.startswith("(The user is speaking Spanish. Reply in Spanish."), prompt)
        self.assertTrue(prompt.endswith("ejecuta los tests"))
        self.assertEqual(self.of("turn_start")[0]["text"], "ejecuta los tests")  # display text untouched
        self.assertEqual(self.speaker.spoken, ["Listo.", "Todo pasa."])
        self.assertEqual(set(self.speaker.languages), {"es"})

    async def test_english_prompts_are_unchanged(self):
        await self.build(language="auto", scripts=[reply("Done.")], texts=["run the tests"], heard=["en"])
        await self.speak()
        self.assertEqual(self.agent.calls[0]["prompt"], "run the tests")

    async def test_fixed_language_ignores_detection(self):
        await self.build(language="fr", scripts=[reply("Ok.")], texts=["lance les tests"], heard=["en"])
        await self.speak()
        self.assertEqual(self.orch.language, "fr")
        self.assertIn("Reply in French", self.agent.calls[0]["prompt"])

    async def test_typed_text_in_a_non_latin_script_switches_auto(self):
        await self.build(scripts=[reply("完了しました。")])
        await self.orch.handle(cmd("text_submit", text="テストを実行して"))
        await self.settle()
        self.assertEqual(self.orch.language, "ja")
        self.assertIn("Reply in Japanese", self.agent.calls[0]["prompt"])

    async def test_spanish_voice_approval_and_localized_prompt(self):
        denial = Denial("t5", "Bash", {"command": "git push origin main"})
        await self.build(scripts=[reply("x")[:1] + [TurnResult("x", MOCK_SESSION, False, 0.0, 1, denials=(denial,))],
                                  reply("Hecho.")],
                         texts=["sube los cambios", "sí, adelante"], heard=["es"])
        await self.speak()
        self.assertIn("Necesito tu aprobación para 1 acción. Está en pantalla.", self.speaker.spoken)
        await self.speak()
        self.assertEqual(self.of("approval_decided")[0]["approved"], True)
        self.assertEqual(self.agent.calls[1]["grant"].allow, ("Bash(git push origin main)",))

    async def test_critical_actions_cannot_be_voice_approved_in_any_language(self):
        for lang, yes in (("es", "sí, adelante"), ("ja", "はい、お願いします"), ("zh", "是的，继续")):
            with self.subTest(lang=lang):
                self.events.clear()
                denial = Denial("c", "Bash", {"command": "git push --force origin main"})
                if hasattr(self, "orch"):
                    await self.orch.shutdown()
                await self.build(language=lang, scripts=[[TurnResult("x", MOCK_SESSION, False, 0.0, 1,
                                                                     denials=(denial,))]], texts=["push", yes])
                await self.speak()
                req = self.of("approval_required")[0]
                self.assertFalse(req["voice_approvable"])
                await self.speak()
                self.assertEqual(self.of("approval_decided"), [])
                self.assertEqual(len(self.agent.calls), 1)
                self.assertEqual(self.orch.state.value, "awaiting_approval")

    async def test_tablet_say_events_carry_the_language_tag(self):
        await self.build(scripts=[reply("完成了。"), reply("Done.")], texts=["运行测试", "run it"],
                         heard=["zh", "en"])
        self.orch.set_remote_listeners(1)
        await self.speak()
        await self.speak()
        self.assertEqual([(e["text"], e["lang"]) for e in self.of("say")], [("完成了。", "zh-CN"), ("Done.", "en-US")])

    async def test_chain_and_task_grammar_follow_the_language(self):
        await self.build(language="es", scripts=[reply("Uno."), reply("Dos.")],
                         texts=["ejecuta los tests y luego haz commit"])
        await self.speak()
        steps = [e["steps"] for e in self.of("command_chain_start")]
        self.assertEqual(steps, [["Ejecuta los tests", "Haz commit"]])
        self.assertIn("Los 2 pasos están hechos.", self.speaker.spoken)

    async def test_changing_language_reloads_only_when_the_model_changes(self):
        made = []

        def factory(c):
            made.append((c.whisper_model, c.spoken_language))
            return FakeTranscriber()
        await self.build(language="en", factory=factory)
        await self.orch.handle(cmd("set_config", values={"spoken_language": "es"}))  # small.en -> small: reload
        self.assertEqual(made, [("small.en", "es")])
        await self.orch.handle(cmd("set_config", values={"spoken_language": "auto"}))  # still small: no reload
        self.assertEqual(len(made), 1)
        self.assertEqual(self.orch.transcriber.language, "auto")
        self.assertEqual(self.orch.language, "es")  # Auto keeps the last language until it hears another
        await self.orch.handle(cmd("set_config", values={"spoken_language": "en"}))  # back to small.en: reload
        self.assertEqual(len(made), 2)
        self.assertEqual(self.orch.language, "en")
        self.assertIn("spoken_language", self.of("config")[-1]["config"])


if __name__ == "__main__":
    unittest.main()
