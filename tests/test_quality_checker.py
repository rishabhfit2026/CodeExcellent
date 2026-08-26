from codeexcellent.core.models import ClaudeCallResult, DifficultyScore, ExecutionMode, RiskLevel, SuiteRunResult
from codeexcellent.quality import quality_checker

DIFFICULTY = DifficultyScore(
    value=3.0, band="easy", risk_level=RiskLevel.LOW, dimensions={},
    planning_required=False, testing_required=True, mode=ExecutionMode.DIRECT,
    estimated_scope="small",
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
