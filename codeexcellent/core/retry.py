"""Builds the follow-up prompt after a failed or incomplete attempt. Never
resends the same prompt verbatim (section 20) -- it always includes concrete
feedback about what went wrong so the next attempt can actually correct it.
"""
from __future__ import annotations

from codeexcellent.core.models import ClaudeCallResult, QualityResult, SuiteRunResult


def build_retry_prompt(
    original_task: str,
    call: ClaudeCallResult,
    tests: SuiteRunResult,
    quality: QualityResult,
) -> str:
    lines = [
        "The previous attempt at this task was not sufficient. Continue the same task, "
        "fixing the issues below. Do not restart from scratch -- build on what already changed.",
        f"\nOriginal task: {original_task}",
    ]

    if not call.success:
        lines.append(f"\nPrevious attempt failed: {call.error}")
    else:
        if quality.issues:
            lines.append("\nIssues found in the previous attempt:")
            lines.extend(f"- {issue}" for issue in quality.issues)
        if tests.ran and not tests.success:
            lines.append(f"\nTest output (tail):\n{tests.output_tail[-1500:]}")

    lines.append("\nMake the minimal additional changes needed to resolve these issues.")
    return "\n".join(lines)
