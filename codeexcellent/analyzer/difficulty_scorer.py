"""Blends TaskAnalysis + RepoContext into one DifficultyScore using
configurable weights (never a blind average -- risk and scope are allowed to
push the score up even when other dimensions are low, since a small-looking
change to a risky area is not actually a small task).
"""
from __future__ import annotations

from codeexcellent.core.models import (
    DifficultyScore,
    ExecutionMode,
    RepoContext,
    RiskLevel,
    TaskAnalysis,
)


def _band_for(value: float, bands: dict[str, list[float]]) -> str:
    for name, (lo, hi) in bands.items():
        if lo <= value < hi or (value == 10 and hi == 10):
            return name
    return "very_hard"


def _risk_level(value: float) -> RiskLevel:
    if value >= 6.5:
        return RiskLevel.HIGH
    if value >= 3.0:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _mode_for(difficulty: float, thresholds: dict) -> ExecutionMode:
    if difficulty < thresholds.get("none_below", 3):
        return ExecutionMode.DIRECT
    if difficulty < thresholds.get("lightweight_below", 6):
        return ExecutionMode.LIGHTWEIGHT
    return ExecutionMode.FULL


def score(task: TaskAnalysis, repo: RepoContext, config: dict) -> DifficultyScore:
    weights = config.get("scoring_weights", {})
    dims = {
        "task_complexity": task.task_complexity,
        "scope": task.scope,
        "risk": task.risk,
        "repo_complexity": repo.repo_complexity,
        "testing_complexity": task.testing_signal,
    }

    weighted = sum(dims[name] * weights.get(name, 0.0) for name in dims)
    total_weight = sum(weights.get(name, 0.0) for name in dims) or 1.0
    blended = weighted / total_weight

    # Risk floor: a high-risk task is never scored as trivially easy, even if
    # other dimensions are low (e.g. "delete the users table" is short but
    # dangerous).
    if task.risk >= 7.0:
        blended = max(blended, 6.0)
    elif task.risk >= 4.0:
        blended = max(blended, 4.0)

    # Ambiguity nudges difficulty up slightly -- an underspecified request
    # carries execution risk even if the described change sounds small.
    blended += task.ambiguity * 0.05

    blended = round(min(10.0, max(0.0, blended)), 2)

    band = _band_for(blended, config.get("difficulty_bands", {}))
    risk_level = _risk_level(task.risk)
    mode = _mode_for(blended, config.get("planning_thresholds", {}))

    planning_required = mode != ExecutionMode.DIRECT
    testing_required = task.testing_signal >= 3.0 or bool(repo.test_dirs) and blended >= 3.0

    if task.scope >= 7 or repo.repo_complexity >= 7:
        estimated_scope = "large"
    elif task.scope >= 3.5 or repo.repo_complexity >= 3.5:
        estimated_scope = "medium"
    else:
        estimated_scope = "small"

    return DifficultyScore(
        value=blended,
        band=band,
        risk_level=risk_level,
        dimensions=dims,
        planning_required=planning_required,
        testing_required=testing_required,
        mode=mode,
        estimated_scope=estimated_scope,
    )
