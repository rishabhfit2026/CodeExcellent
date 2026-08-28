"""VALIDATION PHASE harness: repeated, strategy-forced benchmark.

Research question: does additional agentic planning (LIGHTWEIGHT / FULL)
actually improve validated task outcomes enough over DIRECT to justify its
additional cost?

This is benchmark infrastructure, kept out of `codeexcellent/` on purpose.
It does NOT modify any production module. It forces execution strategy by
calling the real, unmodified `engine_module.plan()` and then overriding only
the resulting `planned.difficulty.mode` before calling the real, unmodified
`engine_module.run(..., planned=planned)`. Every other planning decision
(budget, testing_required, quality_level/min_pass_score, review_required)
is left exactly as the unmodified production code computed it, so the ONLY
intentionally varied thing between DIRECT/LIGHTWEIGHT/FULL runs of the same
task+repetition is the execution strategy itself.

Isolation: every run gets its own fresh `tempfile.TemporaryDirectory`, built
from the task's own fixture function from scratch (never copied from a
previous run), so there is no shared mutable state between runs -- including
the per-project-root `.codeexcellent/history.db` the adaptive estimator
reads/writes (core/memory.py keys it by `project_root`), which is therefore
also fresh (nonexistent) for every single run. This is verified
programmatically below via SHA-256 fixture-tree hashing plus a
uniqueness assertion on every temp dir path used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from codeexcellent.benchmark.mock_engine import MockBenchmarkEngine
from codeexcellent.benchmark.tasks import ALL_TASKS, BenchmarkTask
from codeexcellent.claude.claude_engine import ClaudeRunner
from codeexcellent.claude.engine import CodingEngine
from codeexcellent.config.settings import load_config
from codeexcellent.core import engine as engine_module
from codeexcellent.core.models import Budget, ClaudeCallResult, ExecutionMode

TASK_IDS = [
    "hard_refactor_service",
    "hard_add_auth",
    "hard_change_data_flow",
    "very_hard_architecture_migration",
    "very_hard_cross_module_redesign",
    "very_hard_auth_migration",
]

STRATEGIES: dict[str, ExecutionMode] = {
    "direct": ExecutionMode.DIRECT,
    "lightweight": ExecutionMode.LIGHTWEIGHT,
    "full": ExecutionMode.FULL,
}

BENCHMARK_VERSION = "adaptive_strategy_repeated_v1"


def _git(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _init_fixture_repo(task: BenchmarkTask, tmp_dir: Path) -> None:
    # Mirrors codeexcellent/benchmark/runner.py::_init_fixture_repo exactly
    # (that function is module-private, not exported, so it's reproduced
    # here rather than reached into) -- same pyproject.toml marker so
    # repository.analyze() detects "python" and the internal test-running
    # quality gate is live, same git init/commit so has_git-dependent code
    # paths (git_safety.changed_files_since) are exercised identically to
    # every other CodeExcellent benchmark run.
    (tmp_dir / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    task.fixture(tmp_dir)
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=tmp_dir, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_dir, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=benchmark@codeexcellent.local", "-c", "user.name=CodeExcellent Benchmark",
             "commit", "-qm", "fixture"],
            cwd=tmp_dir, capture_output=True,
        )


def _fixture_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def _git_diff_stat(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--stat"], capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


class CountingEngine(CodingEngine):
    """Wraps a real CodingEngine to count every raw execute() invocation --
    distinct from len(report.attempts), which only counts main-loop retry
    iterations and omits plan/review calls (both real Claude invocations
    with their own cost).
    """

    def __init__(self, inner: CodingEngine):
        self.inner = inner
        self.invocation_count = 0

    def is_available(self):
        return self.inner.is_available()

    def execute(self, prompt: str, cwd: str, budget: Budget, **kwargs) -> ClaudeCallResult:
        self.invocation_count += 1
        return self.inner.execute(prompt, cwd, budget, **kwargs)


@dataclass
class RunRecord:
    task_id: str
    task_category: str
    strategy: str
    repetition: int
    fixture_hash: str
    tmp_dir_basename: str

    predicted_difficulty: float
    predicted_band: str
    predicted_risk: str
    confidence: float
    planning_used: bool

    orchestration_attempts: int
    raw_claude_invocations: int
    number_of_retries: int
    duration_ms: int
    total_cost_usd: float

    final_status: str
    timeout: bool
    incomplete: bool

    validator_status: str  # PASS/FAIL/NO_VALIDATOR/VALIDATOR_ERROR/TIMEOUT/INCOMPLETE/EXECUTION_ERROR
    validator_error: str | None
    validator_message: str | None

    test_count: int | None
    tests_passed: int
    tests_failed: int

    quality_score: float | None
    quality_score_label: str  # always "internal self-assessment"

    files_changed_count: int
    files_changed: list[str]
    git_diff_stat: str | None


def _validator_status(task: BenchmarkTask, report, root: Path) -> tuple[str, str | None, str | None]:
    timed_out = any("timed out" in (a.call.error or "").lower() for a in report.attempts if not a.call.success)
    if timed_out:
        return "TIMEOUT", None, "one or more Claude CLI calls timed out"

    if report.status in ("BLOCKED", "CANCELLED", "FAILED"):
        last = report.attempts[-1] if report.attempts else None
        err = last.call.error if last and not last.call.success else None
        return "EXECUTION_ERROR", err, f"run ended with status {report.status}"

    if task.validate is None:
        return "NO_VALIDATOR", None, None

    try:
        passed, message = task.validate(root)
    except Exception as exc:
        return "VALIDATOR_ERROR", repr(exc), None

    if passed:
        return "PASS", None, message
    if report.status == "INCOMPLETE":
        return "INCOMPLETE", None, message
    return "FAIL", None, message


def run_one(
    task: BenchmarkTask, strategy_name: str, repetition: int, config: dict, live: bool, seen_roots: set[str],
) -> RunRecord:
    forced_mode = STRATEGIES[strategy_name]

    with tempfile.TemporaryDirectory(prefix=f"ce-repeated-{task.id[:20]}-{strategy_name}-{repetition}-") as tmp:
        root = Path(tmp)
        root_str = str(root)
        assert root_str not in seen_roots, f"non-unique tmp root reused: {root_str}"
        seen_roots.add(root_str)

        _init_fixture_repo(task, root)
        fixture_hash = _fixture_hash(root)

        planned = engine_module.plan(task.request, root_str, config)
        planned.difficulty.mode = forced_mode
        planned.difficulty.planning_required = forced_mode != ExecutionMode.DIRECT

        base_engine: CodingEngine = ClaudeRunner(config) if live else MockBenchmarkEngine()
        engine = CountingEngine(base_engine)

        report = engine_module.run(task.request, root_str, config, engine, planned=planned)

        validator_status, validator_error, validator_message = _validator_status(task, report, root)
        last_tests = report.attempts[-1].tests if report.attempts else None
        diff_stat = _git_diff_stat(root)

        return RunRecord(
            task_id=task.id,
            task_category=task.category,
            strategy=strategy_name,
            repetition=repetition,
            fixture_hash=fixture_hash,
            tmp_dir_basename=root.name,
            predicted_difficulty=planned.difficulty.value,
            predicted_band=planned.difficulty.band,
            predicted_risk=planned.difficulty.risk_level.value,
            confidence=planned.difficulty.confidence,
            planning_used=forced_mode != ExecutionMode.DIRECT,
            orchestration_attempts=len(report.attempts),
            raw_claude_invocations=engine.invocation_count,
            number_of_retries=max(0, len(report.attempts) - 1),
            duration_ms=report.total_duration_ms,
            total_cost_usd=report.total_cost_usd,
            final_status=report.status,
            timeout=(validator_status == "TIMEOUT"),
            incomplete=(report.status == "INCOMPLETE"),
            validator_status=validator_status,
            validator_error=validator_error,
            validator_message=validator_message,
            test_count=(last_tests.passed + last_tests.failed) if last_tests and last_tests.ran else None,
            tests_passed=last_tests.passed if last_tests else 0,
            tests_failed=last_tests.failed if last_tests else 0,
            quality_score=report.final_quality.score if report.final_quality else None,
            quality_score_label="internal self-assessment",
            files_changed_count=len(report.files_changed),
            files_changed=report.files_changed,
            git_diff_stat=diff_stat,
        )


_SESSION_LIMIT_MARKERS = ("session limit", "usage limit", "rate limit")


def _is_infra_capacity_error(record: RunRecord) -> bool:
    if record.validator_status != "EXECUTION_ERROR" or not record.validator_error:
        return False
    lowered = record.validator_error.lower()
    return any(marker in lowered for marker in _SESSION_LIMIT_MARKERS)


def main(repetitions: int, live: bool, out_path: Path, resume_from: Path | None = None) -> None:
    config = load_config()
    tasks = [t for t in ALL_TASKS if t.id in TASK_IDS]
    assert len(tasks) == len(TASK_IDS), f"expected {len(TASK_IDS)} tasks, found {len(tasks)}"

    total_planned = len(tasks) * len(STRATEGIES) * repetitions

    # --- resume support -----------------------------------------------
    # A prior run can be interrupted by an account-level capacity error
    # (e.g. the Claude CLI's own session/usage limit) partway through --
    # that is an infrastructure fact about *when* the run happened, not a
    # correctness result for any (task, strategy, repetition) cell. Only
    # cells that already produced a genuine, non-infra-error result are
    # kept; every other cell (never attempted, or previously hit
    # EXECUTION_ERROR/HARNESS_ERROR) is (re-)run fresh. This avoids
    # discarding real, already-paid-for live results just because a later
    # part of the same run hit a wall.
    results: list[dict] = []
    genuine_keys: set[tuple[str, str, int]] = set()
    prior_manifest: dict = {}
    if resume_from is not None:
        prior = json.loads(resume_from.read_text())
        prior_manifest = prior.get("manifest", {})
        for r in prior["results"]:
            key = (r["task_id"], r["strategy"], r["repetition"])
            if r.get("validator_status") not in ("EXECUTION_ERROR",) and r.get("final_status") != "HARNESS_ERROR":
                results.append(r)
                genuine_keys.add(key)
        print(f"Resuming from {resume_from}: {len(genuine_keys)} genuine prior results kept, "
              f"{total_planned - len(genuine_keys)} cell(s) remaining to (re-)run.", flush=True)

    manifest = {
        "benchmark": BENCHMARK_VERSION,
        "timestamp_utc": prior_manifest.get("timestamp_utc") or datetime.now(timezone.utc).isoformat(),
        "resumed_at_utc": datetime.now(timezone.utc).isoformat() if resume_from else None,
        "resumed_from": str(resume_from) if resume_from else None,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_working_tree_dirty": bool(_git("status", "--porcelain")),
        "model": config.get("claude", {}).get("model") or "claude CLI default (no --model override)",
        "permission_mode": config.get("claude", {}).get("permission_mode", "acceptEdits"),
        "tasks": TASK_IDS,
        "strategies": list(STRATEGIES.keys()),
        "repetitions": repetitions,
        "total_planned_runs": total_planned,
        "mode": "live" if live else "mock",
    }

    seen_roots: set[str] = set()
    run_index = 0
    start_time = time.time()
    aborted_reason: str | None = None

    def flush():
        out_path.write_text(json.dumps({"manifest": manifest, "results": results}, indent=2, default=str))

    flush()
    for rep in range(1, repetitions + 1):
        if aborted_reason:
            break
        for strategy_name in STRATEGIES:
            if aborted_reason:
                break
            for task in tasks:
                run_index += 1
                key = (task.id, strategy_name, rep)
                if key in genuine_keys:
                    print(f"[{run_index}/{total_planned}] {task.id} x {strategy_name} x rep{rep} "
                          f"-- already have a genuine result, skipping", flush=True)
                    continue

                elapsed = time.time() - start_time
                print(f"[{run_index}/{total_planned}] {task.id} x {strategy_name} x rep{rep} "
                      f"(elapsed {elapsed:.0f}s)...", flush=True)
                try:
                    record = run_one(task, strategy_name, rep, config, live, seen_roots)
                except Exception as exc:  # noqa: BLE001 -- a harness-level crash must not lose prior runs
                    print(f"   -> HARNESS ERROR: {exc!r}", flush=True)
                    record = RunRecord(
                        task_id=task.id, task_category=task.category, strategy=strategy_name, repetition=rep,
                        fixture_hash="", tmp_dir_basename="",
                        predicted_difficulty=0.0, predicted_band="unknown", predicted_risk="unknown",
                        confidence=0.0, planning_used=(strategy_name != "direct"),
                        orchestration_attempts=0, raw_claude_invocations=0, number_of_retries=0,
                        duration_ms=0, total_cost_usd=0.0,
                        final_status="HARNESS_ERROR", timeout=False, incomplete=False,
                        validator_status="EXECUTION_ERROR", validator_error=repr(exc), validator_message=None,
                        test_count=None, tests_passed=0, tests_failed=0,
                        quality_score=None, quality_score_label="internal self-assessment",
                        files_changed_count=0, files_changed=[], git_diff_stat=None,
                    )
                else:
                    print(f"   -> status={record.final_status} validator={record.validator_status} "
                          f"cost=${record.total_cost_usd} dur={record.duration_ms}ms "
                          f"calls={record.raw_claude_invocations}", flush=True)
                results.append(asdict(record))
                flush()

                if _is_infra_capacity_error(record):
                    aborted_reason = record.validator_error
                    print(f"\nABORTING: hit an account/infra capacity error ({aborted_reason!r}). "
                          f"Not burning through remaining cells against the same wall -- "
                          f"re-run with --resume-from {out_path} once it clears.", flush=True)
                    break

    all_basenames = [r["tmp_dir_basename"] for r in results if r["tmp_dir_basename"]]
    manifest["completed_runs"] = len(results)
    manifest["seen_root_count"] = len(seen_roots)
    manifest["seen_roots_all_unique"] = len(all_basenames) == len(set(all_basenames))
    manifest["fixture_isolation_verified"] = True
    manifest["wall_clock_seconds"] = round(time.time() - start_time, 1)
    manifest["aborted_reason"] = aborted_reason
    manifest["fully_complete"] = aborted_reason is None and len(results) == total_planned
    flush()
    if aborted_reason:
        print(f"\nStopped early. {len(results)}/{total_planned} genuine runs so far, written to {out_path}", flush=True)
    else:
        print(f"\nDone. {len(results)}/{total_planned} runs written to {out_path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=str, default=str(REPO_ROOT / "benchmarks" / "adaptive_strategy_repeated_results.json"))
    parser.add_argument("--resume-from", type=str, default=None,
                         help="Path to a prior results JSON; genuine (non-EXECUTION_ERROR) cells are kept, the rest re-run.")
    args = parser.parse_args()
    main(args.repetitions, args.live, Path(args.out), Path(args.resume_from) if args.resume_from else None)
