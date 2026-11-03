"""Spoken languages: what Barnaby hears, how it answers, and its voice grammar.

One table per language, so adding a language is data, not code:

* ``LANGUAGES``: the languages Kokoro can speak (and Whisper can hear), with the
  BCP-47 tag the tablet's speechSynthesis needs and Kokoro's voices for each.
* ``WHISPER_MODELS``: recognition models with their approximate disk/VRAM cost;
  English-only (``.en``, distil) models are mapped to a multilingual sibling
  when another language is selected.
* ``GRAMMARS``: the short spoken commands per language (yes/no, "send it",
  engine routing, chain sequencing words, task assignment, template runs).

Safety rules hold in every language: a decision or draft command is only the
whole short phrase (anything extra makes it an ordinary instruction), and
critical actions are never voice-approvable whatever the language; that check
lives in the orchestrator and does not look at words at all.

English is always understood as well: developers code-switch ("sí, send it"
isn't one phrase, but "send it" alone is), and English tables are the
fallback when the conversation language's tables don't match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

AUTO = "auto"


@dataclass(frozen=True)
class Voice:
    id: str
    label: str


@dataclass(frozen=True)
class Language:
    code: str            # Whisper's language code; also Barnaby's id
    name: str            # English name, used in the agent instruction
    native: str          # endonym, for the settings menu
    bcp47: str           # tablet speechSynthesis lang
    kokoro: str          # Kokoro lang codes = first letter of this language's voice ids
    voices: tuple[Voice, ...]
    extra: str = ""      # pip extra Kokoro needs for this language, if any

    @property
    def default_voice(self) -> str:
        return self.voices[0].id


def _v(*pairs: tuple[str, str]) -> tuple[Voice, ...]:
    return tuple(Voice(i, label) for i, label in pairs)


# Kokoro v1.0 voices (hexgrad/Kokoro-82M VOICES.md). First voice = default.
LANGUAGES: dict[str, Language] = {
    "en": Language("en", "English", "English", "en-US", "ab", _v(
        ("am_michael", "Michael (US)"), ("am_adam", "Adam (US)"), ("am_fenrir", "Fenrir (US)"),
        ("am_puck", "Puck (US)"), ("af_heart", "Heart (US)"), ("af_bella", "Bella (US)"),
        ("af_nicole", "Nicole (US)"), ("af_sarah", "Sarah (US)"), ("bm_george", "George (UK)"),
        ("bm_lewis", "Lewis (UK)"), ("bm_fable", "Fable (UK)"), ("bf_emma", "Emma (UK)"),
        ("bf_isabella", "Isabella (UK)"))),
    "es": Language("es", "Spanish", "Español", "es-ES", "e", _v(
        ("ef_dora", "Dora"), ("em_alex", "Alex"), ("em_santa", "Santa"))),
    "fr": Language("fr", "French", "Français", "fr-FR", "f", _v(("ff_siwis", "Siwis"),)),
    "it": Language("it", "Italian", "Italiano", "it-IT", "i", _v(("if_sara", "Sara"), ("im_nicola", "Nicola"))),
    "pt": Language("pt", "Portuguese", "Português (Brasil)", "pt-BR", "p", _v(
        ("pf_dora", "Dora"), ("pm_alex", "Alex"), ("pm_santa", "Santa"))),
    "hi": Language("hi", "Hindi", "हिन्दी", "hi-IN", "h", _v(
        ("hf_alpha", "Alpha"), ("hf_beta", "Beta"), ("hm_omega", "Omega"), ("hm_psi", "Psi"))),
    "ja": Language("ja", "Japanese", "日本語", "ja-JP", "j", _v(
        ("jf_alpha", "Alpha"), ("jf_gongitsune", "Gongitsune"), ("jf_nezumi", "Nezumi"),
        ("jf_tebukuro", "Tebukuro"), ("jm_kumo", "Kumo")), extra="misaki[ja]"),
    "zh": Language("zh", "Mandarin Chinese", "中文（普通话）", "zh-CN", "z", _v(
        ("zf_xiaobei", "Xiaobei"), ("zf_xiaoni", "Xiaoni"), ("zf_xiaoxiao", "Xiaoxiao"),
        ("zf_xiaoyi", "Xiaoyi"), ("zm_yunjian", "Yunjian"), ("zm_yunxi", "Yunxi"),
        ("zm_yunxia", "Yunxia"), ("zm_yunyang", "Yunyang")), extra="misaki[zh]"),
}
SPOKEN_CHOICES: tuple[str, ...] = (AUTO, *LANGUAGES)


def voice_language(voice: str) -> str | None:
    """Language a Kokoro voice speaks, from its id's first letter ("ef_dora" -> "es")."""
    first = voice[:1]
    return next((code for code, lang in LANGUAGES.items() if first and first in lang.kokoro), None)


def voice_for(language: str, preferred: str) -> str:
    """The user's voice if it speaks ``language``, else that language's default."""
    lang = LANGUAGES.get(language)
    if lang is None or voice_language(preferred) == language:
        return preferred
    return lang.default_voice


def bcp47(language: str) -> str:
    return LANGUAGES.get(language, LANGUAGES["en"]).bcp47


# ---------------------------------------------------------------- recognition models
@dataclass(frozen=True)
class WhisperModel:
    id: str
    english_only: bool
    disk_mb: int      # download size (CTranslate2 weights), approximate
    vram_mb: int      # GPU memory while loaded (float16), approximate; CPU int8 needs about half in RAM
    multilingual: str  # the model to use instead when the language isn't English


WHISPER_MODELS: dict[str, WhisperModel] = {m.id: m for m in (
    WhisperModel("tiny.en", True, 75, 500, "tiny"),
    WhisperModel("base.en", True, 145, 700, "base"),
    WhisperModel("small.en", True, 485, 1200, "small"),
    WhisperModel("medium.en", True, 1530, 2600, "medium"),
    WhisperModel("distil-large-v3", True, 1510, 2600, "large-v3-turbo"),
    WhisperModel("tiny", False, 75, 500, "tiny"),
    WhisperModel("base", False, 145, 700, "base"),
    WhisperModel("small", False, 485, 1200, "small"),
    WhisperModel("medium", False, 1530, 2600, "medium"),
    WhisperModel("large-v3-turbo", False, 1620, 2800, "large-v3-turbo"),
    WhisperModel("large-v3", False, 3090, 4700, "large-v3"),
)}


