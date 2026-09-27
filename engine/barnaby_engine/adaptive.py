"""Adaptive voice profile: learns from the user's own transcript corrections.

Signal: the user edits the recognized text before sending. A word-level diff
of (heard -> sent) yields candidate substitutions. A substitution becomes an
active rewrite rule only after it recurs (``PROMOTE_AT``), because a single
edit is often a change of mind, not a mishearing. Corrected target phrases and
frequently used identifiers feed Whisper's vocabulary prompt, so the recognizer
itself is biased toward them, not just post-corrected.

This adapts *vocabulary and phrasing*, not the acoustic model. Acoustic
adaptation needs fine-tuning on the opt-in voice sample set.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .store import Store

PROMOTE_AT = 2
_MAX_SPAN_WORDS = 4
_TOKEN = re.compile(r"[A-Za-z0-9_.\-/#+@']+")
_STOP = frozenset("""
a an the and or but if of to in on at for with by from is are was were be been it its this that
these those i you we they he she me my your our can could would should will do does did not no
yes ok okay so then than just please now also as up out into about
""".split())
_IDENTIFIER = re.compile(r"^(?:[a-z]+[A-Z]\w*|[A-Z][a-z]+[A-Z]\w*|\w+[._/]\w+|[A-Z]{2,}\w*)$")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


@dataclass(frozen=True)
class Rule:
    heard: str
    meant: str
    pattern: re.Pattern[str]


def extract_substitutions(heard: str, sent: str) -> list[tuple[str, str]]:
    """Short replace-spans between what was recognized and what was sent."""
    a, b = _tokens(heard), _tokens(sent)
    if not a or not b:
        return []
    matcher = difflib.SequenceMatcher(a=[t.lower() for t in a], b=[t.lower() for t in b], autojunk=False)
    # A correction keeps its context: at least one unchanged token, and unchanged
    # tokens cover >= 1/3 of what was heard. Otherwise it's a new message, not a
    # fix. (A similarity-ratio cutoff wrongly rejects short utterances such as
    # "open doc ops" -> "open DBOps", which is the most common correction shape.)
    anchored = sum(size for *_, size in matcher.get_matching_blocks())
    if anchored == 0 or anchored * 3 < len(a):
        return []
    subs: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "replace":
            continue
        if (i2 - i1) > _MAX_SPAN_WORDS or (j2 - j1) > _MAX_SPAN_WORDS:
            continue
        src, dst = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if src.lower() == dst.lower():
            continue  # case-only change: not a mishearing
        if i2 - i1 == 1 and (src.lower() in _STOP or len(src) < 3):
            continue  # too generic to rewrite everywhere
        subs.append((src.lower(), dst))
    return subs


class VoiceProfile:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._rules: list[Rule] = []
        self.reload()

    def reload(self) -> None:
        best: dict[str, tuple[int, str]] = {}
        for row in self.store.corrections():
            if row["disabled"] or row["count"] < PROMOTE_AT:
                continue
            heard = row["heard"]
            # conflicting targets for the same heard phrase: majority wins
            if heard not in best or row["count"] > best[heard][0]:
                best[heard] = (row["count"], row["meant"])
        # longest first, so "sequel alchemy" wins over a hypothetical "sequel"
        self._rules = [
            Rule(h, m, re.compile(r"(?<![\w])" + re.escape(h) + r"(?![\w])", re.I))
            for h, (_, m) in sorted(best.items(), key=lambda kv: -len(kv[0]))
        ]

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def apply(self, text: str) -> str:
        for rule in self._rules:
            text = rule.pattern.sub(lambda _m, r=rule: r.meant, text)
        return text

    def learn(self, heard: str, sent: str) -> list[tuple[str, str]]:
        """Record corrections and usage. Returns newly promoted (heard, meant)."""
        promoted: list[tuple[str, str]] = []
        for src, dst in extract_substitutions(heard, sent):
            if self.store.record_correction(src, dst) == PROMOTE_AT:
                promoted.append((src, dst))
        identifiers = [t for t in _tokens(sent) if _IDENTIFIER.match(t) and len(t) <= 40]
        corrected_targets = [dst for _, dst in extract_substitutions(heard, sent)]
        if identifiers or corrected_targets:
            self.store.bump_vocab(list(dict.fromkeys(identifiers + corrected_targets)))
        if promoted:
            self.reload()
        return promoted

    def vocabulary(self) -> list[str]:
        learned = [r.meant for r in self._rules]
        return list(dict.fromkeys(learned + self.store.top_vocab()))
