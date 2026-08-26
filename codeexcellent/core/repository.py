"""Inspects the target repository without sending its contents anywhere.
Produces a RepoContext (project type/frameworks/tests/git state) and a
keyword-ranked shortlist of files relevant to the current task, so the
ContextManager never has to consider "send everything".
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from codeexcellent.core.models import RepoContext

_PROJECT_MARKERS = {
    "pyproject.toml": ("python", None),
    "setup.py": ("python", None),
    "requirements.txt": ("python", None),
    "package.json": ("node", None),
    "Cargo.toml": ("rust", None),
    "go.mod": ("go", None),
    "pom.xml": ("java", None),
    "build.gradle": ("java", None),
    "Gemfile": ("ruby", None),
    "composer.json": ("php", None),
}

_LANGUAGE_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".rb": "ruby", ".php": "php", ".c": "c", ".cpp": "c++",
}

_FRAMEWORK_HINTS = {
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "express": "Express", "react": "React", "next": "Next.js",
    "vue": "Vue", "pytest": "pytest", "sqlalchemy": "SQLAlchemy",
}


def _git_info(root: Path) -> tuple[bool, str | None, list[str]]:
    try:
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False, None, []

    branch = None
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        branch = r.stdout.strip() or None
    except subprocess.TimeoutExpired:
        pass

    dirty: list[str] = []
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        dirty = [line[3:] for line in r.stdout.splitlines() if line.strip()]
    except subprocess.TimeoutExpired:
        pass

    return True, branch, dirty


def _walk_files(root: Path, ignored_dirs: set[str], max_files: int) -> list[Path]:
    files: list[Path] = []
    stack = [root]
    while stack and len(files) < max_files:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in ignored_dirs or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.is_file():
                files.append(entry)
                if len(files) >= max_files:
                    break
    return files


def analyze(root: str, config: dict) -> RepoContext:
    root_path = Path(root).resolve()
    repo_cfg = config.get("repository", {})
    ignored_dirs = set(repo_cfg.get("ignored_dirs", []))
    max_files = int(repo_cfg.get("max_files_scanned", 4000))

    project_types: set[str] = set()
    config_files: list[str] = []
    for marker, (ptype, _) in _PROJECT_MARKERS.items():
        marker_path = root_path / marker
        if marker_path.is_file():
            project_types.add(ptype)
            config_files.append(marker)

    for extra in ("Dockerfile", "docker-compose.yml", ".env.example", "tox.ini", "Makefile"):
        if (root_path / extra).is_file():
            config_files.append(extra)

    files = _walk_files(root_path, ignored_dirs, max_files)

    languages: set[str] = set()
    entry_points: list[str] = []
    test_dirs: set[str] = set()
    for f in files:
        lang = _LANGUAGE_BY_EXT.get(f.suffix)
        if lang:
            languages.add(lang)
        rel = f.relative_to(root_path)
        rel_str = str(rel)
        if f.name in ("main.py", "app.py", "manage.py", "index.js", "server.js", "main.go"):
            entry_points.append(rel_str)
        if "test" in f.parts or f.name.startswith("test_") or f.name.endswith(("_test.py", ".test.js", ".test.ts")):
            test_dirs.add(str(rel.parent))

    frameworks: set[str] = set()
    for marker in ("requirements.txt", "package.json", "pyproject.toml"):
        marker_path = root_path / marker
        if marker_path.is_file():
            try:
                content = marker_path.read_text(errors="ignore").lower()
                for hint, name in _FRAMEWORK_HINTS.items():
                    if hint in content:
                        frameworks.add(name)
            except OSError:
                pass

    has_git, branch, dirty = _git_info(root_path)

    file_count = len(files)
    # Repo complexity: file count + number of distinct languages/frameworks,
    # damped with a log-ish curve via simple thresholds (not a raw average).
    complexity = 0.0
    complexity += min(4.0, file_count / 250.0)
    complexity += min(3.0, len(languages) * 1.2)
    complexity += min(3.0, len(frameworks) * 1.0)
    complexity = min(10.0, complexity)

    return RepoContext(
        root=str(root_path),
        project_types=sorted(project_types),
        languages=sorted(languages),
        frameworks=sorted(frameworks),
        entry_points=sorted(set(entry_points)),
        config_files=sorted(set(config_files)),
        test_dirs=sorted(test_dirs),
        has_git=has_git,
        git_branch=branch,
        git_dirty_files=dirty,
        file_count=file_count,
        relevant_files=[],
        repo_complexity=round(complexity, 2),
    )


def snapshot_mtimes(root: str, config: dict) -> dict[str, float]:
    """Fallback change-detection for non-git directories: mtimes before/after
    a run are diffed to report which files an execution touched.
    """
    root_path = Path(root).resolve()
    repo_cfg = config.get("repository", {})
    ignored_dirs = set(repo_cfg.get("ignored_dirs", []))
    max_files = int(repo_cfg.get("max_files_scanned", 4000))

    files = _walk_files(root_path, ignored_dirs, max_files)
    snapshot: dict[str, float] = {}
    for f in files:
        try:
            snapshot[str(f.relative_to(root_path))] = f.stat().st_mtime
        except OSError:
            continue
    return snapshot


def diff_mtimes(before: dict[str, float], after: dict[str, float]) -> list[str]:
    changed = []
    for path, mtime in after.items():
        if path not in before or before[path] != mtime:
            changed.append(path)
    return sorted(changed)


def find_relevant_files(root: str, task_request: str, config: dict, max_results: int = 12) -> list[str]:
    """Keyword-score files by path/name relevance to the task text. Cheap and
    local -- no repo content leaves the machine at this stage.
    """
    root_path = Path(root).resolve()
    repo_cfg = config.get("repository", {})
    ignored_dirs = set(repo_cfg.get("ignored_dirs", []))
    max_files = int(repo_cfg.get("max_files_scanned", 4000))

    words = {w for w in task_request.lower().split() if len(w) > 3}
    if not words:
        return []

    files = _walk_files(root_path, ignored_dirs, max_files)
    scored: list[tuple[float, Path]] = []
    for f in files:
        rel = str(f.relative_to(root_path)).lower()
        score = sum(1.0 for w in words if w in rel)
        if score <= 0:
            continue  # no keyword overlap at all -- do not include as "relevant"
        if f.suffix in _LANGUAGE_BY_EXT:
            score += 0.1  # tiebreaker among files that already matched
        scored.append((score, f))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [str(f.relative_to(root_path)) for _, f in scored[:max_results]]
