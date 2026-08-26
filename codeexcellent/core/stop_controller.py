"""Decides whether to stop. Remaining budget is never, by itself, a reason to
continue (section 14) -- this module only looks at whether quality
requirements are actually satisfied, and whether continuing is still allowed.
"""
from __future__ import annotations

from dataclasses import dataclass

from codeexcellent.core.models import Budget, QualityResult


@dataclass
class StopDecision:
    stop: bool
    reason: str
    status: str  # COMPLETE / INCOMPLETE / FAILED


def decide(
    quality: QualityResult,
    attempt_number: int,
    budget: Budget,
    cost_so_far: float,
    call_succeeded: bool,
) -> StopDecision:
    if not call_succeeded:
        if attempt_number >= budget.max_retries + 1:
            return StopDecision(stop=True, reason="Claude call failed and retries exhausted", status="FAILED")
        return StopDecision(stop=False, reason="Claude call failed, will retry", status="INCOMPLETE")

    if quality.complete:
        return StopDecision(stop=True, reason="Quality requirements satisfied", status="COMPLETE")

    if attempt_number >= budget.max_retries + 1:
        return StopDecision(stop=True, reason="Max retries reached with quality still unmet", status="INCOMPLETE")

    if cost_so_far >= budget.max_budget_usd:
        return StopDecision(stop=True, reason="Budget exhausted before quality requirements were met", status="INCOMPLETE")

    return StopDecision(stop=False, reason="Quality not yet satisfied, retrying", status="INCOMPLETE")