def stt_model_for(model: str, language: str) -> str:
    """The model that can actually hear ``language``: an English-only model is
    swapped for its multilingual sibling unless the language is English."""
    info = WHISPER_MODELS.get(model)
    if info is None or not info.english_only or language == "en":
        return model
    return info.multilingual


def whisper_language(model: str, language: str) -> str | None:
    """The ``language=`` to pass to Whisper: forced for English-only models, the
    chosen language otherwise, ``None`` (detect) in Auto."""
    info = WHISPER_MODELS.get(model)
    if (info and info.english_only) or model.endswith(".en"):
        return "en"
    return None if language == AUTO else language


# ---------------------------------------------------------------- script detection
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿ]")
_HAN = re.compile(r"[一-鿿㐀-䶿]")


def script_language(text: str) -> str | None:
    """A language implied by the writing system alone (typed text in Auto mode).
    Latin script says nothing about which Latin language it is: ``None``."""
    if _DEVANAGARI.search(text):
        return "hi"
    if _KANA.search(text):
        return "ja"
    if _HAN.search(text):
        return "zh"
    return None


# ---------------------------------------------------------------- spoken messages
def _plural_en(n: int) -> str:
    return "s" if n > 1 else ""


# Fixed sentences Barnaby itself says. Keys: approvals(n), confirm, done(n),
# stopped(i, n, declined), task(started, who, title), overflow.
_MESSAGES: dict[str, dict[str, object]] = {
    "en": {
        "approvals": lambda n: f"I need your OK for {n} action{_plural_en(n)}. It's on screen.",
        "confirm": "Some items need you to confirm on screen.",
        "done": lambda n: f"All {n} steps are done.",
        "stopped": lambda i, n, declined: "I stopped after step {} of {}, because {}.".format(
            i, n, "you declined an action" if declined else "that step didn't finish"),
        "task": lambda started, who, title: f"{'Started' if started else 'Queued'} {who} on {title}.",
        "overflow": "The rest is on screen.",
    },
    "es": {
        "approvals": lambda n: (f"Necesito tu aprobación para {n} acci{'ones' if n > 1 else 'ón'}. "
                                "Está en pantalla."),
        "confirm": "Algunas acciones necesitan que confirmes en pantalla.",
        "done": lambda n: f"Los {n} pasos están hechos.",
        "stopped": lambda i, n, declined: f"Me detuve en el paso {i} de {n}, porque "
                                          f"{'rechazaste una acción' if declined else 'ese paso no terminó'}.",
        "task": lambda started, who, title: f"Tarea {'iniciada' if started else 'en cola'} para {who}: {title}.",
        "overflow": "El resto está en pantalla.",
    },
    "fr": {
        "approvals": lambda n: f"J'ai besoin de ton accord pour {n} action{_plural_en(n)}. C'est à l'écran.",
        "confirm": "Certaines actions doivent être confirmées à l'écran.",
        "done": lambda n: f"Les {n} étapes sont terminées.",
        "stopped": lambda i, n, declined: f"Je me suis arrêté à l'étape {i} sur {n}, parce que "
                                          f"{'tu as refusé une action' if declined else 'cette étape n’a pas abouti'}.",
        "task": lambda started, who, title: f"Tâche {'lancée' if started else 'en attente'} pour {who} : {title}.",
        "overflow": "La suite est à l'écran.",
    },
    "it": {
        "approvals": lambda n: f"Mi serve il tuo OK per {n} azion{'i' if n > 1 else 'e'}. È sullo schermo.",
        "confirm": "Alcune azioni vanno confermate sullo schermo.",
        "done": lambda n: f"Tutti i {n} passaggi sono completati.",
        "stopped": lambda i, n, declined: f"Mi sono fermato al passaggio {i} di {n}, perché "
                                          f"{'hai rifiutato un’azione' if declined else 'quel passaggio non è terminato'}.",
        "task": lambda started, who, title: f"Attività {'avviata' if started else 'in coda'} per {who}: {title}.",
        "overflow": "Il resto è sullo schermo.",
    },
    "pt": {
        "approvals": lambda n: f"Preciso da sua aprovação para {n} {'ações' if n > 1 else 'ação'}. Está na tela.",
        "confirm": "Alguns itens precisam da sua confirmação na tela.",
        "done": lambda n: f"Todas as {n} etapas foram concluídas.",
        "stopped": lambda i, n, declined: f"Parei na etapa {i} de {n}, porque "
                                          f"{'você recusou uma ação' if declined else 'essa etapa não terminou'}.",
        "task": lambda started, who, title: f"Tarefa {'iniciada' if started else 'na fila'} para {who}: {title}.",
        "overflow": "O resto está na tela.",
    },
    "hi": {
        "approvals": lambda n: f"मुझे {n} काम के लिए आपकी मंज़ूरी चाहिए। यह स्क्रीन पर है।",
        "confirm": "कुछ चीज़ों की पुष्टि स्क्रीन पर करनी होगी।",
        "done": lambda n: f"सभी {n} चरण पूरे हो गए।",
        "stopped": lambda i, n, declined: f"मैं {n} में से चरण {i} पर रुक गया, क्योंकि "
                                          f"{'आपने एक काम मना कर दिया' if declined else 'वह चरण पूरा नहीं हुआ'}।",
        "task": lambda started, who, title: f"{who} का काम {'शुरू हो गया' if started else 'कतार में है'}: {title}।",
        "overflow": "बाकी स्क्रीन पर है।",
    },
    "ja": {
        "approvals": lambda n: f"{n}件の操作に承認が必要です。画面に表示しています。",
        "confirm": "画面で確認が必要な項目があります。",
        "done": lambda n: f"全{n}ステップが完了しました。",
        "stopped": lambda i, n, declined: f"{n}ステップ中{i}ステップ目で止めました。"
                                          f"{'操作が却下されたためです' if declined else 'そのステップが完了しなかったためです'}。",
        "task": lambda started, who, title: f"{who}のタスクを{'開始しました' if started else '待機中に追加しました'}：{title}。",
        "overflow": "続きは画面に表示しています。",
    },
    "zh": {
        "approvals": lambda n: f"有{n}个操作需要你批准，已显示在屏幕上。",
        "confirm": "有些项目需要你在屏幕上确认。",
        "done": lambda n: f"全部{n}个步骤已完成。",
        "stopped": lambda i, n, declined: f"我在第{i}步（共{n}步）停下了，因为"
                                          f"{'你拒绝了一个操作' if declined else '那一步没有完成'}。",
        "task": lambda started, who, title: f"已为{who}{'启动' if started else '排队'}任务：{title}。",
        "overflow": "其余内容在屏幕上。",
    },
}


