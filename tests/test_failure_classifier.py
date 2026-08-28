from codeexcellent.config.settings import load_config
from codeexcellent.core import failure_classifier
from codeexcellent.core.failure_classifier import FailureClass
from codeexcellent.core.models import ClaudeCallResult, QualityResult, SuiteRunResult, TaskAnalysis

CONFIG = load_config()


def _task(architecture_signal=0.0, cross_module_signal=0.0) -> TaskAnalysis:
    return TaskAnalysis(
        request="do the thing", task_complexity=5.0, scope=5.0, risk=0.0, testing_signal=0.0,
        architecture_signal=architecture_signal, ambiguity=0.0, operation_count=1,
        cross_module_signal=cross_module_signal,
    )


def _call(success=True, error=None, stop_reason="end_turn"):
    return ClaudeCallResult(
        success=success, result_text="done", session_id="s1", cost_usd=0.01,
        input_tokens=10, output_tokens=10, duration_ms=100, num_turns=1,
        stop_reason=stop_reason, error=error,
    )


def _quality(complete=False, issues=None):
    return QualityResult(score=4.0, complete=complete, needs_more_work=not complete, issues=issues or [])


def test_passing_attempt_is_not_classified():
    result = failure_classifier.classify(
        _task(), _call(), SuiteRunResult(ran=False), ["a.py"], _quality(complete=True), CONFIG,
    )
    assert result == FailureClass.NONE


def test_infra_style_call_error_is_environment_failure():
    call = _call(success=False, error="Claude CLI timed out after 300s")
    result = failure_classifier.classify(_task(), call, SuiteRunResult(ran=False), [], _quality(), CONFIG)
    assert result == FailureClass.ENVIRONMENT_FAILURE
    assert failure_classifier.warrants_budget_escalation(result) is True


def test_unrecognized_call_error_is_ambiguous_not_implementation_gap():
    call = _call(success=False, error="the model declined the request")
    result = failure_classifier.classify(_task(), call, SuiteRunResult(ran=False), [], _quality(), CONFIG)
    assert result == FailureClass.AMBIGUOUS
    assert failure_classifier.warrants_budget_escalation(result) is True


def test_test_suite_crash_is_a_measurement_failure_not_implementation_failure():
    # requirement J: a validator/test crash (ran, failed, but produced no
    # actual pass/fail counts -- e.g. a collection/import error) must not be
    # mistaken for "the implementation is wrong".
    tests = SuiteRunResult(ran=True, success=False, passed=0, failed=0, output_tail="ImportError: conftest.py")
    result = failure_classifier.classify(_task(), _call(), tests, ["a.py"], _quality(), CONFIG)
    assert result == FailureClass.VALIDATOR_MEASUREMENT_FAILURE
    # A measurement failure isn't evidence more budget/effort will help --
    # it's evidence the test command itself needs fixing.
    assert failure_classifier.warrants_budget_escalation(result) is False


def test_genuine_test_failures_are_classified_distinctly():
    tests = SuiteRunResult(ran=True, success=False, passed=3, failed=2, output_tail="FAILED test_x")
    result = failure_classifier.classify(_task(), _call(), tests, ["a.py"], _quality(), CONFIG)
    assert result == FailureClass.TEST_FAILURE
    assert failure_classifier.warrants_budget_escalation(result) is False


def test_high_architecture_signal_with_one_file_changed_is_structural_incomplete():
    task = _task(architecture_signal=8.0)
    result = failure_classifier.classify(task, _call(), SuiteRunResult(ran=False), ["app.py"], _quality(), CONFIG)
    assert result == FailureClass.STRUCTURAL_INCOMPLETE
    assert failure_classifier.warrants_budget_escalation(result) is False


def test_high_cross_module_signal_with_one_file_changed_is_structural_incomplete():
    task = _task(cross_module_signal=8.0)
    result = failure_classifier.classify(task, _call(), SuiteRunResult(ran=False), ["a.py"], _quality(), CONFIG)
    assert result == FailureClass.STRUCTURAL_INCOMPLETE


def test_high_architecture_signal_with_several_files_changed_is_not_structural_incomplete():
    # The signal only means something when suspiciously FEW files changed --
    # several files changing is not evidence of an incomplete structural
    # change.
    task = _task(architecture_signal=8.0)
    result = failure_classifier.classify(
        task, _call(), SuiteRunResult(ran=False), ["a.py", "b.py", "c.py"], _quality(issues=["x"]), CONFIG,
    )
    assert result != FailureClass.STRUCTURAL_INCOMPLETE


def test_generic_quality_issues_fall_back_to_implementation_gap():
    result = failure_classifier.classify(
        _task(), _call(), SuiteRunResult(ran=False), ["a.py"],
        _quality(issues=["Changed 12 files, more than expected"]), CONFIG,
    )
    assert result == FailureClass.IMPLEMENTATION_GAP
    assert failure_classifier.warrants_budget_escalation(result) is False


def test_no_specific_signal_is_ambiguous():
    result = failure_classifier.classify(
        _task(), _call(), SuiteRunResult(ran=False), ["a.py"], _quality(issues=[]), CONFIG,
    )
    assert result == FailureClass.AMBIGUOUS


def test_recovery_instruction_is_targeted_for_structural_incomplete():
    instruction = failure_classifier.recovery_instruction(FailureClass.STRUCTURAL_INCOMPLETE, ["app.py"])
    assert instruction is not None
    assert "structural" in instruction.lower()
    assert "app.py" in instruction


def test_recovery_instruction_is_none_for_implementation_gap():
    # Nothing more specific to say than the generic issues list retry.py
    # already includes.
    assert failure_classifier.recovery_instruction(FailureClass.IMPLEMENTATION_GAP, ["a.py"]) is None
