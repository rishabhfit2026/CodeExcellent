import json
import subprocess
from unittest.mock import patch

from codeexcellent.claude.claude_engine import ClaudeRunner
from codeexcellent.core.models import Budget

CONFIG = {"claude": {"permission_mode": "acceptEdits", "allowed_tools": ["Read", "Edit"], "model": None}}
BUDGET = Budget(band="trivial", effort="low", max_budget_usd=0.5, max_budget_usd_step=0.5, max_claude_calls=1, max_retries=1, timeout_seconds=60)


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_successful_call_parses_usage_and_cost():
    payload = {
        "is_error": False, "result": "Done.", "session_id": "abc123",
        "total_cost_usd": 0.0123, "usage": {"input_tokens": 50, "output_tokens": 20},
        "duration_ms": 1200, "num_turns": 1, "stop_reason": "end_turn",
    }
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))):
        runner = ClaudeRunner(CONFIG)
        result = runner.execute("do the thing", cwd=".", budget=BUDGET)

    assert result.success is True
    assert result.cost_usd == 0.0123
    assert result.input_tokens == 50
    assert result.session_id == "abc123"


def test_json_schema_call_captures_structured_output():
    payload = {
        "is_error": False, "result": '{"score": 8, "complete": true, "issues": []}',
        "structured_output": {"score": 8, "complete": True, "issues": []},
        "usage": {}, "total_cost_usd": 0.01,
    }
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))) as mock_run:
        runner = ClaudeRunner(CONFIG)
        result = runner.execute("review this", cwd=".", budget=BUDGET, json_schema={"type": "object"})

    assert result.structured_output == {"score": 8, "complete": True, "issues": []}
    cmd = mock_run.call_args.args[0]
    assert "--json-schema" in cmd


def test_command_includes_confirmed_flags_only():
    payload = {"is_error": False, "result": "ok", "usage": {}, "total_cost_usd": 0}
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))) as mock_run:
        runner = ClaudeRunner(CONFIG)
        runner.execute("task", cwd="/tmp", budget=BUDGET)

    cmd = mock_run.call_args.args[0]
    # The binary is resolved to its full path (cross-platform: on Windows an
    # npm-installed CLI needs the ".cmd"/".ps1" extension, which a bare-name
    # subprocess launch won't find) -- only the basename is still "claude".
    from pathlib import Path

    assert Path(cmd[0]).stem == "claude"
    assert "-p" in cmd
    assert "--effort" in cmd and "low" in cmd
    assert "--max-budget-usd" in cmd and "0.5" in cmd
    assert "--permission-mode" in cmd and "acceptEdits" in cmd


def test_is_error_flag_marks_call_unsuccessful():
    payload = {"is_error": True, "result": "permission denied", "usage": {}, "total_cost_usd": 0}
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))):
        runner = ClaudeRunner(CONFIG)
        result = runner.execute("task", cwd=".", budget=BUDGET)
    assert result.success is False
    assert result.error == "permission denied"


def test_nonzero_exit_without_stdout_reports_error():
    with patch("subprocess.run", return_value=_completed(stdout="", stderr="boom", returncode=1)):
        runner = ClaudeRunner(CONFIG)
        result = runner.execute("task", cwd=".", budget=BUDGET)
    assert result.success is False
    assert "boom" in result.error


def test_invalid_json_output_reports_error():
    with patch("subprocess.run", return_value=_completed(stdout="not json")):
        runner = ClaudeRunner(CONFIG)
        result = runner.execute("task", cwd=".", budget=BUDGET)
    assert result.success is False
    assert "JSON" in result.error


def test_timeout_reports_error():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=60)):
        runner = ClaudeRunner(CONFIG)
        result = runner.execute("task", cwd=".", budget=BUDGET)
    assert result.success is False
    assert "timed out" in result.error


def test_missing_binary_reports_error():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        runner = ClaudeRunner(CONFIG)
        result = runner.execute("task", cwd=".", budget=BUDGET)
    assert result.success is False
    assert "not installed" in result.error