def message(language: str, key: str, *args: object) -> str:
    """A sentence Barnaby says, in the conversation language (English if missing)."""
    entry = _MESSAGES.get(language, _MESSAGES["en"]).get(key) or _MESSAGES["en"][key]
    return entry(*args) if callable(entry) else str(entry)


def reply_instruction(language: str) -> str:
    """Prefix for the agent's prompt so it answers in the user's language. Empty for
    English (prompts stay byte-identical to the English-only builds)."""
    lang = LANGUAGES.get(language)
    if lang is None or language == "en":
        return ""
    return (f"(The user is speaking {lang.name}. Reply in {lang.name}. Keep code, identifiers, file paths, "
            "commands and error messages exactly as they are; don't translate them.)")


# ---------------------------------------------------------------- voice grammar
_I = re.IGNORECASE
_ID = re.IGNORECASE | re.DOTALL

# Punctuation that separates words in spoken text (all scripts), apostrophes excluded.
_PUNCT = r"\s,.!?;:¡¿।॥\"“”«»()\[\]\-–—…、，。！？；：・「」『』（）"


@dataclass(frozen=True)
class Grammar:
    code: str
    spaced: bool                          # False: Japanese/Chinese, no spaces between words
    # yes/no to pending approvals (stop/cancel are "no")
    yes: frozenset[str]
    no: frozenset[str]
    filler: frozenset[str]
    yes_phrases: tuple[tuple[str, ...], ...] = ()   # all-filler phrases that still mean yes ("do it")
    # review drafts in hands-free mode
    send: frozenset[str] = frozenset()
    discard: frozenset[str] = frozenset()
    draft_filler: frozenset[str] = frozenset()
    # engine routing: (pattern with groups engine/rest, has a routing verb)
    engines: dict[str, str] = field(default_factory=dict)
    routes: tuple[tuple[re.Pattern[str], bool], ...] = ()
    verb_only: frozenset[str] = frozenset()         # words that only count after a routing verb ("cloud")
    # chains
    chain_sep: re.Pattern[str] | None = None
    chain_then: frozenset[str] = frozenset()        # separators that don't split a conditional ("if X then Y")
    chain_needs_punct: frozenset[str] = frozenset()  # separators that only split after punctuation
    chain_conditional: re.Pattern[str] | None = None
    chain_lead: re.Pattern[str] | None = None
    # tasks: patterns with groups engine, lanes (optional), rest
    task_patterns: tuple[re.Pattern[str], ...] = ()
    lane_split: re.Pattern[str] | None = None
    lane_filler: frozenset[str] = frozenset()
    # templates: pattern with groups name, ctx (and optional ctx2)
    template: re.Pattern[str] | None = None
    template_filler: frozenset[str] = frozenset()
    template_alias: dict[str, str] = field(default_factory=dict)  # spoken word -> template word
    token_re: re.Pattern[str] | None = None  # words = findall(token_re) instead of splitting on punctuation

    # -- tokenizing
    def tokens(self, text: str) -> list[str] | None:
        """Words of a short utterance; for unspaced scripts a greedy segmentation
        over this grammar's command vocabulary, or None if something is left over
        (so the utterance can't be a command)."""
        lowered = text.lower()
        if self.token_re is not None:
            return self.token_re.findall(lowered)
        if self.spaced:
            return [t for t in re.split(f"[{_PUNCT}]+", lowered) if t and t not in ("'", "’")]
        return self._segment(re.sub(f"[{_PUNCT}]+", "", lowered),
                             self.yes | self.no | self.filler | self.send | self.discard | self.draft_filler)

    @staticmethod
    def _segment(s: str, vocab: frozenset[str]) -> list[str] | None:
        out: list[str] = []
        i = 0
        longest = max((len(w) for w in vocab), default=0)
        while i < len(s):
            for n in range(min(longest, len(s) - i), 0, -1):
                if s[i:i + n] in vocab:
                    out.append(s[i:i + n])
                    i += n
                    break
            else:
                return None
        return out


def _alt(words: str) -> str:
    """'a|b c' -> 'a|b\\s+c': phrases in a regex alternation match any spacing."""
    return "|".join(re.sub(r"\s+", r"\\s+", w.strip()) for w in words.split("|"))


