from codeexcellent.core import memory


def _record(root, outcome_class, observed_difficulty, fingerprint_key="fp"):
    memory.record(
        root,
        memory.TaskRecord(
            created_at="2026-01-01T00:00:00", request="t", predicted_difficulty=2.0, band="easy",
            mode="direct", status="COMPLETE", cost_usd=0.05, duration_ms=1000, claude_calls=1,
            retries=0, files_changed=1, quality_score=9.0, fingerprint_key=fingerprint_key,
            fingerprint_category="small_change", fingerprint_repo_type="python", fingerprint_scope="small",
            fingerprint_risk="low", confidence=0.8, quality_level="basic", outcome_class=outcome_class,
            observed_difficulty=observed_difficulty, difficulty_error=0.0,
        ),
    )


def test_similar_excludes_infra_failures(tmp_path):
    _record(str(tmp_path), "success", 3.0)
    _record(str(tmp_path), "infra_failure", 9.0)  # should never show up as training signal

    rows = memory.similar(str(tmp_path), "fp")
    assert len(rows) == 1
    assert rows[0]["outcome_class"] == "success"


def test_similar_only_matches_exact_fingerprint(tmp_path):
    _record(str(tmp_path), "success", 3.0, fingerprint_key="fp-a")
    _record(str(tmp_path), "success", 5.0, fingerprint_key="fp-b")

    rows = memory.similar(str(tmp_path), "fp-a")
    assert len(rows) == 1
    assert rows[0]["fingerprint_key"] == "fp-a"


def test_legacy_schema_gets_new_columns_added(tmp_path):
    import sqlite3
    from contextlib import closing

    path = memory.db_path(str(tmp_path))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(memory._BASE_SCHEMA)
        conn.execute(
            "INSERT INTO tasks (created_at, request, predicted_difficulty, band, mode, status, "
            "cost_usd, duration_ms, claude_calls, retries, files_changed, quality_score) "
            "VALUES ('2026-01-01', 'old row', 2.0, 'easy', 'direct', 'COMPLETE', 0.01, 100, 1, 0, 1, 9.0)"
        )
        conn.commit()

    rows = memory.recent(str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["request"] == "old row"
    assert rows[0]["fingerprint_key"] is None  # additive column, no backfill for pre-existing rows
