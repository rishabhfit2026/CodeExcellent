"""Builds a compact, non-sensitive TaskFingerprint (section 22) used to look
up similar past executions. Deliberately excludes request text and source
code -- only shape-level facts that are safe to store and compare.
"""
from __future__ import annotations

from codeexcellent.core.models import DifficultyScore, RepoContext, TaskAnalysis, TaskFingerprint


def build(task: TaskAnalysis, repo: RepoContext, difficulty: DifficultyScore) -> TaskFingerprint:
    repo_type = repo.project_types[0] if repo.project_types else "unknown"
    return TaskFingerprint(
        category=task.category,
        repo_type=repo_type,
        scope=difficulty.estimated_scope,
        risk=difficulty.risk_level.value,
    )