def _routes(prefix: str, verbs: str, engines: str, to: str = "", post: str = "",
            spaced: bool = True) -> tuple[tuple[re.Pattern[str], bool], ...]:
    """Routing patterns for one language, tried in order: verb + engine ("have Cursor ..."),
    engine + a verb or particle that follows it (Hindi "Cursor से कहो ..."), engine +
    separator ("Cursor, ..."), engine alone ("Cursor")."""
    eng = f"(?P<engine>{engines})" + (r"\b" if spaced else "")
    gap = r"\s+" if spaced else r"\s*"
    pre = rf"^\s*(?:(?:{prefix}){gap})?" if prefix else r"^\s*"
    sep = r"\s*[,:.\-，、：]\s*"
    if not spaced:
        tail = rf"(?:{sep}|\s*)"
    elif to:
        tail = rf"(?:{sep}|\s+(?:(?:{to})\s+)?|\s*$)"
    else:
        tail = rf"(?:{sep}|\s+|\s*$)"
    out: list[tuple[re.Pattern[str], bool]] = []
    if verbs:
        out.append((re.compile(rf"{pre}(?:{verbs})\s*{eng}{tail}(?P<rest>.*)$", _ID), True))
    if post:
        out.append((re.compile(rf"{pre}{eng}\s*(?:{post})\s*[,:，、：]?\s*(?P<rest>.*)$", _ID), True))
    out.append((re.compile(rf"{pre}{eng}{sep}(?P<rest>.*)$", _ID), False))
    out.append((re.compile(rf"{pre}{eng}\s*$(?P<rest>)", _ID), False))
    return tuple(out)


def _chain_sep(words: str, spaced: bool = True) -> re.Pattern[str]:
    if spaced:
        return re.compile(rf"(?P<punct>\s*[,;.।]\s*|\s+)(?P<word>{words})(?=[\s,:])\s*[,:]?\s+", _I)
    return re.compile(rf"(?P<punct>\s*[,;.，、；。]\s*|\s*)(?P<word>{words})\s*[,:，、：]?\s*", _I)


# Engine names as Whisper writes them. Latin forms everywhere (brand names usually
# stay Latin); native-script forms where Whisper transliterates.
_LATIN_ENGINES = {"claude": "claude", "clod": "claude", "cloud": "claude", "cursor": "cursor", "curser": "cursor",
                  "devin": "devin", "devon": "devin", "deven": "devin"}
_LATIN_ENG = "claude|clod|cloud|cursor|curser|devin|devon|deven"


def _engines(native: dict[str, str]) -> tuple[dict[str, str], str]:
    words = {**_LATIN_ENGINES, **native}
    return words, "|".join(sorted(words, key=len, reverse=True))


def _task_rx(src: str, engines: str) -> re.Pattern[str]:
    return re.compile(src.replace("{ENG}", f"(?P<engine>{engines})"), _ID)


_EN_TASK_SEP = r"\s*[:,.\-]\s*"
_SEP_ANY = r"\s*[:,.\-，、：]\s*"

EN = Grammar(
    code="en", spaced=True, token_re=re.compile(r"[a-z']+"),
    yes=frozenset({"yes", "yeah", "yep", "approve", "approved", "confirm", "confirmed", "proceed", "ahead"}),
    no=frozenset({"no", "nope", "deny", "denied", "don't", "dont", "reject", "cancel", "stop"}),
    filler=frozenset({"go", "do", "it", "please", "ok", "okay", "that", "sure", "thanks", "and", "just"}),
    yes_phrases=(("do", "it"), ("go", "ahead")),
    send=frozenset({"send", "send it", "send that", "submit", "submit it", "go", "go ahead", "do it"}),
    discard=frozenset({"scratch that", "discard", "discard it", "discard that", "cancel that", "never mind",
                       "nevermind", "delete that", "forget it", "forget that"}),
    draft_filler=frozenset({"please", "ok", "okay", "barnaby", "now", "yes"}),
    engines=dict(_LATIN_ENGINES),
    routes=_routes(r"hey|ok(?:ay)?", r"(?:have|ask|use|let|get|tell)\s+", _LATIN_ENG, to="to"),
    verb_only=frozenset({"cloud", "clod"}),
    chain_sep=re.compile(
        r"(?P<punct>\s*[,;.]\s*|\s+)"
        r"(?P<word>and\s+then|then|after\s+that|afterwards|and\s+finally|finally|lastly|next)\b"
        r"\s*[,:]?\s+", _I),
    chain_then=frozenset({"then", "and then"}),
    chain_needs_punct=frozenset({"next"}),
    chain_conditional=re.compile(r"\b(?:if|when|whenever|unless|once|as soon as|in case)\b", _I),
    chain_lead=re.compile(r"^\s*(?:first(?:ly)?|to\s+start)\b\s*[,:]?\s*", _I),
    task_patterns=(
        _task_rx(rf"^\s*(?:(?:new|start\s+an?|add\s+an?|create\s+an?)\s+)?task\s+(?:for|to)\s+{{ENG}}"
                 rf"(?:\s+(?:in|on|for)\s+(?P<lanes>[^:]+?))?{_EN_TASK_SEP}(?P<rest>.+)$", _LATIN_ENG),
        _task_rx(rf"^\s*assign\s+(?P<lanes>.+?)\s+to\s+{{ENG}}{_EN_TASK_SEP}(?P<rest>.+)$", _LATIN_ENG),
    ),
    lane_split=re.compile(r"\s*(?:,|&|\band\b|\bplus\b)\s*", _I),
    lane_filler=frozenset({"the", "folder", "folders", "lane", "lanes", "directory", "part", "side"}),
    template=re.compile(
        r"^\s*(?:please\s+)?(?:run|start|use|do|begin|kick\s+off|launch)\s+"
        r"(?:the\s+|a\s+|my\s+|our\s+)?(?P<name>.+?)\s+(?:template|workflow|checklist|routine|playbook)\b"
        r"(?:\s*(?:on|for|with|against|to)\s+(?P<ctx>.+?))?\s*[.!]?\s*$", _I),
    template_filler=frozenset({"cycle", "process", "flow", "the", "a", "my"}),
)


