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
    assert "CodeExcellent v" in out
    assert "Repository:" in out
    assert "Claude CLI: ✓" in out
    assert 'Type a task, or "exit" to quit.' in out


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
    assert "Difficulty:" in out
    assert "Strategy:" in out
    assert "Implementing..." in out
    assert "Task completed" in out
    assert "Quality:" in out
    # Conciseness: the interactive loop must NOT dump the full one-shot
    # `analyze`/`run` report sections for every turn.
    assert "Resource forecast" not in out
    assert "Max Claude calls:" not in out


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
