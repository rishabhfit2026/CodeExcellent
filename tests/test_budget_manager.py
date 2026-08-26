from codeexcellent.analyzer import difficulty_scorer, task_analyzer
from codeexcellent.budget import budget_manager
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import DifficultyScore, ExecutionMode, RepoContext, RiskLevel

CONFIG = load_config()


def _repo() -> RepoContext:
    return RepoContext(
        root=".", project_types=[], languages=[], frameworks=[], entry_points=[],
        config_files=[], test_dirs=[], has_git=False, git_branch=None, git_dirty_files=[],
        file_count=1, relevant_files=[], repo_complexity=0.0,
    )


def test_trivial_gets_small_budget():
    task = task_analyzer.analyze("Fix a typo in the README.")
    difficulty = difficulty_scorer.score(task, _repo(), CONFIG)
    budget = budget_manager.allocate(difficulty, CONFIG)
    assert budget.effort == "low"
    assert budget.max_claude_calls <= 2


def test_hard_gets_larger_budget_than_trivial():
    trivial_task = task_analyzer.analyze("Fix a typo.")
    hard_task = task_analyzer.analyze(
        "Refactor the authentication system, migrate JWT authentication to OAuth, "
        "preserve backward compatibility, update tests."
    )
    trivial_budget = budget_manager.allocate(difficulty_scorer.score(trivial_task, _repo(), CONFIG), CONFIG)
    hard_budget = budget_manager.allocate(difficulty_scorer.score(hard_task, _repo(), CONFIG), CONFIG)

    assert hard_budget.max_budget_usd > trivial_budget.max_budget_usd
    assert hard_budget.max_claude_calls > trivial_budget.max_claude_calls


def test_escalate_moves_to_next_band():
    task = task_analyzer.analyze("Fix a typo.")
    difficulty = difficulty_scorer.score(task, _repo(), CONFIG)
    budget = budget_manager.allocate(difficulty, CONFIG)
    escalated = budget_manager.escalate(budget, CONFIG)
    assert escalated.max_budget_usd >= budget.max_budget_usd


def test_escalate_at_top_band_increases_usd_only():
    very_hard = DifficultyScore(
        value=9.5, band="very_hard", risk_level=RiskLevel.HIGH,
        dimensions={}, planning_required=True, testing_required=True,
        mode=ExecutionMode.FULL, estimated_scope="large",
    )
    budget = budget_manager.allocate(very_hard, CONFIG)
    escalated = budget_manager.escalate(budget, CONFIG)
    assert escalated.band == "very_hard"
    assert escalated.max_budget_usd > budget.max_budget_usd