def _romance(code: str, *, yes: str, no: str, filler: str, yes_phrases: tuple[tuple[str, ...], ...],
             send: str, discard: str, draft_filler: str, greet: str, verbs: str, to: str,
             chain: str, then: str, needs_punct: str, conditional: str, lead: str,
             task: str, assign: str, lane_and: str, lane_filler: str,
             tpl_verbs: str, tpl_nouns: str, tpl_det: str, tpl_ctx: str, tpl_filler: str,
             alias: dict[str, str]) -> Grammar:
    """Spanish, French, Italian, Portuguese share a shape: verb-first commands,
    spaced words, "<noun> de <name>" template phrasing."""
    engines, eng = _engines({})

    def words(s: str) -> frozenset[str]:
        return frozenset(w.strip() for w in s.split("|") if w.strip())

    return Grammar(
        code=code, spaced=True,
        yes=words(yes), no=words(no), filler=words(filler), yes_phrases=yes_phrases,
        send=words(send), discard=words(discard), draft_filler=words(draft_filler),
        engines=engines, routes=_routes(greet, rf"(?:{_alt(verbs)})\s+", eng, to=_alt(to)),
        verb_only=frozenset({"cloud", "clod"}),
        chain_sep=_chain_sep(_alt(chain)),
        chain_then=words(then), chain_needs_punct=words(needs_punct),
        chain_conditional=re.compile(rf"(?<!\w)(?:{_alt(conditional)})(?!\w)", _I),
        chain_lead=re.compile(rf"^\s*(?:{_alt(lead)})(?!\w)\s*[,:]?\s*", _I),
        task_patterns=(_task_rx(task, eng), _task_rx(assign, eng)),
        lane_split=re.compile(rf"\s*(?:,|&|(?<!\w)(?:{_alt(lane_and)})(?!\w))\s*", _I),
        lane_filler=words(lane_filler),
        template=re.compile(
            rf"^\s*(?:{_alt(tpl_verbs)})\s+(?:(?:{_alt(tpl_det)})\s+)?(?:{_alt(tpl_nouns)})\s+"
            rf"(?P<name>.+?)(?:\s+(?:{_alt(tpl_ctx)})\s+(?P<ctx>.+?))?\s*[.!]?\s*$", _I),
        template_filler=words(tpl_filler), template_alias=alias,
    )


_TPL_ALIAS_COMMON = {"tdd": "tdd"}

ES = _romance(
    "es",
    yes="sí|si|claro|adelante|aprueba|apruebo|aprobado|confirma|confirmo|confirmado|procede|hazlo|dale",
    no="no|niega|deniega|denegado|rechaza|rechazado|rechazo|cancela|cancelar|para|detente|alto",
    filler="por|favor|ok|okay|vale|bueno|eso|lo|gracias|y|solo|venga|ya|hagas",
    yes_phrases=(),
    send="envía|envia|envíalo|envialo|envía eso|enviar|mándalo|mandalo|manda|adelante|hazlo|dale",
    discard="borra eso|bórralo|borralo|descarta|descártalo|descartalo|descarta eso|olvídalo|olvidalo|"
            "olvida eso|cancela eso|déjalo|dejalo|no importa",
    draft_filler="por|favor|ok|okay|vale|barnaby|ya|ahora|sí|si",
    greet=r"oye|hola|ok(?:ay)?|vale", verbs="usa|pídele a|pidele a|pide a|dile a|que",
    to="que|a",
    chain=_alt("y luego|luego|y después|después de eso|después|a continuación|y finalmente|finalmente|por último"),
    then="luego|y luego", needs_punct="luego|después",
    conditional="si|cuando|siempre que|a menos que|una vez que|en cuanto|en caso de que",
    lead="primero|para empezar|antes que nada",
    task=rf"^\s*(?:(?:nueva|crea\s+una|crear\s+una|añade\s+una|agrega\s+una|inicia\s+una)\s+)?tarea\s+para\s+{{ENG}}"
         rf"(?:\s+(?:en|sobre)\s+(?P<lanes>[^:]+?))?{_EN_TASK_SEP}(?P<rest>.+)$",
    assign=rf"^\s*asigna(?:r)?\s+(?P<lanes>.+?)\s+a\s+{{ENG}}{_EN_TASK_SEP}(?P<rest>.+)$",
    lane_and="y|e|más|mas", lane_filler="el|la|los|las|carpeta|carpetas|parte|lado|directorio|de",
    tpl_verbs="ejecuta|corre|usa|inicia|lanza|haz|empieza|arranca|por favor ejecuta",
    tpl_nouns="plantilla|flujo de trabajo|flujo|lista de verificación|checklist|rutina",
    tpl_det="el|la|mi|nuestra|nuestro", tpl_ctx="para|en|con|sobre",
    tpl_filler="de|del|la|el|mi|ciclo|proceso|flujo",
    alias={**_TPL_ALIAS_COMMON, "depuración": "debugging", "depuracion": "debugging", "depurar": "debug",
           "revisión": "review", "revision": "review", "código": "code", "codigo": "code"},
)

FR = _romance(
    "fr",
    yes="oui|ouais|approuve|approuvé|approuvée|confirme|confirmé|accepte|accepté|valide|validé|allez|vas-y",
    no="non|nan|refuse|refusé|annule|annuler|arrête|arrete|stop|rejette|rejeté|pas",
    filler="s'il|te|vous|plaît|plait|ok|okay|d'accord|merci|bon|fais|le|ça|ca|juste|et|y|vas|ne|c'est",
    yes_phrases=(("fais", "le"), ("vas", "y"), ("d'accord", "fais", "le"), ("d'accord", "vas", "y")),
    send="envoie|envoie le|envoie ça|envoyer|vas y|valide|soumets",
    discard="efface|efface ça|annule ça|oublie|oublie ça|laisse tomber|supprime ça|jette ça|abandonne",
    draft_filler="s'il|te|vous|plaît|plait|ok|okay|barnaby|maintenant|oui",
    greet=r"hé|hey|ok(?:ay)?|dis", verbs="utilise|demande à|demande a|dis à|dis a|laisse|fais",
    to="de|d'|que",
    chain=_alt("et ensuite|ensuite|et puis|puis|après ça|après cela|et enfin|enfin|finalement|pour finir"),
    then="puis|et puis|ensuite|et ensuite", needs_punct="enfin|finalement",
    conditional="si|s'il|quand|lorsque|dès que|une fois que|à moins que|au cas où",
    lead="tout d'abord|d'abord|premièrement|pour commencer",
    task=rf"^\s*(?:(?:nouvelle|crée\s+une|créer\s+une|ajoute\s+une|lance\s+une)\s+)?tâche\s+pour\s+{{ENG}}"
         rf"(?:\s+(?:dans|sur)\s+(?P<lanes>[^:]+?))?{_EN_TASK_SEP}(?P<rest>.+)$",
    assign=rf"^\s*assigne(?:r)?\s+(?P<lanes>.+?)\s+à\s+{{ENG}}{_EN_TASK_SEP}(?P<rest>.+)$",
    lane_and="et|plus", lane_filler="le|la|les|l'|dossier|dossiers|partie|côté|répertoire|du|de",
    tpl_verbs="lance|exécute|execute|utilise|démarre|demarre|fais|commence",
    tpl_nouns="modèle|modele|template|workflow|flux de travail|checklist|liste de contrôle|routine",
    tpl_det="le|la|mon|ma|notre", tpl_ctx="pour|sur|avec",
    tpl_filler="de|du|le|la|mon|ma|cycle|processus",
    alias={**_TPL_ALIAS_COMMON, "débogage": "debugging", "debogage": "debugging", "déboguer": "debug",
           "revue": "review", "relecture": "review", "code": "code"},
)

