from codeexcellent.core.models import Budget, QualityResult
from codeexcellent.core.stop_controller import decide

BUDGET = Budget(band="medium", effort="medium", max_budget_usd=3.0, max_budget_usd_step=1.0, max_claude_calls=3, max_retries=2, timeout_seconds=300)


def test_stops_when_quality_complete():
    quality = QualityResult(score=9.0, complete=True, needs_more_work=False)
    decision = decide(quality, attempt_number=1, budget=BUDGET, cost_so_far=0.5, call_succeeded=True)
    assert decision.stop is True
    assert decision.status == "COMPLETE"


def test_does_not_stop_just_because_budget_remains():
    # Quality incomplete but plenty of budget/retries left -> should continue, not stop.
    quality = QualityResult(score=4.0, complete=False, needs_more_work=True)
    decision = decide(quality, attempt_number=1, budget=BUDGET, cost_so_far=0.5, call_succeeded=True)
    assert decision.stop is False


def test_stops_after_max_retries_even_if_incomplete():
    quality = QualityResult(score=4.0, complete=False, needs_more_work=True)
    decision = decide(quality, attempt_number=3, budget=BUDGET, cost_so_far=0.5, call_succeeded=True)
    assert decision.stop is True
    assert decision.status == "INCOMPLETE"


def test_stops_on_repeated_call_failure():
    quality = QualityResult(score=0.0, complete=False, needs_more_work=True)
    decision = decide(quality, attempt_number=3, budget=BUDGET, cost_so_far=0.0, call_succeeded=False)
    assert decision.stop is True
    assert decision.status == "FAILED"


def test_stops_when_budget_exhausted():
    quality = QualityResult(score=4.0, complete=False, needs_more_work=True)
    decision = decide(quality, attempt_number=1, budget=BUDGET, cost_so_far=3.5, call_succeeded=True)
    assert decision.stop is True
    assert decision.status == "INCOMPLETE"
