from codeexcellent.analyzer import difficulty_scorer, fingerprint, task_analyzer
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import RepoContext

CONFIG = load_config()


def _repo(project_types=None) -> RepoContext:
    return RepoContext(
        root=".", project_types=project_types or [], languages=[], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=5, relevant_files=[], repo_complexity=0.0,
    )


def test_fingerprint_key_is_stable_for_same_shape_tasks():
    task_a = task_analyzer.analyze("Fix the login validation bug")
    task_b = task_analyzer.analyze("Fix the signup validation bug")
    repo = _repo(["python"])
    diff_a = difficulty_scorer.score(task_a, repo, CONFIG)
    diff_b = difficulty_scorer.score(task_b, repo, CONFIG)

    fp_a = fingerprint.build(task_a, repo, diff_a)
    fp_b = fingerprint.build(task_b, repo, diff_b)
    assert fp_a.key() == fp_b.key()


def test_fingerprint_key_differs_by_category():
    repo = _repo(["python"])
    rename_task = task_analyzer.analyze("Rename userName to username")
    refactor_task = task_analyzer.analyze("Refactor and migrate the authentication architecture")

    rename_diff = difficulty_scorer.score(rename_task, repo, CONFIG)
    refactor_diff = difficulty_scorer.score(refactor_task, repo, CONFIG)

    rename_fp = fingerprint.build(rename_task, repo, rename_diff)
    refactor_fp = fingerprint.build(refactor_task, repo, refactor_diff)
    assert rename_fp.key() != refactor_fp.key()


def test_repo_type_defaults_to_unknown():
    task = task_analyzer.analyze("Fix a bug")
    repo = _repo([])
    diff = difficulty_scorer.score(task, repo, CONFIG)
    fp = fingerprint.build(task, repo, diff)
    assert fp.repo_type == "unknown"
