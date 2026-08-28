"""Quality gating. Cheap heuristics run every time (git diff shape, whether
tests passed, scope discipline, structural completeness) so a one-line
change never pays for a Claude review call. A structured Claude review
(via --json-schema) is only used when the execution strategy itself called
for one, or the task's risk makes it mandatory regardless of strategy --
see `review_required`.
"""
from __future__ import annotations

from codeexcellent.core.models import ClaudeCallResult, DifficultyScore, ExecutionMode, QualityResult, SuiteRunResult, TaskAnalysis

REVIEW_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 10},
        "complete": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "complete", "issues"],
}

_SCOPE_FILE_LIMITS = {"small": 4, "medium": 10, "large": 30}


def min_pass_score_for(difficulty: DifficultyScore, config: dict) -> float:
    """Quality-level-aware pass threshold (section 17): a typo fix and an
    auth migration should not be held to the same bar.
    """
    by_level = config.get("quality", {}).get("min_pass_score_by_level", {})
    default = config.get("quality", {}).get("min_pass_score", 7.0)
    return float(by_level.get(difficulty.quality_level.value, default))


def review_required(difficulty: DifficultyScore, mode: ExecutionMode, config: dict) -> bool:
    """Whether a structured Claude review call is warranted.

    This used to also independently trigger at `difficulty.value >= 6`,
    regardless of the execution mode `strategy_selector` had already chosen
    -- which meant a `lightweight` task above that threshold paid for a
    review call the strategy layer never asked for, stacking cost on top of
    (and sometimes in tension with) the mode decision. A live A/B benchmark
    traced part of the cost increase from stricter difficulty scoring to
    exactly this duplication. `mode` is now the single source of truth for
    "does this task's *process* call for a review" -- quality_level's
    mandatory-review list remains a separate, risk-driven reason a review is
    required even at a cheap mode (a CRITICAL one-line change still gets
    reviewed).
    """
    quality_cfg = config.get("quality", {})
    if difficulty.quality_level.value in quality_cfg.get("mandatory_review_levels", []):
        return True
    return mode in (ExecutionMode.FULL, ExecutionMode.REVIEW_REQUIRED)


def heuristic_check(
    difficulty: DifficultyScore,
    call: ClaudeCallResult,
    tests: SuiteRunResult,
    changed_files: list[str],
    min_pass_score: float,
    task: TaskAnalysis | None = None,
    config: dict | None = None,
) -> QualityResult:
    if not call.success:
        return QualityResult(score=0.0, complete=False, needs_more_work=True, issues=[call.error or "Claude call failed"])

    issues: list[str] = []
    score = 10.0

    if not changed_files:
        issues.append("No files were changed")
        score -= 6.0

    limit = _SCOPE_FILE_LIMITS.get(difficulty.estimated_scope, 10)
    if len(changed_files) > limit:
        issues.append(f"Changed {len(changed_files)} files, more than expected for a {difficulty.estimated_scope} scope task")
        score -= 2.0

    if task is not None and changed_files:
        recovery_cfg = (config or {}).get("recovery", {})
        structural_signal = max(task.architecture_signal, task.cross_module_signal)
        signal_threshold = float(recovery_cfg.get("structural_incomplete_signal_at_or_above", 6.0))
        max_files = int(recovery_cfg.get("structural_incomplete_max_files_changed", 1))
        if structural_signal >= signal_threshold and len(changed_files) <= max_files:
            issues.append(
                f"Request implies a structural/multi-module change (signal={structural_signal}/10), "
                f"but only {len(changed_files)} file(s) changed -- implementation may be incomplete"
            )
            score -= 3.0

    if difficulty.testing_required:
        if not tests.ran:
            issues.append("Testing was required but no test suite ran")
            score -= 1.5
        elif not tests.success:
            issues.append(f"Tests failing ({tests.failed} failed, {tests.passed} passed)")
            score -= 4.0

    if call.stop_reason and call.stop_reason not in ("end_turn", "stop_sequence"):
        issues.append(f"Claude stopped for reason '{call.stop_reason}', which may mean the task was cut short")
        score -= 2.0

    score = max(0.0, min(10.0, score))
    needs_more_work = score < min_pass_score
    complete = not needs_more_work

    return QualityResult(score=round(score, 1), complete=complete, needs_more_work=needs_more_work, issues=issues)


def build_review_prompt(task: str, changed_files: list[str], diff_text: str, tests: SuiteRunResult) -> str:
    test_summary = (
        f"Tests: {'passed' if tests.success else 'FAILED'} ({tests.passed} passed, {tests.failed} failed)"
        if tests.ran else "Tests: not run"
    )
    return (
        "You are reviewing a code change made by another Claude session for quality.\n"
        f"Original task: {task}\n\n"
        f"Files changed: {', '.join(changed_files) or 'none'}\n"
        f"{test_summary}\n\n"
        f"Diff:\n{diff_text[:8000]}\n\n"
        "Score this change from 0-10 on correctness, completeness, and scope discipline "
        "(did it change only what the task required?). Respond with the requested JSON only."
    )


def parse_review_response(call: ClaudeCallResult, min_pass_score: float) -> QualityResult | None:
    if not call.success:
        return None
    import json

    try:
        data = call.structured_output if call.structured_output is not None else json.loads(call.result_text)
        score = float(data["score"])
        issues = list(data.get("issues", []))
        needs_more_work = score < min_pass_score
        return QualityResult(score=score, complete=not needs_more_work, needs_more_work=needs_more_work, issues=issues)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
