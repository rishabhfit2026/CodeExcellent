import subprocess
import sys
from unittest.mock import patch

from codeexcellent.core import test_runner
from codeexcellent.core.models import RepoContext


def _repo(project_types, test_dirs=None, root=".") -> RepoContext:
    return RepoContext(
        root=root, project_types=project_types, languages=[], frameworks=[],
        entry_points=[], config_files=[], test_dirs=test_dirs or ["tests"],
        has_git=False, git_branch=None, git_dirty_files=[], file_count=1, relevant_files=[],
        repo_complexity=0.0,
    )


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_python_project_uses_running_interpreter_not_hardcoded_python3():
    with patch("subprocess.run", return_value=_completed("2 passed")) as mock_run:
        test_runner.run(_repo(["python"]))
    resolved_cmd = mock_run.call_args.args[0]
    assert resolved_cmd[0] == sys.executable


def test_npm_command_is_resolved_to_a_real_path_not_bare_name():
    # Regression: subprocess.run(["npm", ...]) fails on Windows because npm
    # resolves to npm.cmd, which a bare-name (non-shell) launch won't find.
    with patch("codeexcellent.core.test_runner.resolve_executable", return_value="/usr/local/bin/npm.cmd") as mock_resolve:
        with patch("subprocess.run", return_value=_completed("3 passing")) as mock_run:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                (__import__("pathlib").Path(tmp) / "package.json").write_text("{}")
                result = test_runner.run(_repo(["node"], test_dirs=[], root=tmp))

    mock_resolve.assert_called_with("npm")
    resolved_cmd = mock_run.call_args.args[0]
    assert resolved_cmd[0] == "/usr/local/bin/npm.cmd"
    assert result.command == "npm test --silent"  # displayed command stays the logical name
    assert result.passed == 3


def test_unresolved_command_still_reports_missing_gracefully():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = test_runner.run(_repo(["go"]))
    assert result.ran is False
    assert result.success is True
