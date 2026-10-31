"""Turns streamed agent markdown into speakable sentences.

Speaking sentence-by-sentence as deltas arrive (instead of waiting for the
final result) cuts perceived latency from "whole turn" to "first sentence".
Code fences, inline code-heavy lines, tables and secrets are never spoken.
"""
from __future__ import annotations

import re

from .safety import redact_for_speech

# Sentence ends: Latin ". " before a capital (or ¿ ¡ or a non-Latin letter), the
# Hindi danda, and CJK 。！？ which need no space after them.
_SENTENCE_END = re.compile(
    r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(\[¿¡À-ÖØ-Þऀ-ॿ぀-ヿ一-鿿])"
    r"|(?<=[।॥])\s*|(?<=[。！？])[」』）\"']*\s*|\n{2,}")
_LETTER = re.compile(r"[^\W\d_]")  # a letter in any script: there is something to say
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_INLINE_CODE = re.compile(r"`([^`]{1,40})`")
_LONG_INLINE_CODE = re.compile(r"`[^`]{41,}`")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_URL = re.compile(r"https?://\S+")

OVERFLOW_NOTE = "The rest is on screen."


def _clean(sentence: str) -> str:
    s = _LINK.sub(r"\1", sentence)
    s = _URL.sub("a link", s)
    s = _LONG_INLINE_CODE.sub("the code shown", s)
    s = _INLINE_CODE.sub(r"\1", s)
    s = _EMPHASIS.sub(r"\2", s)
    lines = []
    for line in s.splitlines():
        line = line.strip()
        if not line or line.startswith("|") or set(line) <= set("-=*_ "):
            continue  # tables, rules
        line = re.sub(r"^(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", "", line)
        lines.append(line)
    s = " ".join(lines)
    return redact_for_speech(re.sub(r"\s{2,}", " ", s)).strip()


class SpeechComposer:
    def __init__(self, max_chars: int = 700, overflow_note: str = OVERFLOW_NOTE) -> None:
        self.max_chars = max_chars
        self.overflow_note = overflow_note
        self._buf = ""
        self._in_fence = False
        self._spoken = 0
        self._overflowed = False

    def feed(self, delta: str) -> list[str]:
        """Consume a text delta; return sentences ready to speak."""
        self._buf += delta
        out: list[str] = []
        while True:
            if self._in_fence:
                end = self._buf.find("```")
                if end < 0:
                    # keep a possible partial "``" at the tail
                    self._buf = self._buf[-2:]
                    return out
                self._buf = self._buf[end + 3:]
                nl = self._buf.find("\n")
                if nl < 0:
                    return out
                self._buf = self._buf[nl + 1:]
                self._in_fence = False
                continue
            fence = self._buf.find("```")
            head = self._buf if fence < 0 else self._buf[:fence]
            parts = _SENTENCE_END.split(head)
            complete, rest = parts[:-1], parts[-1]
            for sentence in complete:
                out.extend(self._accept(sentence))
            if fence < 0:
                self._buf = rest
                return out
            out.extend(self._accept(rest))
            self._buf = self._buf[fence + 3:]
            self._in_fence = True

    def flush(self) -> list[str]:
        out: list[str] = []
        if not self._in_fence:
            out.extend(self._accept(self._buf))
        self._buf = ""
        self._in_fence = False
        return out

    def _accept(self, raw: str) -> list[str]:
        text = _clean(raw)
        if not text or not _LETTER.search(text):
            return []
        if self._overflowed:
            return []
        if self._spoken + len(text) > self.max_chars:
            self._overflowed = True
            if self._spoken:
                return [self.overflow_note]
            head = text[: self.max_chars]
            head = head.rsplit(" ", 1)[0] if " " in head else head  # CJK has no spaces to cut at
            stop = "。" if re.match(r"[぀-ヿ一-鿿]", head[-1:]) else "."
            return [head + stop, self.overflow_note]
        self._spoken += len(text)
        return [text]
