"""Regression coverage for a real V1->V2 bug: `codeexcellent analyze` used to
call the heuristic difficulty scorer directly and skip the strategy
selector, so a CRITICAL-risk task showed "Strategy: direct" instead of the
mandatory review it should get. `engine.plan()` is now the one function both
`run()` and the CLI call, so they cannot diverge again.
"""
from codeexcellent.config.settings import load_config
from codeexcellent.core.engine import plan
from codeexcellent.core.models import ExecutionMode

CONFIG = load_config()


def test_plan_routes_critical_risk_through_strategy_selector(tmp_path):
    result = plan("Update the payment charge amount calculation", str(tmp_path), CONFIG)
    assert result.difficulty.mode in (ExecutionMode.REVIEW_REQUIRED, ExecutionMode.FULL)
    assert result.difficulty.risk_level.value == "critical"
    assert not result.blocked_reason


def test_plan_trivial_task_is_direct(tmp_path):
    result = plan("Rename the variable userName to username.", str(tmp_path), CONFIG)
    assert result.difficulty.mode == ExecutionMode.DIRECT


def test_plan_blocks_oversized_repository(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    tiny_ceiling_config = {**CONFIG, "repository": {**CONFIG["repository"], "hard_file_ceiling": 0}}
    result = plan("Fix a bug", str(tmp_path), tiny_ceiling_config)
    assert result.blocked_reason is not None


def test_plan_computes_forecast_and_fingerprint(tmp_path):
    result = plan("Fix a small validation bug", str(tmp_path), CONFIG)
    assert result.forecast is not None
    assert result.fingerprint.key()


def test_plan_blocks_chitchat_before_any_claude_call(tmp_path):
    # Regression: a plain greeting typed into the interactive REPL scored as
    # a valid low-difficulty DIRECT task instead of being recognized as not
    # a coding request at all -- this must be caught in plan() itself, which
    # never calls Claude, so run() can short-circuit for free (see
    # _blocked_report in engine.py).
    result = plan("hey how are you", str(tmp_path), CONFIG)
    assert result.blocked_reason is not None


def test_plan_does_not_block_a_terse_real_task(tmp_path):
    result = plan("the login is broken", str(tmp_path), CONFIG)
    assert result.blocked_reason is None
