"""Classifies why a run ended the way it did, and derives an "observed
difficulty" from what actually happened. Both feed the adaptive estimator --
but only for outcomes that are actually informative about task difficulty
(section 24). A Claude CLI crash says nothing about whether the task was
hard; a task that burned every retry on failing tests does.
"""
from __future__ import annotations

from codeexcellent.core.models import (
    Budget,
    ExecutionAttempt,
    OutcomeClass,
    QualityResult,
    TaskAnalysis,
)

_INFRA_ERROR_PATTERNS = (
    "not installed", "timed out", "exited", "could not parse",
    "is not available", "not found on path",
)


def classify(
    status: str,
    attempts: list[ExecutionAttempt],
    task: TaskAnalysis,
) -> OutcomeClass:
    if status == "COMPLETE":
        return OutcomeClass.SUCCESS

    if status in ("BLOCKED", "CANCELLED"):
        return OutcomeClass.INFRA_FAILURE

    last = attempts[-1] if attempts else None
    if last and not last.call.success and last.call.error:
        error_lower = last.call.error.lower()
        if any(pattern in error_lower for pattern in _INFRA_ERROR_PATTERNS):
            return OutcomeClass.INFRA_FAILURE

    if last and last.tests.ran and not last.tests.success and "not available" in last.tests.output_tail.lower():
        return OutcomeClass.EXTERNAL_DEPENDENCY_FAILURE

    if task.ambiguity >= 6.0 and status == "INCOMPLETE":
        return OutcomeClass.AMBIGUOUS_REQUIREMENT

    return OutcomeClass.TASK_DIFFICULTY_FAILURE


def observed_difficulty(
    outcome: OutcomeClass,
    attempts: list[ExecutionAttempt],
    budget: Budget,
    final_quality: QualityResult | None,
) -> float | None:
    """A 0-10 heuristic reconstruction of how hard the task actually turned
    out to be, from measurable signals only (calls consumed, quality
    shortfall, time taken). Not comparable in kind to Claude's own token
    accounting -- this is a proxy for calibrating our own predictions.
    """
    if outcome in (OutcomeClass.INFRA_FAILURE, OutcomeClass.EXTERNAL_DEPENDENCY_FAILURE):
        return None
    if not attempts:
        return None

    # Extra calls beyond the first are the actual difficulty signal -- a
    # single successful call always has calls_used == max_claude_calls for
    # the trivial/easy bands (their ceiling *is* 1), so ratio-to-budget would
    # score every clean trivial success as "used 100% of budget" == hard.
    # A fixed retry scale avoids that self-referential distortion.
    extra_calls = len(attempts) - 1
    calls_component = min(1.0, extra_calls / 4.0)

    quality_score = final_quality.score if final_quality else 0.0
    quality_deficit = max(0.0, (10.0 - quality_score) / 10.0)
    avg_duration_ms = sum(a.call.duration_ms for a in attempts) / len(attempts)
    duration_ratio = min(1.0, avg_duration_ms / max(1, budget.timeout_seconds * 1000))

    value = calls_component * 4.0 + quality_deficit * 3.5 + duration_ratio * 2.5
    return round(min(10.0, value), 2)
