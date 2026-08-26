"""Tests for the benchmark framework (phase 8): task validation wiring and
report aggregation. Uses scripted/mock engines -- no real Claude calls.
"""
from pathlib import Path

from codeexcellent.benchmark import runner
from codeexcellent.benchmark.mock_engine import MockBenchmarkEngine
from codeexcellent.benchmark.tasks import BenchmarkTask
from codeexcellent.claude.engine import CodingEngine
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import ClaudeCallResult

CONFIG = load_config()


class _CorrectFixEngine(CodingEngine):
    """Makes the actual correct edit a task asks for, so validate() should pass."""

    def is_available(self):
        return True, "mock"

    def execute(self, prompt, cwd, budget, *, json_schema=None, session_id=None, allowed_tools=None):
        greet_path = Path(cwd) / "greet.py"
        if greet_path.exists():
            greet_path.write_text('def greet(name):\n    return "Hello, " + name\n')
        return ClaudeCallResult(
            success=True, result_text="Done.", session_id="s1", cost_usd=0.01,
            input_tokens=10, output_tokens=10, duration_ms=100, num_turns=1, stop_reason="end_turn",
        )


def _rename_task(validate=None) -> BenchmarkTask:
    from codeexcellent.benchmark.tasks import _validate_trivial_rename, _write

    return BenchmarkTask(
        "trivial_rename", "trivial", "Rename the parameter nam to name in greet.py",
        lambda r: _write(r, "greet.py", 'def greet(nam):\n    return "Hello, " + nam\n'),
        expected_behavior="parameter renamed",
        validate=validate if validate is not None else _validate_trivial_rename,
    )


def test_validate_passes_when_the_engine_makes_the_correct_change():
    report = runner.run_suite(CONFIG, _CorrectFixEngine, "live", tasks=[_rename_task()])
    result = report.results[0]
    assert result.validated is True
    assert "renamed" in result.validation_message


def test_validate_fails_when_the_engine_does_not_make_the_change():
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[_rename_task()])
    result = report.results[0]
    assert result.validated is False
    assert result.validation_message


def test_tasks_without_a_validate_function_report_none():
    from codeexcellent.benchmark.tasks import _write

    task = BenchmarkTask(
        "no_validator", "trivial", "Do something", lambda r: _write(r, "x.py", "pass\n"),
        expected_behavior="something happens",
    )
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[task])
    assert report.results[0].validated is None
    assert "validated_tasks" not in report.totals()


def test_totals_computes_validated_pass_rate_only_over_validated_tasks():
    from codeexcellent.benchmark.tasks import _write

    unvalidated = BenchmarkTask(
        "no_validator", "trivial", "Do something", lambda r: _write(r, "x.py", "pass\n"),
        expected_behavior="something happens",
    )
    report = runner.run_suite(
        CONFIG, _CorrectFixEngine, "live", tasks=[_rename_task(), unvalidated],
    )
    totals = report.totals()
    assert totals["validated_tasks"] == 1  # only the task with a validate() counts
    assert totals["validated_pass_rate"] == 1.0


def test_compare_totals_is_none_without_a_raw_comparison():
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[_rename_task()], compare=False)
    assert report.compare_totals() is None


def test_trivial_rename_validator_does_not_false_positive_on_substring(tmp_path):
    # Regression: a naive "nam" in content check would flag "name" itself
    # as still containing the old identifier and always report failure.
    from codeexcellent.benchmark.tasks import _validate_trivial_rename

    (tmp_path / "greet.py").write_text('def greet(name):\n    return "Hello, " + name\n')
    validated, message = _validate_trivial_rename(tmp_path)
    assert validated is True, message


def test_trivial_constant_validator_does_not_false_positive_on_prefix(tmp_path):
    # Regression: "MAX_RETRIES = 5" is a substring of "MAX_RETRIES = 50". The
    # untouched TIMEOUT_SECONDS is included so this fails specifically on the
    # MAX_RETRIES value, not incidentally on the (also-checked) other field.
    from codeexcellent.benchmark.tasks import _validate_trivial_constant

    (tmp_path / "config.py").write_text("MAX_RETRIES = 50\nTIMEOUT_SECONDS = 30\n")
    validated, message = _validate_trivial_constant(tmp_path)
    assert validated is False, "50 should not satisfy a check for exactly 5"
    assert "MAX_RETRIES" in message


