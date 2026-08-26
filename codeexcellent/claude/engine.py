"""CodingEngine is the seam that keeps CodeExcellent from being a
Claude-specific script (section 32). ClaudeEngine is the only implementation
today; a future OpenCodeEngine/LocalModelEngine would implement this same
interface and slot into the orchestrator unchanged.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from codeexcellent.core.models import Budget, ClaudeCallResult


class CodingEngine(ABC):
    @abstractmethod
    def execute(
        self,
        prompt: str,
        cwd: str,
        budget: Budget,
        *,
        json_schema: dict | None = None,
        session_id: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> ClaudeCallResult:
        """Run one coding turn against the target repository at `cwd`."""

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """Return (available, detail) -- used by `codeexcellent doctor`."""
