"""Quality gating. Cheap heuristics run every time (git diff shape, whether
tests passed, scope discipline) so a one-line change never pays for a Claude
review call. A structured Claude review (via --json-schema) is only used for
harder/riskier tasks, gated by config.
"""
from __future__ import annotations

from codeexcellent.core.models import ClaudeCallResult, DifficultyScore, QualityResult, SuiteRunResult

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


def heuristic_check(
    difficulty: DifficultyScore,
    call: ClaudeCallResult,
    tests: SuiteRunResult,
    changed_files: list[str],
    min_pass_score: float,
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
