"""Runs the benchmark suite (section 20) and, optionally, an A/B comparison
against calling Claude directly with no orchestration (section 21) -- same
tasks, same fixture repos, so the two are actually comparable.

Safety: this module never decides on its own to spend real money. The
caller (CLI) is responsible for only invoking `run_raw` / passing a real
ClaudeRunner when the user has explicitly opted into --live, and for
warning about cost before doing so.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from codeexcellent.benchmark.tasks import ALL_TASKS, BenchmarkTask
from codeexcellent.claude.engine import CodingEngine
from codeexcellent.core.engine import run as run_engine
from codeexcellent.core.models import ExecutionMode
from codeexcellent.core.platform_utils import resolve_executable

EngineFactory = Callable[[], CodingEngine]
StepCallback = Callable[[str], None]


@dataclass
class BenchmarkResult:
    """One task's result. Correctness, efficiency, quality, and prediction
    metrics are kept as separate fields (not folded into one score) per the
    benchmarking phase's instruction not to conflate "was it correct" with
    "did CodeExcellent think it was hard" -- those answer different
    questions and must stay independently inspectable.
    """

    task_id: str
    category: str
    status: str  # CORRECTNESS: COMPLETE/INCOMPLETE/FAILED/BLOCKED/CANCELLED
    validated: bool | None  # CORRECTNESS: None = task has no automated validate() check
    validation_message: str | None
    cost_usd: float  # EFFICIENCY -- real cost from the CLI's own accounting, never estimated
    duration_ms: int  # EFFICIENCY
    claude_calls: int  # EFFICIENCY
    retries: int  # EFFICIENCY
    quality_score: float | None  # QUALITY
    tests_ran: bool  # QUALITY
    tests_passed: int  # QUALITY
    tests_failed: int  # QUALITY
    files_changed: int  # QUALITY
    predicted_difficulty: float  # PREDICTION
    predicted_band: str  # PREDICTION
    confidence: float  # PREDICTION
    risk: str  # PREDICTION
    mode: str  # PREDICTION -- selected strategy
    planning_used: bool  # PREDICTION
    raw_cost_usd: float | None = None
    raw_duration_ms: int | None = None
    raw_success: bool | None = None


@dataclass
class BenchmarkReport:
    mode: str  # "mock" or "live"
    results: list[BenchmarkResult] = field(default_factory=list)

    def totals(self) -> dict:
        """Aggregate metrics. Never averages over a denominator that
        includes tasks the metric doesn't apply to (e.g. tasks with no
        validate() are excluded from validated_pass_rate, tasks that never
        ran tests are excluded from test_pass_rate) -- an average over a
        mix of "0" and "not applicable" would misrepresent both.
        """
        n = len(self.results) or 1
        validated_results = [r for r in self.results if r.validated is not None]
        tested_results = [r for r in self.results if r.tests_ran]

        totals = {
            "total_tasks": len(self.results),
            "successful_tasks": sum(1 for r in self.results if r.status == "COMPLETE"),
            "success_rate": round(sum(1 for r in self.results if r.status == "COMPLETE") / n, 2),
            "average_agent_calls": round(sum(r.claude_calls for r in self.results) / n, 2),
            "average_retries": round(sum(r.retries for r in self.results) / n, 2),
            "average_resource_usage_usd": round(sum(r.cost_usd for r in self.results) / n, 4),
            "total_resource_usage_usd": round(sum(r.cost_usd for r in self.results), 4),
            "average_duration_ms": round(sum(r.duration_ms for r in self.results) / n, 1),
            "average_quality": round(sum(r.quality_score or 0 for r in self.results) / n, 2),
        }
        if validated_results:
            totals["validated_tasks"] = len(validated_results)
            totals["validated_pass_rate"] = round(sum(1 for r in validated_results if r.validated) / len(validated_results), 2)
        if tested_results:
            totals["tasks_with_tests_run"] = len(tested_results)
            totals["test_pass_rate"] = round(
                sum(1 for r in tested_results if r.tests_failed == 0) / len(tested_results), 2
            )
        return totals

    def by_difficulty(self) -> dict[str, dict]:
        """Per-band breakdown (success_by_difficulty / efficiency_by_difficulty),
        keyed by the task's benchmark category (trivial/easy/medium/hard/
        very_hard). A band with zero results simply doesn't appear -- it is
        not reported as a misleading 0%.
        """
        bands: dict[str, list[BenchmarkResult]] = {}
        for r in self.results:
            bands.setdefault(r.category, []).append(r)

        breakdown = {}
        for band, band_results in bands.items():
            n = len(band_results)
            breakdown[band] = {
                "tasks": n,
                "success_rate": round(sum(1 for r in band_results if r.status == "COMPLETE") / n, 2),
                "average_agent_calls": round(sum(r.claude_calls for r in band_results) / n, 2),
                "average_resource_usage_usd": round(sum(r.cost_usd for r in band_results) / n, 4),
                "average_duration_ms": round(sum(r.duration_ms for r in band_results) / n, 1),
            }
        return breakdown

    def compare_totals(self) -> dict | None:
        with_raw = [r for r in self.results if r.raw_cost_usd is not None]
        if not with_raw:
            return None
        n = len(with_raw)
        return {
            "tasks": n,
            "codeexcellent_total_cost_usd": round(sum(r.cost_usd for r in with_raw), 4),
            "raw_total_cost_usd": round(sum(r.raw_cost_usd for r in with_raw), 4),
            "codeexcellent_avg_calls": round(sum(r.claude_calls for r in with_raw) / n, 2),
        }


def _init_fixture_repo(task: BenchmarkTask, tmp_dir: Path) -> None:
    task.fixture(tmp_dir)
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=tmp_dir, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_dir, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=benchmark@codeexcellent.local", "-c", "user.name=CodeExcellent Benchmark",
             "commit", "-qm", "fixture"],
            cwd=tmp_dir, capture_output=True,
        )


def run_raw(request: str, root: str, timeout_seconds: int = 300) -> tuple[bool, float, int]:
    """Calls Claude directly with no CodeExcellent orchestration -- no
    context curation, no effort/budget tuning, a single call. This is the
    "just use Claude directly" side of the A/B comparison (section 21).
    Returns (success, cost_usd, duration_ms).
    """
    try:
        proc = subprocess.run(
            [resolve_executable("claude"), "-p", request, "--output-format", "json", "--permission-mode", "acceptEdits"],
            cwd=root, capture_output=True, text=True, timeout=timeout_seconds,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, 0.0, timeout_seconds * 1000

    try:
        data = json.loads(proc.stdout)
        return not data.get("is_error", True), float(data.get("total_cost_usd", 0.0)), int(data.get("duration_ms", 0))
    except (json.JSONDecodeError, ValueError):
        return False, 0.0, 0


def run_suite(
    config: dict,
    engine_factory: EngineFactory,
    mode: str,
    tasks: list[BenchmarkTask] | None = None,
    compare: bool = False,
    on_step: StepCallback | None = None,
) -> BenchmarkReport:
    tasks = tasks if tasks is not None else ALL_TASKS
    on_step = on_step or (lambda _: None)
    results: list[BenchmarkResult] = []

    for task in tasks:
        on_step(f"[{task.category}] {task.id}...")
        with tempfile.TemporaryDirectory(prefix="codeexcellent-bench-") as tmp:
            tmp_path = Path(tmp)
            _init_fixture_repo(task, tmp_path)

            engine = engine_factory()
            report = run_engine(task.request, str(tmp_path), config, engine)
            last_tests = report.attempts[-1].tests if report.attempts else None

            result = BenchmarkResult(
                task_id=task.id, category=task.category, status=report.status,
                validated=None, validation_message=None,
                cost_usd=report.total_cost_usd, duration_ms=report.total_duration_ms,
                claude_calls=len(report.attempts), retries=max(0, len(report.attempts) - 1),
                quality_score=report.final_quality.score if report.final_quality else None,
                tests_ran=bool(last_tests and last_tests.ran),
                tests_passed=last_tests.passed if last_tests else 0,
                tests_failed=last_tests.failed if last_tests else 0,
                files_changed=len(report.files_changed),
                predicted_difficulty=report.difficulty.value, predicted_band=report.difficulty.band,
                confidence=report.difficulty.confidence, risk=report.difficulty.risk_level.value,
                mode=report.difficulty.mode.value,
                planning_used=report.difficulty.mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL),
            )

            if task.validate is not None:
                try:
                    result.validated, result.validation_message = task.validate(tmp_path)
                except Exception as exc:
                    # Broad on purpose: a validator imports and executes the
                    # (possibly broken) fixture code after an agent's edit --
                    # a SyntaxError/ImportError/AttributeError there means
                    # "the task genuinely failed," not "the validator has a
                    # bug." Narrower exception types would let a broken
                    # implementation crash the whole benchmark run instead
                    # of correctly recording it as not validated.
                    result.validated, result.validation_message = False, f"validation check raised {exc!r}"

            if compare and mode == "live":
                with tempfile.TemporaryDirectory(prefix="codeexcellent-bench-raw-") as raw_tmp:
                    raw_path = Path(raw_tmp)
                    _init_fixture_repo(task, raw_path)
                    start = time.time()
                    success, cost, duration_ms = run_raw(task.request, str(raw_path))
                    result.raw_success = success
                    result.raw_cost_usd = cost
                    result.raw_duration_ms = duration_ms or int((time.time() - start) * 1000)

            results.append(result)

    return BenchmarkReport(mode=mode, results=results)
