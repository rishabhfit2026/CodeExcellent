"""Blends TaskAnalysis + RepoContext into one DifficultyScore using
configurable weights (never a blind average -- risk and scope are allowed to
push the score up even when other dimensions are low, since a small-looking
change to a risky area is not actually a small task).

This produces the heuristic estimate and a default mode/confidence as a
self-contained fallback. `AdaptiveDifficultyEstimator` (analyzer/adaptive_estimator.py)
wraps this with historical calibration; `StrategySelector`
(analyzer/strategy_selector.py) is the authoritative source for execution
mode once confidence and risk are known -- the `mode` field here is a
reasonable default for callers that skip that stage.
"""
from __future__ import annotations

from codeexcellent.analyzer import risk_classifier
from codeexcellent.core.models import (
    DifficultyScore,
    ExecutionMode,
    RepoContext,
    TaskAnalysis,
)


def band_for(value: float, bands: dict[str, list[float]]) -> str:
    for name, (lo, hi) in bands.items():
        if lo <= value < hi or (value == 10 and hi == 10):
            return name
    return "very_hard"


def _mode_for(difficulty: float, thresholds: dict) -> ExecutionMode:
    if difficulty < thresholds.get("none_below", 3):
        return ExecutionMode.DIRECT
    if difficulty < thresholds.get("lightweight_below", 6):
        return ExecutionMode.LIGHTWEIGHT
    return ExecutionMode.FULL


def _confidence(task: TaskAnalysis, repo: RepoContext) -> float:
    confidence = 0.85
    if task.ambiguity >= 5.0:
        confidence -= 0.25
    if not task.keywords_matched:
        confidence -= 0.10
    if repo.file_count == 0:
        confidence -= 0.10
    return round(max(0.3, min(0.95, confidence)), 2)


def _reasons(task: TaskAnalysis, repo: RepoContext, estimated_scope: str, risk_level) -> list[str]:
    from codeexcellent.core.models import RiskLevel

    reasons = []
    if risk_level == RiskLevel.CRITICAL:
        reasons.append("risk is CRITICAL (money movement or destructive-in-production signals detected)")
    elif risk_level == RiskLevel.HIGH:
        reasons.append("high-risk keywords detected in the request")
    elif risk_level == RiskLevel.MEDIUM:
        reasons.append("moderate risk signals in the request")
    if repo.repo_complexity >= 5.0:
        reasons.append(f"repository complexity is elevated ({repo.repo_complexity}/10)")
    if task.architecture_signal >= 5.0:
        reasons.append("request touches an architecture-sensitive area")
    if estimated_scope == "large":
        reasons.append("estimated change scope is large")
    if task.ambiguity >= 5.0:
        reasons.append("request wording is ambiguous, which adds execution risk")
    if task.operation_count > 2:
        reasons.append(f"request bundles {task.operation_count} distinct operations")
    if not reasons:
        reasons.append("no elevated risk or complexity signals found")
    return reasons


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

    band = band_for(blended, config.get("difficulty_bands", {}))
    risk_level = risk_classifier.classify_risk(task)
    mode = _mode_for(blended, config.get("planning_thresholds", {}))

    planning_required = mode != ExecutionMode.DIRECT
    testing_required = task.testing_signal >= 3.0 or bool(repo.test_dirs) and blended >= 3.0

    if task.scope >= 7 or repo.repo_complexity >= 7:
        estimated_scope = "large"
    elif task.scope >= 3.5 or repo.repo_complexity >= 3.5:
        estimated_scope = "medium"
    else:
        estimated_scope = "small"

    quality_level = risk_classifier.classify_quality_level(risk_level, estimated_scope, testing_required)

    return DifficultyScore(
        value=blended,
        band=band,
        risk_level=risk_level,
        dimensions=dims,
        planning_required=planning_required,
        testing_required=testing_required,
        mode=mode,
        estimated_scope=estimated_scope,
        quality_level=quality_level,
        confidence=_confidence(task, repo),
        reasons=_reasons(task, repo, estimated_scope, risk_level),
        basis="heuristic",
        historical_sample_size=0,
    )
