"""ResourceForecaster (section 7). Estimates expected Claude calls, retries,
and duration before execution -- using historical averages for this task's
fingerprint when enough samples exist, and heuristic proxies (the allocated
budget's own ceilings, the actual context size already computed) otherwise.

No token counts are invented here: the CLI's real usage numbers are only
known after a call completes. This forecasts measurable proxies only
(section 7), clearly labeled by their basis.
"""
from __future__ import annotations

from codeexcellent.core import memory
from codeexcellent.core.models import Budget, ResourceForecast


def forecast(
    fingerprint_key: str,
    project_root: str,
    budget: Budget,
    context_chars: int,
    config: dict,
) -> ResourceForecast:
    adaptive_cfg = config.get("adaptive", {})
    min_samples = int(adaptive_cfg.get("min_samples_for_blend", 3))

    rows = memory.similar(project_root, fingerprint_key, limit=50)
    if len(rows) >= min_samples:
        calls = [row["claude_calls"] for row in rows if row["claude_calls"] is not None]
        retries = [row["retries"] for row in rows if row["retries"] is not None]
        durations = [row["duration_ms"] for row in rows if row["duration_ms"] is not None]
        if calls:
            return ResourceForecast(
                expected_calls=round(sum(calls) / len(calls)),
                expected_context_chars=context_chars,
                expected_retries=round(sum(retries) / len(retries)) if retries else 0,
                expected_duration_ms=round(sum(durations) / len(durations)) if durations else 0,
                basis="historical",
                sample_size=len(calls),
            )

    # Heuristic fallback: the budget's own ceilings are the worst case: a
    # well-behaved task uses one call and no retries.
    return ResourceForecast(
        expected_calls=1,
        expected_context_chars=context_chars,
        expected_retries=0,
        expected_duration_ms=round(budget.timeout_seconds * 1000 * 0.3),
        basis="heuristic",
        sample_size=0,
    )
