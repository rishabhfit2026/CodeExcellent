"""Cross-platform subprocess helpers. `subprocess.run(["npm", ...])` fails on
Windows with FileNotFoundError because `npm` resolves to `npm.cmd`, and
Windows CreateProcess (unlike a shell) doesn't search PATHEXT for a bare
command name the way `shutil.which` does. Resolving the executable path
first works identically on every platform.
"""
from __future__ import annotations

import shutil
import sys


def resolve_executable(name: str) -> str:
    """Return the full resolved path to `name` if found on PATH, otherwise
    `name` unchanged so the caller's existing FileNotFoundError handling
    still applies.
    """
    return shutil.which(name) or name


def python_executable() -> str:
    """The interpreter currently running CodeExcellent -- more correct than
    guessing a binary name ("python3", "python", "py") that may not exist
    under that name on every platform.
    """
    return sys.executable
