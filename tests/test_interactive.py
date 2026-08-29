"""Interactive-mode CLI tests. Feeds scripted stdin via a mocked `input`, and
mocks ClaudeRunner so no real Claude subscription/API access is required.
"""
import json
from unittest.mock import patch

import pytest

from codeexcellent.cli import main as cli_main
from codeexcellent.core.models import ClaudeCallResult


class _StubEngine:
    """Stands in for ClaudeRunner: same interface, no subprocess calls."""

    def __init__(self, available=True, detail="stub 1.0.0"):
        self._available = available
        self._detail = detail
        self.calls = 0

    def is_available(self):
        return self._available, self._detail

    def auth_status(self):
        return {"loggedIn": True, "subscriptionType": "pro"}

    def execute(self, prompt, cwd, budget, *, json_schema=None, session_id=None, allowed_tools=None):
        self.calls += 1
        from pathlib import Path

        for path in sorted(Path(cwd).glob("*.py")):
            path.write_text(path.read_text() + "\n# edited\n")
            break
        return ClaudeCallResult(
            success=True, result_text="Done.", session_id="s1", cost_usd=0.01,
            input_tokens=10, output_tokens=10, duration_ms=100, num_turns=1, stop_reason="end_turn",
        )


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("def greet(nam):\n    return nam\n")
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def test_interactive_shows_startup_banner(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=["exit"]):
        exit_code = cli_main.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "CodeExcellent" in out and "v" in out
    assert "Repository" in out
    assert "Claude CLI" in out and "ready" in out
    assert "Type a task" in out and "/help" in out


def test_interactive_reports_missing_claude_cli(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine(available=False, detail="not found"))
    exit_code = cli_main.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not available" in captured.err
    assert "doctor" in captured.err


def test_interactive_handles_eof_gracefully(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=EOFError()):
        exit_code = cli_main.main([])
    assert exit_code == 0


def test_interactive_handles_keyboard_interrupt_between_prompts_gracefully(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=KeyboardInterrupt()):
        exit_code = cli_main.main([])
    assert exit_code == 0


def test_interactive_runs_a_task_with_concise_output(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=["Rename nam to name in app.py", "exit"]):
        exit_code = cli_main.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "difficulty" in out
    assert "strategy" in out
    assert "Task completed" in out
    assert "quality" in out
    # Conciseness: the interactive loop must NOT dump the full one-shot
    # `analyze`/`run` report sections for every turn.
    assert "Resource forecast" not in out
    assert "Max Claude calls:" not in out


def test_interactive_step_maps_engine_messages_to_short_progress_phrases():
    # The spinner text itself is transient (live-updating, cleared once the
    # task finishes) so it isn't present in captured final output -- this
    # tests the mapping function directly instead.
    assert cli_main._interactive_step("Calling Claude (attempt 1, effort=medium)...") == "Implementing..."
    assert cli_main._interactive_step("Calling Claude (attempt 2, effort=medium)...") == "Retrying..."
    assert cli_main._interactive_step("Running tests...") == "Testing..."
    assert cli_main._interactive_step("Running quality review...") == "Reviewing..."
    assert cli_main._interactive_step("Difficulty 4.0/10 (medium)...") is None


def test_interactive_blocked_task_does_not_crash_the_loop(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"repository": {"hard_file_ceiling": 0}}))
    monkeypatch.setenv("CODEEXCELLENT_CONFIG", str(config_path))

    with patch("builtins.input", side_effect=["fix something", "exit"]):
        exit_code = cli_main.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Blocked:" in out


def test_slash_help_shows_the_command_menu_without_treating_it_as_a_task(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=["/help", "exit"]):
        exit_code = cli_main.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "/doctor" in out
    assert "/loop" in out
    # Must not have gone through the task-analysis path at all.
    assert "difficulty" not in out


def test_slash_doctor_runs_inline_without_leaving_the_session(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=["/doctor", "exit"]):
        exit_code = cli_main.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Environment check" in out
    assert "Claude CLI" in out


def test_slash_loop_without_a_task_shows_usage_and_does_not_run_anything(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=["/loop", "exit"]):
        exit_code = cli_main.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Usage:" in out
    assert "difficulty" not in out


def test_slash_loop_runs_the_task_under_the_loop_budget(capsys, monkeypatch):
    from codeexcellent.budget import budget_manager
    from codeexcellent.config.settings import load_config

    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())

    captured = {}
    real_run_engine = cli_main.run_engine

    def _spy(request, root, config, engine, on_step=None, planned=None):
        captured["planned"] = planned
        return real_run_engine(request, root, config, engine, on_step=on_step, planned=planned)

    monkeypatch.setattr(cli_main, "run_engine", _spy)

    with patch("builtins.input", side_effect=["/loop rename nam to name in app.py", "exit"]):
        exit_code = cli_main.main([])

    assert exit_code == 0
    loop_budget = budget_manager.allocate_loop(load_config())
    assert captured["planned"].budget.max_claude_calls == loop_budget.max_claude_calls
    assert captured["planned"].budget.band == "loop"


def test_unknown_slash_command_shows_a_hint_and_does_not_crash(capsys, monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())
    with patch("builtins.input", side_effect=["/nonsense", "exit"]):
        exit_code = cli_main.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Unknown command" in out
