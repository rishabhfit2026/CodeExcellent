"""Builds the follow-up prompt after a failed or incomplete attempt. Never
resends the same prompt verbatim (section 20) -- it always includes concrete
feedback about what went wrong so the next attempt can actually correct it.

Validation-driven recovery: when a `FailureClass` is available (see
core/failure_classifier.py), its targeted recovery instruction is included
alongside the generic issues list -- e.g. a STRUCTURAL_INCOMPLETE
classification says explicitly that too few files changed for what the task
implies, rather than leaving the next attempt to re-derive that from a
generic "issues" list that was never task-shape-aware to begin with.
"""
from __future__ import annotations

from codeexcellent.core.failure_classifier import FailureClass, recovery_instruction
from codeexcellent.core.models import ClaudeCallResult, QualityResult, SuiteRunResult


def build_retry_prompt(
    original_task: str,
    call: ClaudeCallResult,
    tests: SuiteRunResult,
    quality: QualityResult,
    failure_class: FailureClass | None = None,
    changed_files: list[str] | None = None,
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

    if failure_class is not None:
        instruction = recovery_instruction(failure_class, changed_files or [])
        if instruction:
            lines.append(f"\n{instruction}")

    lines.append("\nMake the minimal additional changes needed to resolve these issues.")
    return "\n".join(lines)
