"""Shared dataclasses passed between components. Keeping these in one module
avoids circular imports between analyzer/claude/budget/quality packages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ExecutionMode(str, Enum):
    DIRECT = "direct"
    LIGHTWEIGHT = "lightweight_plan_implement_test"
    FULL = "plan_implement_test_review"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class TaskAnalysis:
    """Heuristic read of the raw request text, before the repo is inspected."""

    request: str
    task_complexity: float  # 0-10
    scope: float  # 0-10
    risk: float  # 0-10
    testing_signal: float  # 0-10
    architecture_signal: float  # 0-10
    ambiguity: float  # 0-10
    operation_count: int
    keywords_matched: list[str] = field(default_factory=list)


@dataclass
class RepoContext:
    root: str
    project_types: list[str]
    languages: list[str]
    frameworks: list[str]
    entry_points: list[str]
    config_files: list[str]
    test_dirs: list[str]
    has_git: bool
    git_branch: str | None
    git_dirty_files: list[str]
    file_count: int
    relevant_files: list[str] = field(default_factory=list)
    repo_complexity: float = 0.0  # 0-10


@dataclass
class DifficultyScore:
    value: float  # 0-10, final blended score
    band: str  # trivial/easy/medium/hard/very_hard
    risk_level: RiskLevel
    dimensions: dict[str, float]
    planning_required: bool
    testing_required: bool
    mode: ExecutionMode
    estimated_scope: str  # small/medium/large


@dataclass
class Budget:
    band: str
    effort: str  # low/medium/high/xhigh/max -> maps to `claude --effort`
    max_budget_usd: float
    max_budget_usd_step: float
    max_claude_calls: int
    max_retries: int
    timeout_seconds: int


@dataclass
class ContextBundle:
    summary: str
    files: dict[str, str]  # relative path -> (possibly truncated) content
    total_bytes: int


@dataclass
class ClaudeCallResult:
    success: bool
    result_text: str
    session_id: str | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    duration_ms: int
    num_turns: int
    stop_reason: str | None
    error: str | None = None
    raw: dict | None = None
    structured_output: dict | None = None


@dataclass
class SuiteRunResult:
    ran: bool
    passed: int = 0
    failed: int = 0
    command: str | None = None
    success: bool = True
    output_tail: str = ""


@dataclass
class QualityResult:
    score: float  # 0-10
    complete: bool
    needs_more_work: bool
    issues: list[str] = field(default_factory=list)


@dataclass
class ExecutionAttempt:
    call: ClaudeCallResult
    tests: SuiteRunResult
    quality: QualityResult
    changed_files: list[str] = field(default_factory=list)


@dataclass
class ExecutionReport:
    task: str
    difficulty: DifficultyScore
    budget: Budget
    attempts: list[ExecutionAttempt]
    status: str  # COMPLETE / INCOMPLETE / FAILED
    total_cost_usd: float
    total_duration_ms: int
    files_changed: list[str]
    final_quality: QualityResult | None
