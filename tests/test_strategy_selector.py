from codeexcellent.analyzer import risk_classifier, strategy_selector, task_analyzer
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import DifficultyScore, ExecutionMode, QualityLevel, RepoContext, RiskLevel

CONFIG = load_config()


def _difficulty(value, risk_level, confidence=0.85, estimated_scope="small") -> DifficultyScore:
    quality_level = risk_classifier.classify_quality_level(risk_level, estimated_scope, False)
    return DifficultyScore(
        value=value, band="medium", risk_level=risk_level, dimensions={},
        planning_required=False, testing_required=False, mode=ExecutionMode.DIRECT,
        estimated_scope=estimated_scope, quality_level=quality_level, confidence=confidence,
    )


def test_trivial_low_risk_high_confidence_is_direct():
    task = task_analyzer.analyze("Rename userName to username.")
    difficulty = _difficulty(1.0, RiskLevel.LOW)
    mode, reasons = strategy_selector.select(task, difficulty, CONFIG)
    assert mode == ExecutionMode.DIRECT
    assert reasons


def test_critical_risk_low_difficulty_forces_review_not_full_plan():
    task = task_analyzer.analyze("Update the payment charge amount calculation")
    difficulty = _difficulty(2.0, RiskLevel.CRITICAL)
    mode, reasons = strategy_selector.select(task, difficulty, CONFIG)
    assert mode == ExecutionMode.REVIEW_REQUIRED
    assert any("CRITICAL" in r for r in reasons)


def test_critical_risk_high_difficulty_gets_full_plan():
    task = task_analyzer.analyze("Migrate the payment processing architecture")
    difficulty = _difficulty(8.0, RiskLevel.CRITICAL)
    mode, _ = strategy_selector.select(task, difficulty, CONFIG)
    assert mode == ExecutionMode.FULL


def test_low_confidence_pushes_direct_toward_planning():
    task = task_analyzer.analyze("Improve the thing")
    confident = _difficulty(2.0, RiskLevel.LOW, confidence=0.9)
    unsure = _difficulty(2.0, RiskLevel.LOW, confidence=0.2)
    confident_mode, _ = strategy_selector.select(task, confident, CONFIG)
    unsure_mode, reasons = strategy_selector.select(task, unsure, CONFIG)
    assert confident_mode == ExecutionMode.DIRECT
    assert unsure_mode != ExecutionMode.DIRECT
    assert any("confidence is low" in r for r in reasons)


# --- regression coverage added after the live-benchmark adaptive-scoring audit ---

def test_low_confidence_hard_floor_fires_even_when_soft_bonus_alone_would_not():
    # The soft confidence_gap bonus is proportional to the gap and can stay
    # below planning_cost on its own (a confidence just barely under the
    # threshold adds very little benefit). Section 8 asks for a hard floor,
    # not just a nudge -- direct execution must never be the answer when the
    # estimate itself says it's uncertain, regardless of the cost/benefit
    # arithmetic's own conclusion.
    task = task_analyzer.analyze("Improve the thing")
    difficulty = _difficulty(2.0, RiskLevel.LOW, confidence=0.45)  # just under the 0.5 low_threshold
    mode, reasons = strategy_selector.select(task, difficulty, CONFIG)
    assert mode != ExecutionMode.DIRECT
    assert any("safety threshold" in r for r in reasons)


def test_low_confidence_floor_never_downgrades_a_more_cautious_mode():
    # CRITICAL risk already forces REVIEW_REQUIRED/FULL for a stronger
    # reason -- the confidence floor (which only forces up to LIGHTWEIGHT by
    # default) must never accidentally downgrade that.
    task = task_analyzer.analyze("Update the payment charge amount calculation")
    difficulty = _difficulty(2.0, RiskLevel.CRITICAL, confidence=0.2)
    mode, _ = strategy_selector.select(task, difficulty, CONFIG)
    assert mode == ExecutionMode.REVIEW_REQUIRED


def test_high_confidence_trivial_task_is_unaffected_by_the_floor():
    task = task_analyzer.analyze("Rename userName to username.")
    difficulty = _difficulty(1.0, RiskLevel.LOW, confidence=0.9)
    mode, _ = strategy_selector.select(task, difficulty, CONFIG)
    assert mode == ExecutionMode.DIRECT


def test_repo_context_relevant_files_count_pushes_toward_planning():
    # Independent of what the task text itself says: real evidence from the
    # repo scan that a change touches multiple files should count on its
    # own, not only be inferable from the request text.
    task = task_analyzer.analyze("Update the shared config handling")
    difficulty = _difficulty(3.0, RiskLevel.LOW, confidence=0.85)

    no_repo_mode, _ = strategy_selector.select(task, difficulty, CONFIG, repo=None)

    many_files_repo = RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=20,
        relevant_files=["a.py", "b.py", "c.py", "d.py", "e.py"],
    )
    many_files_mode, reasons = strategy_selector.select(task, difficulty, CONFIG, repo=many_files_repo)

    assert no_repo_mode == ExecutionMode.DIRECT
    assert many_files_mode != ExecutionMode.DIRECT
    assert any("relevant file" in r for r in reasons)


