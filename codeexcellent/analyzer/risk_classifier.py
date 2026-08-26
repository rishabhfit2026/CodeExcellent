"""Risk and quality-level classification (sections 17-18). Single source of
truth for RiskLevel so DifficultyScorer, QualityChecker, and StopController
all agree on what "critical" means instead of each guessing independently.
"""
from __future__ import annotations

import re

from codeexcellent.core.models import QualityLevel, RiskLevel, TaskAnalysis

# Combos that mark a task CRITICAL even if the general risk-keyword count is
# moderate -- money movement and destructive-in-production operations carry
# consequences no keyword tally alone captures.
_CRITICAL_PRIMARY = {"payment", "billing", "charge", "refund"}
_CRITICAL_DESTRUCTIVE = {"drop", "delete", "truncate", "wipe"}
_CRITICAL_TARGETS = {"database", "table", "production", "prod", "users", "customers"}


def classify_risk(task: TaskAnalysis) -> RiskLevel:
    words = set(re.findall(r"[a-z]+", task.request.lower()))

    if words & _CRITICAL_PRIMARY:
        return RiskLevel.CRITICAL
    if (words & _CRITICAL_DESTRUCTIVE) and (words & _CRITICAL_TARGETS):
        return RiskLevel.CRITICAL
    if task.risk >= 8.0:
        return RiskLevel.CRITICAL
    if task.risk >= 6.5:
        return RiskLevel.HIGH
    if task.risk >= 3.0:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def classify_quality_level(risk: RiskLevel, estimated_scope: str, testing_required: bool) -> QualityLevel:
    if risk == RiskLevel.CRITICAL:
        return QualityLevel.CRITICAL
    if risk == RiskLevel.HIGH:
        return QualityLevel.HIGH
    if risk == RiskLevel.MEDIUM or estimated_scope == "large":
        return QualityLevel.STANDARD
    if testing_required:
        return QualityLevel.BASIC
    return QualityLevel.TRIVIAL
