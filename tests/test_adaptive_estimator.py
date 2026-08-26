from codeexcellent.analyzer import adaptive_estimator, difficulty_scorer, fingerprint, task_analyzer
from codeexcellent.config.settings import load_config
from codeexcellent.core import memory
from codeexcellent.core.models import RepoContext

CONFIG = load_config()


def _repo() -> RepoContext:
    return RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=5, relevant_files=[], repo_complexity=0.0,
    )


def _seed(root: str, fingerprint_key: str, observed_difficulty: float, n: int) -> None:
    for i in range(n):
        memory.record(
            root,
            memory.TaskRecord(
                created_at=f"2026-01-0{i + 1}T00:00:00", request=f"task {i}", predicted_difficulty=2.0,
                band="easy", mode="direct", status="COMPLETE", cost_usd=0.05, duration_ms=1000,
                claude_calls=1, retries=0, files_changed=1, quality_score=9.0,
                fingerprint_key=fingerprint_key, fingerprint_category="small_change",
                fingerprint_repo_type="python", fingerprint_scope="small", fingerprint_risk="low",
                confidence=0.8, quality_level="basic", outcome_class="success",
                observed_difficulty=observed_difficulty, difficulty_error=0.0,
            ),
        )


def test_no_history_returns_heuristic_unchanged(tmp_path):
    task = task_analyzer.analyze("Fix a small validation bug")
    repo = _repo()
    heuristic = difficulty_scorer.score(task, repo, CONFIG)

    result = adaptive_estimator.estimate(task, repo, heuristic, str(tmp_path), CONFIG)
    assert result.basis == "heuristic"
    assert result.value == heuristic.value


def test_sufficient_history_blends_toward_observed_average(tmp_path):
    task = task_analyzer.analyze("Fix a small validation bug")
    repo = _repo()
    heuristic = difficulty_scorer.score(task, repo, CONFIG)
    fp = fingerprint.build(task, repo, heuristic)

    # Past similar tasks turned out much harder than the heuristic predicts.
    _seed(str(tmp_path), fp.key(), observed_difficulty=9.0, n=5)

    result = adaptive_estimator.estimate(task, repo, heuristic, str(tmp_path), CONFIG)
    assert result.basis == "heuristic+historical"
    assert result.value > heuristic.value
    assert result.historical_sample_size == 5


def test_below_min_samples_does_not_blend(tmp_path):
    task = task_analyzer.analyze("Fix a small validation bug")
    repo = _repo()
    heuristic = difficulty_scorer.score(task, repo, CONFIG)
    fp = fingerprint.build(task, repo, heuristic)

    _seed(str(tmp_path), fp.key(), observed_difficulty=9.0, n=1)  # below default min_samples_for_blend=3

    result = adaptive_estimator.estimate(task, repo, heuristic, str(tmp_path), CONFIG)
    assert result.basis == "heuristic"
