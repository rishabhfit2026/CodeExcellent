"""Engine-level tests for validation-driven recovery: after a live A/B
benchmark showed escalating strategy on difficulty alone increased cost
without improving validated correctness, the engine now classifies *why*
an attempt fell short and retries with a targeted prompt at the *same*
budget unless the failure itself is evidence more resources would help
(see core/failure_classifier.py).

These construct a `PlanResult` directly (rather than deriving it from real
task text through the heuristic scorer) so each scenario isolates one
engine behavior -- what's under test here is the engine's recovery/budget
logic, not difficulty scoring, which already has its own test coverage.
"""
from pathlib import Path
from unittest.mock import patch

from codeexcellent.analyzer import fingerprint
from codeexcellent.budget import budget_manager, resource_forecaster
from codeexcellent.claude.engine import CodingEngine
from codeexcellent.config.settings import load_config
from codeexcellent.core import context, test_runner
from codeexcellent.core.engine import run
from codeexcellent.core.models import (
    Budget,
    ClaudeCallResult,
    DifficultyScore,
    ExecutionMode,
    PlanResult,
    QualityLevel,
    RepoContext,
    RiskLevel,
    SuiteRunResult,
    TaskAnalysis,
)

CONFIG = load_config()


class ScriptedEngine(CodingEngine):
    """Replays a scripted sequence of (files_to_write, ClaudeCallResult)
    pairs, one per call to execute(), and records every prompt it was given
    so a test can assert the *content* of a targeted recovery prompt.
    """

    def __init__(self, script: list[tuple[list[str] | None, ClaudeCallResult]]):
        self.script = script
        self.calls = 0
        self.prompts: list[str] = []

    def is_available(self):
        return True, "mock"

    def execute(self, prompt, cwd, budget: Budget, *, json_schema=None, session_id=None, allowed_tools=None):
        self.prompts.append(prompt)
        files, result = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if files:
            for name in files:
                (Path(cwd) / name).write_text("changed = True\n")
        return result


def _task(architecture_signal=0.0, cross_module_signal=0.0, testing_signal=0.0) -> TaskAnalysis:
    return TaskAnalysis(
        request="do the thing", task_complexity=5.0, scope=5.0, risk=0.0, testing_signal=testing_signal,
        architecture_signal=architecture_signal, ambiguity=0.0, operation_count=1,
        cross_module_signal=cross_module_signal,
    )


def _repo(root: str) -> RepoContext:
    return RepoContext(
        root=root, project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=1, relevant_files=[],
    )


def _difficulty(
    value=7.0, risk=RiskLevel.LOW, mode=ExecutionMode.LIGHTWEIGHT,
    testing_required=False, quality_level=QualityLevel.STANDARD, confidence=0.8, estimated_scope="medium",
) -> DifficultyScore:
    return DifficultyScore(
        value=value, band="hard", risk_level=risk, dimensions={},
        planning_required=mode != ExecutionMode.DIRECT, testing_required=testing_required,
        mode=mode, estimated_scope=estimated_scope, quality_level=quality_level, confidence=confidence,
    )


def _budget(max_claude_calls=3, max_retries=2, max_budget_usd=8.0, timeout_seconds=900) -> Budget:
    return Budget(
        band="hard", effort="high", max_budget_usd=max_budget_usd, max_budget_usd_step=4.0,
        max_claude_calls=max_claude_calls, max_retries=max_retries, timeout_seconds=timeout_seconds,
    )


def _planned(tmp_path, task: TaskAnalysis, difficulty: DifficultyScore, budget: Budget) -> PlanResult:
    root = str(tmp_path)
    repo = _repo(root)
    fp = fingerprint.build(task, repo, difficulty)
    forecast = resource_forecaster.forecast(fp.key(), root, budget, 0, CONFIG)
    bundle = context.build(repo, [], CONFIG)
    return PlanResult(task=task, repo=repo, difficulty=difficulty, budget=budget, fingerprint=fp, forecast=forecast, context_bundle=bundle)


def _ok_call(cost=0.05, stop_reason="end_turn"):
    return ClaudeCallResult(
        success=True, result_text="Done.", session_id="s1", cost_usd=cost,
        input_tokens=10, output_tokens=10, duration_ms=500, num_turns=1, stop_reason=stop_reason,
    )


# --- E. hard task where validation fails -> targeted recovery, no blanket budget escalation ---

def test_test_failure_gets_a_second_attempt_without_budget_escalation(tmp_path):
    task = _task()
    difficulty = _difficulty(testing_required=True, quality_level=QualityLevel.STANDARD)
    budget = _budget()
    planned = _planned(tmp_path, task, difficulty, budget)

    engine = ScriptedEngine([(["a.py"], _ok_call()), (["a.py"], _ok_call())])
    test_results = iter([
        SuiteRunResult(ran=True, passed=3, failed=2, success=False, output_tail="FAILED test_x"),
        SuiteRunResult(ran=True, passed=5, failed=0, success=True),
    ])

    with patch.object(test_runner, "run", side_effect=lambda *a, **k: next(test_results)), \
         patch.object(budget_manager, "escalate", wraps=budget_manager.escalate) as spy_escalate:
        report = run("irrelevant", str(tmp_path), CONFIG, engine, planned=planned)

    assert report.status == "COMPLETE"
    assert len(report.attempts) == 2
    assert report.attempts[0].failure_class == "test_failure"
    spy_escalate.assert_not_called()


# --- F. architecture migration with a missing structural change -> diagnosed recovery ---

