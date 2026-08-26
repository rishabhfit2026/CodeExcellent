"""Runs the target project's own tests directly via subprocess -- this is
never delegated to Claude, so validating a change never costs a Claude call.
Detection is best-effort: pick the first matching project type and skip
cleanly if nothing recognizable is found.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from codeexcellent.core.models import RepoContext, SuiteRunResult
from codeexcellent.core.platform_utils import python_executable, resolve_executable

_PYTEST_SUMMARY = re.compile(r"(\d+) passed|(\d+) failed")
_NPM_SUMMARY_PASS = re.compile(r"(\d+) passing")
_NPM_SUMMARY_FAIL = re.compile(r"(\d+) failing")
_GO_FAIL = re.compile(r"^--- FAIL", re.MULTILINE)
_GO_OK = re.compile(r"^ok\s", re.MULTILINE)


def _detect_command(repo: RepoContext) -> list[str] | None:
    """Returns the logical command (for display/branching) -- `run()`
    resolves the executable name to a real path separately, since the
    resolved path shouldn't leak into the displayed command or the `go`
    branch check below.
    """
    root = Path(repo.root)
    if "python" in repo.project_types and (repo.test_dirs or (root / "tests").exists()):
        return [python_executable(), "-m", "pytest", "-q"]
    if "node" in repo.project_types and (root / "package.json").exists():
        return ["npm", "test", "--silent"]
    if "go" in repo.project_types:
        return ["go", "test", "./..."]
    if "rust" in repo.project_types:
        return ["cargo", "test"]
    return None


def run(repo: RepoContext, timeout_seconds: int = 300) -> SuiteRunResult:
    cmd = _detect_command(repo)
    if not cmd:
        return SuiteRunResult(ran=False, success=True, output_tail="No recognizable test suite found; skipped.")

    # Resolve just the executable (e.g. "npm" -> "npm.cmd" on Windows) --
    # subprocess.run with a bare list of args doesn't search PATHEXT the way
    # a shell would, so an unresolved ".cmd"/".bat" command fails to launch.
    resolved_cmd = [resolve_executable(cmd[0]), *cmd[1:]]

    try:
        proc = subprocess.run(
            resolved_cmd, cwd=repo.root, capture_output=True, text=True, timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return SuiteRunResult(ran=False, success=True, command=" ".join(cmd), output_tail=f"'{cmd[0]}' not available; skipped.")
    except subprocess.TimeoutExpired:
        return SuiteRunResult(ran=True, success=False, command=" ".join(cmd), output_tail=f"Test run timed out after {timeout_seconds}s")

    output = proc.stdout + "\n" + proc.stderr
    passed, failed = 0, 0

    for match in _PYTEST_SUMMARY.finditer(output):
        if match.group(1):
            passed = int(match.group(1))
        if match.group(2):
            failed = int(match.group(2))

    npm_pass = _NPM_SUMMARY_PASS.search(output)
    npm_fail = _NPM_SUMMARY_FAIL.search(output)
    if npm_pass:
        passed = int(npm_pass.group(1))
    if npm_fail:
        failed = int(npm_fail.group(1))

    if cmd[0] == "go":
        failed = len(_GO_FAIL.findall(output))
        passed = len(_GO_OK.findall(output)) if failed == 0 else passed

    success = proc.returncode == 0
    tail = "\n".join(output.strip().splitlines()[-30:])

    return SuiteRunResult(
        ran=True, passed=passed, failed=failed, command=" ".join(cmd),
        success=success, output_tail=tail,
    )
