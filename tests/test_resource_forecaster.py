from codeexcellent.budget import resource_forecaster
from codeexcellent.config.settings import load_config
from codeexcellent.core import memory
from codeexcellent.core.models import Budget

CONFIG = load_config()
BUDGET = Budget(band="easy", effort="low", max_budget_usd=1.0, max_budget_usd_step=1.0, max_claude_calls=2, max_retries=1, timeout_seconds=200)


def test_no_history_uses_heuristic_basis(tmp_path):
    result = resource_forecaster.forecast("fp-key", str(tmp_path), BUDGET, context_chars=500, config=CONFIG)
    assert result.basis == "heuristic"
    assert result.expected_calls == 1


def test_sufficient_history_uses_historical_average(tmp_path):
    for calls in (1, 1, 3):
        memory.record(
            str(tmp_path),
            memory.TaskRecord(
                created_at="2026-01-01T00:00:00", request="t", predicted_difficulty=2.0, band="easy",
                mode="direct", status="COMPLETE", cost_usd=0.05, duration_ms=2000, claude_calls=calls,
                retries=calls - 1, files_changed=1, quality_score=9.0, fingerprint_key="fp-key",
                fingerprint_category="small_change", fingerprint_repo_type="python", fingerprint_scope="small",
                fingerprint_risk="low", confidence=0.8, quality_level="basic", outcome_class="success",
                observed_difficulty=3.0, difficulty_error=0.0,
            ),
        )

    result = resource_forecaster.forecast("fp-key", str(tmp_path), BUDGET, context_chars=500, config=CONFIG)
    assert result.basis == "historical"
    assert result.expected_calls == round((1 + 1 + 3) / 3)
    assert result.sample_size == 3
