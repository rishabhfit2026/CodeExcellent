"""`codeexcellent run --loop`: a much higher, still-finite call/cost ceiling
for a single prompt that should keep retrying with feedback until the
task's own validation says done, instead of stopping at the normal
difficulty-band budget (see budget_manager.allocate_loop).
"""
from unittest.mock import patch

import pytest

from codeexcellent.budget import budget_manager
from codeexcellent.cli import main as cli_main
from codeexcellent.config.settings import load_config
from codeexcellent.core.models import ClaudeCallResult

CONFIG = load_config()


class _StubEngine:
    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True, "stub 1.0.0"

    def execute(self, prompt, cwd, budget, *, json_schema=None, session_id=None, allowed_tools=None):
        self.calls += 1
        return ClaudeCallResult(
            success=True, result_text="Done.", session_id="s1", cost_usd=0.01,
            input_tokens=10, output_tokens=10, duration_ms=50, num_turns=1, stop_reason="end_turn",
        )


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("def greet(nam):\n    return nam\n")
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def test_loop_flag_overrides_the_budget_before_run_engine_is_called(monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())

    captured = {}
    real_run_engine = cli_main.run_engine

    def _spy(request, root, config, engine, on_step=None, planned=None):
        captured["planned"] = planned
        return real_run_engine(request, root, config, engine, on_step=on_step, planned=planned)

    monkeypatch.setattr(cli_main, "run_engine", _spy)

    exit_code = cli_main.main(["run", "build a small project", "--loop", "-y"])

    assert exit_code in (0, 2)  # COMPLETE or INCOMPLETE -- what matters here is the budget, not the outcome
    loop_budget = budget_manager.allocate_loop(CONFIG)
    assert captured["planned"].budget.max_claude_calls == loop_budget.max_claude_calls
    assert captured["planned"].budget.max_budget_usd == loop_budget.max_budget_usd
    assert captured["planned"].budget.band == "loop"


def test_without_loop_flag_the_normal_difficulty_band_budget_is_used(monkeypatch):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())

    captured = {}
    real_run_engine = cli_main.run_engine

    def _spy(request, root, config, engine, on_step=None, planned=None):
        captured["planned"] = planned
        return real_run_engine(request, root, config, engine, on_step=on_step, planned=planned)

    monkeypatch.setattr(cli_main, "run_engine", _spy)

    exit_code = cli_main.main(["run", "rename nam to name in app.py", "-y"])

    assert exit_code in (0, 2)
    assert captured["planned"].budget.band != "loop"


def test_loop_mode_asks_for_confirmation_unless_yes_flag_given(monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "ClaudeRunner", lambda config: _StubEngine())

    with patch("builtins.input", return_value="n"):
        exit_code = cli_main.main(["run", "build a small project", "--loop"])

    assert exit_code == 1
    assert "Aborted." in capsys.readouterr().out
