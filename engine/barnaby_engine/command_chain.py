import uuid
from typing import List, Optional

class CommandChain:
    """
    Command chaining system for executing multiple voice instructions sequentially.

    Maintains context across chained commands and enforces depth limits.
    """

    def __init__(self, max_depth: int = 5):
        self.max_depth = max_depth
        self.is_active = False
        self.depth = 0
        self.chain_id: Optional[str] = None
        self.commands: List[str] = []

    def start_chain(self) -> str:
        """
        Start a new command chain.

        Returns:
            The chain ID for this chain
        """
        if self.is_active:
            self.end_chain(success=False)  # End previous chain

        self.is_active = True
        self.depth = 0
        self.chain_id = str(uuid.uuid4())
        self.commands = []
        return self.chain_id

    def add_command(self, command: str) -> bool:
        """
        Add a command to the current chain.

        Args:
            command: The voice command text

        Returns:
            True if command was added, False if chain is inactive or at max depth
        """
        if not self.is_active:
            return False

        if self.depth >= self.max_depth:
            return False

        self.commands.append(command)
        self.depth += 1
        return True

    def end_chain(self, success: bool = True) -> None:
        """
        End the current command chain.

        Args:
            success: Whether the chain completed successfully
        """
        self.is_active = False
        # Keep chain_id and commands for history, but reset depth
        self.depth = 0

    def get_context(self) -> str:
        """
        Get the context string from all commands in the chain.

        Returns:
            Concatenated command history
        """
        return " ".join(self.commands)

    def get_chain_id(self) -> Optional[str]:
        """Get the current chain ID."""
        return self.chain_id

    def can_add_command(self) -> bool:
        """Check if another command can be added to the chain."""
        return self.is_active and self.depth < self.max_depth


# ------------------------------------------------------------------ splitting
import re as _re  # noqa: E402

from . import languages as _languages  # noqa: E402

_CODE = _re.compile(r"`[^`]*`")


def split_chain(text: str, lang: str = "en") -> list[str]:
    """Split one spoken instruction into sequential steps.

    Deliberately conservative, because a wrong split sends the agent fragments:
    * separators: "then", "and then", "after that", "afterwards", "finally",
      "lastly", and "next" only after punctuation ("..., next add tests");
    * a "then" whose clause starts a conditional ("if the tests fail then
      revert", "when it builds, then deploy") is part of one step;
    * text inside backticks is never split.
    Other languages use their own words from ``languages.GRAMMARS`` with the
    same rules; English separators are understood in every language.
    Returns ``[text]`` when there's nothing to split.
    """
    original = " ".join(text.split())
    if not original:
        return []
    # Mask code spans so separators inside them are invisible.
    spans: list[str] = []

    def _mask(m: _re.Match[str]) -> str:
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    masked = _CODE.sub(_mask, original)
    grammars = _languages.grammars(lang)
    steps: list[str] = []
    start = 0
    for g, m in _separators(masked, grammars):
        if m.start() < start:
            continue  # overlaps a separator already used
        clause = masked[start:m.start()]
        word = " ".join(m.group("word").lower().split())
        punct = m.group("punct").strip()
        if not clause.strip():
            continue  # leading "then ..." isn't a split
        if word in g.chain_needs_punct and not punct:
            continue  # "the next page" is content
        if word in g.chain_then and any(c.chain_conditional.search(clause) for c in grammars
                                        if c.chain_conditional):
            continue  # "if X then Y" is one step
        steps.append(clause)
        start = m.end()
    steps.append(masked[start:])

    def _unmask(s: str) -> str:
        return _re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], s)

    cleaned = []
    for i, s in enumerate(steps):
        s = _unmask(s).strip().rstrip(".;,。；，、").strip()
        if i == 0:
            for g in grammars:
                if g.chain_lead:
                    s = g.chain_lead.sub("", s, count=1)
        if s:
            cleaned.append(s[0].upper() + s[1:])
    return cleaned or [original]


def _separators(text: str, grammars: tuple[_languages.Grammar, ...]) -> list[tuple[_languages.Grammar, _re.Match[str]]]:
    """(grammar, match) for every separator in ``text``, in order of position."""
    found = [(m.start(), -len(m.group(0)), i, g, m) for i, g in enumerate(grammars) if g.chain_sep
             for m in g.chain_sep.finditer(text)]
    return [(g, m) for *_, g, m in sorted(found, key=lambda f: f[:3])]
