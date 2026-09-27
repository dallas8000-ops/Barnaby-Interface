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
import re as _re

# "then" joins steps only when it isn't the consequent of a conditional clause.
_CONDITIONAL = _re.compile(r"\b(?:if|when|whenever|unless|once|as soon as|in case)\b", _re.IGNORECASE)
_SEPARATOR = _re.compile(
    r"(?P<punct>\s*[,;.]\s*|\s+)"
    r"(?P<word>and\s+then|then|after\s+that|afterwards|and\s+finally|finally|lastly|next)\b"
    r"\s*[,:]?\s+",
    _re.IGNORECASE,
)
_LEAD = _re.compile(r"^\s*(?:first(?:ly)?|to\s+start)\b\s*[,:]?\s*", _re.IGNORECASE)
_CODE = _re.compile(r"`[^`]*`")


def split_chain(text: str) -> list[str]:
    """Split one spoken instruction into sequential steps.

    Deliberately conservative, because a wrong split sends the agent fragments:
    * separators: "then", "and then", "after that", "afterwards", "finally",
      "lastly", and "next" only after punctuation ("..., next add tests");
    * a "then" whose clause starts a conditional ("if the tests fail then
      revert", "when it builds, then deploy") is part of one step;
    * text inside backticks is never split.
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
    steps: list[str] = []
    start = 0
    for m in _SEPARATOR.finditer(masked):
        clause = masked[start:m.start()]
        word = m.group("word").lower().split()[-1] if m.group("word") else ""
        punct = m.group("punct").strip()
        if not clause.strip():
            continue  # leading "then ..." isn't a split
        if word == "next" and not punct:
            continue  # "the next page" is content
        if word == "then" and _CONDITIONAL.search(clause):
            continue  # "if X then Y" is one step
        steps.append(clause)
        start = m.end()
    steps.append(masked[start:])

    def _unmask(s: str) -> str:
        return _re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], s)

    cleaned = []
    for i, s in enumerate(steps):
        s = _unmask(s).strip().rstrip(".;,").strip()
        if i == 0:
            s = _LEAD.sub("", s)
        if s:
            cleaned.append(s[0].upper() + s[1:])
    return cleaned or [original]
