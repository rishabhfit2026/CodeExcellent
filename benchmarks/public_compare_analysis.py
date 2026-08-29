"""Aggregate analysis for public_compare_benchmark.py's raw results:
CodeExcellent (real adaptive orchestration) vs raw `claude` CLI (no
orchestration). Produces a machine-readable summary plus prints a
human-readable digest.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATEGORY_ORDER = ["trivial", "easy", "medium", "hard", "very_hard"]


def _mean(xs):
    return round(statistics.mean(xs), 4) if xs else None


def _median(xs):
    return round(statistics.median(xs), 4) if xs else None


def main(path: Path) -> dict:
    data = json.loads(path.read_text())
    manifest = data["manifest"]
    results = data["results"]

    ce_costs = [r["ce_cost_usd"] for r in results]
    raw_costs = [r["raw_cost_usd"] for r in results]
    ce_durs = [r["ce_duration_ms"] for r in results]
    raw_durs = [r["raw_duration_ms"] for r in results]

    overall = {
        "n_runs": len(results),
        "ce_total_cost_usd": round(sum(ce_costs), 4),
        "raw_total_cost_usd": round(sum(raw_costs), 4),
        "ce_mean_cost_usd": _mean(ce_costs), "raw_mean_cost_usd": _mean(raw_costs),
        "ce_median_cost_usd": _median(ce_costs), "raw_median_cost_usd": _median(raw_costs),
        "ce_mean_duration_ms": _mean(ce_durs), "raw_mean_duration_ms": _mean(raw_durs),
        "ce_median_duration_ms": _median(ce_durs), "raw_median_duration_ms": _median(raw_durs),
        "cost_ratio_ce_over_raw_total": round(sum(ce_costs) / sum(raw_costs), 3) if sum(raw_costs) else None,
    }

    # Validated pass rate -- only over tasks with a real validate(), and
    # only counting a PASS where BOTH sides could even be judged (raw and
    # CE each independently validated in their own fixture copy).
    validator_bearing = [r for r in results if r["has_validator"]]
    ce_pass = sum(1 for r in validator_bearing if r["ce_validated"] is True)
    raw_pass = sum(1 for r in validator_bearing if r["raw_validated"] is True)
    n_val = len(validator_bearing) or 1
    correctness = {
        "validator_bearing_runs": len(validator_bearing),
        "ce_pass_count": ce_pass, "ce_pass_rate": round(ce_pass / n_val, 4),
        "raw_pass_count": raw_pass, "raw_pass_rate": round(raw_pass / n_val, 4),
        "raw_call_failure_count": sum(1 for r in results if not r["raw_success"]),
        "ce_execution_error_count": sum(1 for r in results if r["ce_validator_status"] == "EXECUTION_ERROR"),
    }

    by_category = {}
    cat_groups = defaultdict(list)
    for r in results:
        cat_groups[r["task_category"]].append(r)
    for cat in CATEGORY_ORDER:
        if cat not in cat_groups:
            continue
        rs = cat_groups[cat]
        vb = [r for r in rs if r["has_validator"]]
        n = len(vb) or 1
        by_category[cat] = {
            "n_runs": len(rs),
            "ce_mean_cost_usd": _mean([r["ce_cost_usd"] for r in rs]),
            "raw_mean_cost_usd": _mean([r["raw_cost_usd"] for r in rs]),
            "ce_mean_duration_ms": _mean([r["ce_duration_ms"] for r in rs]),
            "raw_mean_duration_ms": _mean([r["raw_duration_ms"] for r in rs]),
            "ce_pass_rate": round(sum(1 for r in vb if r["ce_validated"] is True) / n, 4) if vb else None,
            "raw_pass_rate": round(sum(1 for r in vb if r["raw_validated"] is True) / n, 4) if vb else None,
        }

    by_task = {}
    task_groups = defaultdict(list)
    for r in results:
        task_groups[r["task_id"]].append(r)
    for task_id, rs in task_groups.items():
        vb = [r for r in rs if r["has_validator"]]
        n = len(vb) or 1
        by_task[task_id] = {
            "category": rs[0]["task_category"],
            "n_runs": len(rs),
            "has_validator": rs[0]["has_validator"],
            "ce_mean_cost_usd": _mean([r["ce_cost_usd"] for r in rs]),
            "raw_mean_cost_usd": _mean([r["raw_cost_usd"] for r in rs]),
            "ce_median_duration_ms": _median([r["ce_duration_ms"] for r in rs]),
            "raw_median_duration_ms": _median([r["raw_duration_ms"] for r in rs]),
            "ce_pass_count": sum(1 for r in vb if r["ce_validated"] is True),
            "raw_pass_count": sum(1 for r in vb if r["raw_validated"] is True),
            "ce_modes_used": sorted({r["ce_mode"] for r in rs}),
        }

    return {
        "source_file": str(path),
        "manifest": manifest,
        "overall": overall,
        "correctness": correctness,
        "by_category": by_category,
        "by_task": by_task,
    }


if __name__ == "__main__":
    in_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "benchmarks" / "public_compare_results.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO_ROOT / "benchmarks" / "public_compare_summary.json"
    result = main(in_path)
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"Summary written to {out_path}")
