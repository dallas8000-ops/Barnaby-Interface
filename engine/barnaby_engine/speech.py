"""Turns streamed agent markdown into speakable sentences.

Speaking sentence-by-sentence as deltas arrive (instead of waiting for the
final result) cuts perceived latency from "whole turn" to "first sentence".
Code fences, inline code-heavy lines, tables and secrets are never spoken.
"""
from __future__ import annotations

import re

from .safety import redact_for_speech

_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(\[])|\n{2,}")
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
    def __init__(self, max_chars: int = 700) -> None:
        self.max_chars = max_chars
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
        if not text or not re.search(r"[A-Za-z]", text):
            return []
        if self._overflowed:
            return []
        if self._spoken + len(text) > self.max_chars:
            self._overflowed = True
            return [OVERFLOW_NOTE] if self._spoken else [text[: self.max_chars].rsplit(" ", 1)[0] + ".", OVERFLOW_NOTE]
        self._spoken += len(text)
        return [text]
