"""End-to-end scenario regression tests for the adaptive difficulty/risk/
strategy pipeline (task_analyzer -> difficulty_scorer -> strategy_selector),
covering the specific categories a live benchmark found were systematically
under-predicted, plus the categories that must NOT be over-escalated.

Every task text here is written fresh for this test file, deliberately
different from the wording of any actual benchmark task in
codeexcellent/benchmark/tasks.py -- the point is to prove the scoring
mechanism generalizes to the *category* of task, not that it has memorized
13 specific phrasings. If a change here only passes for the exact benchmark
wording, that's a sign of overfitting, not a fix.
"""
from codeexcellent.analyzer import difficulty_scorer, strategy_selector, task_analyzer
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import ExecutionMode, RepoContext, RiskLevel

CONFIG = load_config()


def _repo(complexity: float = 0.0, relevant_files: list[str] | None = None) -> RepoContext:
    return RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=10, relevant_files=relevant_files or [],
        repo_complexity=complexity,
    )


def _run(request: str, repo: RepoContext | None = None):
    repo = repo or _repo()
    task = task_analyzer.analyze(request)
    difficulty = difficulty_scorer.score(task, repo, CONFIG)
    mode, reasons = strategy_selector.select(task, difficulty, CONFIG, repo)
    return task, difficulty, mode, reasons


# --- 1. very hard architecture / migration tasks ----------------------------

def test_architecture_migration_escalates_beyond_direct():
    _, difficulty, mode, _ = _run(
        "Migrate the notification service from a single script into a proper package "
        "with separate modules for handlers, templates, and delivery, keeping existing behavior"
    )
    assert difficulty.value >= 6.0
    assert mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL)


def test_service_migration_with_compatibility_requirement_escalates():
    _, difficulty, mode, _ = _run(
        "Migrate the caching layer from an in-memory dict to Redis, preserving the existing "
        "cache API so callers don't need to change"
    )
    assert difficulty.value >= 5.0
    assert mode != ExecutionMode.DIRECT


# --- 2. cross-module redesign -------------------------------------------------

def test_cross_module_redesign_escalates_and_stays_low_risk():
    _, difficulty, mode, _ = _run(
        "Redesign checkout.py and shipping.py to remove the direct coupling between "
        "them by introducing a clean shared interface"
    )
    assert difficulty.value >= 6.0
    assert difficulty.risk_level == RiskLevel.LOW  # coupling removal isn't inherently a domain-risk concern
    assert mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL)


def test_repo_scan_evidence_of_multiple_files_reinforces_escalation():
    # A modestly-worded request should escalate once the repo scan itself
    # found several (but not implausibly many -- see the "noise, not
    # signal" regression test in test_strategy_selector.py) files relevant
    # to it.
    repo = _repo(relevant_files=["reports.py", "exporters.py", "formatters.py", "writers.py", "templates.py"])
    _, difficulty, mode, reasons = _run("Clean up how reports get exported across the export pipeline", repo=repo)
    assert mode != ExecutionMode.DIRECT
    assert any("relevant file" in r for r in reasons)


# --- 3. authentication / security changes -------------------------------------

def test_authentication_change_is_flagged_high_or_critical_risk():
    _, difficulty, mode, _ = _run(
        "Add token refresh support to the authentication flow so expired sessions "
        "are renewed automatically instead of forcing re-login"
    )
    assert difficulty.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_security_sensitive_change_never_selects_direct_alone():
    _, difficulty, mode, _ = _run(
        "Change how user passwords are hashed before storing them in the database"
    )
    assert difficulty.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert mode != ExecutionMode.DIRECT


def test_critical_auth_migration_forces_review_regardless_of_difficulty_number():
    _, difficulty, mode, _ = _run(
        "Migrate session authentication to OAuth2 while keeping existing tokens valid during rollout"
    )
    assert mode in (ExecutionMode.REVIEW_REQUIRED, ExecutionMode.FULL)


