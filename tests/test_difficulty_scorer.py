from codeexcellent.analyzer import difficulty_scorer, task_analyzer
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import ExecutionMode, RepoContext, RiskLevel

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


# --- regression coverage added after the live-benchmark adaptive-scoring audit ---

def test_complexity_floor_prevents_dilution_by_unrelated_low_dimensions():
    # Regression (the core bug this audit fixed): a genuine architecture
    # migration scored task_complexity=10 and scope=10, but risk and
    # repo_complexity were both near-zero, and a blind weighted average
    # diluted the strong signal down to "medium" (~5.3). A small/empty repo
    # is used deliberately -- the floor must work even when repo_complexity
    # provides no signal at all.
    task = task_analyzer.analyze(
        "Migrate app.py from a single-file script into a package with separate "
        "modules for routes, models, and services, preserving behavior"
    )
    result = difficulty_scorer.score(task, _empty_repo(complexity=0.0), CONFIG)
    assert result.value >= 6.0, f"expected hard-or-above, got {result.value}"
    assert result.mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL)


def test_complexity_floor_does_not_fire_on_a_single_weak_signal():
    # The floor requires the top TWO complexity-relevant dimensions to
    # agree, not just one spike -- a task with one elevated dimension and
    # everything else low should not be floored.
    task = task_analyzer.analyze("Add a small helper function to utils.py")
    result = difficulty_scorer.score(task, _empty_repo(), CONFIG)
    assert result.value < 4.0
    assert result.mode == ExecutionMode.DIRECT


def test_cross_module_signal_contributes_to_difficulty():
    # A generic cross-module scenario in a different domain from any
    # benchmark task, to prove the mechanism generalizes rather than
    # matching one specific example's exact wording.
    task = task_analyzer.analyze(
        "Redesign billing.py and notifications.py to decouple them through a clean shared interface module"
    )
    result = difficulty_scorer.score(task, _empty_repo(), CONFIG)
    assert result.value >= 6.0
    assert result.dimensions["cross_module_signal"] > 0


def test_difficulty_and_risk_are_independent_high_difficulty_low_risk():
    # Section 6: a cross-module redesign is genuinely difficult (touches two
    # files, real coordination complexity) but has no domain-risk signal at
    # all (no auth/payment/production/destructive keywords) -- it must be
    # scored as high-difficulty, low-risk, not have one axis bleed into the
    # other.
    task = task_analyzer.analyze(
        "Redesign the reporting.py and export.py modules to remove the tight coupling between them"
    )
    result = difficulty_scorer.score(task, _empty_repo(), CONFIG)
    assert result.value >= 6.0
    assert result.risk_level == RiskLevel.LOW


def test_difficulty_and_risk_are_independent_low_difficulty_high_risk():
    # The inverse: a one-line change to payment logic is textually tiny
    # (low task_complexity/scope) but must still be scored as high risk.
    task = task_analyzer.analyze("Change the payment amount rounding to always round up")
    result = difficulty_scorer.score(task, _empty_repo(), CONFIG)
    assert result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_confidence_drops_when_dimensions_strongly_disagree():
    # A task where task_complexity/scope are maxed out but risk and
    # repo_complexity are near-zero is a case the heuristic itself should
    # flag as uncertain (the floor firing is direct evidence of internal
    # disagreement), not report with its usual high confidence.
    high_disagreement = task_analyzer.analyze(
        "Migrate app.py from a single-file script into a package with separate "
        "modules for routes, models, and services, preserving behavior"
    )
    low_disagreement = task_analyzer.analyze("Rename userName to username.")

    high = difficulty_scorer.score(high_disagreement, _empty_repo(complexity=0.0), CONFIG)
    low = difficulty_scorer.score(low_disagreement, _empty_repo(), CONFIG)

    assert high.confidence < low.confidence
    assert high.confidence <= 0.7


def test_reasons_mention_floor_override_when_it_fires():
    task = task_analyzer.analyze(
        "Migrate app.py from a single-file script into a package with separate "
        "modules for routes, models, and services, preserving behavior"
    )
    result = difficulty_scorer.score(task, _empty_repo(complexity=0.0), CONFIG)
    assert any("override" in r.lower() for r in result.reasons)


# --- trivial tasks that must remain direct (not over-escalated) --------------

def test_trivial_tasks_stay_low_difficulty_and_high_confidence():
    for request in (
        "Rename the parameter nam to name in greet.py",
        "Fix the typo 'Wecome' in README.md",
        "Change the MAX_RETRIES constant in config.py from 3 to 5",
    ):
        task = task_analyzer.analyze(request)
        result = difficulty_scorer.score(task, _empty_repo(), CONFIG)
        assert result.value < 4.0, f"{request!r} scored {result.value}, expected trivial/easy"
        assert result.mode == ExecutionMode.DIRECT, f"{request!r} selected {result.mode}, expected direct"
        assert result.confidence >= 0.7, f"{request!r} had unexpectedly low confidence {result.confidence}"
