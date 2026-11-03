from typing import List, Dict, Any, Optional

class TemplateManager:
    """
    Pre-built task templates for common development patterns.
    """

    BUILTIN_TEMPLATES = {
        "tdd-cycle": {
            "id": "tdd-cycle",
            "name": "TDD Cycle",
            "description": "Test-driven development workflow",
            "steps": [
                {"instruction": "Write a failing test for the feature", "agent": "claude"},
                {"instruction": "Implement the minimal code to make the test pass", "agent": "claude"},
                {"instruction": "Run the tests to verify they pass", "agent": "claude"},
                {"instruction": "Refactor the code if needed", "agent": "claude"},
            ],
        },
        "debugging": {
            "id": "debugging",
            "name": "Debugging Workflow",
            "description": "Systematic debugging process",
            "steps": [
                {"instruction": "Add logging to understand the issue", "agent": "claude"},
                {"instruction": "Reproduce the bug with the logging in place", "agent": "claude"},
                {"instruction": "Analyze the logs to identify the root cause", "agent": "claude"},
                {"instruction": "Fix the identified issue", "agent": "claude"},
                {"instruction": "Verify the fix resolves the bug", "agent": "claude"},
            ],
        },
        "code-review": {
            "id": "code-review",
            "name": "Code Review Checklist",
            "description": "Comprehensive code review workflow",
            "steps": [
                {"instruction": "Check code style and formatting", "agent": "claude"},
                {"instruction": "Review for security vulnerabilities", "agent": "claude"},
                {"instruction": "Check for performance issues", "agent": "claude"},
                {"instruction": "Verify test coverage", "agent": "claude"},
                {"instruction": "Review documentation completeness", "agent": "claude"},
            ],
        },
    }

    def __init__(self):
        self._templates = self.BUILTIN_TEMPLATES.copy()

    def list_templates(self) -> List[Dict[str, Any]]:
        """
        List all available templates.

        Returns:
            List of template dictionaries
        """
        return list(self._templates.values())

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific template by ID.

        Args:
            template_id: The template ID

        Returns:
            The template dictionary, or None if not found
        """
        return self._templates.get(template_id)

    def add_custom_template(self, template_id: str, template: Dict[str, Any]) -> None:
        """
        Add a custom template.

        Args:
            template_id: The template ID
            template: The template definition
        """
        self._templates[template_id] = template

    def remove_template(self, template_id: str) -> bool:
        """
        Remove a template.

        Args:
            template_id: The template ID to remove

        Returns:
            True if removed, False if not found
        """
        if template_id in self._templates and template_id not in self.BUILTIN_TEMPLATES:
            del self._templates[template_id]
            return True
        return False


# ------------------------------------------------------------------ voice matching
import re as _re  # noqa: E402

from . import languages as _languages  # noqa: E402

_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
_WORD = _re.compile(rf"[^\W_{_CJK}]+|[{_CJK}]+")


def _tokens(text: str) -> list[str]:
    # Letters/digits in any script; a run of Han/kana is one token ("デバッグ").
    return _WORD.findall(text.lower())


def match_template(text: str, templates: list[dict[str, Any]],
                   lang: str = "en") -> tuple[dict[str, Any], str | None] | None:
    """ "run the TDD template for the cart total" -> (tdd template, "the cart total").

    Only an explicit "<verb> the <name> template|workflow|checklist" phrasing
    counts, so ordinary instructions that merely mention a template ("update
    the email template") are never hijacked. Spoken words match template name
    or id words by prefix ("debug" ~ "debugging", "tdd" ~ "tdd-cycle").
    Each language has its own phrasing and a few aliases ("depuración" ->
    "debugging") in ``languages.GRAMMARS``.
    """
    for g in _languages.grammars(lang):
        m = g.template.match(text) if g.template else None
        if not m:
            continue
        spoken: list[str] = []
        for t in _tokens(m.group("name")):
            if t not in g.template_filler:
                spoken.extend(g.template_alias.get(t, t).split())
        found = _best_template(spoken, templates)
        if found is not None:
            groups = m.groupdict()
            ctx = (groups.get("ctx") or groups.get("ctx2") or "").strip() or None
            return found, ctx
    return None


def _best_template(spoken: list[str], templates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not spoken:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for tpl in templates:
        words = set(_tokens(str(tpl.get("name", ""))) + _tokens(str(tpl.get("id", ""))))
        if all(any(w.startswith(s) or s.startswith(w) for w in words) for s in spoken):
            score = len(words)  # prefer the tightest match
            if best is None or score < best[0]:
                best = (score, tpl)
    return best[1] if best else None


def template_steps(template: dict[str, Any], limit: int = 12) -> list[str]:
    """Instruction strings from a template or saved workflow, tolerating extra keys."""
    steps = []
    for step in template.get("steps") or []:
        text = step.get("instruction") if isinstance(step, dict) else step
        if isinstance(text, str) and text.strip():
            steps.append(" ".join(text.split()))
    return steps[:limit]