IT = _romance(
    "it",
    yes="sì|si|certo|approva|approvato|approvo|conferma|confermato|confermo|procedi|vai|avanti|fallo",
    no="no|non|nega|negato|rifiuta|rifiutato|annulla|ferma|fermati|stop|basta",
    filler="per|favore|ok|okay|va|bene|d'accordo|grazie|e|solo|pure|lo|farlo|fare",
    yes_phrases=(),
    send="invia|invialo|invia questo|manda|mandalo|vai|procedi|fallo",
    discard="cancella|cancellalo|cancella quello|lascia perdere|scarta|scartalo|dimentica|dimenticalo|"
            "non importa|annulla quello",
    draft_filler="per|favore|ok|okay|barnaby|ora|adesso|sì|si",
    greet=r"ehi|hey|ok(?:ay)?", verbs="usa|chiedi a|di' a|dì a|di a|fai|lascia",
    to="di|che",
    chain=_alt("e poi|poi|dopo di che|dopodiché|dopodiche|in seguito|successivamente|e infine|infine|alla fine"),
    then="poi|e poi", needs_punct="alla fine",
    conditional="se|quando|appena|una volta che|a meno che|nel caso in cui",
    lead="prima di tutto|innanzitutto|per prima cosa|per iniziare|prima",
    task=rf"^\s*(?:(?:nuova|nuovo|crea\s+un[a']?|aggiungi\s+un[a']?|avvia\s+un[a']?)\s*)?(?:attività|compito)\s+per\s+{{ENG}}"
         rf"(?:\s+(?:in|su|nella|nel)\s+(?P<lanes>[^:]+?))?{_EN_TASK_SEP}(?P<rest>.+)$",
    assign=rf"^\s*assegna(?:re)?\s+(?P<lanes>.+?)\s+a\s+{{ENG}}{_EN_TASK_SEP}(?P<rest>.+)$",
    lane_and="e|più|piu", lane_filler="il|lo|la|i|gli|le|cartella|cartelle|parte|lato|directory|di|del|della",
    tpl_verbs="esegui|avvia|usa|lancia|fai|inizia",
    tpl_nouns="modello|template|flusso di lavoro|workflow|checklist|lista di controllo|routine",
    tpl_det="il|lo|la|mio|mia|nostro|nostra", tpl_ctx="per|su|con",
    tpl_filler="di|del|della|il|la|ciclo|processo",
    alias={**_TPL_ALIAS_COMMON, "debug": "debug", "revisione": "review", "codice": "code"},
)

PT = _romance(
    "pt",
    yes="sim|claro|aprova|aprovado|aprovo|confirma|confirmado|confirmo|prossiga|prossegue|manda",
    no="não|nao|nega|negado|rejeita|rejeitado|recuso|cancela|cancelar|para|pare",
    filler="por|favor|ok|okay|beleza|obrigado|obrigada|e|só|so|isso|fazer|faça|faca|faz|pode|o|vai|ir",
    yes_phrases=(("pode",), ("pode", "fazer"), ("pode", "ir"), ("faça", "isso"), ("faz", "isso"), ("vai",)),
    send="envia|envie|enviar|envia isso|manda|mande|manda isso|pode mandar|pode enviar",
    discard="apaga isso|apague isso|descarta|descarte|esquece|esqueça|esquece isso|deixa pra lá|deixa para lá|"
            "cancela isso|deixa",
    draft_filler="por|favor|ok|okay|barnaby|agora|sim",
    greet=r"ei|oi|ok(?:ay)?", verbs="use|usa|peça ao|peça para o|pede ao|pede pro|diga ao|diz pro|fala pro|deixa o",
    to="para|pra|que",
    chain=_alt("e depois|depois disso|depois|e então|então|em seguida|e finalmente|finalmente|por último|por fim"),
    then="então|e então|depois|e depois", needs_punct="depois|então",
    conditional="se|quando|assim que|caso|a menos que|uma vez que",
    lead="primeiro|primeiramente|para começar|antes de tudo",
    task=rf"^\s*(?:(?:nova|crie\s+uma|cria\s+uma|adicione\s+uma|inicie\s+uma)\s+)?tarefa\s+(?:para|pro|pra)\s+(?:o\s+)?{{ENG}}"
         rf"(?:\s+(?:em|no|na|nos|nas)\s+(?P<lanes>[^:]+?))?{_EN_TASK_SEP}(?P<rest>.+)$",
    assign=rf"^\s*atribu(?:a|i|ir)\s+(?P<lanes>.+?)\s+(?:ao|a|para\s+o)\s+{{ENG}}{_EN_TASK_SEP}(?P<rest>.+)$",
    lane_and="e|mais", lane_filler="o|a|os|as|pasta|pastas|parte|lado|diretório|diretorio|de|do|da",
    tpl_verbs="rode|roda|execute|executa|use|usa|inicie|inicia|comece|faça",
    tpl_nouns="modelo|template|fluxo de trabalho|fluxo|workflow|checklist|lista de verificação|rotina",
    tpl_det="o|a|meu|minha|nosso|nossa", tpl_ctx="para|no|na|em|com",
    tpl_filler="de|do|da|o|a|ciclo|processo",
    alias={**_TPL_ALIAS_COMMON, "depuração": "debugging", "depuracao": "debugging", "revisão": "review",
           "revisao": "review", "código": "code", "codigo": "code"},
)

