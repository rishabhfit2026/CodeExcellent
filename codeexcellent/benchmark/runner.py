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
from codeexcellent.core.platform_utils import resolve_executable

EngineFactory = Callable[[], CodingEngine]
StepCallback = Callable[[str], None]


@dataclass
class BenchmarkResult:
    task_id: str
    category: str
    predicted_difficulty: float
    predicted_band: str
    mode: str
    status: str
    quality_score: float | None
    claude_calls: int
    cost_usd: float
    duration_ms: int
    raw_cost_usd: float | None = None
    raw_duration_ms: int | None = None
    raw_success: bool | None = None


@dataclass
class BenchmarkReport:
    mode: str  # "mock" or "live"
    results: list[BenchmarkResult] = field(default_factory=list)

    def totals(self) -> dict:
        n = len(self.results) or 1
        return {
            "tasks": len(self.results),
            "success_rate": round(sum(1 for r in self.results if r.status == "COMPLETE") / n, 2),
            "avg_claude_calls": round(sum(r.claude_calls for r in self.results) / n, 2),
            "avg_cost_usd": round(sum(r.cost_usd for r in self.results) / n, 4),
            "total_cost_usd": round(sum(r.cost_usd for r in self.results), 4),
            "avg_quality": round(sum(r.quality_score or 0 for r in self.results) / n, 2),
        }

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

            result = BenchmarkResult(
                task_id=task.id, category=task.category, predicted_difficulty=report.difficulty.value,
                predicted_band=report.difficulty.band, mode=report.difficulty.mode.value, status=report.status,
                quality_score=report.final_quality.score if report.final_quality else None,
                claude_calls=len(report.attempts), cost_usd=report.total_cost_usd,
                duration_ms=report.total_duration_ms,
            )

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
