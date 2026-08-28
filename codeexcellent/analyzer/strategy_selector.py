"""StrategySelector (section 10-11): the authoritative choice of execution
mode, separate from the raw difficulty number. Three things a pure
difficulty threshold can't express on its own:

- Planning is cost-aware (section 11): it's only worth doing when its
  estimated benefit (reduced execution risk from complexity/low confidence/
  architecture impact) exceeds its cost (one extra Claude call + context).
- CRITICAL risk always forces at least a review, independent of how "hard"
  the task numerically looks -- a one-line change to payment logic is still
  CRITICAL even if difficulty comes out at 3/10 (section 18).
- Uncertainty itself is a reason for caution (added after a benchmark audit):
  when the difficulty estimate's own confidence is low, `direct` execution
  is a bet that the heuristic guessed right with no safety net if it didn't.
  Below `low_confidence_forces_at_least`, this is now a hard floor on the
  selected mode, not just a soft addition to planning_benefit -- a case
  where the benefit math alone doesn't quite cross the cost threshold
  should not still end up at `direct` when the estimate itself says it
  isn't sure.

Repository context now also has a direct, independent vote: the number of
files `find_relevant_files` actually identified as relevant to the task
(`repo.relevant_files`) is real evidence from the repo scan that a change
touches more than one file, separate from and corroborating the task text's
own `cross_module_signal`. Two files that talk about "the same thing" from
the text alone, and two files independently discovered by the repo scanner
to both be relevant, are different strengths of evidence, and both should
count.

## FULL vs LIGHTWEIGHT (added after a live A/B benchmark)

Difficulty alone used to decide FULL vs LIGHTWEIGHT once a task cleared the
planning-benefit threshold: `difficulty.value >= full_at_or_above -> FULL`.
A live benchmark showed this reliably added an upfront planning call plus
(via quality_checker's now-removed independent difficulty>=6 review trigger)
a review call to every hard/very_hard task, at 3-13x the cost of the
cheaper strategy that had already produced a validated-correct result for
the same task before difficulty scoring was fixed -- with zero cases where
the extra process turned a failure into a success. "The task is hard" is
evidence execution needs *some* extra care; it is not, by itself, evidence
that a full plan-then-review pass specifically is what that task needs.

FULL is now reserved for cases with a *specific* reason planning would
reduce risk more than validation-driven recovery (see engine.py's retry
loop) could recover from cheaply after the fact:
  - risk is HIGH/CRITICAL (handled above, never reaches this branch), or
  - confidence is low enough that the difficulty estimate itself might be
    wrong (a separate, lower bar than the hard low-confidence floor below --
    this one only affects FULL-vs-LIGHTWEIGHT, not DIRECT-vs-something), or
  - the task genuinely names several distinct files/modules (a high
    `cross_module_signal`) AND no reliable test suite exists to cheaply
    catch a wrong first attempt -- when a real validator IS available, a
    cheaper first attempt plus targeted recovery is preferred over paying
    for a plan upfront, since failure can be caught and fixed without ever
    having guessed wrong about how much planning was needed.

Deliberately `cross_module_signal` here, not `architecture_signal`: the
latter is mostly keyword density (auth/API/security vocabulary) and is
already a poor proxy for "this needs upfront coordination planning" -- a
single-file task about authentication scores high on it without actually
touching more than one file (a live benchmark case: `hard_add_auth`, one
file, was already correctly solved without a plan phase before difficulty
scoring was fixed). `cross_module_signal` only rises when the request
itself names multiple distinct files, which is a much more specific signal
that a plan genuinely has multiple things to coordinate. A task that only
scores high on architecture_signal still gets caught by
`failure_classifier`'s post-hoc structural-completeness check (which does
use both signals) if its single-file attempt turns out to be incomplete --
recovery there is what's supposed to catch that, not a mandatory plan
upfront on every architecturally-flavored task.

Otherwise, a hard/very_hard task defaults to LIGHTWEIGHT: execute at the
band's already-larger effort/budget, validate, and let the engine's
failure-classification-driven recovery escalate only if evidence (an actual
failure, diagnosed) says more process is warranted.
"""
from __future__ import annotations

from codeexcellent.core.models import DifficultyScore, ExecutionMode, RepoContext, RiskLevel, TaskAnalysis

# Modes considered "at least as cautious as" each other, most permissive
# first -- used to enforce a floor without ever *downgrading* a mode a
# different rule already selected for a stronger reason (e.g. CRITICAL risk).
_CAUTION_ORDER = [
    ExecutionMode.DIRECT,
    ExecutionMode.LIGHTWEIGHT,
    ExecutionMode.REVIEW_REQUIRED,
    ExecutionMode.FULL,
]


def _at_least(mode: ExecutionMode, minimum: ExecutionMode) -> ExecutionMode:
    if _CAUTION_ORDER.index(mode) >= _CAUTION_ORDER.index(minimum):
        return mode
    return minimum


