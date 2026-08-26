from codeexcellent.core import outcome
from codeexcellent.core.models import (
    Budget,
    ClaudeCallResult,
    ExecutionAttempt,
    OutcomeClass,
    QualityResult,
    SuiteRunResult,
    TaskAnalysis,
)

BUDGET = Budget(band="easy", effort="low", max_budget_usd=1.0, max_budget_usd_step=1.0, max_claude_calls=2, max_retries=1, timeout_seconds=200)


def _task(ambiguity=1.0):
    return TaskAnalysis(
        request="x", task_complexity=1, scope=1, risk=1, testing_signal=0,
        architecture_signal=0, ambiguity=ambiguity, operation_count=1,
    )


def _attempt(success=True, error=None, duration_ms=1000, quality_score=9.0):
    call = ClaudeCallResult(
        success=success, result_text="", session_id=None, cost_usd=0.01, input_tokens=1,
        output_tokens=1, duration_ms=duration_ms, num_turns=1, stop_reason="end_turn", error=error,
    )
    tests = SuiteRunResult(ran=False, success=True)
    quality = QualityResult(score=quality_score, complete=True, needs_more_work=False)
    return ExecutionAttempt(call=call, tests=tests, quality=quality, changed_files=["a.py"])


def test_complete_status_is_success():
    assert outcome.classify("COMPLETE", [_attempt()], _task()) == OutcomeClass.SUCCESS


def test_timeout_error_is_infra_failure():
    attempt = _attempt(success=False, error="Claude CLI timed out after 200s")
    assert outcome.classify("INCOMPLETE", [attempt], _task()) == OutcomeClass.INFRA_FAILURE


def test_ambiguous_incomplete_task_is_classified_ambiguous():
    attempt = _attempt(success=True, quality_score=3.0)
    result = outcome.classify("INCOMPLETE", [attempt], _task(ambiguity=8.0))
    assert result == OutcomeClass.AMBIGUOUS_REQUIREMENT


def test_generic_incomplete_is_task_difficulty_failure():
    attempt = _attempt(success=True, quality_score=3.0)
    result = outcome.classify("INCOMPLETE", [attempt], _task(ambiguity=1.0))
    assert result == OutcomeClass.TASK_DIFFICULTY_FAILURE


def test_infra_failure_has_no_observed_difficulty():
    attempt = _attempt(success=False, error="not installed")
    value = outcome.observed_difficulty(OutcomeClass.INFRA_FAILURE, [attempt], BUDGET, None)
    assert value is None


def test_observed_difficulty_increases_with_more_calls_and_lower_quality():
    easy = [_attempt(quality_score=10.0)]
    hard = [_attempt(quality_score=2.0), _attempt(quality_score=2.0)]
    easy_score = outcome.observed_difficulty(OutcomeClass.SUCCESS, easy, BUDGET, QualityResult(score=10.0, complete=True, needs_more_work=False))
    hard_score = outcome.observed_difficulty(OutcomeClass.TASK_DIFFICULTY_FAILURE, hard, BUDGET, QualityResult(score=2.0, complete=False, needs_more_work=True))
    assert hard_score > easy_score


def test_single_call_perfect_success_is_not_scored_as_hard_even_on_a_one_call_budget():
    # Regression: a trivial-band budget's ceiling IS 1 call, so calls_used ==
    # max_claude_calls for every clean success there. Observed difficulty
    # must not treat "used all of a 1-call budget" as "used up all budget".
    trivial_budget = Budget(band="trivial", effort="low", max_budget_usd=0.5, max_budget_usd_step=0.5, max_claude_calls=1, max_retries=0, timeout_seconds=180)
    attempt = _attempt(quality_score=10.0, duration_ms=5000)
    quality = QualityResult(score=10.0, complete=True, needs_more_work=False)
    value = outcome.observed_difficulty(OutcomeClass.SUCCESS, [attempt], trivial_budget, quality)
    assert value < 2.0
