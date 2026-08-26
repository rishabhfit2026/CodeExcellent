"""StrategySelector (section 10-11): the authoritative choice of execution
mode, separate from the raw difficulty number. Two things a pure difficulty
threshold can't express on its own:

- Planning is cost-aware (section 11): it's only worth doing when its
  estimated benefit (reduced execution risk from complexity/low confidence/
  architecture impact) exceeds its cost (one extra Claude call + context).
- CRITICAL risk always forces at least a review, independent of how "hard"
  the task numerically looks -- a one-line change to payment logic is still
  CRITICAL even if difficulty comes out at 3/10 (section 18).
"""
from __future__ import annotations

from codeexcellent.core.models import DifficultyScore, ExecutionMode, RiskLevel, TaskAnalysis


def select(task: TaskAnalysis, difficulty: DifficultyScore, config: dict) -> tuple[ExecutionMode, list[str]]:
    thresholds = config.get("planning_thresholds", {})
    strategy_cfg = config.get("strategy", {})
    confidence_cfg = config.get("confidence", {})
    reasons: list[str] = []

    if strategy_cfg.get("critical_forces_review", True) and difficulty.risk_level == RiskLevel.CRITICAL:
        if difficulty.value >= thresholds.get("lightweight_below", 6):
            reasons.append("risk is CRITICAL and difficulty is high -- full plan with mandatory review")
            return ExecutionMode.FULL, reasons
        reasons.append("risk is CRITICAL -- mandatory review even though difficulty/scope is otherwise modest")
        return ExecutionMode.REVIEW_REQUIRED, reasons

    planning_cost = float(strategy_cfg.get("planning_cost", 1.5))
    planning_benefit = 0.0

    if difficulty.value >= thresholds.get("lightweight_below", 6):
        planning_benefit += 4.0

    planning_benefit += task.architecture_signal * 0.3

    low_conf_threshold = float(confidence_cfg.get("low_threshold", 0.5))
    if difficulty.confidence < low_conf_threshold:
        confidence_gap = low_conf_threshold - difficulty.confidence
        planning_benefit += confidence_gap * 6.0
        reasons.append(f"confidence is low ({difficulty.confidence}) -- planning reduces execution risk")

    if difficulty.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        planning_benefit += 3.0

    if planning_benefit <= planning_cost:
        reasons.append(
            f"planning benefit ({planning_benefit:.1f}) does not exceed its cost "
            f"({planning_cost:.1f}) -- executing directly"
        )
        return ExecutionMode.DIRECT, reasons

    full_threshold = thresholds.get("full_at_or_above", thresholds.get("lightweight_below", 6))
    if difficulty.value >= full_threshold:
        reasons.append(
            f"planning benefit ({planning_benefit:.1f}) exceeds its cost and difficulty is high -- full plan"
        )
        return ExecutionMode.FULL, reasons

    reasons.append(
        f"planning benefit ({planning_benefit:.1f}) exceeds its cost ({planning_cost:.1f}) -- lightweight plan"
    )
    return ExecutionMode.LIGHTWEIGHT, reasons
