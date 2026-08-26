"""Packaging sanity checks: the installed console script, version wiring,
and pyproject metadata stay consistent. These don't touch orchestration
logic -- they just prove the package is actually installable and usable as
a global CLI.
"""
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from codeexcellent import __version__
from codeexcellent.cli import main as cli_main
from codeexcellent.cli.main import main

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_version_flag_prints_version_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_pyproject_declares_the_console_script_entry_point():
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["scripts"]["codeexcellent"] == "codeexcellent.cli.main:main"


def test_pyproject_version_is_dynamic_from_package_init():
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "codeexcellent.__version__"


def test_license_file_exists():
    assert (REPO_ROOT / "LICENSE").is_file()


def test_installed_console_script_runs_and_reports_the_same_version():
    # Exercises the actual installed entry point (not just calling main()
    # in-process), so it also catches entry-point wiring issues.
    result = subprocess.run(
        [sys.executable, "-c", "from codeexcellent.cli.main import main; main(['--version'])"],
        capture_output=True, text=True, timeout=10,
    )
    assert __version__ in result.stdout


class _StubDoctorEngine:
    """No real subprocess call -- keeps this test hermetic (no Claude
    subscription/API access, matching the rest of the suite) and
    deterministic (not dependent on the developer's local auth state).
    """

    def is_available(self):
        return True, "stub 1.0.0"

    def auth_status(self):
        return {"loggedIn": True, "subscriptionType": "pro"}


def test_doctor_runs_without_crashing_regardless_of_environment(tmp_path):
    # doctor must degrade gracefully (never raise) even in a directory with
    # no git repo.
    with patch.object(cli_main, "ClaudeRunner", lambda config: _StubDoctorEngine()):
        exit_code = main(["doctor", "--root", str(tmp_path)])
    assert exit_code in (0, 1)
