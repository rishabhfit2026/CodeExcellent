"""AdaptiveDifficultyEstimator (section 5, 23). Wraps the heuristic
DifficultyScorer with historical calibration: if enough past tasks with the
same TaskFingerprint exist, blend the heuristic estimate toward the observed
average, with a confidence that grows with sample size and shrinks with
variance. This is deliberately a transparent statistical blend, not machine
learning -- the docstring is the whole algorithm.

With fewer than `min_samples_for_blend` matching samples, this is a no-op:
the heuristic estimate stands unchanged (basis stays "heuristic").
"""
from __future__ import annotations

from dataclasses import replace

from codeexcellent.analyzer import difficulty_scorer, fingerprint
from codeexcellent.core import memory
from codeexcellent.core.models import DifficultyScore, RepoContext, TaskAnalysis


def estimate(
    task: TaskAnalysis,
    repo: RepoContext,
    heuristic: DifficultyScore,
    project_root: str,
    config: dict,
) -> DifficultyScore:
    fp = fingerprint.build(task, repo, heuristic)
    adaptive_cfg = config.get("adaptive", {})
    min_samples = int(adaptive_cfg.get("min_samples_for_blend", 3))
    max_blend_weight = float(adaptive_cfg.get("max_blend_weight", 0.7))

    rows = memory.similar(project_root, fp.key(), limit=50)
    observed_values = [row["observed_difficulty"] for row in rows if row["observed_difficulty"] is not None]

    if len(observed_values) < min_samples:
        return heuristic

    mean = sum(observed_values) / len(observed_values)
    variance = sum((v - mean) ** 2 for v in observed_values) / len(observed_values)
    stdev = variance ** 0.5

    # More samples -> more trust. Higher variance among those samples -> less
    # trust (the fingerprint bucket isn't actually predicting a stable outcome).
    sample_confidence = min(0.95, 0.4 + 0.05 * len(observed_values))
    variance_penalty = min(0.4, stdev / 10.0)
    historical_confidence = round(max(0.3, sample_confidence - variance_penalty), 2)

    blend_weight = min(max_blend_weight, historical_confidence)
    blended_value = round(heuristic.value * (1 - blend_weight) + mean * blend_weight, 2)
    combined_confidence = round(
        heuristic.confidence * (1 - blend_weight) + historical_confidence * blend_weight, 2
    )

    band = difficulty_scorer.band_for(blended_value, config.get("difficulty_bands", {}))
    reasons = [
        *heuristic.reasons,
        f"calibrated against {len(observed_values)} similar past task(s) for '{fp.key()}' "
        f"(observed avg {mean:.1f}/10, blend weight {blend_weight:.2f})",
    ]

    return replace(
        heuristic,
        value=blended_value,
        band=band,
        confidence=combined_confidence,
        reasons=reasons,
        basis="heuristic+historical",
        historical_sample_size=len(observed_values),
    )
