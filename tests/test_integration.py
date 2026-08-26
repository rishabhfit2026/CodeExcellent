"""End-to-end pipeline tests using a mocked CodingEngine -- no real Claude
subscription/API access is required (section 28).
"""
from pathlib import Path

from codeexcellent.claude.engine import CodingEngine
from codeexcellent.config.settings import load_config
from codeexcellent.core.engine import run
from codeexcellent.core.models import Budget, ClaudeCallResult

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
