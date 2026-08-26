"""The only place in CodeExcellent that shells out to the Claude CLI.
Only uses flags confirmed present in `claude --help` for the installed
version -- no invented options, no assumed API access. Real cost/token usage
comes straight from `--output-format json`; nothing here is fabricated
(section 33).
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess

from codeexcellent.claude.engine import CodingEngine
from codeexcellent.core.models import Budget, ClaudeCallResult
from codeexcellent.core.platform_utils import resolve_executable

logger = logging.getLogger("codeexcellent.claude")


class ClaudeRunner(CodingEngine):
    def __init__(self, config: dict, binary: str = "claude"):
        self.binary = binary
        claude_cfg = config.get("claude", {})
        self.permission_mode = claude_cfg.get("permission_mode", "acceptEdits")
        self.allowed_tools = claude_cfg.get("allowed_tools", [])
        self.model = claude_cfg.get("model")

    def is_available(self) -> tuple[bool, str]:
        path = shutil.which(self.binary)
        if not path:
            return False, f"'{self.binary}' not found on PATH"
        try:
            # Use the resolved path, not the bare name: on Windows an npm-
            # installed CLI is a ".cmd"/".ps1" shim that `shutil.which` finds
            # but a bare-name subprocess launch (no shell) will not.
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=10,
            )
            return True, result.stdout.strip() or result.stderr.strip()
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f"'{self.binary}' found but failed to run: {exc}"

    def auth_status(self) -> dict | None:
        """Best-effort read of `claude auth status`. Returns None if the
        binary is missing or the output isn't parseable JSON -- callers
        (doctor) treat that as "unknown", not as a hard failure.
        """
        try:
            result = subprocess.run(
                [resolve_executable(self.binary), "auth", "status"], capture_output=True, text=True, timeout=10,
            )
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            return None

    def _build_command(
        self,
        prompt: str,
        budget: Budget,
        json_schema: dict | None,
        session_id: str | None,
        allowed_tools: list[str] | None,
    ) -> list[str]:
        cmd = [resolve_executable(self.binary), "-p", prompt, "--output-format", "json"]
        cmd += ["--effort", budget.effort]
        cmd += ["--max-budget-usd", str(budget.max_budget_usd)]
        cmd += ["--permission-mode", self.permission_mode]
        tools = allowed_tools if allowed_tools is not None else self.allowed_tools
        if tools:
            cmd += ["--allowedTools", *tools]
        if self.model:
            cmd += ["--model", self.model]
        if json_schema is not None:
            cmd += ["--json-schema", json.dumps(json_schema)]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

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
        cmd = self._build_command(prompt, budget, json_schema, session_id, allowed_tools)
        logger.debug("claude invocation: effort=%s max_budget_usd=%s cwd=%s", budget.effort, budget.max_budget_usd, cwd)

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=budget.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ClaudeCallResult(
                success=False, result_text="", session_id=session_id, cost_usd=0.0,
                input_tokens=0, output_tokens=0, duration_ms=budget.timeout_seconds * 1000,
                num_turns=0, stop_reason=None,
                error=f"Claude CLI timed out after {budget.timeout_seconds}s",
            )
        except FileNotFoundError:
            return ClaudeCallResult(
                success=False, result_text="", session_id=session_id, cost_usd=0.0,
                input_tokens=0, output_tokens=0, duration_ms=0, num_turns=0, stop_reason=None,
                error=f"'{self.binary}' is not installed or not on PATH",
            )

        if proc.returncode != 0 and not proc.stdout.strip():
            return ClaudeCallResult(
                success=False, result_text="", session_id=session_id, cost_usd=0.0,
                input_tokens=0, output_tokens=0, duration_ms=0, num_turns=0, stop_reason=None,
                error=f"Claude CLI exited {proc.returncode}: {proc.stderr.strip()[:2000]}",
            )

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return ClaudeCallResult(
                success=False, result_text=proc.stdout[:2000], session_id=session_id, cost_usd=0.0,
                input_tokens=0, output_tokens=0, duration_ms=0, num_turns=0, stop_reason=None,
                error=f"Could not parse Claude CLI output as JSON: {proc.stderr.strip()[:500]}",
            )

        usage = data.get("usage", {})
        is_error = bool(data.get("is_error", False))
        return ClaudeCallResult(
            success=not is_error,
            result_text=data.get("result", ""),
            session_id=data.get("session_id", session_id),
            cost_usd=float(data.get("total_cost_usd", 0.0)),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            duration_ms=int(data.get("duration_ms", 0)),
            num_turns=int(data.get("num_turns", 0)),
            stop_reason=data.get("stop_reason"),
            error=data.get("result") if is_error else None,
            raw=data,
            structured_output=data.get("structured_output"),
        )