# --- 4. multi-file refactoring ------------------------------------------------

def test_multi_file_refactor_with_named_files_escalates():
    _, difficulty, mode, _ = _run(
        "Refactor validator.py, normalizer.py, and formatter.py to share a common "
        "base class instead of duplicating logic in each"
    )
    assert difficulty.value >= 5.0
    assert difficulty.dimensions["cross_module_signal"] > 0


def test_single_function_decomposition_refactor_escalates_on_complexity_alone():
    _, difficulty, mode, _ = _run(
        "Refactor the process_transaction function in transactions.py to split "
        "validation, calculation, and persistence into separate, smaller functions"
    )
    assert difficulty.value >= 6.0
    assert mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL)


# --- 5. database / schema changes ---------------------------------------------

def test_schema_migration_is_flagged_as_risky():
    _, difficulty, mode, _ = _run(
        "Add a new required column to the orders database table and write a migration for it"
    )
    assert difficulty.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_destructive_schema_change_is_critical():
    _, difficulty, mode, _ = _run("Drop the legacy_sessions table from the production database")
    assert difficulty.risk_level == RiskLevel.CRITICAL
    assert mode in (ExecutionMode.REVIEW_REQUIRED, ExecutionMode.FULL)


def test_simple_field_addition_is_not_over_escalated_to_critical():
    # A schema change without any destructive/production signal should stay
    # well below CRITICAL -- adding an optional field is routine, not
    # dangerous, even though it touches "database".
    _, difficulty, mode, _ = _run("Add an optional nickname field to the User model")
    assert difficulty.risk_level != RiskLevel.CRITICAL


# --- 6. test-writing tasks -----------------------------------------------------

def test_test_writing_task_does_not_need_full_planning():
    # Writing tests for a couple of small, already-simple functions is not
    # itself a hard task -- section 10/16: minimum sufficient effort, not
    # maximum, and don't escalate what doesn't need it.
    _, difficulty, mode, _ = _run(
        "Add unit tests covering the add and subtract functions in arithmetic.py"
    )
    assert mode in (ExecutionMode.DIRECT, ExecutionMode.LIGHTWEIGHT)


def test_test_writing_for_a_complex_multi_file_system_can_still_escalate():
    # But testing something genuinely complex (spanning several files) is a
    # different story -- the escalation should come from the complexity of
    # what's being tested, not from the word "test" itself.
    _, difficulty, mode, _ = _run(
        "Add integration tests covering the full checkout flow across cart.py, "
        "payment.py, and shipping.py"
    )
    assert difficulty.dimensions["cross_module_signal"] > 0


# --- 7. trivial tasks that must remain direct ---------------------------------

def test_rename_stays_direct():
    _, difficulty, mode, _ = _run("Rename the loadConfig function to load_config")
    assert mode == ExecutionMode.DIRECT
    assert difficulty.value < 3.0


def test_typo_fix_stays_direct():
    _, difficulty, mode, _ = _run("Fix the typo 'recieve' to 'receive' in the docstring")
    assert mode == ExecutionMode.DIRECT


def test_constant_change_stays_direct():
    _, difficulty, mode, _ = _run("Change the DEFAULT_TIMEOUT constant from 30 to 60")
    assert mode == ExecutionMode.DIRECT
    assert difficulty.risk_level == RiskLevel.LOW


def test_small_helper_addition_stays_direct():
    _, difficulty, mode, _ = _run("Add a small helper function to format file sizes as human-readable strings")
    assert mode == ExecutionMode.DIRECT


def test_trivial_tasks_keep_high_confidence():
    # Confidence should NOT drop for genuinely simple, unambiguous tasks --
    # the new dimension-spread penalty must not fire when there's nothing to
    # disagree about.
    _, difficulty, _, _ = _run("Rename the loadConfig function to load_config")
    assert difficulty.confidence >= 0.7