def select(
    task: TaskAnalysis, difficulty: DifficultyScore, config: dict, repo: RepoContext | None = None,
) -> tuple[ExecutionMode, list[str]]:
    thresholds = config.get("planning_thresholds", {})
    strategy_cfg = config.get("strategy", {})
    confidence_cfg = config.get("confidence", {})
    reasons: list[str] = []

    if strategy_cfg.get("critical_forces_review", True) and difficulty.risk_level == RiskLevel.CRITICAL:
        if difficulty.value >= thresholds.get("lightweight_below", 6):
            reasons.append("risk is CRITICAL and difficulty is high -- full plan with mandatory review")
            return ExecutionMode.FULL, reasons
        reasons.append("risk is CRITICAL -- mandatory review even though difficulty/scope is otherwise modest")
        return ExecutionMode.REVIEW_REQUIRED, reasons

    planning_cost = float(strategy_cfg.get("planning_cost", 1.5))
    planning_benefit = 0.0

    if difficulty.value >= thresholds.get("lightweight_below", 6):
        planning_benefit += 4.0

    planning_benefit += task.architecture_signal * 0.3
    planning_benefit += task.cross_module_signal * 0.3

    # Repository context, independent of the text-based signals above: real
    # evidence from the repo scan, not just what the request text implies.
    #
    # Deliberately bounded to a small window, not raw file count: caught by
    # testing against this project's own (real, established) repo, where a
    # trivial one-line rename request matched 15 "relevant" files -- not
    # because the task is genuinely large, but because the content-scan
    # fallback in find_relevant_files() matched the query's generic words
    # against example strings sitting inside this repo's own test files.
    # That's a real characteristic of an established codebase (tests
    # reference example phrases, docs mention many things in passing), not
    # a defect introduced here -- but it means a LARGE relevant-file count
    # is more often noise than signal, while a SMALL, precise count (a
    # genuine multi-file task typically touches 2-5 files, not 15) is real
    # evidence. Below the minimum, there's nothing to corroborate; above
    # the maximum, the match is more likely broad/imprecise than a true
    # multi-file task.
    if repo is not None and repo.relevant_files:
        relevant_count = len(repo.relevant_files)
        min_count = int(strategy_cfg.get("relevant_files_min_count", 2))
        max_count = int(strategy_cfg.get("relevant_files_max_count", 6))
        if min_count <= relevant_count <= max_count:
            extra_files = relevant_count - 1
            per_file = float(strategy_cfg.get("relevant_files_benefit_per_file", 0.5))
            cap = float(strategy_cfg.get("relevant_files_benefit_cap", 2.0))
            files_benefit = min(cap, extra_files * per_file)
            if files_benefit > 0:
                planning_benefit += files_benefit
                reasons.append(f"repository scan found {relevant_count} relevant file(s)")

    low_conf_threshold = float(confidence_cfg.get("low_threshold", 0.5))
    if difficulty.confidence < low_conf_threshold:
        confidence_gap = low_conf_threshold - difficulty.confidence
        planning_benefit += confidence_gap * 6.0
        reasons.append(f"confidence is low ({difficulty.confidence}) -- planning reduces execution risk")

    if difficulty.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        planning_benefit += 3.0

    if planning_benefit <= planning_cost:
        reasons.append(
            f"planning benefit ({planning_benefit:.1f}) does not exceed its cost "
            f"({planning_cost:.1f}) -- executing directly"
        )
        mode = ExecutionMode.DIRECT
    else:
        full_threshold = thresholds.get("full_at_or_above", thresholds.get("lightweight_below", 6))
        full_confidence_threshold = float(strategy_cfg.get("full_requires_confidence_below", 0.6))
        full_structural_threshold = float(strategy_cfg.get("full_requires_structural_signal_at_or_above", 7.0))
        has_reliable_validator = bool(difficulty.testing_required and repo is not None and repo.test_dirs)
        structural_signal = task.cross_module_signal

        full_reason = None
        if difficulty.value >= full_threshold:
            if difficulty.confidence < full_confidence_threshold:
                full_reason = (
                    f"difficulty is high and confidence ({difficulty.confidence}) is low enough that the "
                    "estimate itself may be wrong -- planning upfront reduces that risk"
                )
            elif structural_signal >= full_structural_threshold and not has_reliable_validator:
                full_reason = (
                    f"difficulty is high, structural signal is high ({structural_signal:.1f}/10), and no "
                    "reliable test suite exists to cheaply catch a wrong first attempt -- planning upfront "
                    "is cheaper than an unverifiable guess"
                )

        if full_reason:
            reasons.append(f"planning benefit ({planning_benefit:.1f}) exceeds its cost -- {full_reason} -- full plan")
            mode = ExecutionMode.FULL
        else:
            reasons.append(
                f"planning benefit ({planning_benefit:.1f}) exceeds its cost ({planning_cost:.1f}) -- lightweight "
                "plan (execute at higher effort and validate, rather than paying for upfront planning without a "
                "specific reason to expect it's needed)"
            )
            mode = ExecutionMode.LIGHTWEIGHT

    # Hard uncertainty floor: below this confidence, `direct` is never the
    # answer, regardless of what the cost/benefit arithmetic above concluded
    # -- a low-confidence estimate has no safety net if it's wrong, and
    # section 8 of the audit asks explicitly for this to be a floor, not
    # just an additive nudge (which the confidence_gap bonus above already
    # is, and which can fail to cross the cost threshold on its own).
    forced_minimum_name = strategy_cfg.get("low_confidence_forces_at_least")
    if forced_minimum_name and difficulty.confidence < low_conf_threshold and mode == ExecutionMode.DIRECT:
        forced_minimum = ExecutionMode(forced_minimum_name)
        if _at_least(mode, forced_minimum) != mode:
            reasons.append(
                f"confidence ({difficulty.confidence}) is below the safety threshold -- "
                f"direct execution is not used when the estimate itself is uncertain"
            )
            mode = forced_minimum

    return mode, reasons
