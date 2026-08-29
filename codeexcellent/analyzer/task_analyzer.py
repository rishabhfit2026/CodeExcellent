"""Turns raw request text into a TaskAnalysis using deterministic keyword/shape
heuristics. This is intentionally simple (section 24/25 of the spec): start
with heuristics, let DifficultyScorer + repo inspection refine the estimate,
and leave room for a learned estimator later without changing the interface.

Keyword sets were audited and extended against real under-prediction cases
found in live benchmarking (not tuned to specific task names): verb-form
gaps ("migrate" was missing even though "migration" was present; "preserving"
was missing even though "preserve" was present -- neither is a substring of
the other, so the existing substring-based matching genuinely never saw
them), and two vocabulary gaps entirely -- general software-architecture
terms (module/package/interface/coupling) and data-flow/processing-model
terms (pipeline/batch/concurrency/async/stream) that weren't represented at
all. `_count_matches` itself is unchanged: switching it to word-boundary
regex was considered and rejected -- it would break the *intentional*
prefix-match trick where "backward compat" is meant to match inside
"backward compatibility".
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
    # verb-form counterpart of "migration", missed by substring matching
    "migrate", "migrating",
    # general architecture vocabulary -- module/interface boundaries
    "module", "modules", "package", "packages", "interface", "interfaces",
    "coupling", "decouple", "decoupling",
    # data-flow / processing-model vocabulary
    "pipeline", "batch", "batches", "concurrency", "concurrent",
    "async", "asynchronous", "stream", "streaming",
}
_RISK_KEYWORDS = {
    "auth", "authentication", "password", "payment", "billing", "security",
    "production", "prod", "database", "delete", "drop", "migration",
    "encryption", "secret", "credential", "token", "permission",
    "migrate", "migrating",
    # standard security vocabulary that "encryption" alone didn't cover
    "hash", "hashing", "encrypt", "decrypt",
}
_TEST_KEYWORDS = {"test", "tests", "testing", "coverage", "unit test", "integration test"}
_AMBIGUITY_KEYWORDS = {
    "somehow", "something", "etc", "improve", "better", "nicer", "cleanup",
    "clean up", "optimize", "modernize", "various", "stuff",
}
_BACKWARD_COMPAT_KEYWORDS = {
    "backward compat", "backwards compat", "preserve", "without breaking",
    "preserving", "preserved",
}

# General-purpose file reference detector -- deliberately broad (common
# source extensions), not tied to any specific task's filenames.
_FILE_REF_PATTERN = re.compile(
    r"\b[\w\-]+\.(?:py|js|jsx|ts|tsx|go|rs|java|rb|php|c|cpp|h|hpp|cs)\b", re.IGNORECASE,
)


def _count_matches(text: str, keywords: set[str]) -> tuple[int, list[str]]:
    hits = [kw for kw in keywords if kw in text]
    return len(hits), hits


def _operation_count(text: str) -> int:
    # Rough proxy: count coordinating conjunctions / sentence-like separators
    # that suggest multiple distinct asks bundled into one request.
    separators = re.findall(r"\band\b|,|\n|;", text)
    return max(1, len(separators) + 1) if text.strip() else 0


def _count_distinct_modules(text: str) -> int:
    """How many distinct source files/modules the request names. A file and
    its own test counterpart ("foo.py" / "test_foo.py") are collapsed to one
    -- that describes one unit of work (implementation + its test), not
    cross-module coupling, and would otherwise be a systematic false
    positive on every test-writing task.
    """
    files = {m.group(0).lower() for m in _FILE_REF_PATTERN.finditer(text)}
    core_stems: set[str] = set()
    for f in files:
        stem = f.rsplit(".", 1)[0]
        if stem.startswith("test_"):
            stem = stem[len("test_"):]
        elif stem.endswith("_test"):
            stem = stem[: -len("_test")]
        core_stems.add(stem)
    return len(core_stems)


# A cheap pre-flight guard against spending a real Claude call on input that
# was never a coding task to begin with -- found via dogfooding: typing a
# plain greeting into the interactive REPL ("hey how are you") scored as a
# low-difficulty DIRECT task, spent a real call trying to "implement" it,
# and reported a confusing INCOMPLETE failure for a request that was never
# asking for a code change. Deliberately narrow: matched only when NOTHING
# in the heuristic analysis suggests an actionable task (no verb/arch/risk/
# test/ambiguity keyword, no file reference) AND the text itself reads as
# conversational -- so a real terse request with no verb keyword ("the
# login is broken") is never caught, only text that also superficially
# reads as small talk is.
_CHITCHAT_PATTERNS = re.compile(
    r"^(hey|hi|hello|yo+|sup|hiya|howdy)\b"
    r"|\bhow('?s| is| are) (it|you|things|everything) (going|doing)?\b"
    r"|\bwhat'?s up\b"
    r"|^who are you\??$"
    r"|^what (are|can) you (do|help with)\??$"
    r"|^(thanks|thank you|thx|ty|cheers)\b"
    r"|^(ok|okay|cool|nice|great|lol|haha|got it)(\s+(ok|okay|cool|nice|great))?\.?!?$"
    r"|^(bye|goodbye|see ya|see you|later)\b",
    re.IGNORECASE,
)


def is_chitchat(request: str, task: TaskAnalysis) -> bool:
    text = request.lower().strip()
    if not text:
        return True
    if task.category != "general" or task.keywords_matched:
        return False
    if _FILE_REF_PATTERN.search(text):
        return False
    return bool(_CHITCHAT_PATTERNS.search(text))


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
    distinct_modules = _count_distinct_modules(text)

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

    # --- testing signal: explicit mention, or implied by risk/scope/compat
    # (preserving behavior during a change is exactly when regression tests
    # matter most, even if the request never says the word "test").
    testing_signal = min(
        10.0,
        test_hits * 4.0 + (2.0 if scope > 5 else 0.0) + (2.0 if risk > 4 else 0.0)
        + (2.0 if compat_hits > 0 else 0.0),
    )

    # --- architecture impact
    architecture_signal = min(10.0, arch_hits * 2.0 + (3.0 if large_hits else 0.0))

    # --- cross-module signal: 0 for a single (or zero) file mentioned, rising
    # for each additional distinct module named -- a request that explicitly
    # spans multiple files carries coordination complexity a single-file
    # task doesn't, regardless of how "big" any one file's change looks.
    cross_module_signal = min(10.0, max(0, (distinct_modules - 1)) * 4.0)

    # --- ambiguity: vague language, or a request too short to be specific
    ambiguity = min(10.0, ambiguity_hits * 3.0 + (2.0 if word_count <= 3 else 0.0))
    if re.search(r"['\"`].+?['\"`]", request) or re.search(r"\b\w+\(\)", request):
        ambiguity = max(0.0, ambiguity - 2.0)  # quoted identifiers/functions = specific

    keywords_matched = large_kw + arch_kw + risk_kw + test_kw + amb_kw + compat_kw

    if large_hits > 0:
        category = "large_refactor"
    elif trivial_hits > 0:
        category = "rename_or_typo"
    elif small_hits > 0:
        category = "small_change"
    else:
        category = "general"

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
        category=category,
        cross_module_signal=round(cross_module_signal, 2),
    )
