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
    REVIEW_REQUIRED = "review_required"  # no plan phase, but a Claude review is mandatory (risk/quality-driven)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class QualityLevel(str, Enum):
    TRIVIAL = "trivial"
    BASIC = "basic"
    STANDARD = "standard"
    HIGH = "high"
    CRITICAL = "critical"


class OutcomeClass(str, Enum):
    """Why a run ended the way it did. Only SUCCESS and TASK_DIFFICULTY_*
    outcomes are valid training signal for the adaptive estimator (section
    24) -- infra/external failures say nothing about how hard the task was.
    """

    SUCCESS = "success"
    TASK_DIFFICULTY_FAILURE = "task_difficulty_failure"
    INFRA_FAILURE = "infra_failure"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    EXTERNAL_DEPENDENCY_FAILURE = "external_dependency_failure"


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
    category: str = "general"  # rename_or_typo / small_change / large_refactor / general
    cross_module_signal: float = 0.0  # 0-10 -- distinct file/module references beyond the first (section: adaptive difficulty audit)


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
    quality_level: QualityLevel = QualityLevel.STANDARD
    confidence: float = 0.5  # 0-1, how much to trust `value` (see AdaptiveDifficultyEstimator)
    reasons: list[str] = field(default_factory=list)  # explainability (section 27)
    basis: str = "heuristic"  # "heuristic" or "heuristic+historical"
    historical_sample_size: int = 0


@dataclass
class TaskFingerprint:
    """A compact, non-sensitive signature used to look up similar past
    executions (section 22). Deliberately excludes source code / request
    text -- only shape-level facts.
    """

    category: str  # e.g. rename, fix, add, refactor, migrate, redesign
    repo_type: str  # primary detected project type, or "unknown"
    scope: str  # small/medium/large
    risk: str  # low/medium/high/critical

    def key(self, granularity: str = "full") -> str:
        if granularity == "category_only":
            return self.category
        if granularity == "category_repo":
            return f"{self.category}|{self.repo_type}"
        return f"{self.category}|{self.repo_type}|{self.scope}|{self.risk}"


@dataclass
class PlanResult:
    """Everything decided about a task before any Claude call is made --
    shared by `engine.run()` and `codeexcellent analyze` so the two can never
    drift (they call the same function).
    """

    task: TaskAnalysis
    repo: RepoContext
    difficulty: DifficultyScore
    budget: Budget
    fingerprint: TaskFingerprint
    forecast: ResourceForecast
    context_bundle: ContextBundle | None = None
    blocked_reason: str | None = None


@dataclass
class ResourceForecast:
    expected_calls: int
    expected_context_chars: int
    expected_retries: int
    expected_duration_ms: int
    basis: str  # "historical" or "heuristic"
    sample_size: int = 0


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
    failure_class: str | None = None  # see core/failure_classifier.py -- None when the attempt passed


@dataclass
class ExecutionReport:
    task: str
    difficulty: DifficultyScore
    budget: Budget
    attempts: list[ExecutionAttempt]
    status: str  # COMPLETE / INCOMPLETE / FAILED / BLOCKED / CANCELLED
    total_cost_usd: float
    total_duration_ms: int
    files_changed: list[str]
    final_quality: QualityResult | None
    fingerprint: TaskFingerprint | None = None
    outcome_class: OutcomeClass = OutcomeClass.SUCCESS
    observed_difficulty: float | None = None  # derived from actual run stats
    difficulty_error: float | None = None  # observed - predicted
    resource_forecast: ResourceForecast | None = None
    escalation_reasons: list[str] = field(default_factory=list)
