"""Git-awareness so CodeExcellent never silently clobbers work that was
already there (section 21). This does not attempt to sandbox Claude's edits
(Claude edits the real working tree) -- it establishes a baseline so callers
can warn about pre-existing changes and report only what this run touched.
"""
from __future__ import annotations

import subprocess


def changed_files_since(root: str, baseline_dirty: list[str]) -> list[str]:
    """Diff current git status against the pre-execution baseline to isolate
    files this run touched, ignoring pre-existing uncommitted changes.
    """
    try:
        result = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    current = {line[3:] for line in result.stdout.splitlines() if line.strip()}
    baseline = set(baseline_dirty)
    return sorted(current - baseline)