def test_structural_incompleteness_is_diagnosed_and_recovered_without_escalation(tmp_path):
    task = _task(architecture_signal=8.0)
    difficulty = _difficulty(testing_required=False, quality_level=QualityLevel.STANDARD)
    budget = _budget()
    planned = _planned(tmp_path, task, difficulty, budget)

    # Attempt 1 only touches one file (the structural split didn't happen)
    # and was cut short; attempt 2 adds the missing second file cleanly.
    engine = ScriptedEngine([
        (["app.py"], _ok_call(stop_reason="max_tokens")),
        (["services.py"], _ok_call()),
    ])

    with patch.object(budget_manager, "escalate", wraps=budget_manager.escalate) as spy_escalate:
        report = run("irrelevant", str(tmp_path), CONFIG, engine, planned=planned)

    assert report.status == "COMPLETE"
    assert len(report.attempts) == 2
    assert report.attempts[0].failure_class == "structural_incomplete"
    spy_escalate.assert_not_called()
    # The retry prompt should carry the targeted diagnosis, not just a
    # generic "try again".
    assert "structural" in engine.prompts[1].lower()
    assert "app.py" in engine.prompts[1]


# --- H. CRITICAL security task -> bounded execution, not unlimited process ---

def test_critical_risk_task_stops_at_the_retry_budget_not_indefinitely(tmp_path):
    task = _task()
    difficulty = _difficulty(
        risk=RiskLevel.CRITICAL, mode=ExecutionMode.FULL,
        testing_required=True, quality_level=QualityLevel.CRITICAL,  # min_pass_score 9.0
    )
    budget = _budget(max_claude_calls=3, max_retries=2, max_budget_usd=8.0)
    planned = _planned(tmp_path, task, difficulty, budget)

    # Every attempt "succeeds" but tests keep failing, so the CRITICAL 9.0
    # quality bar is never met -- this must still terminate, not loop.
    engine = ScriptedEngine([(["a.py"], _ok_call(cost=0.5))])
    failing_tests = SuiteRunResult(ran=True, passed=4, failed=1, success=False, output_tail="FAILED test_x")

    with patch.object(test_runner, "run", return_value=failing_tests):
        report = run("irrelevant", str(tmp_path), CONFIG, engine, planned=planned)

    assert report.status == "INCOMPLETE"
    # Attempts are bounded by the retry budget regardless of the extra
    # planning/review calls CRITICAL risk also pays for (a separate,
    # legitimate cost -- what must NOT happen is the implementation loop
    # itself running unboundedly).
    assert len(report.attempts) == budget.max_retries + 1
    assert engine.calls <= 1 + 2 * (budget.max_retries + 1)  # 1 plan call + up to 1 review per attempt
    assert report.total_cost_usd < budget.max_budget_usd


# --- I. repeated, well-diagnosed failure -> controlled stop, not an infinite loop ---

def test_repeated_implementation_gap_still_terminates(tmp_path):
    task = _task()
    difficulty = _difficulty(quality_level=QualityLevel.STANDARD, estimated_scope="small")
    budget = _budget(max_claude_calls=3, max_retries=2)
    planned = _planned(tmp_path, task, difficulty, budget)

    # Every attempt changes far more files than the scope limit allows AND
    # is cut short -- a real, always-recurring IMPLEMENTATION_GAP.
    many_files = [f"f{i}.py" for i in range(20)]
    engine = ScriptedEngine([(many_files, _ok_call(stop_reason="max_tokens"))])

    report = run("irrelevant", str(tmp_path), CONFIG, engine, planned=planned)

    assert report.status == "INCOMPLETE"
    assert engine.calls == budget.max_retries + 1
    assert all(a.failure_class == "implementation_gap" for a in report.attempts)


# --- K. retry budget prevents infinite execution ---

def test_retry_budget_caps_attempts_regardless_of_how_recovery_is_classified(tmp_path):
    task = _task()
    difficulty = _difficulty(quality_level=QualityLevel.STANDARD)
    budget = _budget(max_claude_calls=2, max_retries=1)
    planned = _planned(tmp_path, task, difficulty, budget)

    # No files ever change -- a persistently ambiguous/unrecoverable case,
    # the class that DOES warrant budget escalation -- but the call count
    # must still be capped by max_retries+1.
    engine = ScriptedEngine([(None, _ok_call())])

    report = run("irrelevant", str(tmp_path), CONFIG, engine, planned=planned)

    assert report.status == "INCOMPLETE"
    assert engine.calls == budget.max_retries + 1 == 2


# --- L. cost/time budget prevents runaway tasks ---

def test_cost_budget_stops_the_run_before_the_retry_ceiling(tmp_path):
    task = _task()
    difficulty = _difficulty(quality_level=QualityLevel.STANDARD)
    # max_retries is generous (5) so the cost cap, not the retry cap, is
    # what's actually exercised here.
    budget = _budget(max_claude_calls=6, max_retries=5, max_budget_usd=8.0)
    planned = _planned(tmp_path, task, difficulty, budget)

    # Each "successful" call is expensive and never actually finishes the
    # task (no files changed), so cost accumulates every attempt.
    engine = ScriptedEngine([(None, _ok_call(cost=5.0))])

    report = run("irrelevant", str(tmp_path), CONFIG, engine, planned=planned)

    assert report.status == "INCOMPLETE"
    assert engine.calls < budget.max_retries + 1
    assert report.total_cost_usd >= budget.max_budget_usd
