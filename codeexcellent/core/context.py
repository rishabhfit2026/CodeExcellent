"""Decides what Claude actually needs to see. Reads only the files the
RepositoryAnalyzer already ranked as relevant, truncates each, and caps the
total -- this is the piece of the system that keeps prompts (and therefore
cost) small instead of shipping the whole repo.
"""
from __future__ import annotations

from pathlib import Path

from codeexcellent.core.models import ContextBundle, RepoContext


def build(repo: RepoContext, relevant_files: list[str], config: dict) -> ContextBundle:
    ctx_cfg = config.get("context", {})
    max_files = int(ctx_cfg.get("max_files", 12))
    max_bytes_per_file = int(ctx_cfg.get("max_bytes_per_file", 4000))
    max_total_bytes = int(ctx_cfg.get("max_total_bytes", 16000))

    root = Path(repo.root)
    files: dict[str, str] = {}
    total = 0

    for rel_path in relevant_files[:max_files]:
        if total >= max_total_bytes:
            break
        full_path = root / rel_path
        try:
            content = full_path.read_text(errors="ignore")
        except OSError:
            continue
        truncated = content[:max_bytes_per_file]
        remaining = max_total_bytes - total
        truncated = truncated[:remaining]
        files[rel_path] = truncated
        total += len(truncated)

    summary_lines = [
        f"Project types: {', '.join(repo.project_types) or 'unknown'}",
        f"Languages: {', '.join(repo.languages) or 'unknown'}",
        f"Frameworks: {', '.join(repo.frameworks) or 'none detected'}",
    ]
    if repo.entry_points:
        summary_lines.append(f"Entry points: {', '.join(repo.entry_points)}")
    if repo.test_dirs:
        summary_lines.append(f"Test locations: {', '.join(repo.test_dirs)}")
    if repo.git_branch:
        summary_lines.append(f"Git branch: {repo.git_branch}")

    return ContextBundle(summary="\n".join(summary_lines), files=files, total_bytes=total)


def render_prompt_context(bundle: ContextBundle) -> str:
    """Render the bundle as the block of text prepended to a Claude prompt."""
    parts = [f"Repository summary:\n{bundle.summary}"]
    if bundle.files:
        parts.append("Relevant files (may be truncated):")
        for path, content in bundle.files.items():
            parts.append(f"--- {path} ---\n{content}")
    return "\n\n".join(parts)
