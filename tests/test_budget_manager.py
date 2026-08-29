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


def test_allocate_loop_gives_a_much_higher_but_still_finite_ceiling():
    # --loop mode's whole point: far more room than any single difficulty
    # band's normal 1-8 call budget, but never truly unbounded -- a stuck
    # task must still stop eventually rather than spend indefinitely.
    loop_budget = budget_manager.allocate_loop(CONFIG)
    very_hard_budget = budget_manager.allocate(
        DifficultyScore(
            value=9.0, band="very_hard", risk_level=RiskLevel.LOW, dimensions={},
            planning_required=True, testing_required=True, mode=ExecutionMode.FULL,
            estimated_scope="large",
        ),
        CONFIG,
    )
    assert loop_budget.max_claude_calls > very_hard_budget.max_claude_calls
    assert loop_budget.max_retries == loop_budget.max_claude_calls - 1
    assert 0 < loop_budget.max_budget_usd < float("inf")


def test_allocate_loop_respects_a_user_override():
    custom_config = {**CONFIG, "loop_mode": {**CONFIG["loop_mode"], "max_claude_calls": 5, "max_budget_usd": 2.0}}
    budget = budget_manager.allocate_loop(custom_config)
    assert budget.max_claude_calls == 5
    assert budget.max_retries == 4
    assert budget.max_budget_usd == 2.0
