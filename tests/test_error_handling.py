"""Phase 10: the CLI must never leak a raw Python traceback to the user
unless --debug/$CODEEXCELLENT_DEBUG is set.
"""
import json
from unittest.mock import patch

import pytest

from codeexcellent.cli import main as cli_main


def test_invalid_config_json_fails_gracefully_not_with_a_traceback(tmp_path, capsys, monkeypatch):
    bad_config = tmp_path / "config.json"
    bad_config.write_text("{ not valid json")
    monkeypatch.setenv("CODEEXCELLENT_CONFIG", str(bad_config))

    exit_code = cli_main.main(["analyze", "fix the bug", "--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_unexpected_exception_is_caught_and_reported_cleanly(capsys):
    with patch.object(cli_main, "_dispatch", side_effect=RuntimeError("boom")):
        exit_code = cli_main.main(["doctor"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Unexpected error: boom" in captured.err
    assert "Traceback" not in captured.err
    assert "--debug" in captured.err


def test_debug_flag_lets_the_real_exception_propagate():
    with patch.object(cli_main, "_dispatch", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            cli_main.main(["doctor", "--debug"])


def test_debug_env_var_also_lets_the_real_exception_propagate(monkeypatch):
    monkeypatch.setenv("CODEEXCELLENT_DEBUG", "1")
    with patch.object(cli_main, "_dispatch", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            cli_main.main(["doctor"])


def test_keyboard_interrupt_at_top_level_exits_cleanly():
    with patch.object(cli_main, "_dispatch", side_effect=KeyboardInterrupt()):
        exit_code = cli_main.main(["doctor"])
    assert exit_code == 130


def test_version_and_help_still_exit_via_systemexit_not_swallowed():
    # argparse's own version/help/usage-error paths raise SystemExit, which
    # must NOT be caught by the generic Exception handler.
    with pytest.raises(SystemExit) as exc_info:
        cli_main.main(["--version"])
    assert exc_info.value.code == 0
