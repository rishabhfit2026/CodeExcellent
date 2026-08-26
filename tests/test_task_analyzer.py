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
