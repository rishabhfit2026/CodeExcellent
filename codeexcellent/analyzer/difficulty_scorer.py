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

## Difficulty floor (added after a live benchmark audit)

A weighted average can dilute a genuinely strong individual signal with
unrelated near-zero dimensions -- confirmed on real data: an architecture
migration scored task_complexity=10 and scope=10, but repo_complexity was
near-zero (a small repo genuinely has low repo-side complexity) and the
original risk keyword list didn't recognize "migrate" (only "migration"),
so risk=0. The weighted average of [10, 10, 0, ~0, low] came out to ~5.3 --
"medium" -- even though two dimensions independently, unambiguously said
"very high". The risk floor already existing here follows the same
principle for risk; `_complexity_floor` below applies it to complexity too,
using the top-2 average of the complexity-relevant dimensions (not just the
single max) so a task needs at least two dimensions in agreement, not one
noisy spike, to trigger it. Difficulty and risk floors are computed
independently and never combined into one number -- a task can still be
high-difficulty/low-risk or low-difficulty/high-risk.

## Confidence (reworked after the same audit)

Confidence no longer only reflects textual ambiguity -- it now also drops
when the dimensions strongly disagree with each other (high task_complexity
next to near-zero risk is itself evidence of uncertainty about the true
difficulty, not evidence the task is actually easy) and when the floor had
to override the raw blend (the floor firing means the heuristic's first
answer and the strongest individual signal disagreed). This feeds directly
into `strategy_selector`, which now treats low confidence as a hard reason
to avoid `direct` execution rather than a soft nudge.
"""
from __future__ import annotations

import statistics

from codeexcellent.analyzer import risk_classifier
from codeexcellent.core.models import (
    DifficultyScore,
    ExecutionMode,
    RepoContext,
    TaskAnalysis,
)

# Dimensions considered when checking whether complexity is concentrated in
# a small number of strong signals rather than spread evenly (see
# `_complexity_floor`). Kept separate from `risk` on purpose -- section 6 of
# the audit: difficulty and risk are different axes and must stay that way.
_COMPLEXITY_DIMENSION_NAMES = ("task_complexity", "scope", "architecture_signal", "cross_module_signal")


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


def _complexity_floor(complexity_dims: dict[str, float], config: dict) -> tuple[float, bool]:
    """Top-2-average-based floor over the complexity-relevant dimensions.
    Requires at least two dimensions to agree (not a single spike) before
    overriding the blend. Returns (floor_value, whether any tier matched).
    """
    floor_cfg = config.get("difficulty_floor", {})
    tiers = floor_cfg.get("tiers", [])
    if not tiers:
        return 0.0, False

    top2 = sorted(complexity_dims.values(), reverse=True)[:2]
    top2_avg = sum(top2) / len(top2) if top2 else 0.0

    # Tiers are configured highest-threshold-first; take the first (highest) match.
    for tier in sorted(tiers, key=lambda t: t.get("top2_avg_at_or_above", 0), reverse=True):
        if top2_avg >= tier.get("top2_avg_at_or_above", 999):
            return float(tier.get("floor", 0.0)), True
    return 0.0, False


def _confidence(
    task: TaskAnalysis,
    repo: RepoContext,
    dims: dict[str, float],
    floor_applied: bool,
    config: dict,
) -> float:
    confidence_cfg = config.get("confidence", {})
    confidence = 0.85
    if task.ambiguity >= 5.0:
        confidence -= 0.25
    if not task.keywords_matched:
        confidence -= 0.10
    if repo.file_count == 0:
        confidence -= 0.10

    spread_cfg = confidence_cfg.get("dimension_spread_penalty", {})
    spread = statistics.pstdev(dims.values()) if len(dims) > 1 else 0.0
    if spread >= spread_cfg.get("high_spread_at_or_above", 3.5):
        confidence -= spread_cfg.get("high_spread_penalty", 0.20)
    elif spread >= spread_cfg.get("moderate_spread_at_or_above", 2.0):
        confidence -= spread_cfg.get("moderate_spread_penalty", 0.10)

    if floor_applied:
        confidence -= confidence_cfg.get("floor_override_penalty", 0.10)

    return round(max(0.25, min(0.95, confidence)), 2)


def _reasons(
    task: TaskAnalysis, repo: RepoContext, estimated_scope: str, risk_level, floor_applied: bool,
) -> list[str]:
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
    if task.cross_module_signal > 0:
        reasons.append("request explicitly names multiple files/modules")
    if estimated_scope == "large":
        reasons.append("estimated change scope is large")
    if task.ambiguity >= 5.0:
        reasons.append("request wording is ambiguous, which adds execution risk")
    if task.operation_count > 2:
        reasons.append(f"request bundles {task.operation_count} distinct operations")
    if floor_applied:
        reasons.append("complexity signals were strong enough to override a lower weighted-average estimate")
    if not reasons:
        reasons.append("no elevated risk or complexity signals found")
    return reasons


def score(task: TaskAnalysis, repo: RepoContext, config: dict) -> DifficultyScore:
    weights = config.get("scoring_weights", {})
    dims = {
        "task_complexity": task.task_complexity,
        "scope": task.scope,
        "risk": task.risk,
        "architecture_signal": task.architecture_signal,
        "cross_module_signal": task.cross_module_signal,
        "repo_complexity": repo.repo_complexity,
        "testing_complexity": task.testing_signal,
    }

    weighted = sum(dims[name] * weights.get(name, 0.0) for name in dims)
    total_weight = sum(weights.get(name, 0.0) for name in dims) or 1.0
    blended = weighted / total_weight

    # Risk floor: a high-risk task is never scored as trivially easy, even if
    # other dimensions are low (e.g. "delete the users table" is short but
    # dangerous). Independent of the complexity floor below -- risk and
    # difficulty stay separate axes.
    if task.risk >= 7.0:
        blended = max(blended, 6.0)
    elif task.risk >= 4.0:
        blended = max(blended, 4.0)

    # Complexity floor: a genuinely complex task is never scored as easy just
    # because unrelated dimensions (risk, repo size) happened to be low.
    complexity_dims = {name: dims[name] for name in _COMPLEXITY_DIMENSION_NAMES}
    complexity_floor_value, floor_applied = _complexity_floor(complexity_dims, config)
    if floor_applied and complexity_floor_value > blended:
        blended = complexity_floor_value
    else:
        floor_applied = False  # didn't actually change anything -- not evidence of disagreement

    # Ambiguity nudges difficulty up slightly -- an underspecified request
    # carries execution risk even if the described change sounds small.
    blended += task.ambiguity * 0.05

    blended = round(min(10.0, max(0.0, blended)), 2)

    band = band_for(blended, config.get("difficulty_bands", {}))
    risk_level = risk_classifier.classify_risk(task)
    mode = _mode_for(blended, config.get("planning_thresholds", {}))

    planning_required = mode != ExecutionMode.DIRECT
    testing_required = task.testing_signal >= 3.0 or bool(repo.test_dirs) and blended >= 3.0

    if task.scope >= 7 or repo.repo_complexity >= 7 or task.cross_module_signal >= 4:
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
        confidence=_confidence(task, repo, dims, floor_applied, config),
        reasons=_reasons(task, repo, estimated_scope, risk_level, floor_applied),
        basis="heuristic",
        historical_sample_size=0,
    )
