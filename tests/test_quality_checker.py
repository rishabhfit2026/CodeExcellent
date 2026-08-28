from codeexcellent.config.settings import load_config
from codeexcellent.core.models import (
    ClaudeCallResult, DifficultyScore, ExecutionMode, QualityLevel, RiskLevel, SuiteRunResult, TaskAnalysis,
)
from codeexcellent.quality import quality_checker

CONFIG = load_config()

DIFFICULTY = DifficultyScore(
    value=3.0, band="easy", risk_level=RiskLevel.LOW, dimensions={},
    planning_required=False, testing_required=True, mode=ExecutionMode.DIRECT,
    estimated_scope="small",
)


def _task(architecture_signal=0.0, cross_module_signal=0.0) -> TaskAnalysis:
    return TaskAnalysis(
        request="do the thing", task_complexity=5.0, scope=5.0, risk=0.0, testing_signal=0.0,
        architecture_signal=architecture_signal, ambiguity=0.0, operation_count=1,
        cross_module_signal=cross_module_signal,
    )


def _call(success=True, stop_reason="end_turn"):
    return ClaudeCallResult(
        success=success, result_text="done", session_id="s1", cost_usd=0.01,
        input_tokens=10, output_tokens=10, duration_ms=100, num_turns=1,
        stop_reason=stop_reason, error=None if success else "boom",
    )


def test_no_changed_files_scores_low():
    result = quality_checker.heuristic_check(DIFFICULTY, _call(), SuiteRunResult(ran=False), [], min_pass_score=7.0)
    assert result.needs_more_work is True
    assert "No files were changed" in result.issues[0]


def test_passing_tests_and_reasonable_scope_completes():
    tests = SuiteRunResult(ran=True, passed=5, failed=0, success=True)
    result = quality_checker.heuristic_check(DIFFICULTY, _call(), tests, ["app.py"], min_pass_score=7.0)
    assert result.complete is True


def test_failing_tests_prevent_completion():
    tests = SuiteRunResult(ran=True, passed=3, failed=2, success=False)
    result = quality_checker.heuristic_check(DIFFICULTY, _call(), tests, ["app.py"], min_pass_score=7.0)
    assert result.complete is False
    assert any("failing" in i for i in result.issues)


def test_failed_call_scores_zero():
    result = quality_checker.heuristic_check(DIFFICULTY, _call(success=False), SuiteRunResult(ran=False), [], min_pass_score=7.0)
    assert result.score == 0.0
    assert result.complete is False


def test_review_response_parsing():
    call = ClaudeCallResult(
        success=True, result_text='{"score": 8, "complete": true, "issues": []}',
        session_id=None, cost_usd=0.0, input_tokens=0, output_tokens=0,
        duration_ms=0, num_turns=1, stop_reason="end_turn",
    )
    parsed = quality_checker.parse_review_response(call, min_pass_score=7.0)
    assert parsed is not None
    assert parsed.complete is True


def test_review_response_parsing_handles_malformed_json():
    call = ClaudeCallResult(
        success=True, result_text="not json", session_id=None, cost_usd=0.0,
        input_tokens=0, output_tokens=0, duration_ms=0, num_turns=1, stop_reason="end_turn",
    )
    assert quality_checker.parse_review_response(call, min_pass_score=7.0) is None


# --- review_required: mode-driven, not an independent difficulty threshold ---
# (added after a live A/B benchmark found the old difficulty>=6 trigger
# stacked an unrequested review call on top of whatever strategy_selector
# had already decided)

def test_direct_mode_does_not_require_review_even_at_high_difficulty():
    difficulty = DifficultyScore(
        value=8.0, band="very_hard", risk_level=RiskLevel.LOW, dimensions={},
        planning_required=False, testing_required=False, mode=ExecutionMode.DIRECT,
        estimated_scope="small", quality_level=QualityLevel.BASIC,
    )
    assert quality_checker.review_required(difficulty, ExecutionMode.DIRECT, CONFIG) is False


def test_lightweight_mode_does_not_require_review():
    difficulty = DifficultyScore(
        value=7.0, band="hard", risk_level=RiskLevel.LOW, dimensions={},
        planning_required=True, testing_required=False, mode=ExecutionMode.LIGHTWEIGHT,
        estimated_scope="medium", quality_level=QualityLevel.BASIC,
    )
    assert quality_checker.review_required(difficulty, ExecutionMode.LIGHTWEIGHT, CONFIG) is False


def test_full_mode_requires_review():
    difficulty = DifficultyScore(
        value=7.5, band="hard", risk_level=RiskLevel.LOW, dimensions={},
        planning_required=True, testing_required=False, mode=ExecutionMode.FULL,
        estimated_scope="medium", quality_level=QualityLevel.STANDARD,
    )
    assert quality_checker.review_required(difficulty, ExecutionMode.FULL, CONFIG) is True


def test_mandatory_review_level_requires_review_even_in_lightweight_mode():
    # A CRITICAL-quality-level task must still be reviewed even if the
    # execution strategy itself is cheap -- this is a risk-driven
    # requirement, independent of (and not overridden by) the mode decision.
    difficulty = DifficultyScore(
        value=3.0, band="easy", risk_level=RiskLevel.CRITICAL, dimensions={},
        planning_required=False, testing_required=False, mode=ExecutionMode.LIGHTWEIGHT,
        estimated_scope="small", quality_level=QualityLevel.CRITICAL,
    )
    assert quality_checker.review_required(difficulty, ExecutionMode.LIGHTWEIGHT, CONFIG) is True


# --- structural-completeness heuristic in heuristic_check ---

def test_structural_signal_with_one_file_changed_flags_possible_incompleteness():
    task = _task(cross_module_signal=8.0)
    result = quality_checker.heuristic_check(
        DIFFICULTY, _call(), SuiteRunResult(ran=False), ["a.py"], min_pass_score=7.0, task=task, config=CONFIG,
    )
    assert result.complete is False
    assert any("structural" in i.lower() or "multi-module" in i.lower() for i in result.issues)


def test_structural_signal_with_several_files_changed_is_not_flagged():
    task = _task(cross_module_signal=8.0)
    result = quality_checker.heuristic_check(
        DIFFICULTY, _call(), SuiteRunResult(ran=True, success=True), ["a.py", "b.py", "c.py"],
        min_pass_score=7.0, task=task, config=CONFIG,
    )
    assert not any("structural" in i.lower() for i in result.issues)


def test_low_structural_signal_is_never_flagged():
    task = _task(cross_module_signal=0.0, architecture_signal=0.0)
    result = quality_checker.heuristic_check(
        DIFFICULTY, _call(), SuiteRunResult(ran=False), ["a.py"], min_pass_score=7.0, task=task, config=CONFIG,
    )
    assert not any("structural" in i.lower() for i in result.issues)


def test_heuristic_check_without_task_is_unaffected_backward_compatible():
    # Existing callers that don't pass task/config (none remain in this repo,
    # but the signature must stay backward compatible) get the same
    # behavior as before this change.
    result = quality_checker.heuristic_check(DIFFICULTY, _call(), SuiteRunResult(ran=True, success=True), ["a.py"], min_pass_score=7.0)
    assert result.complete is True