_HI_ENGINES, _HI_ENG = _engines({"क्लॉड": "claude", "क्लोड": "claude", "क्लाउड": "claude", "कर्सर": "cursor",
                                 "डेविन": "devin", "डेविड": "devin"})
HI = Grammar(
    code="hi", spaced=True,
    yes=frozenset({"हाँ", "हां", "हा", "हाँजी", "हांजी", "haan", "han", "मंज़ूर", "मंजूर", "स्वीकार", "अनुमति", "बढ़ो", "बढ़ें"}),
    no=frozenset({"नहीं", "नही", "ना", "मत", "रुको", "रुकिए", "रोको", "रद्द", "nahi", "nahin", "cancel", "stop"}),
    filler=frozenset({"जी", "ठीक", "है", "करो", "करें", "कर", "दो", "दें", "please", "प्लीज़", "प्लीज", "ok", "okay",
                      "ओके", "इसे", "यह", "बस", "धन्यवाद", "और", "आगे", "करना"}),
    yes_phrases=(("कर", "दो"), ("करो",), ("कर", "दें"), ("करें",)),
    send=frozenset({"भेजो", "भेज दो", "भेजें", "भेज दीजिए", "send", "send करो", "send कर दो", "सबमिट करो"}),
    discard=frozenset({"रहने दो", "छोड़ो", "छोड़ दो", "हटाओ", "हटा दो", "मिटा दो", "कैंसल करो", "भूल जाओ", "रद्द करो"}),
    draft_filler=frozenset({"please", "प्लीज़", "प्लीज", "ok", "okay", "ओके", "barnaby", "बार्नबी", "अब", "हाँ", "हां", "जी"}),
    engines=_HI_ENGINES,
    routes=_routes(r"हे|अरे|ok(?:ay)?|ओके", "", _HI_ENG,
                   post=r"(?:से|को)\s+(?:कहो|कहें|बोलो|बोलें)(?:\s+कि)?"),
    verb_only=frozenset({"cloud", "clod", "क्लाउड"}),
    chain_sep=_chain_sep(_alt("और फिर|फिर|उसके बाद|इसके बाद|और आखिर में|आखिर में|अंत में")),
    chain_then=frozenset({"फिर", "और फिर"}),
    chain_conditional=re.compile(r"(?:^|\s)(?:अगर|यदि|जब|जैसे\s+ही)(?=\s|$)"),
    chain_lead=re.compile(r"^\s*(?:सबसे\s+पहले|पहले)(?=[\s,:]|$)\s*[,:]?\s*"),
    task_patterns=(
        _task_rx(rf"^\s*{{ENG}}\s+के\s+लिए\s+(?:नया\s+|एक\s+)?(?:टास्क|काम|task)"
                 rf"(?:\s+(?P<lanes>[^:]+?)\s+में)?{_EN_TASK_SEP}(?P<rest>.+)$", _HI_ENG),
        _task_rx(rf"^\s*(?P<lanes>.+?)\s+{{ENG}}\s+को\s+(?:सौंपो|सौंपें|दो|दे\s+दो){_EN_TASK_SEP}(?P<rest>.+)$",
                 _HI_ENG),
    ),
    lane_split=re.compile(r"\s*(?:,|&|(?<!\S)(?:और|तथा)(?!\S))\s*"),
    lane_filler=frozenset({"फ़ोल्डर", "फोल्डर", "folder", "हिस्सा", "का", "की", "के"}),
    template=re.compile(
        r"^\s*(?:(?P<ctx>.+?)\s+(?:के\s+लिए|पर)\s+)?(?P<name>.+?)\s+(?:का\s+)?"
        r"(?:टेम्पलेट|टेम्प्लेट|template|वर्कफ़्लो|वर्कफ्लो|workflow|चेकलिस्ट|checklist)\s+"
        r"(?:चलाओ|चलाएं|चलाएँ|चलाइए|शुरू\s+करो|शुरू\s+करें|इस्तेमाल\s+करो|run\s+करो)\s*[।.!]?\s*$", _I),
    template_filler=frozenset({"का", "की", "के"}),
    template_alias={**_TPL_ALIAS_COMMON, "डीबगिंग": "debugging", "डिबगिंग": "debugging", "कोड": "code",
                    "रिव्यू": "review", "समीक्षा": "review"},
)