def test_run_suite_reports_predicted_difficulty_and_mode():
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[_rename_task()])
    result = report.results[0]
    assert result.predicted_difficulty >= 0
    assert result.mode
    assert result.claude_calls >= 1


# --- phase 9: isolation ------------------------------------------------------

class _MarkerEngine(CodingEngine):
    """Writes a marker file and records whether it already existed at the
    start of the call -- used to prove one task's directory can't leak into
    another's.
    """

    def __init__(self):
        self.saw_marker_at_start: list[bool] = []
        self.dirs_used: list[str] = []

    def is_available(self):
        return True, "mock"

    def execute(self, prompt, cwd, budget, *, json_schema=None, session_id=None, allowed_tools=None):
        marker = Path(cwd) / "marker.txt"
        self.saw_marker_at_start.append(marker.exists())
        self.dirs_used.append(cwd)
        marker.write_text("touched")
        return ClaudeCallResult(
            success=True, result_text="Done.", session_id="s1", cost_usd=0.01,
            input_tokens=10, output_tokens=10, duration_ms=100, num_turns=1, stop_reason="end_turn",
        )


def test_running_the_same_task_twice_never_leaks_state_between_runs():
    # The literal isolation contract from phase 9: prepare clean state ->
    # execute -> destroy -> prepare the SAME original state -> execute
    # again. A stray file from run 1 must never be visible in run 2.
    engine = _MarkerEngine()
    report = runner.run_suite(CONFIG, lambda: engine, "mock", tasks=[_rename_task(), _rename_task()])

    assert engine.saw_marker_at_start == [False, False]
    assert len(set(engine.dirs_used)) == 2  # two genuinely different directories
    assert report.results[0].task_id == report.results[1].task_id == "trivial_rename"


def test_compare_mode_runs_codeexcellent_and_raw_in_separate_directories(monkeypatch):
    engine = _MarkerEngine()
    raw_dirs_seen: list[str] = []

    def _fake_run_raw(request, root, timeout_seconds=300):
        raw_dirs_seen.append(root)
        # If isolation were broken, the CodeExcellent run's marker file
        # would already exist here.
        assert not (Path(root) / "marker.txt").exists()
        return True, 0.05, 500

    monkeypatch.setattr(runner, "run_raw", _fake_run_raw)
    report = runner.run_suite(CONFIG, lambda: engine, "live", tasks=[_rename_task()], compare=True)

    assert len(raw_dirs_seen) == 1
    assert raw_dirs_seen[0] not in engine.dirs_used  # genuinely different directory
    assert report.results[0].raw_cost_usd == 0.05


# --- phase 10/11: metrics and report shape -----------------------------------

def test_benchmark_result_captures_all_phase_10_metric_categories():
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[_rename_task()])
    result = report.results[0]

    # correctness
    assert result.status
    assert hasattr(result, "validated") and hasattr(result, "validation_message")
    # efficiency
    assert result.cost_usd >= 0
    assert result.duration_ms >= 0
    assert result.claude_calls >= 1
    assert result.retries >= 0
    # quality
    assert result.quality_score is not None
    assert isinstance(result.tests_ran, bool)
    assert result.tests_passed >= 0 and result.tests_failed >= 0
    assert result.files_changed >= 0
    # prediction
    assert 0 <= result.predicted_difficulty <= 10
    assert result.predicted_band
    assert 0 <= result.confidence <= 1
    assert result.risk
    assert isinstance(result.planning_used, bool)


def test_totals_never_reports_tests_metrics_when_no_task_ran_tests():
    # medium_db_field's fixture doesn't trigger testing_required, so no
    # tests run for it -- the aggregate must not report a misleading 0%
    # test_pass_rate as if tests had been attempted and all failed.
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[_rename_task()])
    assert "tasks_with_tests_run" not in report.totals()
    assert "test_pass_rate" not in report.totals()


def test_by_difficulty_only_reports_bands_actually_present():
    from codeexcellent.benchmark.tasks import _write

    easy_task = BenchmarkTask(
        "easy_task", "easy", "Do an easy thing", lambda r: _write(r, "x.py", "pass\n"),
        expected_behavior="something happens",
    )
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[_rename_task(), easy_task])
    breakdown = report.by_difficulty()

    assert set(breakdown.keys()) == {"trivial", "easy"}
    assert breakdown["trivial"]["tasks"] == 1
    assert breakdown["easy"]["tasks"] == 1
    assert "hard" not in breakdown  # no hard tasks were run -- not reported as 0%
