"""Aggregate analysis for the repeated strategy-forced benchmark
(repeated_strategy_benchmark.py). Reads the raw per-run JSON and produces a
machine-readable summary JSON with cost, correctness, reliability,
nondeterminism, and a non-subjective per-task strategy recommendation.

Does not touch production code or the raw results; purely derives
statistics from what's already recorded.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STRATEGIES = ["direct", "lightweight", "full"]
VALIDATOR_STATES = ["PASS", "FAIL", "NO_VALIDATOR", "VALIDATOR_ERROR", "TIMEOUT", "INCOMPLETE", "EXECUTION_ERROR"]

# very_hard_cross_module_redesign's validator audit: see codeexcellent/
# benchmark/tasks.py module docstring + _validate_very_hard_cross_module_
# redesign's own comment -- a prior live benchmark run found the original
# "inventory.adjust() no longer exists" check crashed on a legitimate
# refactor that dropped the internal STOCK dict while still exposing
# adjust(). The validator was already fixed (before this benchmark ran) to
# be a hybrid: a precise structural check that orders.py doesn't reach into
# inventory.STOCK directly (the literal definition of the requested change),
# paired with a behavioral check driven entirely through inventory's own
# public adjust() function rather than the validator reaching into internals
# itself. This is more robust than a pure text-match, but it still assumes
# the implementation keeps a function literally named `adjust` on
# inventory.py -- a real, if narrow, implementation assumption. Flagged here
# as PARTIALLY OBJECTIVE, not fully objective, so a PASS/FAIL on this task
# should be read with that caveat rather than as unconditional ground truth.
UNRELIABLE_VALIDATOR_TASKS = {"very_hard_cross_module_redesign"}

# hard_change_data_flow and very_hard_auth_migration have no validate() by
# design (see tasks.py) -- NO_VALIDATOR runs on these must never be counted
# as PASS; correctness on these two tasks is genuinely unknown.
NO_VALIDATOR_TASKS = {"hard_change_data_flow", "very_hard_auth_migration"}


def _mean(xs):
    return round(statistics.mean(xs), 4) if xs else None


def _median(xs):
    return round(statistics.median(xs), 4) if xs else None


def cost_analysis(results: list[dict]) -> dict:
    costs = [r["total_cost_usd"] for r in results]
    by_strategy = defaultdict(list)
    by_task = defaultdict(list)
    by_category = defaultdict(list)
    for r in results:
        by_strategy[r["strategy"]].append(r["total_cost_usd"])
        by_task[r["task_id"]].append(r["total_cost_usd"])
        by_category[r["task_category"]].append(r["total_cost_usd"])

    cost_per_validated_success = {}
    for strat in STRATEGIES:
        strat_results = [r for r in results if r["strategy"] == strat]
        total_cost = sum(r["total_cost_usd"] for r in strat_results)
        passes = sum(1 for r in strat_results if r["validator_status"] == "PASS")
        cost_per_validated_success[strat] = {
            "total_cost_usd": round(total_cost, 4),
            "validated_successes": passes,
            "cost_per_validated_success_usd": round(total_cost / passes, 4) if passes else None,
            "note": "None means zero validated successes for this strategy -- cost-per-success is undefined, not infinite or zero.",
        }

    return {
        "overall": {
            "total_usd": round(sum(costs), 4),
            "mean_usd": _mean(costs),
            "median_usd": _median(costs),
            "min_usd": round(min(costs), 4) if costs else None,
            "max_usd": round(max(costs), 4) if costs else None,
        },
        "by_strategy": {
            s: {"total_usd": round(sum(v), 4), "mean_usd": _mean(v), "median_usd": _median(v),
                "min_usd": round(min(v), 4), "max_usd": round(max(v), 4)}
            for s, v in by_strategy.items()
        },
        "by_task": {
            t: {"total_usd": round(sum(v), 4), "mean_usd": _mean(v), "median_usd": _median(v)}
            for t, v in by_task.items()
        },
        "by_category": {
            c: {"total_usd": round(sum(v), 4), "mean_usd": _mean(v), "median_usd": _median(v)}
            for c, v in by_category.items()
        },
        "cost_per_validated_success": cost_per_validated_success,
    }


def duration_analysis(results: list[dict]) -> dict:
    by_strategy = defaultdict(list)
    for r in results:
        by_strategy[r["strategy"]].append(r["duration_ms"])
    durations = [r["duration_ms"] for r in results]
    return {
        "overall": {"mean_ms": _mean(durations), "median_ms": _median(durations),
                    "min_ms": min(durations) if durations else None, "max_ms": max(durations) if durations else None},
        "by_strategy": {
            s: {"mean_ms": _mean(v), "median_ms": _median(v), "min_ms": min(v), "max_ms": max(v)}
            for s, v in by_strategy.items()
        },
    }


def outliers(results: list[dict], factor: float = 3.0) -> list[dict]:
    """A run is flagged if its cost or duration exceeds `factor`x its own
    task x strategy cell's median -- not a global threshold, so an
    inherently expensive strategy/task doesn't drown out genuine
    within-cell divergence (e.g. one run of a normally-cheap cell blowing
    up), and vice versa.
    """
    cells = defaultdict(list)
    for r in results:
        cells[(r["task_id"], r["strategy"])].append(r)

    flagged = []
    for (task_id, strategy), cell_results in cells.items():
        cell_costs = [r["total_cost_usd"] for r in cell_results]
        cell_durations = [r["duration_ms"] for r in cell_results]
        med_cost = statistics.median(cell_costs)
        med_dur = statistics.median(cell_durations)
        for r in cell_results:
            reasons = []
            if med_cost > 0 and r["total_cost_usd"] > factor * med_cost:
                reasons.append(f"cost ${r['total_cost_usd']} > {factor}x cell median ${round(med_cost, 4)}")
            if med_dur > 0 and r["duration_ms"] > factor * med_dur:
                reasons.append(f"duration {r['duration_ms']}ms > {factor}x cell median {round(med_dur)}ms")
            if reasons:
                flagged.append({
                    "task_id": task_id, "strategy": strategy, "repetition": r["repetition"],
                    "cost_usd": r["total_cost_usd"], "duration_ms": r["duration_ms"],
                    "validator_status": r["validator_status"], "reasons": reasons,
                })
    return flagged


def correctness_by_strategy(results: list[dict]) -> dict:
    """Rates are computed only over runs whose task actually has a
    validate() (validator-bearing runs) -- NO_VALIDATOR runs are reported
    separately and never folded into pass/fail rates, per the explicit
    instruction not to count NO_VALIDATOR as PASS or let it dilute the
    denominator either direction.
    """
    out = {}
    for strat in STRATEGIES:
        strat_results = [r for r in results if r["strategy"] == strat]
        validator_bearing = [r for r in strat_results if r["task_id"] not in NO_VALIDATOR_TASKS]
        n = len(validator_bearing) or 1
        state_counts = {state: sum(1 for r in validator_bearing if r["validator_status"] == state) for state in VALIDATOR_STATES}
        out[strat] = {
            "validator_bearing_runs": len(validator_bearing),
            "no_validator_runs": len(strat_results) - len(validator_bearing),
            "state_counts": state_counts,
            "pass_rate": round(state_counts["PASS"] / n, 4),
            "fail_rate": round(state_counts["FAIL"] / n, 4),
            "validator_error_rate": round(state_counts["VALIDATOR_ERROR"] / n, 4),
            "timeout_rate": round(state_counts["TIMEOUT"] / n, 4),
            "incomplete_rate": round(state_counts["INCOMPLETE"] / n, 4),
            "execution_error_rate": round(state_counts["EXECUTION_ERROR"] / n, 4),
        }
    return out


def task_by_strategy_matrix(results: list[dict], task_ids: list[str]) -> dict:
    matrix = {}
    for task_id in task_ids:
        matrix[task_id] = {}
        for strat in STRATEGIES:
            cell = [r for r in results if r["task_id"] == task_id and r["strategy"] == strat]
            state_counts = {state: sum(1 for r in cell if r["validator_status"] == state) for state in VALIDATOR_STATES}
            costs = [r["total_cost_usd"] for r in cell]
            durations = [r["duration_ms"] for r in cell]
            n = len(cell) or 1
            matrix[task_id][strat] = {
                "n_runs": len(cell),
                "state_counts": state_counts,
                "pass_fraction": round(state_counts["PASS"] / n, 4),
                "mean_cost_usd": _mean(costs), "median_cost_usd": _median(costs),
                "mean_duration_ms": _mean(durations), "median_duration_ms": _median(durations),
                "has_validator": task_id not in NO_VALIDATOR_TASKS,
                "validator_reliability_flag": "unreliable -- see UNRELIABLE_VALIDATOR_TASKS" if task_id in UNRELIABLE_VALIDATOR_TASKS else "ok",
            }
    return matrix


def strategy_benefit(matrix: dict) -> dict:
    benefit = {}
    for task_id, by_strat in matrix.items():
        d, l, f = by_strat["direct"], by_strat["lightweight"], by_strat["full"]
        d_cost, l_cost, f_cost = d["mean_cost_usd"] or 0, l["mean_cost_usd"] or 0, f["mean_cost_usd"] or 0
        benefit[task_id] = {
            "has_validator": d["has_validator"],
            "lightweight_vs_direct_pp": round((l["pass_fraction"] - d["pass_fraction"]) * 100, 1),
            "full_vs_direct_pp": round((f["pass_fraction"] - d["pass_fraction"]) * 100, 1),
            "full_vs_lightweight_pp": round((f["pass_fraction"] - l["pass_fraction"]) * 100, 1),
            "lightweight_cost_multiple_vs_direct": round(l_cost / d_cost, 2) if d_cost else None,
            "full_cost_multiple_vs_direct": round(f_cost / d_cost, 2) if d_cost else None,
            "full_cost_multiple_vs_lightweight": round(f_cost / l_cost, 2) if l_cost else None,
        }
    return benefit


def recommend(matrix: dict, tolerance_runs: int = 1) -> dict:
    """Mechanical, pre-specified rule -- NOT a subjective judgment call:

    - If the task has no validator: recommend the cheapest strategy by
      median cost, explicitly labelled "cost-only -- correctness unknown".
    - If the task has a validator: best_pass = the highest PASS count among
      the three forced strategies. Candidates = every strategy whose PASS
      count is within `tolerance_runs` of best_pass. Recommended = the
      cheapest (by median cost) among the candidates.
    - If best_pass == 0 (no strategy ever passed): "none -- inconclusive",
      since cost-effectiveness of zero correctness is not a meaningful
      comparison.
    """
    out = {}
    for task_id, by_strat in matrix.items():
        has_validator = by_strat["direct"]["has_validator"]
        if not has_validator:
            cheapest = min(STRATEGIES, key=lambda s: (by_strat[s]["median_cost_usd"] or float("inf")))
            out[task_id] = {
                "rule": "cost-only -- correctness unknown (no validator)",
                "recommended_strategy": cheapest,
                "cheapest_strategy": cheapest,
                "best_pass_count_strategy": None,
            }
            continue

        pass_counts = {s: by_strat[s]["state_counts"]["PASS"] for s in STRATEGIES}
        best_pass = max(pass_counts.values())
        if best_pass == 0:
            cheapest = min(STRATEGIES, key=lambda s: (by_strat[s]["median_cost_usd"] or float("inf")))
            out[task_id] = {
                "rule": f"none -- inconclusive (0 PASS across all strategies; cheapest by cost is {cheapest} but correctness was never achieved)",
                "recommended_strategy": "none -- inconclusive",
                "cheapest_strategy": cheapest,
                "best_pass_count_strategy": None,
            }
            continue

        candidates = [s for s in STRATEGIES if pass_counts[s] >= best_pass - tolerance_runs]
        recommended = min(candidates, key=lambda s: (by_strat[s]["median_cost_usd"] or float("inf")))
        best_strat = max(STRATEGIES, key=lambda s: pass_counts[s])
        out[task_id] = {
            "rule": f"cheapest strategy (by median cost) among those within {tolerance_runs} PASS run(s) of the best "
                    f"PASS count ({best_pass}); pass_counts={pass_counts}",
            "recommended_strategy": recommended,
            "cheapest_strategy": min(STRATEGIES, key=lambda s: (by_strat[s]["median_cost_usd"] or float("inf"))),
            "best_pass_count_strategy": best_strat,
        }
    return out


def isolation_check(manifest: dict, results: list[dict]) -> dict:
    by_task = defaultdict(set)
    for r in results:
        by_task[r["task_id"]].add(r["fixture_hash"])
    return {
        "manifest_completed_runs": manifest.get("completed_runs"),
        "len_results": len(results),
        "manifest_seen_roots_all_unique": manifest.get("seen_roots_all_unique"),
        "fixture_hash_unique_per_task": {t: len(h) for t, h in by_task.items()},
        "all_tasks_had_single_fixture_hash": all(len(h) <= 1 for h in by_task.values()),
    }


def main(path: Path) -> dict:
    data = json.loads(path.read_text())
    manifest = data["manifest"]
    results = data["results"]
    task_ids = manifest["tasks"]

    matrix = task_by_strategy_matrix(results, task_ids)
    summary = {
        "source_file": str(path),
        "manifest": manifest,
        "isolation_check": isolation_check(manifest, results),
        "cost": cost_analysis(results),
        "duration": duration_analysis(results),
        "outliers": outliers(results),
        "correctness_by_strategy": correctness_by_strategy(results),
        "task_by_strategy_matrix": matrix,
        "strategy_benefit": strategy_benefit(matrix),
        "recommendation": recommend(matrix),
        "unreliable_validator_tasks": sorted(UNRELIABLE_VALIDATOR_TASKS),
        "no_validator_tasks": sorted(NO_VALIDATOR_TASKS),
    }
    return summary


if __name__ == "__main__":
    in_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "benchmarks" / "adaptive_strategy_repeated_results.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO_ROOT / "benchmarks" / "adaptive_strategy_repeated_summary.json"
    result = main(in_path)
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"Summary written to {out_path}")
