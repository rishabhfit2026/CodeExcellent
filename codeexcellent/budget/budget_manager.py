"""Maps a difficulty band to an execution budget. This is deliberately NOT a
token bank -- it configures the real levers the installed Claude CLI exposes
(`--effort`, `--max-budget-usd`) plus orchestrator-side limits (call count,
retries, timeout) that bound resource usage even where the CLI has no direct
knob for it.
"""
from __future__ import annotations

from codeexcellent.core.models import Budget, DifficultyScore


def allocate(difficulty: DifficultyScore, config: dict) -> Budget:
    budgets = config.get("budgets", {})
    spec = budgets.get(difficulty.band, budgets.get("medium", {}))

    max_claude_calls = int(spec.get("max_claude_calls", 3))
    max_retries = int(spec.get("max_retries", 1))
    # A retry can only happen if there's a call slot left for it -- otherwise
    # the call-count cap silently swallows the retry path and the run gets
    # mislabeled INCOMPLETE instead of FAILED/COMPLETE on the last attempt.
    max_retries = min(max_retries, max(0, max_claude_calls - 1))

    return Budget(
        band=difficulty.band,
        effort=spec.get("effort", "medium"),
        max_budget_usd=float(spec.get("max_budget_usd", 3.0)),
        max_budget_usd_step=float(spec.get("max_budget_usd_step", spec.get("max_budget_usd", 3.0))),
        max_claude_calls=max_claude_calls,
        max_retries=max_retries,
        timeout_seconds=int(spec.get("timeout_seconds", 300)),
    )


def escalate(budget: Budget, config: dict) -> Budget:
    """Progressive allocation (section 13): step up to the next budget band
    rather than handing out the maximum from the start. Used by the retry
    loop when the current allocation proves insufficient.
    """
    order = ["trivial", "easy", "medium", "hard", "very_hard"]
    if budget.band not in order:
        return budget
    idx = order.index(budget.band)
    if idx + 1 >= len(order):
        # Already at the top band -- just add one more step of USD budget.
        return Budget(
            band=budget.band,
            effort=budget.effort,
            max_budget_usd=budget.max_budget_usd + budget.max_budget_usd_step,
            max_budget_usd_step=budget.max_budget_usd_step,
            max_claude_calls=budget.max_claude_calls,
            max_retries=budget.max_retries,
            timeout_seconds=budget.timeout_seconds,
        )

    next_band = order[idx + 1]
    budgets = config.get("budgets", {})
    spec = budgets.get(next_band, {})
    return Budget(
        band=next_band,
        effort=spec.get("effort", budget.effort),
        max_budget_usd=float(spec.get("max_budget_usd", budget.max_budget_usd)),
        max_budget_usd_step=float(spec.get("max_budget_usd_step", budget.max_budget_usd_step)),
        max_claude_calls=budget.max_claude_calls,
        max_retries=budget.max_retries,
        timeout_seconds=max(budget.timeout_seconds, int(spec.get("timeout_seconds", budget.timeout_seconds))),
    )