def test_repo_context_single_relevant_file_does_not_force_planning():
    task = task_analyzer.analyze("Fix a small bug")
    difficulty = _difficulty(1.5, RiskLevel.LOW, confidence=0.85)
    repo = RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=5, relevant_files=["only_one.py"],
    )
    mode, _ = strategy_selector.select(task, difficulty, CONFIG, repo=repo)
    assert mode == ExecutionMode.DIRECT


# --- FULL vs LIGHTWEIGHT: difficulty alone must not imply FULL (added
# after a live A/B benchmark showed escalating to FULL purely on difficulty
# cost 3-13x more without ever turning a validator failure into a pass) ---

def test_hard_single_file_task_with_decent_confidence_gets_lightweight_not_full():
    # A genuinely hard but single-file, well-specified, low-risk task: no
    # named coordination across files, no unusual uncertainty -- a plan
    # phase has nothing specific to plan beyond what a cheaper strategy plus
    # validation-driven recovery can already handle.
    task = task_analyzer.analyze(
        "Refactor the summarize_shipment function in shipment_report.py to split calculation, "
        "formatting, and output into separate smaller functions"
    )
    difficulty = _difficulty(7.5, RiskLevel.LOW, confidence=0.55, estimated_scope="medium")
    mode, reasons = strategy_selector.select(task, difficulty, CONFIG)
    assert mode == ExecutionMode.LIGHTWEIGHT


def test_low_confidence_hard_task_gets_full_plan():
    # Distinct from the cross-module/validator reasoning below: confidence
    # this low means the difficulty estimate itself might be wrong, which
    # planning upfront (rather than a cheap guess plus recovery) is the
    # right response to.
    task = task_analyzer.analyze("Somehow migrate the architecture, not sure exactly how, various things need to change")
    difficulty = _difficulty(6.8, RiskLevel.LOW, confidence=0.3)
    mode, reasons = strategy_selector.select(task, difficulty, CONFIG)
    assert mode == ExecutionMode.FULL


def test_medium_task_with_strong_validator_stays_at_a_cheap_strategy():
    # A real test suite existing shouldn't itself be a reason to escalate --
    # if anything it's evidence a cheap strategy plus validation-driven
    # recovery is *sufficient*, since a wrong attempt would be caught cheaply.
    task = task_analyzer.analyze("Add an email field to the User model and keep the existing fields working")
    difficulty = _difficulty(4.5, RiskLevel.LOW, confidence=0.8)
    difficulty.testing_required = True
    repo = RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=["tests"], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=10, relevant_files=[],
    )
    mode, _ = strategy_selector.select(task, difficulty, CONFIG, repo=repo)
    assert mode in (ExecutionMode.DIRECT, ExecutionMode.LIGHTWEIGHT)


def test_multi_file_hard_task_gets_full_only_without_a_reliable_validator():
    task = task_analyzer.analyze(
        "Coordinate a shared retry policy across scheduler.py, worker.py, and dispatcher.py "
        "so they all back off the same way"
    )
    difficulty = _difficulty(6.5, RiskLevel.LOW, confidence=0.55, estimated_scope="large")
    # This exact DifficultyScore doesn't carry testing_required from the
    # real scorer, so set it explicitly on both sides of the comparison --
    # what's under test is the presence of repo.test_dirs plus
    # testing_required together meaning "failure can be cheaply detected".
    difficulty.testing_required = True

    no_tests_repo = RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=10, relevant_files=[],
    )
    with_tests_repo = RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=["tests"], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=10, relevant_files=[],
    )

    mode_no_validator, _ = strategy_selector.select(task, difficulty, CONFIG, repo=no_tests_repo)
    mode_with_validator, _ = strategy_selector.select(task, difficulty, CONFIG, repo=with_tests_repo)

    assert mode_no_validator == ExecutionMode.FULL
    assert mode_with_validator == ExecutionMode.LIGHTWEIGHT


def test_repo_context_a_large_relevant_file_count_is_treated_as_noise_not_signal():
    # Regression: found by testing a trivial rename against this project's
    # OWN real repo -- find_relevant_files's content-scan fallback matched
    # the query's generic words against example strings inside this repo's
    # own test files, returning 15 "relevant" files for a one-line rename.
    # A large count is characteristic of broad/imprecise matching in an
    # established codebase, not evidence of a genuine multi-file task (which
    # in practice matches a small, precise set) -- it must NOT escalate a
    # task that direct heuristic signals say is trivial.
    task = task_analyzer.analyze("Rename userName to username")
    difficulty = _difficulty(0.6, RiskLevel.LOW, confidence=0.85)
    noisy_repo = RepoContext(
        root=".", project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=200,
        relevant_files=[f"file_{i}.py" for i in range(15)],
    )
    mode, reasons = strategy_selector.select(task, difficulty, CONFIG, repo=noisy_repo)
    assert mode == ExecutionMode.DIRECT
    assert not any("relevant file" in r for r in reasons)
