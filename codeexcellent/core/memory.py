"""TaskMemory: a small SQLite history of past executions, stored per-project
at <root>/.codeexcellent/history.db. V1 keeps one flat table -- enough to
support `codeexcellent history` and to compare predicted vs actual difficulty
later (section 24/25). A normalized schema (separate executions/quality/file
tables) is a natural extension once the learning phase actually needs it;
building it now would be speculative.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    request TEXT NOT NULL,
    predicted_difficulty REAL,
    band TEXT,
    mode TEXT,
    status TEXT,
    cost_usd REAL,
    duration_ms INTEGER,
    claude_calls INTEGER,
    retries INTEGER,
    files_changed INTEGER,
    quality_score REAL
);
"""


@dataclass
class TaskRecord:
    created_at: str
    request: str
    predicted_difficulty: float
    band: str
    mode: str
    status: str
    cost_usd: float
    duration_ms: int
    claude_calls: int
    retries: int
    files_changed: int
    quality_score: float | None


def db_path(project_root: str) -> Path:
    path = Path(project_root) / ".codeexcellent"
    path.mkdir(exist_ok=True)
    return path / "history.db"


def record(project_root: str, task: TaskRecord) -> None:
    path = db_path(project_root)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(_SCHEMA)
        conn.execute(
            """INSERT INTO tasks (
                created_at, request, predicted_difficulty, band, mode, status,
                cost_usd, duration_ms, claude_calls, retries, files_changed, quality_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.created_at, task.request, task.predicted_difficulty, task.band,
                task.mode, task.status, task.cost_usd, task.duration_ms, task.claude_calls,
                task.retries, task.files_changed, task.quality_score,
            ),
        )
        conn.commit()


def recent(project_root: str, limit: int = 20) -> list[sqlite3.Row]:
    path = db_path(project_root)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        cursor = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cursor.fetchall()
