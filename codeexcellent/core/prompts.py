"""Prompt templates. Kept separate from the orchestrator so the "minimal
change, no unnecessary comments, scope discipline" instructions (sections
16-18) live in one reviewable place instead of being scattered inline.
"""
from __future__ import annotations

from codeexcellent.core.models import DifficultyScore

_DISCIPLINE = (
    "Guidelines:\n"
    "- Change only what this task requires. Do not perform unrelated refactoring.\n"
    "- Prefer clear code and naming over comments; only add a comment when it explains "
    "a non-obvious WHY (a constraint, a workaround, a subtle invariant).\n"
    "- Implement the minimal correct solution -- do not add speculative abstractions, "
    "config flags, or error handling for cases that cannot occur here.\n"
)

PLAN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "items": {"type": "string"}},
        "risk_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["steps"],
}


def implementation_prompt(request: str, context_text: str, difficulty: DifficultyScore, plan: str | None = None) -> str:
    parts = [f"Task: {request}\n", context_text, _DISCIPLINE]
    if plan:
        parts.append(f"Follow this plan:\n{plan}")
    if difficulty.testing_required:
        parts.append("Update or add tests as needed for this change.")
    return "\n\n".join(p for p in parts if p.strip())


def planning_prompt(request: str, context_text: str) -> str:
    return (
        f"Task: {request}\n\n{context_text}\n\n"
        "Do not write or edit any code yet. Produce a short implementation plan: "
        "a small ordered list of concrete steps, and any risk notes (backward "
        "compatibility, migrations, breaking changes). Respond with the requested JSON only."
    )
