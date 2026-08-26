from codeexcellent.analyzer import difficulty_scorer, task_analyzer
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import ExecutionMode, RepoContext

CONFIG = load_config()


def _empty_repo(complexity: float = 0.0) -> RepoContext:
    return RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=10, relevant_files=[], repo_complexity=complexity,
    )


def test_trivial_task_scores_low_and_direct_mode():
    task = task_analyzer.analyze("Rename userName to username.")
    result = difficulty_scorer.score(task, _empty_repo(), CONFIG)
    assert result.value < 4.0
    assert result.mode == ExecutionMode.DIRECT


def test_hard_task_scores_high_and_full_mode():
    task = task_analyzer.analyze(
        "Refactor the authentication system, migrate JWT authentication to OAuth, "
        "preserve backward compatibility, update tests, and make sure existing APIs "
        "continue working."
    )
    result = difficulty_scorer.score(task, _empty_repo(complexity=5.0), CONFIG)
    assert result.value >= 6.0
    assert result.mode == ExecutionMode.FULL
    assert result.planning_required is True


def test_high_risk_floors_difficulty_even_if_request_is_short():
    task = task_analyzer.analyze("Delete the users table in production.")
    result = difficulty_scorer.score(task, _empty_repo(), CONFIG)
    assert result.value >= 6.0
