"""Public benchmark: CodeExcellent's real, unmodified adaptive orchestration
vs. calling the raw `claude` CLI directly with no orchestration at all --
the comparison intended for public use ("use CodeExcellent, not bare
Claude, for efficient work").

Distinct from repeated_strategy_benchmark.py (which forces DIRECT/
LIGHTWEIGHT/FULL to isolate whether planning itself pays off *within*
CodeExcellent). This benchmark does NOT force strategy -- it lets
`engine.plan()` pick naturally, because the claim under test here is
whether CodeExcellent's adaptive behavior as a whole is worth using, not
which internal strategy is best.

No production code is modified. Runs the full ALL_TASKS suite (16 tasks,
trivial through very_hard) so the result isn't just "CodeExcellent wins on
hard tasks" -- it has to hold (or not) across the whole difficulty range.
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
from codeexcellent.core.platform_utils import resolve_executable

BENCHMARK_VERSION = "public_compare_v1"
_SESSION_LIMIT_MARKERS = ("session limit", "usage limit", "rate limit")


def _git(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _init_fixture_repo(task: BenchmarkTask, tmp_dir: Path) -> None:
    # Mirrors codeexcellent/benchmark/runner.py::_init_fixture_repo exactly
    # (module-private, not exported -- reproduced rather than reached into).
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


class CountingEngine(CodingEngine):
    def __init__(self, inner: CodingEngine):
        self.inner = inner
        self.invocation_count = 0

    def is_available(self):
        return self.inner.is_available()

    def execute(self, prompt: str, cwd: str, budget: Budget, **kwargs) -> ClaudeCallResult:
        self.invocation_count += 1
        return self.inner.execute(prompt, cwd, budget, **kwargs)


def _raw_call(request: str, root: str, timeout_seconds: int, live: bool) -> dict:
    """Reimplements benchmark/runner.py::run_raw's exact command, but keeps
    the real stderr/is_error text (run_raw collapses everything to a bare
    bool + 0.0 cost) -- needed to tell a genuine raw-Claude failure apart
    from the same account-level session-limit wall the orchestrated side
    can hit, using the same diagnostic standard as the orchestrated side's
    ClaudeCallResult.error.
    """
    if not live:
        return {"success": True, "cost_usd": 0.001, "duration_ms": 50, "num_turns": 1, "error": None, "result_text": "mock raw response"}

    cmd = [resolve_executable("claude"), "-p", request, "--output-format", "json", "--permission-mode", "acceptEdits"]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"success": False, "cost_usd": 0.0, "duration_ms": timeout_seconds * 1000, "num_turns": None,
                "error": f"claude CLI timed out after {timeout_seconds}s", "result_text": ""}
    except FileNotFoundError:
        return {"success": False, "cost_usd": 0.0, "duration_ms": 0, "num_turns": None,
                "error": "'claude' is not installed or not on PATH", "result_text": ""}

    if proc.returncode != 0 and not proc.stdout.strip():
        return {"success": False, "cost_usd": 0.0, "duration_ms": 0, "num_turns": None,
                "error": f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:2000]}", "result_text": ""}

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"success": False, "cost_usd": 0.0, "duration_ms": 0, "num_turns": None,
                "error": f"could not parse Claude CLI output as JSON: {proc.stderr.strip()[:500]}", "result_text": proc.stdout[:2000]}

    is_error = bool(data.get("is_error", False))
    return {
        "success": not is_error,
        "cost_usd": float(data.get("total_cost_usd", 0.0)),
        "duration_ms": int(data.get("duration_ms", 0)),
        "num_turns": data.get("num_turns"),
        "error": data.get("result") if is_error else None,
        "result_text": data.get("result", ""),
    }


@dataclass
class CompareRecord:
    task_id: str
    task_category: str
    repetition: int
    has_validator: bool

    # -- CodeExcellent side --
    ce_fixture_hash: str
    ce_predicted_difficulty: float
    ce_predicted_band: str
    ce_predicted_risk: str
    ce_confidence: float
    ce_mode: str  # the strategy the *unforced* adaptive selector actually chose
    ce_planning_used: bool
    ce_claude_invocations: int
    ce_retries: int
    ce_duration_ms: int
    ce_cost_usd: float
    ce_final_status: str
    ce_validator_status: str
    ce_validator_error: str | None
    ce_validated: bool | None
    ce_quality_score: float | None
    ce_quality_score_label: str
    ce_files_changed_count: int

    # -- raw Claude CLI side (no orchestration) --
    raw_fixture_hash: str
    raw_success: bool
    raw_error: str | None
    raw_cost_usd: float
    raw_duration_ms: int
    raw_num_turns: int | None
    raw_validated: bool | None
    raw_validation_message: str | None

    genuine: bool
    infra_error_detail: str | None = None


def _is_infra_capacity_text(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _SESSION_LIMIT_MARKERS)


def _ce_validator_status(task: BenchmarkTask, report, root: Path) -> tuple[str, str | None, bool | None]:
    timed_out = any("timed out" in (a.call.error or "").lower() for a in report.attempts if not a.call.success)
    if timed_out:
        return "TIMEOUT", None, None
    if report.status in ("BLOCKED", "CANCELLED", "FAILED"):
        last = report.attempts[-1] if report.attempts else None
        err = last.call.error if last and not last.call.success else None
        return "EXECUTION_ERROR", err, None
    if task.validate is None:
        return "NO_VALIDATOR", None, None
    try:
        passed, _msg = task.validate(root)
    except Exception as exc:
        return "VALIDATOR_ERROR", repr(exc), None
    if passed:
        return "PASS", None, True
    if report.status == "INCOMPLETE":
        return "INCOMPLETE", None, False
    return "FAIL", None, False


def run_one(task: BenchmarkTask, repetition: int, config: dict, live: bool, timeout_seconds: int, seen_roots: set[str]) -> CompareRecord:
    # Two independent fresh fixture roots -- CodeExcellent's run must never
    # share state with, or be able to influence, the raw-Claude run of the
    # same task.
    with tempfile.TemporaryDirectory(prefix=f"ce-cmp-ce-{task.id[:16]}-{repetition}-") as ce_tmp, \
         tempfile.TemporaryDirectory(prefix=f"ce-cmp-raw-{task.id[:16]}-{repetition}-") as raw_tmp:
        ce_root, raw_root = Path(ce_tmp), Path(raw_tmp)
        for r in (str(ce_root), str(raw_root)):
            assert r not in seen_roots, f"non-unique tmp root reused: {r}"
            seen_roots.add(r)

        _init_fixture_repo(task, ce_root)
        _init_fixture_repo(task, raw_root)
        ce_hash = _fixture_hash(ce_root)
        raw_hash = _fixture_hash(raw_root)

        base_engine: CodingEngine = ClaudeRunner(config) if live else MockBenchmarkEngine()
        engine = CountingEngine(base_engine)
        report = engine_module.run(task.request, str(ce_root), config, engine)
        ce_status, ce_error, ce_validated = _ce_validator_status(task, report, ce_root)

        raw = _raw_call(task.request, str(raw_root), timeout_seconds, live)
        raw_validated: bool | None = None
        raw_validation_message: str | None = None
        if task.validate is not None:
            try:
                raw_validated, raw_validation_message = task.validate(raw_root)
            except Exception as exc:
                raw_validated, raw_validation_message = False, f"validation check raised {exc!r}"

        ce_infra = _is_infra_capacity_text(ce_error)
        raw_infra = _is_infra_capacity_text(raw["error"])
        infra_detail = ce_error if ce_infra else (raw["error"] if raw_infra else None)

        return CompareRecord(
            task_id=task.id, task_category=task.category, repetition=repetition,
            has_validator=task.validate is not None,
            ce_fixture_hash=ce_hash,
            ce_predicted_difficulty=report.difficulty.value, ce_predicted_band=report.difficulty.band,
            ce_predicted_risk=report.difficulty.risk_level.value, ce_confidence=report.difficulty.confidence,
            ce_mode=report.difficulty.mode.value,
            ce_planning_used=report.difficulty.mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL),
            ce_claude_invocations=engine.invocation_count, ce_retries=max(0, len(report.attempts) - 1),
            ce_duration_ms=report.total_duration_ms, ce_cost_usd=report.total_cost_usd,
            ce_final_status=report.status, ce_validator_status=ce_status, ce_validator_error=ce_error,
            ce_validated=ce_validated,
            ce_quality_score=report.final_quality.score if report.final_quality else None,
            ce_quality_score_label="internal self-assessment",
            ce_files_changed_count=len(report.files_changed),
            raw_fixture_hash=raw_hash,
            raw_success=raw["success"], raw_error=raw["error"], raw_cost_usd=raw["cost_usd"],
            raw_duration_ms=raw["duration_ms"], raw_num_turns=raw["num_turns"],
            raw_validated=raw_validated, raw_validation_message=raw_validation_message,
            genuine=not (ce_infra or raw_infra),
            infra_error_detail=infra_detail,
        )


def main(repetitions: int, live: bool, out_path: Path, resume_from: Path | None, timeout_seconds: int) -> None:
    config = load_config()
    tasks = ALL_TASKS
    total_planned = len(tasks) * repetitions

    results: list[dict] = []
    genuine_keys: set[tuple[str, int]] = set()
    prior_manifest: dict = {}
    if resume_from is not None:
        prior = json.loads(resume_from.read_text())
        prior_manifest = prior.get("manifest", {})
        for r in prior["results"]:
            key = (r["task_id"], r["repetition"])
            if r.get("genuine"):
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
        "tasks": [t.id for t in tasks],
        "task_count": len(tasks),
        "repetitions": repetitions,
        "total_planned_runs": total_planned,
        "timeout_seconds": timeout_seconds,
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
        for task in tasks:
            run_index += 1
            key = (task.id, rep)
            if key in genuine_keys:
                print(f"[{run_index}/{total_planned}] {task.id} x rep{rep} -- already have a genuine result, skipping", flush=True)
                continue

            elapsed = time.time() - start_time
            print(f"[{run_index}/{total_planned}] {task.id} x rep{rep} (elapsed {elapsed:.0f}s)...", flush=True)
            try:
                record = run_one(task, rep, config, live, timeout_seconds, seen_roots)
            except Exception as exc:  # noqa: BLE001
                print(f"   -> HARNESS ERROR: {exc!r}", flush=True)
                record = CompareRecord(
                    task_id=task.id, task_category=task.category, repetition=rep, has_validator=task.validate is not None,
                    ce_fixture_hash="", ce_predicted_difficulty=0.0, ce_predicted_band="unknown", ce_predicted_risk="unknown",
                    ce_confidence=0.0, ce_mode="unknown", ce_planning_used=False, ce_claude_invocations=0, ce_retries=0,
                    ce_duration_ms=0, ce_cost_usd=0.0, ce_final_status="HARNESS_ERROR", ce_validator_status="EXECUTION_ERROR",
                    ce_validator_error=repr(exc), ce_validated=None, ce_quality_score=None,
                    ce_quality_score_label="internal self-assessment", ce_files_changed_count=0,
                    raw_fixture_hash="", raw_success=False, raw_error=repr(exc), raw_cost_usd=0.0, raw_duration_ms=0,
                    raw_num_turns=None, raw_validated=None, raw_validation_message=None,
                    genuine=False, infra_error_detail=None,
                )
            else:
                print(f"   -> CE: status={record.ce_final_status} validator={record.ce_validator_status} "
                      f"cost=${record.ce_cost_usd} dur={record.ce_duration_ms}ms mode={record.ce_mode} | "
                      f"RAW: success={record.raw_success} cost=${record.raw_cost_usd} dur={record.raw_duration_ms}ms", flush=True)
            results.append(asdict(record))
            flush()

            if not record.genuine:
                aborted_reason = record.infra_error_detail or "harness error"
                print(f"\nABORTING: hit an account/infra capacity error ({aborted_reason!r}). "
                      f"Not burning through remaining cells against the same wall -- "
                      f"re-run with --resume-from {out_path} once it clears.", flush=True)
                break

    all_ce_basenames = [r["ce_fixture_hash"] for r in results if r["ce_fixture_hash"]]
    manifest["completed_runs"] = len(results)
    manifest["seen_root_count"] = len(seen_roots)
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
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--out", type=str, default=str(REPO_ROOT / "benchmarks" / "public_compare_results.json"))
    parser.add_argument("--resume-from", type=str, default=None)
    args = parser.parse_args()
    main(args.repetitions, args.live, Path(args.out), Path(args.resume_from) if args.resume_from else None, args.timeout_seconds)
