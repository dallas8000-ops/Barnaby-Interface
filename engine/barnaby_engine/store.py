"""Local SQLite persistence: sessions, turns, and the adaptive voice profile.

Single-writer (the engine process), WAL mode so the UI-driven reads never block
a turn being written. All SQL is parameterized.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    claude_session_id TEXT,
    title TEXT NOT NULL DEFAULT 'New session',
    workspace TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_updated ON sessions(updated_at DESC);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL DEFAULT '',
    tools_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'ok',
    cost_usd REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_turns_session ON turns(session_id, created_at);
CREATE TABLE IF NOT EXISTS corrections (
    heard TEXT NOT NULL,
    meant TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    disabled INTEGER NOT NULL DEFAULT 0,
    last_seen REAL NOT NULL,
    PRIMARY KEY (heard, meant)
);
CREATE TABLE IF NOT EXISTS vocab (
    term TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 1,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    engine TEXT NOT NULL,
    title TEXT NOT NULL,
    instruction TEXT NOT NULL,
    lanes_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    summary TEXT NOT NULL DEFAULT '',
    result_text TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    cost_usd REAL,
    cli_session_id TEXT,
    turns INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tasks_created ON tasks(created_at DESC);
CREATE TABLE IF NOT EXISTS voice_samples (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    heard TEXT NOT NULL,
    final_text TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.recovered_to: Path | None = None  # where a damaged database was set aside, if one was
        path = Path(path)
        try:
            self._open(path)
        except sqlite3.DatabaseError:
            self.recovered_to = self._set_aside(path)
            self._open(path)

    def _open(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                # A truncated or torn file opens fine and only fails on the first write, mid-conversation.
                if self._conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("database disk image is malformed")
                self._conn.executescript(SCHEMA)
                self._migrate()
        except sqlite3.DatabaseError:
            self._conn.close()  # Windows won't let us move a file that is still open
            raise

    @staticmethod
    def _set_aside(path: Path) -> Path:
        """Move a damaged database (and its WAL files) out of the way, keeping it for recovery."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = path.with_name(f"{path.name}.damaged-{stamp}")
        for suffix in ("", "-wal", "-shm"):
            src = path.with_name(path.name + suffix)
            if src.exists():
                try:
                    os.replace(src, target.with_name(target.name + suffix))
                except OSError:
                    src.unlink(missing_ok=True)
        return target

    def _migrate(self) -> None:
        # v1.1: sessions are bound to the agent engine that created them
        # (a Claude session id can't be resumed by Cursor and vice versa).
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "engine" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN engine TEXT NOT NULL DEFAULT 'claude'")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------ sessions
    def create_session(self, workspace: str, engine: str = "claude") -> str:
        sid, now = str(uuid.uuid4()), time.time()
        self._exec("INSERT INTO sessions(id, workspace, engine, created_at, updated_at) VALUES (?,?,?,?,?)",
                   (sid, workspace, engine, now, now))
        return sid

    def set_claude_session(self, session_id: str, claude_session_id: str) -> None:
        self._exec("UPDATE sessions SET claude_session_id=?, updated_at=? WHERE id=?",
                   (claude_session_id, time.time(), session_id))

    def set_title_if_default(self, session_id: str, title: str) -> None:
        self._exec("UPDATE sessions SET title=? WHERE id=? AND title='New session'",
                   (title[:80], session_id))

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM sessions WHERE id=?", (session_id,))
        if not rows:
            return None
        session = rows[0]
        turns = self._rows("SELECT * FROM turns WHERE session_id=? ORDER BY created_at", (session_id,))
        for t in turns:
            t["tools"] = json.loads(t.pop("tools_json") or "[]")
        session["turns"] = turns
        return session

    def list_sessions(self, limit: int = 50, workspace: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE s.workspace=? ", (workspace, limit)) if workspace is not None else ("", (limit,))
        return self._rows(
            "SELECT s.id, s.title, s.workspace, s.engine, s.updated_at, "
            "(SELECT COUNT(*) FROM turns t WHERE t.session_id=s.id) AS turn_count "
            f"FROM sessions s {where}ORDER BY s.updated_at DESC LIMIT ?", params)

    def workspaces(self) -> list[dict[str, Any]]:
        """Every project folder in history (sessions and tasks), most recently used first,
        with how many sessions each has."""
        return self._rows(
            "SELECT workspace, MAX(at) AS last_used, SUM(n) AS sessions FROM ("
            " SELECT workspace, updated_at AS at, 1 AS n FROM sessions"
            " UNION ALL SELECT workspace, created_at AS at, 0 AS n FROM tasks"
            ") WHERE workspace != '' GROUP BY workspace ORDER BY last_used DESC")

    def add_turn(self, session_id: str, *, user_text: str, assistant_text: str,
                 tools: list[dict[str, Any]], status: str, cost_usd: float | None) -> str:
        tid, now = str(uuid.uuid4()), time.time()
        self._exec(
            "INSERT INTO turns(id, session_id, user_text, assistant_text, tools_json, status, cost_usd, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (tid, session_id, user_text, assistant_text, json.dumps(tools), status, cost_usd, now))
        self._exec("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
        return tid

    # ------------------------------------------------------------ voice profile
    def record_correction(self, heard: str, meant: str) -> int:
        now = time.time()
        self._exec(
            "INSERT INTO corrections(heard, meant, last_seen) VALUES (?,?,?) "
            "ON CONFLICT(heard, meant) DO UPDATE SET count=count+1, last_seen=excluded.last_seen",
            (heard, meant, now))
        row = self._rows("SELECT count FROM corrections WHERE heard=? AND meant=?", (heard, meant))
        return int(row[0]["count"]) if row else 0

    def corrections(self) -> list[dict[str, Any]]:
        return self._rows("SELECT heard, meant, count, disabled, last_seen FROM corrections "
                          "ORDER BY count DESC, last_seen DESC")

    def set_correction_disabled(self, heard: str, meant: str, disabled: bool) -> None:
        self._exec("UPDATE corrections SET disabled=? WHERE heard=? AND meant=?",
                   (int(disabled), heard, meant))

    def delete_correction(self, heard: str, meant: str) -> None:
        self._exec("DELETE FROM corrections WHERE heard=? AND meant=?", (heard, meant))

    def bump_vocab(self, terms: list[str]) -> None:
        now = time.time()
        with self._lock:
            self._conn.executemany(
                "INSERT INTO vocab(term, last_seen) VALUES (?,?) "
                "ON CONFLICT(term) DO UPDATE SET count=count+1, last_seen=excluded.last_seen",
                [(t, now) for t in terms])

    def top_vocab(self, min_count: int = 3, limit: int = 40) -> list[str]:
        rows = self._rows("SELECT term FROM vocab WHERE count>=? ORDER BY count DESC, last_seen DESC LIMIT ?",
                          (min_count, limit))
        return [r["term"] for r in rows]

    def delete_vocab(self, term: str) -> None:
        self._exec("DELETE FROM vocab WHERE term=?", (term,))

    def add_voice_sample(self, path: str, heard: str, final_text: str) -> None:
        self._exec("INSERT INTO voice_samples(id, path, heard, final_text, created_at) VALUES (?,?,?,?,?)",
                   (str(uuid.uuid4()), path, heard, final_text, time.time()))

    def voice_sample_count(self) -> int:
        return int(self._rows("SELECT COUNT(*) AS n FROM voice_samples")[0]["n"])

    # ------------------------------------------------------------ background tasks
    MAX_RESULT_CHARS = 20_000

    def save_task(self, t: Any) -> None:
        """Insert or update a tasks.TaskRecord (the pending prompt is not persisted)."""
        self._exec(
            "INSERT INTO tasks (id, workspace, engine, title, instruction, lanes_json, status, created_at,"
            " started_at, finished_at, summary, result_text, error, cost_usd, cli_session_id, turns)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET title=excluded.title, instruction=excluded.instruction,"
            " lanes_json=excluded.lanes_json, status=excluded.status, started_at=excluded.started_at,"
            " finished_at=excluded.finished_at, summary=excluded.summary, result_text=excluded.result_text,"
            " error=excluded.error, cost_usd=excluded.cost_usd, cli_session_id=excluded.cli_session_id,"
            " turns=excluded.turns",
            (t.id, t.workspace, t.engine, t.title, t.instruction, json.dumps(list(t.lanes)), t.status,
             t.created_at, t.started_at, t.finished_at, t.summary, t.result_text[: self.MAX_RESULT_CHARS],
             t.error, t.cost_usd, t.cli_session_id, t.turns))

    def list_tasks(self, limit: int = 100) -> list[Any]:
        from .tasks import TaskRecord
        rows = self._rows("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            lanes = tuple(json.loads(r.pop("lanes_json") or "[]"))
            out.append(TaskRecord(lanes=lanes, **r))
        return out

    def interrupt_active_tasks(self) -> int:
        cur = self._exec("UPDATE tasks SET status='interrupted', finished_at=COALESCE(finished_at, ?)"
                         " WHERE status IN ('queued','running','needs_approval')", (time.time(),))
        return cur.rowcount

    def delete_tasks(self, ids: list[str]) -> None:
        for tid in ids:
            self._exec("DELETE FROM tasks WHERE id = ?", (tid,))
