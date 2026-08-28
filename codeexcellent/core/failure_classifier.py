"""Validation-driven recovery: classifies *why* an attempt didn't reach
'complete' so the next retry can be targeted at the actual problem instead
of re-running the same generic prompt (or unconditionally paying for more
process). Added after a live A/B benchmark showed that escalating strategy
on difficulty alone increased cost 3-13x without improving validated
correctness -- the missing piece wasn't more planning, it was diagnosis.

Every signal used here is already computed elsewhere for general reasons
(task_analyzer's architecture/cross-module signals, the heuristic quality
check, the test runner, the CLI call's own success/error) -- nothing in
this module references a specific task, benchmark id, or filename. It
classifies a *class* of failure, not "did benchmark task X pass."
"""
from __future__ import annotations

from enum import Enum

from codeexcellent.core.models import ClaudeCallResult, QualityResult, SuiteRunResult, TaskAnalysis

_ENVIRONMENT_ERROR_PATTERNS = (
    "not installed", "timed out", "exited", "could not parse",
    "is not available", "not found on path",
)


class FailureClass(str, Enum):
    NONE = "none"  # attempt actually passed -- no recovery needed
    ENVIRONMENT_FAILURE = "environment_failure"  # CLI/tooling problem, not a code problem
    VALIDATOR_MEASUREMENT_FAILURE = "validator_measurement_failure"  # the test run itself produced no signal
    TEST_FAILURE = "test_failure"  # tests ran and genuinely failed
    STRUCTURAL_INCOMPLETE = "structural_incomplete"  # an architecture/multi-module change was implied but barely anything changed
    IMPLEMENTATION_GAP = "implementation_gap"  # heuristic quality issues (scope, no changes) without a more specific class
    AMBIGUOUS = "ambiguous"  # no specific signal available -- generic retry, and the one class that still warrants more budget


def classify(
    task: TaskAnalysis,
    call: ClaudeCallResult,
    tests: SuiteRunResult,
    changed_files: list[str],
    quality: QualityResult,
    config: dict,
) -> FailureClass:
    if quality.complete:
        return FailureClass.NONE

    if not call.success:
        error_lower = (call.error or "").lower()
        if any(pattern in error_lower for pattern in _ENVIRONMENT_ERROR_PATTERNS):
            return FailureClass.ENVIRONMENT_FAILURE
        # The call itself reported failure for a reason that isn't a known
        # infra pattern -- genuinely unclear why, not evidence of any
        # specific code defect. Treated like AMBIGUOUS (warrants a bigger
        # budget on retry), not like a diagnosable implementation issue.
        return FailureClass.AMBIGUOUS

    if tests.ran and not tests.success:
        # A failed test run with no pass/fail counts at all usually means
        # the test command itself errored (import/collection error) rather
        # than that real assertions failed -- a measurement problem, not
        # proof the implementation is wrong (requirement J).
        if tests.passed == 0 and tests.failed == 0:
            return FailureClass.VALIDATOR_MEASUREMENT_FAILURE
        return FailureClass.TEST_FAILURE

    recovery_cfg = config.get("recovery", {})
    structural_signal = max(task.architecture_signal, task.cross_module_signal)
    signal_threshold = float(recovery_cfg.get("structural_incomplete_signal_at_or_above", 6.0))
    max_files = int(recovery_cfg.get("structural_incomplete_max_files_changed", 1))
    if structural_signal >= signal_threshold and len(changed_files) <= max_files:
        # e.g. "migrate into a package with separate modules" (high
        # architecture_signal) but only one file changed -- the requested
        # restructuring most likely didn't happen. General pattern-matching
        # on the task's own signals, not on what the task actually is.
        return FailureClass.STRUCTURAL_INCOMPLETE

    if quality.issues:
        return FailureClass.IMPLEMENTATION_GAP

    return FailureClass.AMBIGUOUS


def warrants_budget_escalation(failure_class: FailureClass) -> bool:
    """Whether the next attempt should get a bigger budget/effort band, vs.
    a targeted prompt at the current budget. Escalating unconditionally on
    every retry (the old behavior) spends more without evidence that
    resources were the actual bottleneck -- only a genuine
    tooling/timeout failure, or a failure we couldn't diagnose at all,
    justifies it. A diagnosed, targetable gap (structural, test, or
    implementation) should be fixable with a better-targeted prompt at the
    *same* budget -- that's the entire point of recovery being cheaper than
    blanket escalation.
    """
    return failure_class in (FailureClass.ENVIRONMENT_FAILURE, FailureClass.AMBIGUOUS)


def recovery_instruction(failure_class: FailureClass, changed_files: list[str]) -> str | None:
    """A short, targeted correction instruction for the retry prompt. None
    for classes with nothing more specific to add than the generic issues
    list retry.py already includes.
    """
    if failure_class == FailureClass.STRUCTURAL_INCOMPLETE:
        touched = ", ".join(changed_files) if changed_files else "none"
        return (
            f"Only {len(changed_files)} file(s) changed so far ({touched}), but this task implies a "
            "structural or multi-module change (new files/modules, or coordinated changes across the files "
            "it names). Check whether that restructuring actually happened, and complete it -- do not stop "
            "after confirming existing behavior still works if the structural change itself hasn't been made."
        )
    if failure_class == FailureClass.VALIDATOR_MEASUREMENT_FAILURE:
        return (
            "The test run did not produce a clear pass/fail result (likely a collection or import error in "
            "the test command itself). Check that the test suite runs at all in this project before assuming "
            "the implementation is wrong, and fix whatever is preventing it from running."
        )
    if failure_class == FailureClass.ENVIRONMENT_FAILURE:
        return "The previous attempt failed for a tooling/environment reason, not a code reason. Retry the same change."
    return None
