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
    # Regression: "MAX_RETRIES = 5" is a substring of "MAX_RETRIES = 50".
    from codeexcellent.benchmark.tasks import _validate_trivial_constant

    (tmp_path / "config.py").write_text("MAX_RETRIES = 50\n")
    validated, message = _validate_trivial_constant(tmp_path)
    assert validated is False, "50 should not satisfy a check for exactly 5"


def test_run_suite_reports_predicted_difficulty_and_mode():
    report = runner.run_suite(CONFIG, MockBenchmarkEngine, "mock", tasks=[_rename_task()])
    result = report.results[0]
    assert result.predicted_difficulty >= 0
    assert result.mode
    assert result.claude_calls >= 1
