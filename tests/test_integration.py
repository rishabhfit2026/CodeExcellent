"""End-to-end pipeline tests using a mocked CodingEngine -- no real Claude
subscription/API access is required (section 28).
"""
from pathlib import Path

from unittest.mock import patch

from codeexcellent.claude.engine import CodingEngine
from codeexcellent.config.settings import load_config
from codeexcellent.core import engine as engine_module
from codeexcellent.core.engine import plan, run
from codeexcellent.core.models import Budget, ClaudeCallResult, SuiteRunResult

CONFIG = load_config()


class ScriptedEngine(CodingEngine):
    """Replays a scripted sequence of (file_to_write, ClaudeCallResult) pairs,
    one per call to execute(), simulating Claude editing the repo each turn.
    """

    def __init__(self, script: list[tuple[str | None, ClaudeCallResult]]):
        self.script = script
        self.calls = 0

    def is_available(self):
        return True, "mock"

    def execute(self, prompt, cwd, budget: Budget, *, json_schema=None, session_id=None, allowed_tools=None):
        file_to_write, result = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if file_to_write:
            (Path(cwd) / file_to_write).write_text("changed = True\n")
        return result


def _ok_call(cost=0.01):
    return ClaudeCallResult(
        success=True, result_text="Done.", session_id="s1", cost_usd=cost,
        input_tokens=10, output_tokens=10, duration_ms=500, num_turns=1, stop_reason="end_turn",
    )


def test_trivial_task_completes_in_one_call(tmp_path):
    engine = ScriptedEngine([("output.py", _ok_call())])
    report = run("Rename userName to username.", str(tmp_path), CONFIG, engine)

    assert report.status == "COMPLETE"
    assert len(report.attempts) == 1
    assert "output.py" in report.files_changed
    assert engine.calls == 1


def test_no_op_call_is_marked_incomplete_and_does_not_loop_forever(tmp_path):
    # Claude "succeeds" every time but never actually changes a file --
    # quality should never pass, and the run must still terminate.
    engine = ScriptedEngine([(None, _ok_call())])
    report = run("Rename userName to username.", str(tmp_path), CONFIG, engine)

    assert report.status == "INCOMPLETE"
    assert report.files_changed == []
    assert engine.calls == report.budget.max_claude_calls or engine.calls <= report.budget.max_claude_calls + 1


def test_failed_call_stops_after_retries_exhausted(tmp_path):
    failing = ClaudeCallResult(
        success=False, result_text="", session_id=None, cost_usd=0.0, input_tokens=0,
        output_tokens=0, duration_ms=0, num_turns=0, stop_reason=None, error="simulated failure",
    )
    engine = ScriptedEngine([(None, failing)])
    report = run("Rename userName to username.", str(tmp_path), CONFIG, engine)

    assert report.status == "FAILED"
    assert engine.calls == report.budget.max_retries + 1


def test_execution_recorded_in_history(tmp_path):
    from codeexcellent.core import memory

    engine = ScriptedEngine([("output.py", _ok_call())])
    run("Rename userName to username.", str(tmp_path), CONFIG, engine)

    rows = memory.recent(str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["status"] == "COMPLETE"


def test_passing_a_precomputed_plan_skips_recomputing_it(tmp_path):
    # A caller that already displayed the analysis (e.g. the interactive CLI)
    # should not pay for a second repo scan + adaptive-history lookup.
    engine = ScriptedEngine([("output.py", _ok_call())])
    precomputed = plan("Rename userName to username.", str(tmp_path), CONFIG)

    with patch.object(engine_module, "plan", wraps=engine_module.plan) as spy_plan:
        report = run("Rename userName to username.", str(tmp_path), CONFIG, engine, planned=precomputed)

    spy_plan.assert_not_called()
    assert report.status == "COMPLETE"
    assert report.difficulty is precomputed.difficulty


def test_omitting_planned_still_computes_it_internally(tmp_path):
    # Backward compatibility: existing callers that don't pass `planned`
    # (benchmark runner, direct API use) must behave exactly as before.
    engine = ScriptedEngine([("output.py", _ok_call())])

    with patch.object(engine_module, "plan", wraps=engine_module.plan) as spy_plan:
        report = run("Rename userName to username.", str(tmp_path), CONFIG, engine)

    spy_plan.assert_called_once()
    assert report.status == "COMPLETE"


def test_observability_fields_flow_from_test_results_into_history(tmp_path):
    from codeexcellent.core import memory, test_runner

    # "tests" in the request text pushes testing_signal above the threshold
    # that makes difficulty_scorer set testing_required=True (no mocking of
    # orchestration logic needed -- this is real heuristic behavior).
    engine = ScriptedEngine([("output.py", _ok_call())])
    fake_tests = SuiteRunResult(ran=True, passed=7, failed=1, command="pytest -q", success=False)

    with patch.object(test_runner, "run", return_value=fake_tests):
        report = run("Add unit tests for the calculator module", str(tmp_path), CONFIG, engine)

    assert report.difficulty.testing_required is True
    rows = memory.recent(str(tmp_path))
    assert rows[0]["tests_ran"] == 1
    assert rows[0]["tests_passed"] == 7
    assert rows[0]["tests_failed"] == 1
