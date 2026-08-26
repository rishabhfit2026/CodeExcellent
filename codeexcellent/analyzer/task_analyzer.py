"""Turns raw request text into a TaskAnalysis using deterministic keyword/shape
heuristics. This is intentionally simple (section 24/25 of the spec): start
with heuristics, let DifficultyScorer + repo inspection refine the estimate,
and leave room for a learned estimator later without changing the interface.
"""
from __future__ import annotations

import re

from codeexcellent.core.models import TaskAnalysis

_TRIVIAL_VERBS = {"rename", "typo", "fix typo", "capitalize", "reformat", "format"}
_SMALL_VERBS = {"fix", "add", "update", "remove", "delete", "adjust", "tweak"}
_LARGE_VERBS = {
    "refactor", "migrate", "redesign", "rewrite", "rearchitect",
    "restructure", "overhaul",
}
_ARCH_KEYWORDS = {
    "architecture", "authentication", "auth", "database", "schema",
    "api", "endpoint", "migration", "microservice", "protocol", "oauth",
    "jwt", "queue", "cache", "deployment", "infrastructure",
}
_RISK_KEYWORDS = {
    "auth", "authentication", "password", "payment", "billing", "security",
    "production", "prod", "database", "delete", "drop", "migration",
    "encryption", "secret", "credential", "token", "permission",
}
_TEST_KEYWORDS = {"test", "tests", "testing", "coverage", "unit test", "integration test"}
_AMBIGUITY_KEYWORDS = {
    "somehow", "something", "etc", "improve", "better", "nicer", "cleanup",
    "clean up", "optimize", "modernize", "various", "stuff",
}
_BACKWARD_COMPAT_KEYWORDS = {"backward compat", "backwards compat", "preserve", "without breaking"}


def _count_matches(text: str, keywords: set[str]) -> tuple[int, list[str]]:
    hits = [kw for kw in keywords if kw in text]
    return len(hits), hits


def _operation_count(text: str) -> int:
    # Rough proxy: count coordinating conjunctions / sentence-like separators
    # that suggest multiple distinct asks bundled into one request.
    separators = re.findall(r"\band\b|,|\n|;", text)
    return max(1, len(separators) + 1) if text.strip() else 0


def analyze(request: str) -> TaskAnalysis:
    text = request.lower().strip()
    word_count = len(text.split())

    trivial_hits, _ = _count_matches(text, _TRIVIAL_VERBS)
    small_hits, _ = _count_matches(text, _SMALL_VERBS)
    large_hits, large_kw = _count_matches(text, _LARGE_VERBS)
    arch_hits, arch_kw = _count_matches(text, _ARCH_KEYWORDS)
    risk_hits, risk_kw = _count_matches(text, _RISK_KEYWORDS)
    test_hits, test_kw = _count_matches(text, _TEST_KEYWORDS)
    ambiguity_hits, amb_kw = _count_matches(text, _AMBIGUITY_KEYWORDS)
    compat_hits, compat_kw = _count_matches(text, _BACKWARD_COMPAT_KEYWORDS)

    op_count = _operation_count(text)

    # --- task_complexity: driven by verb class + number of bundled operations
    if large_hits > 0:
        complexity = 7.0
    elif trivial_hits > 0 and small_hits == 0 and large_hits == 0:
        complexity = 1.0
    elif small_hits > 0:
        complexity = 3.0
    else:
        complexity = 4.0
    complexity += min(3.0, (op_count - 1) * 1.0)
    complexity = min(10.0, complexity)

    # --- scope: word count + operation count + large-verb signal
    scope = min(10.0, word_count / 8.0 + (op_count - 1) * 1.5 + (3.0 if large_hits else 0.0))

    # --- risk: keyword hits + backward-compat constraints raise the stakes
    risk = min(10.0, risk_hits * 2.5 + compat_hits * 2.0)

    # --- testing signal: explicit mention, or implied by risk/scope
    testing_signal = min(10.0, test_hits * 4.0 + (2.0 if scope > 5 else 0.0) + (2.0 if risk > 4 else 0.0))

    # --- architecture impact
    architecture_signal = min(10.0, arch_hits * 2.0 + (3.0 if large_hits else 0.0))

    # --- ambiguity: vague language, or a request too short to be specific
    ambiguity = min(10.0, ambiguity_hits * 3.0 + (2.0 if word_count <= 3 else 0.0))
    if re.search(r"['\"`].+?['\"`]", request) or re.search(r"\b\w+\(\)", request):
        ambiguity = max(0.0, ambiguity - 2.0)  # quoted identifiers/functions = specific

    keywords_matched = large_kw + arch_kw + risk_kw + test_kw + amb_kw + compat_kw

    return TaskAnalysis(
        request=request,
        task_complexity=round(complexity, 2),
        scope=round(scope, 2),
        risk=round(risk, 2),
        testing_signal=round(testing_signal, 2),
        architecture_signal=round(architecture_signal, 2),
        ambiguity=round(ambiguity, 2),
        operation_count=op_count,
        keywords_matched=sorted(set(keywords_matched)),
    )