_JA_ENGINES, _JA_ENG = _engines({"クロード": "claude", "カーソル": "cursor", "デビン": "devin", "デヴィン": "devin"})
JA = Grammar(
    code="ja", spaced=False,
    yes=frozenset({"はい", "ええ", "うん", "承認", "承認します", "許可", "許可します", "進めて", "進めます", "実行して",
                   "どうぞ", "いいよ", "いいです", "お願いします"}),
    no=frozenset({"いいえ", "いや", "だめ", "ダメ", "却下", "却下します", "拒否", "拒否します", "キャンセル", "やめて",
                  "止めて", "ストップ", "しないで", "中止", "キャンセルして"}),
    filler=frozenset({"ください", "お願い", "ok", "okay", "オッケー", "です", "ね", "よ", "じゃあ", "それで", "で", "では",
                      "して"}),
    send=frozenset({"送信", "送信して", "送って", "送信する", "送る", "それで送って", "提出して"}),
    discard=frozenset({"取り消し", "取り消して", "破棄", "破棄して", "消して", "削除して", "なしで", "忘れて",
                       "やっぱりいい", "キャンセル", "やめ"}),
    draft_filler=frozenset({"ください", "お願いします", "お願い", "ok", "okay", "オッケー", "はい", "じゃあ", "barnaby",
                            "バーナビー"}),
    engines=_JA_ENGINES,
    routes=_routes(r"ねえ|ok(?:ay)?|オッケー", "", _JA_ENG,
                   post=r"(?:に|へ)(?=\s*[,，、:：])|(?:に|へ)(?:頼んで|お願いして)", spaced=False),
    verb_only=frozenset({"cloud", "clod"}),
    chain_sep=_chain_sep("それから|そのあと|その後|次に|最後に|そして", spaced=False),
    chain_then=frozenset({"それから", "そして"}),
    chain_needs_punct=frozenset({"最後に", "そして"}),
    chain_conditional=re.compile(r"もし|たら|れば|場合|なら"),
    chain_lead=re.compile(r"^\s*(?:まずは|まず|最初に|はじめに)\s*[,，、:：]?\s*"),
    task_patterns=(
        _task_rx(rf"^\s*{{ENG}}\s*(?:に|へ|向けの?)\s*(?:新しい)?タスク(?:\s*[(（]?(?P<lanes>[^:：)）]+?)[)）]?\s*で)?"
                 rf"{_SEP_ANY}(?P<rest>.+)$", _JA_ENG),
        _task_rx(rf"^\s*(?P<lanes>.+?)\s*を\s*{{ENG}}\s*に\s*(?:割り当て|任せ)(?:て|る)?{_SEP_ANY}(?P<rest>.+)$",
                 _JA_ENG),
    ),
    lane_split=re.compile(r"\s*(?:,|，|、|&|と)\s*"),
    lane_filler=frozenset({"フォルダ", "フォルダー", "ディレクトリ", "の"}),
    template=re.compile(
        r"^\s*(?:(?P<ctx>.+?)\s*(?:に対して|について|で)\s*[、,]?\s*)?(?P<name>.+?)\s*の?\s*"
        r"(?:テンプレート|ワークフロー|チェックリスト)\s*を?\s*(?:実行|開始|使っ|使用|始め|起動)", _I),
    template_alias={**_TPL_ALIAS_COMMON, "デバッグ": "debugging", "コードレビュー": "code review", "レビュー": "review"},
)

_ZH_ENGINES, _ZH_ENG = _engines({"克劳德": "claude"})
ZH = Grammar(
    code="zh", spaced=False,
    yes=frozenset({"是", "是的", "对", "批准", "同意", "确认", "继续", "执行吧", "可以", "没问题"}),
    no=frozenset({"不", "不要", "不行", "不用", "别", "拒绝", "取消", "停", "停止", "否", "算了"}),
    filler=frozenset({"请", "吧", "嗯", "啊", "谢谢", "ok", "okay", "好", "好的", "行", "就", "这样", "了", "做", "执行"}),
    send=frozenset({"发送", "发吧", "发出去", "提交", "发", "送出", "发送吧"}),
    discard=frozenset({"算了", "删掉", "删除", "取消", "不要了", "丢弃", "撤销", "删了"}),
    draft_filler=frozenset({"请", "吧", "好", "好的", "ok", "okay", "现在", "barnaby", "了"}),
    engines=_ZH_ENGINES,
    routes=_routes(r"嘿|ok(?:ay)?", r"让|叫", _ZH_ENG, spaced=False),
    verb_only=frozenset({"cloud", "clod"}),
    chain_sep=_chain_sep("再然后|然后|接着|随后|之后|最后", spaced=False),
    chain_then=frozenset({"然后", "再然后"}),
    chain_needs_punct=frozenset({"最后", "之后"}),
    chain_conditional=re.compile(r"如果|要是|假如|一旦|等到"),
    chain_lead=re.compile(r"^\s*(?:首先|第一步)\s*[,，、:：]?\s*"),
    task_patterns=(
        _task_rx(rf"^\s*(?:新建|创建|添加)?(?:一个)?任务\s*给\s*{{ENG}}(?:\s*[,，]?\s*在\s*(?P<lanes>[^:：，,]+?)\s*(?:里|中|下)?)?"
                 rf"{_SEP_ANY}(?P<rest>.+)$", _ZH_ENG),
        _task_rx(rf"^\s*给\s*{{ENG}}\s*(?:一个|新|新建一个)?任务(?:\s*[,，]?\s*在\s*(?P<lanes>[^:：，,]+?)\s*(?:里|中|下)?)?"
                 rf"{_SEP_ANY}(?P<rest>.+)$", _ZH_ENG),
        _task_rx(rf"^\s*把\s*(?P<lanes>.+?)\s*(?:分配|交)给\s*{{ENG}}{_SEP_ANY}(?P<rest>.+)$", _ZH_ENG),
    ),
    lane_split=re.compile(r"\s*(?:,|，|、|&|和|及)\s*"),
    lane_filler=frozenset({"文件夹", "目录", "部分"}),
    template=re.compile(
        r"^\s*(?:请)?(?:(?:对|针对|为)\s*(?P<ctx>.+?)\s*)?(?:运行|执行|启动|使用|开始)\s*(?P<name>.+?)\s*的?\s*"
        r"(?:模板|工作流|流程|检查清单)(?:\s*[,，]\s*(?:针对|对|用于)\s*(?P<ctx2>.+?))?\s*[。.!！]?\s*$", _I),
    template_alias={**_TPL_ALIAS_COMMON, "调试": "debugging", "代码审查": "code review", "代码评审": "code review",
                    "审查": "review"},
)

GRAMMARS: dict[str, Grammar] = {g.code: g for g in (EN, ES, FR, IT, PT, HI, JA, ZH)}


def grammars(language: str | None) -> tuple[Grammar, ...]:
    """Tables to try for an utterance, most specific first; English is always last."""
    g = GRAMMARS.get(language or "en")
    return (EN,) if g is None or g is EN else (g, EN)
