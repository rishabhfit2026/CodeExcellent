"""TaskMemory: a SQLite history of past executions, stored per-project at
<root>/.codeexcellent/history.db. Beyond supporting `codeexcellent history`,
this is now the training data for AdaptiveDifficultyEstimator and
ResourceForecaster (section 22) -- it stores the task fingerprint, the
prediction, and what actually happened, so future predictions can be
calibrated against reality (section 6).

Schema changes are applied additively (ALTER TABLE ADD COLUMN) so an
existing history.db from V1 keeps working without a manual migration step.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

_BASE_SCHEMA = """
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

# (column, sql type + default) added since V1, applied via ALTER TABLE if missing.
_ADDITIVE_COLUMNS = [
    ("fingerprint_key", "TEXT"),
    ("fingerprint_category", "TEXT"),
    ("fingerprint_repo_type", "TEXT"),
    ("fingerprint_scope", "TEXT"),
    ("fingerprint_risk", "TEXT"),
    ("confidence", "REAL"),
    ("quality_level", "TEXT"),
    ("outcome_class", "TEXT"),
    ("observed_difficulty", "REAL"),
    ("difficulty_error", "REAL"),
    ("forecast_calls", "INTEGER"),
    ("forecast_basis", "TEXT"),
]


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
    fingerprint_key: str = ""
    fingerprint_category: str = ""
    fingerprint_repo_type: str = ""
    fingerprint_scope: str = ""
    fingerprint_risk: str = ""
    confidence: float = 0.5
    quality_level: str = "standard"
    outcome_class: str = "success"
    observed_difficulty: float | None = None
    difficulty_error: float | None = None
    forecast_calls: int | None = None
    forecast_basis: str | None = None


def db_path(project_root: str) -> Path:
    path = Path(project_root) / ".codeexcellent"
    path.mkdir(exist_ok=True)
    return path / "history.db"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_BASE_SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for column, sql_type in _ADDITIVE_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {sql_type}")


def record(project_root: str, task: TaskRecord) -> None:
    path = db_path(project_root)
    with closing(sqlite3.connect(path)) as conn:
        _ensure_schema(conn)
        conn.execute(
            """INSERT INTO tasks (
                created_at, request, predicted_difficulty, band, mode, status,
                cost_usd, duration_ms, claude_calls, retries, files_changed, quality_score,
                fingerprint_key, fingerprint_category, fingerprint_repo_type,
                fingerprint_scope, fingerprint_risk, confidence, quality_level,
                outcome_class, observed_difficulty, difficulty_error, forecast_calls, forecast_basis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.created_at, task.request, task.predicted_difficulty, task.band,
                task.mode, task.status, task.cost_usd, task.duration_ms, task.claude_calls,
                task.retries, task.files_changed, task.quality_score,
                task.fingerprint_key, task.fingerprint_category, task.fingerprint_repo_type,
                task.fingerprint_scope, task.fingerprint_risk, task.confidence, task.quality_level,
                task.outcome_class, task.observed_difficulty, task.difficulty_error,
                task.forecast_calls, task.forecast_basis,
            ),
        )
        conn.commit()


def recent(project_root: str, limit: int = 20) -> list[sqlite3.Row]:
    path = db_path(project_root)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        cursor = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cursor.fetchall()


_TRAINABLE_OUTCOMES = ("success", "task_difficulty_failure", "ambiguous_requirement")


def similar(project_root: str, fingerprint_key: str, limit: int = 50) -> list[sqlite3.Row]:
    """Past runs with an exact fingerprint match, restricted to outcomes that
    are actually informative about difficulty (section 24) -- infra and
    external-dependency failures are excluded.
    """
    path = db_path(project_root)
    if not path.exists():
        return []
    placeholders = ",".join("?" for _ in _TRAINABLE_OUTCOMES)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        cursor = conn.execute(
            f"""SELECT * FROM tasks
                WHERE fingerprint_key = ? AND outcome_class IN ({placeholders})
                  AND observed_difficulty IS NOT NULL
                ORDER BY id DESC LIMIT ?""",
            (fingerprint_key, *_TRAINABLE_OUTCOMES, limit),
        )
        return cursor.fetchall()
