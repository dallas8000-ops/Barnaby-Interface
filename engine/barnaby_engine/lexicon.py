"""Vocabulary biasing for speech recognition.

Whisper's ``initial_prompt`` conditions the decoder on preceding text, which
strongly biases spelling of rare tokens ("Zustand", "SQLAlchemy"). It is capped
at ~224 tokens by the model, so we budget characters and put the project's own
dependency names first: they are the terms most likely to be spoken and least
likely to be in Whisper's prior.

``normalize`` rewrites a small set of unambiguous spoken forms. It is
deliberately conservative: a wrong rewrite is worse than none, because the user
sees the transcript and can correct it, but may not notice a silent change.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

BASE_VOCAB: tuple[str, ...] = (
    # languages / runtimes
    "Python", "TypeScript", "JavaScript", "SQL", "PostgreSQL", "Bash", "PowerShell",
    "Go", "Rust", "Java", "Kotlin", "C#", ".NET", "PHP", "Node.js", "Deno", "Bun",
    # frameworks / libs
    "Django", "DRF", "FastAPI", "Flask", "SQLAlchemy", "Alembic", "Celery", "Redis",
    "Pydantic", "React", "Vite", "Next.js", "Tailwind", "Zustand", "React Router",
    "Express", "NestJS", "Prisma", "Laravel", "Spring Boot", "Tauri", "Electron",
    # testing / tooling
    "pytest", "unittest", "Vitest", "Jest", "Playwright", "ESLint", "Prettier", "Ruff",
    "mypy", "npm", "pnpm", "npx", "pip", "uv", "Docker", "docker-compose", "kubectl",
    "GitHub Actions", "CI/CD", "Railway", "Vercel", "Nginx", "JWT", "OAuth", "CORS",
    "CSRF", "RBAC", "JSON", "YAML", "TOML", "GraphQL", "REST", "WebSocket", "async",
    "await", "useEffect", "useState", "useMemo", "useCallback", "props", "middleware",
    "serializer", "queryset", "migration", "endpoint", "schema", "env", ".env",
)

_MAX_PROMPT_CHARS = 700  # ~200 tokens for mostly-ASCII identifiers
_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "composer.json")


def project_terms(workspace: str | Path, limit: int = 60) -> list[str]:
    """Dependency names from the project's manifests (top level only)."""
    root = Path(workspace)
    terms: list[str] = []
    if not root.is_dir():
        return terms

    def add(name: str) -> None:
        name = name.strip()
        # scoped npm packages: keep the readable part (@tanstack/react-query -> react-query)
        if name.startswith("@") and "/" in name:
            name = name.split("/", 1)[1]
        if 2 <= len(name) <= 40 and name not in terms:
            terms.append(name)

    for manifest in _MANIFESTS:
        path = root / manifest
        if not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if manifest in ("package.json", "composer.json"):
                data = json.loads(text)
                for section in ("dependencies", "devDependencies", "require", "require-dev"):
                    for name in (data.get(section) or {}):
                        add(name)
            elif manifest == "pyproject.toml":
                data = tomllib.loads(text)
                deps = list(data.get("project", {}).get("dependencies", []))
                deps += list(data.get("tool", {}).get("poetry", {}).get("dependencies", {}).keys())
                for spec in deps:
                    add(re.split(r"[\s<>=!~;\[]", spec, maxsplit=1)[0])
            elif manifest == "requirements.txt":
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.startswith(("#", "-")):
                        add(re.split(r"[\s<>=!~;\[]", line, maxsplit=1)[0])
            elif manifest == "go.mod":
                for m in re.finditer(r"^\s*([\w.\-]+/[\w.\-/]+)\s+v", text, re.M):
                    add(m.group(1).rsplit("/", 1)[-1])
            elif manifest == "Cargo.toml":
                data = tomllib.loads(text)
                for name in (data.get("dependencies") or {}):
                    add(name)
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            continue  # a broken manifest must never break recognition
        if len(terms) >= limit:
            break
    return terms[:limit]


def build_prompt(extra_terms: list[str] | None = None) -> str:
    """Comma-joined vocabulary, project terms first, within Whisper's budget."""
    seen: set[str] = set()
    ordered: list[str] = []
    for term in [*(extra_terms or []), *BASE_VOCAB]:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(term)
    out = ""
    for term in ordered:
        candidate = f"{out}, {term}" if out else term
        if len(candidate) > _MAX_PROMPT_CHARS:
            break
        out = candidate
    return out + "."


# (pattern, replacement). Word-bounded, case-insensitive. Only forms that have
# no plausible non-code meaning in a coding dictation context.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.I), r) for p, r in (
        (r"\bdot (py|ts|tsx|js|jsx|json|md|toml|yaml|yml|sql|css|html|env|go|rs)\b", r".\1"),
        (r"\buse (effect|state|memo|callback|ref|context|reducer)\b",
         lambda m: "use" + m.group(1).capitalize()),  # type: ignore[arg-type]
        (r"\bsequel alchemy\b", "SQLAlchemy"),
        (r"\bzoo stand\b", "Zustand"),
        (r"\bpost gres(?:ql| q l)?\b", "PostgreSQL"),
        (r"\bfast api\b", "FastAPI"),
        (r"\bvit test\b", "Vitest"),
        (r"\btype script\b", "TypeScript"),
        (r"\bjava script\b", "JavaScript"),
        (r"\bnode js\b", "Node.js"),
        (r"\bpie test\b", "pytest"),
        (r"\bpi test\b", "pytest"),
        (r"\bget hub\b", "GitHub"),
    )
)


def normalize(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)  # type: ignore[arg-type]
    return re.sub(r"\s{2,}", " ", text).strip()
