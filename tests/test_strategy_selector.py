from codeexcellent.analyzer import risk_classifier, strategy_selector, task_analyzer
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import DifficultyScore, ExecutionMode, QualityLevel, RiskLevel

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
