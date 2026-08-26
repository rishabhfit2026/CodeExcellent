from codeexcellent.analyzer import risk_classifier, task_analyzer
from codeexcellent.core.models import QualityLevel, RiskLevel


def test_payment_keyword_is_critical():
    task = task_analyzer.analyze("Update the payment charge amount calculation")
    assert risk_classifier.classify_risk(task) == RiskLevel.CRITICAL


def test_destructive_production_combo_is_critical():
    task = task_analyzer.analyze("Drop the production database table for old sessions")
    assert risk_classifier.classify_risk(task) == RiskLevel.CRITICAL


def test_readme_typo_is_low_risk():
    task = task_analyzer.analyze("Fix a typo in the README")
    assert risk_classifier.classify_risk(task) == RiskLevel.LOW


def test_critical_risk_yields_critical_quality_level():
    task = task_analyzer.analyze("Update the payment charge amount calculation")
    risk = risk_classifier.classify_risk(task)
    assert risk_classifier.classify_quality_level(risk, "small", False) == QualityLevel.CRITICAL


def test_low_risk_small_scope_no_testing_yields_trivial_quality_level():
    assert risk_classifier.classify_quality_level(RiskLevel.LOW, "small", False) == QualityLevel.TRIVIAL
