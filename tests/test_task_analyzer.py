from codeexcellent.analyzer import task_analyzer


def test_trivial_rename_is_low_complexity():
    result = task_analyzer.analyze("Rename the variable userName to username.")
    assert result.task_complexity <= 3.0
    assert result.scope <= 5.0


def test_large_refactor_has_high_complexity():
    result = task_analyzer.analyze(
        "Refactor the authentication system, migrate JWT authentication to OAuth, "
        "preserve backward compatibility, update tests, and make sure existing APIs "
        "continue working."
    )
    assert result.task_complexity >= 6.0
    assert result.risk > 0
    assert result.operation_count >= 3


def test_risk_keywords_raise_risk_score():
    low_risk = task_analyzer.analyze("Add a footer to the homepage.")
    high_risk = task_analyzer.analyze("Change how passwords are hashed in production.")
    assert high_risk.risk > low_risk.risk


def test_ambiguous_short_request_has_higher_ambiguity():
    vague = task_analyzer.analyze("Make it better")
    specific = task_analyzer.analyze("Fix the off-by-one error in paginate()")
    assert vague.ambiguity > specific.ambiguity


# --- regression coverage added after the live-benchmark adaptive-scoring audit ---

def test_cross_module_signal_is_zero_for_a_single_file_mention():
    result = task_analyzer.analyze("Add a new health_check function to api.py")
    assert result.cross_module_signal == 0.0


def test_cross_module_signal_rises_for_multiple_distinct_files():
    result = task_analyzer.analyze("Redesign orders.py and inventory.py to remove the tight coupling between them")
    assert result.cross_module_signal > 0.0


def test_cross_module_signal_does_not_false_positive_on_a_file_and_its_own_test():
    # A source file plus its own test file describes ONE unit of work, not
    # cross-module coupling -- this would otherwise inflate every
    # test-writing task's difficulty for the wrong reason.
    result = task_analyzer.analyze("Add unit tests for calculator.py, placed in test_calculator.py")
    assert result.cross_module_signal == 0.0


def test_migrate_verb_form_is_recognized_not_just_migration_noun_form():
    # Regression: "migration" was in the risk keyword list but "migrate" was
    # not, and "migrate" is not a substring of "migration" (or vice versa),
    # so a request phrased with the verb scored zero risk from this signal.
    migrate_phrasing = task_analyzer.analyze("Migrate the authentication system to OAuth2")
    assert migrate_phrasing.risk > 0
    assert migrate_phrasing.architecture_signal > 0


def test_preserving_form_is_recognized_not_just_preserve_form():
    # Regression: "preserve" was in the backward-compat keyword list but
    # "preserving" is not a substring of "preserve" (both end differently),
    # so "preserving X" scored no backward-compat signal at all.
    result = task_analyzer.analyze("Migrate the service while preserving existing behavior for callers")
    assert result.testing_signal > 0


def test_generic_change_verb_is_not_treated_as_a_large_refactor():
    # "change" must NOT be classified as a large/high-complexity verb --
    # "change a constant" and "change the processing model" use the same
    # verb but are very different in difficulty; the verb alone can't and
    # shouldn't discriminate that.
    trivial_change = task_analyzer.analyze("Change the MAX_RETRIES constant from 3 to 5")
    assert trivial_change.task_complexity < 6.0


def test_architecture_vocabulary_covers_module_and_data_flow_terms():
    # General software-architecture and data-flow vocabulary that wasn't
    # represented at all before -- not tied to any specific benchmark task.
    module_task = task_analyzer.analyze("Split this into separate modules with a clean interface")
    assert module_task.architecture_signal > 0

    pipeline_task = task_analyzer.analyze("Change the pipeline to process records in batches")
    assert pipeline_task.architecture_signal > 0
