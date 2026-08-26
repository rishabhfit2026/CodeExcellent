"""ExecutionController: the orchestrator. Wires TaskAnalyzer -> RepoAnalyzer
-> DifficultyScorer -> BudgetManager -> ContextManager -> CodingEngine ->
TestRunner -> QualityChecker -> StopController into one run, with progressive
budget escalation and feedback-carrying retries. This is the only module that
knows the full pipeline; every other module is independently testable.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from codeexcellent.analyzer import difficulty_scorer, task_analyzer
from codeexcellent.budget import budget_manager
from codeexcellent.claude.engine import CodingEngine
from codeexcellent.core import context, git_safety, memory, prompts, repository, test_runner
from codeexcellent.core.models import (
    ExecutionAttempt,
    ExecutionMode,
    ExecutionReport,
    QualityResult,
    SuiteRunResult,
)
from codeexcellent.core.retry import build_retry_prompt
from codeexcellent.core.stop_controller import decide
from codeexcellent.quality import quality_checker

StepCallback = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _diff_text(root: str, changed_files: list[str], has_git: bool) -> str:
    if not changed_files:
        return ""
    if has_git:
        try:
            result = subprocess.run(
                ["git", "-C", root, "diff", "--", *changed_files],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                return result.stdout
            # Files may be untracked (new files) -- fall back to their content.
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    chunks = []
    for rel in changed_files[:10]:
        try:
            content = (Path(root) / rel).read_text(errors="ignore")
            chunks.append(f"--- {rel} (new/current content) ---\n{content[:2000]}")
        except OSError:
            continue
    return "\n\n".join(chunks)


def run(request: str, root: str, config: dict, engine: CodingEngine, on_step: StepCallback | None = None) -> ExecutionReport:
    on_step = on_step or _noop

    on_step("Analyzing task...")
    task = task_analyzer.analyze(request)

    on_step("Inspecting repository...")
    repo = repository.analyze(root, config)
    repo.relevant_files = repository.find_relevant_files(
        root, request, config, max_results=config.get("context", {}).get("max_files", 12)
    )

    difficulty = difficulty_scorer.score(task, repo, config)
    budget = budget_manager.allocate(difficulty, config)

    on_step(
        f"Difficulty {difficulty.value}/10 ({difficulty.band}), mode={difficulty.mode.value}, "
        f"budget=${budget.max_budget_usd} effort={budget.effort}"
    )

    context_bundle = context.build(repo, repo.relevant_files, config)
    context_text = context.render_prompt_context(context_bundle)

    mtime_baseline = None if repo.has_git else repository.snapshot_mtimes(root, config)

    plan_text: str | None = None
    plan_cost = 0.0
    if difficulty.mode == ExecutionMode.FULL:
        on_step("Planning...")
        plan_call = engine.execute(
            prompts.planning_prompt(request, context_text), root, budget,
            json_schema=prompts.PLAN_JSON_SCHEMA, allowed_tools=["Read", "Glob", "Grep"],
        )
        plan_cost += plan_call.cost_usd
        if plan_call.success and (plan_call.structured_output or plan_call.result_text):
            import json as _json
            try:
                plan_data = plan_call.structured_output if plan_call.structured_output is not None else _json.loads(plan_call.result_text)
                steps = plan_data.get("steps", [])
                risks = plan_data.get("risk_notes", [])
                plan_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
                if risks:
                    plan_text += "\n\nRisk notes:\n" + "\n".join(f"- {r}" for r in risks)
            except ValueError:
                plan_text = plan_call.result_text

    base_prompt = prompts.implementation_prompt(request, context_text, difficulty, plan=plan_text)

    attempts: list[ExecutionAttempt] = []
    session_id: str | None = None
    cost_so_far = plan_cost
    attempt_number = 0
    quality = QualityResult(score=0.0, complete=False, needs_more_work=True)
    status = "INCOMPLETE"
    current_budget = budget

    while True:
        attempt_number += 1
        on_step(f"Calling Claude (attempt {attempt_number}, effort={current_budget.effort})...")

        if attempt_number == 1:
            prompt = base_prompt
        else:
            prompt = build_retry_prompt(request, attempts[-1].call, attempts[-1].tests, attempts[-1].quality)

        call = engine.execute(prompt, root, current_budget, session_id=session_id)
        session_id = call.session_id or session_id
        cost_so_far += call.cost_usd

        if repo.has_git:
            changed = git_safety.changed_files_since(root, repo.git_dirty_files)
        else:
            after = repository.snapshot_mtimes(root, config)
            changed = repository.diff_mtimes(mtime_baseline or {}, after)

        if difficulty.testing_required:
            on_step("Running tests...")
            tests = test_runner.run(repo, timeout_seconds=current_budget.timeout_seconds)
        else:
            tests = SuiteRunResult(ran=False, success=True, output_tail="Testing not required for this task.")

        min_pass = config.get("quality", {}).get("min_pass_score", 7.0)
        quality = quality_checker.heuristic_check(difficulty, call, tests, changed, min_pass)

        review_threshold = config.get("quality", {}).get("use_claude_review_at_or_above_difficulty", 6)
        if (
            call.success
            and difficulty.value >= review_threshold
            and attempt_number < current_budget.max_claude_calls
        ):
            on_step("Running quality review...")
            diff_text = _diff_text(root, changed, repo.has_git)
            review_call = engine.execute(
                quality_checker.build_review_prompt(request, changed, diff_text, tests),
                root, current_budget,
                json_schema=quality_checker.REVIEW_JSON_SCHEMA,
                allowed_tools=["Read", "Glob", "Grep"],
            )
            cost_so_far += review_call.cost_usd
            reviewed = quality_checker.parse_review_response(review_call, min_pass)
            if reviewed:
                quality = reviewed

        attempts.append(ExecutionAttempt(call=call, tests=tests, quality=quality, changed_files=changed))

        decision = decide(quality, attempt_number, current_budget, cost_so_far, call.success)
        on_step(decision.reason)

        if decision.stop:
            status = decision.status
            break

        if attempt_number >= current_budget.max_claude_calls:
            status = "INCOMPLETE"
            break

        current_budget = budget_manager.escalate(current_budget, config)

    all_changed: list[str] = sorted({f for a in attempts for f in a.changed_files})
    total_duration = sum(a.call.duration_ms for a in attempts)

    report = ExecutionReport(
        task=request,
        difficulty=difficulty,
        budget=budget,
        attempts=attempts,
        status=status,
        total_cost_usd=round(cost_so_far, 4),
        total_duration_ms=total_duration,
        files_changed=all_changed,
        final_quality=quality,
    )

    memory.record(
        root,
        memory.TaskRecord(
            created_at=datetime.now(timezone.utc).isoformat(),
            request=request,
            predicted_difficulty=difficulty.value,
            band=difficulty.band,
            mode=difficulty.mode.value,
            status=status,
            cost_usd=report.total_cost_usd,
            duration_ms=total_duration,
            claude_calls=len(attempts),
            retries=max(0, len(attempts) - 1),
            files_changed=len(all_changed),
            quality_score=quality.score if quality else None,
        ),
    )

    return report
