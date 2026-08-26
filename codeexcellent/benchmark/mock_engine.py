"""A deterministic, zero-cost CodingEngine used by `codeexcellent benchmark`'s
default (mock) mode. It proves out CodeExcellent's own decision-making
(difficulty scoring, strategy selection, budget allocation, call counts)
across the whole task suite without spending anything or requiring a real
Claude account. It does NOT produce correct code -- judging actual code
quality requires --live against the real `claude` CLI.
"""
from __future__ import annotations

from pathlib import Path

from codeexcellent.claude.engine import CodingEngine
from codeexcellent.core.models import Budget, ClaudeCallResult


class MockBenchmarkEngine(CodingEngine):
    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> tuple[bool, str]:
        return True, "mock benchmark engine"

    def execute(
        self,
        prompt: str,
        cwd: str,
        budget: Budget,
        *,
        json_schema=None,
        session_id=None,
        allowed_tools=None,
    ) -> ClaudeCallResult:
        self.calls += 1

        if json_schema is not None:
            # Plan/review calls -- return a minimal valid structured response.
            if "score" in json_schema.get("properties", {}):
                structured = {"score": 8.0, "complete": True, "issues": []}
            else:
                structured = {"steps": ["Make the requested change"], "risk_notes": []}
            return ClaudeCallResult(
                success=True, result_text="", session_id=session_id or "mock-session",
                cost_usd=0.001, input_tokens=50, output_tokens=20, duration_ms=50,
                num_turns=1, stop_reason="end_turn", structured_output=structured,
            )

        # Implementation call: touch the first source file in cwd so the run
        # has a non-empty diff to evaluate, without claiming to be correct.
        root = Path(cwd)
        touched = None
        for path in sorted(root.rglob("*.py")):
            if ".git" in path.parts:
                continue
            content = path.read_text(errors="ignore")
            path.write_text(content + "\n# touched by mock benchmark engine\n")
            touched = path
            break

        return ClaudeCallResult(
            success=True,
            result_text=f"Edited {touched.name if touched else 'no file'} (mock).",
            session_id=session_id or "mock-session",
            cost_usd=0.002, input_tokens=200, output_tokens=80, duration_ms=100,
            num_turns=1, stop_reason="end_turn",
        )
