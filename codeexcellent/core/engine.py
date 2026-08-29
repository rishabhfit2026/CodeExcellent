"""ExecutionController: the orchestrator. Wires TaskAnalyzer -> RepoAnalyzer
-> AdaptiveDifficultyEstimator -> ResourceForecaster -> StrategySelector ->
BudgetManager -> ContextManager -> CodingEngine -> TestRunner ->
QualityChecker -> StopController -> OutcomeClassifier -> TaskMemory into one
run, with confidence-aware progressive budget escalation and
feedback-carrying retries. This is the only module that knows the full
pipeline; every other module is independently testable.

Claude's own session continuation (`--resume`) is what keeps retries cheap
(sections 14-15): a retry prompt (core/retry.py) carries only the new
feedback, not a re-statement of the original context, because the resumed
session already has it.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from codeexcellent.analyzer import adaptive_estimator, difficulty_scorer, fingerprint, strategy_selector, task_analyzer
from codeexcellent.budget import budget_manager, resource_forecaster
from codeexcellent.claude.engine import CodingEngine
from codeexcellent.core import context, failure_classifier, git_safety, memory, outcome, prompts, repository, test_runner
from codeexcellent.core.models import (
    ExecutionAttempt,
    ExecutionMode,
    ExecutionReport,
    PlanResult,
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


def _early_exit_report(request: str, reason: str, status: str, difficulty=None, budget=None) -> ExecutionReport:
    """Builds a report for a run that never reached the Claude-call loop --
    blocked pre-flight (engine unavailable, repo too large) or cancelled
    (Ctrl-C) during analysis. Neither has partial attempt data to report, so
    this is a minimal, valid ExecutionReport rather than a crash.
    """
    from codeexcellent.core.models import Budget, DifficultyScore, QualityLevel, RiskLevel

    difficulty = difficulty or DifficultyScore(
        value=0.0, band="unknown", risk_level=RiskLevel.LOW, dimensions={},
        planning_required=False, testing_required=False, mode=ExecutionMode.DIRECT,
        estimated_scope="small", quality_level=QualityLevel.TRIVIAL, reasons=[reason],
    )
    budget = budget or Budget(
        band="unknown", effort="low", max_budget_usd=0.0, max_budget_usd_step=0.0,
        max_claude_calls=0, max_retries=0, timeout_seconds=0,
    )
    return ExecutionReport(
        task=request, difficulty=difficulty, budget=budget, attempts=[], status=status,
        total_cost_usd=0.0, total_duration_ms=0, files_changed=[],
        final_quality=QualityResult(score=0.0, complete=False, needs_more_work=True, issues=[reason]),
        outcome_class=outcome.OutcomeClass.INFRA_FAILURE,
    )


def _blocked_report(request: str, reason: str, difficulty=None, budget=None) -> ExecutionReport:
    return _early_exit_report(request, reason, "BLOCKED", difficulty, budget)


def plan(request: str, root: str, config: dict, on_step: StepCallback | None = None) -> PlanResult:
    """Everything decidable before spending a single Claude call: task
    analysis, repo inspection, adaptive difficulty, strategy, budget, and
    resource forecast. Used by both `run()` and `codeexcellent analyze` so
    the two can never show different numbers for the same task (a real V1
    bug: `analyze` used to call the heuristic scorer directly and skip the
    strategy selector entirely).
    """
    on_step = on_step or _noop

    on_step("Analyzing task...")
    task = task_analyzer.analyze(request)

    if task_analyzer.is_chitchat(request, task):
        on_step("BLOCKED: doesn't look like a coding task")
        reason = (
            "That doesn't look like a coding task. Describe what you want changed, "
            "e.g. \"add input validation to the signup form\" or \"fix the login bug\"."
        )
        empty_repo = repository.analyze(root, config)
        empty_difficulty = difficulty_scorer.score(task, empty_repo, config)
        empty_budget = budget_manager.allocate(empty_difficulty, config)
        return PlanResult(
            task=task, repo=empty_repo, difficulty=empty_difficulty, budget=empty_budget,
            fingerprint=fingerprint.build(task, empty_repo, empty_difficulty),
            forecast=resource_forecaster.forecast("blocked", root, empty_budget, 0, config),
            blocked_reason=reason,
        )

    on_step("Inspecting repository...")
    repo = repository.analyze(root, config)

    hard_ceiling = int(config.get("repository", {}).get("hard_file_ceiling", 100000))
    if repo.file_count >= hard_ceiling:
        on_step("BLOCKED: repository too large to safely operate on")
        reason = f"Repository has {repo.file_count}+ files, over the safety ceiling of {hard_ceiling}"
        empty_difficulty = difficulty_scorer.score(task, repo, config)
        empty_budget = budget_manager.allocate(empty_difficulty, config)
        return PlanResult(
            task=task, repo=repo, difficulty=empty_difficulty, budget=empty_budget,
            fingerprint=fingerprint.build(task, repo, empty_difficulty),
            forecast=resource_forecaster.forecast("blocked", root, empty_budget, 0, config),
            blocked_reason=reason,
        )

    repo.relevant_files = repository.find_relevant_files(
        root, request, config, max_results=config.get("context", {}).get("max_files", 12)
    )

    heuristic_difficulty = difficulty_scorer.score(task, repo, config)
    difficulty = adaptive_estimator.estimate(task, repo, heuristic_difficulty, root, config)

    task_fingerprint = fingerprint.build(task, repo, difficulty)
    mode, strategy_reasons = strategy_selector.select(task, difficulty, config, repo)
    difficulty.mode = mode
    difficulty.planning_required = mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL)
    difficulty.reasons = [*difficulty.reasons, *strategy_reasons]

    budget = budget_manager.allocate_adaptive(difficulty, config)

    on_step(
        f"Difficulty {difficulty.value}/10 ({difficulty.band}, confidence={difficulty.confidence}, "
        f"basis={difficulty.basis}), risk={difficulty.risk_level.value}, mode={difficulty.mode.value}, "
        f"budget=${budget.max_budget_usd} effort={budget.effort}"
    )
    for reason in difficulty.reasons:
        on_step(f"  reason: {reason}")

    context_bundle = context.build(repo, repo.relevant_files, config)
    forecast = resource_forecaster.forecast(task_fingerprint.key(), root, budget, context_bundle.total_bytes, config)
    on_step(
        f"Forecast ({forecast.basis}, n={forecast.sample_size}): ~{forecast.expected_calls} call(s), "
        f"~{forecast.expected_retries} retr(y/ies)"
    )

    return PlanResult(
        task=task, repo=repo, difficulty=difficulty, budget=budget,
        fingerprint=task_fingerprint, forecast=forecast, context_bundle=context_bundle,
    )


def run(
    request: str,
    root: str,
    config: dict,
    engine: CodingEngine,
    on_step: StepCallback | None = None,
    planned: PlanResult | None = None,
) -> ExecutionReport:
    """Pass a `planned` from a prior `plan()` call (e.g. one the caller
    already displayed to the user) to skip recomputing it here -- otherwise
    `run()` computes it itself. Recomputing is not wrong, just wasteful: a
    full repo scan + adaptive-history lookup twice per task for no reason.
    """
    on_step = on_step or _noop

    available, detail = engine.is_available()
    if not available:
        on_step(f"BLOCKED: {detail}")
        return _blocked_report(request, f"Coding engine unavailable: {detail}")

    try:
        if planned is None:
            planned = plan(request, root, config, on_step)
    except KeyboardInterrupt:
        return _early_exit_report(request, "Cancelled by user during task analysis.", "CANCELLED")

    if planned.blocked_reason:
        return _blocked_report(request, planned.blocked_reason, planned.difficulty, planned.budget)

    task, repo, difficulty, budget = planned.task, planned.repo, planned.difficulty, planned.budget
    task_fingerprint, forecast = planned.fingerprint, planned.forecast

    context_bundle = planned.context_bundle
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
    min_pass = quality_checker.min_pass_score_for(difficulty, config)
    needs_review = quality_checker.review_required(difficulty, difficulty.mode, config)

    attempts: list[ExecutionAttempt] = []
    session_id: str | None = None
    cost_so_far = plan_cost
    attempt_number = 0
    quality = QualityResult(score=0.0, complete=False, needs_more_work=True)
    status = "INCOMPLETE"
    current_budget = budget
    escalation_reasons: list[str] = []
    cancelled = False

    try:
        while True:
            attempt_number += 1
            on_step(f"Calling Claude (attempt {attempt_number}, effort={current_budget.effort})...")

            if attempt_number == 1:
                prompt = base_prompt
            else:
                previous = attempts[-1]
                previous_failure_class = (
                    failure_classifier.FailureClass(previous.failure_class) if previous.failure_class else None
                )
                prompt = build_retry_prompt(
                    request, previous.call, previous.tests, previous.quality,
                    failure_class=previous_failure_class, changed_files=previous.changed_files,
                )

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

            quality = quality_checker.heuristic_check(difficulty, call, tests, changed, min_pass, task=task, config=config)

            if call.success and needs_review and attempt_number < current_budget.max_claude_calls:
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

            fclass = failure_classifier.classify(task, call, tests, changed, quality, config)
            attempts.append(ExecutionAttempt(
                call=call, tests=tests, quality=quality, changed_files=changed,
                failure_class=fclass.value if fclass != failure_classifier.FailureClass.NONE else None,
            ))

            decision = decide(quality, attempt_number, current_budget, cost_so_far, call.success)
            on_step(decision.reason)

            if decision.stop:
                status = decision.status
                break

            if attempt_number >= current_budget.max_claude_calls:
                status = "INCOMPLETE"
                break

            escalation_reasons.append(decision.reason)
            # Validation-driven recovery: only pay for a bigger budget/effort
            # band on the next attempt when the diagnosed failure actually
            # suggests resources were the bottleneck (a tooling/timeout
            # failure, or one we couldn't diagnose at all). A diagnosed,
            # targetable gap (structural/test/implementation) gets a
            # targeted retry prompt at the *same* budget instead -- escalating
            # unconditionally on every retry was a real, measured source of
            # cost that a live A/B benchmark showed didn't improve validated
            # correctness.
            if failure_classifier.warrants_budget_escalation(fclass):
                current_budget = budget_manager.escalate(current_budget, config)
    except KeyboardInterrupt:
        status = "CANCELLED"
        cancelled = True

    all_changed: list[str] = sorted({f for a in attempts for f in a.changed_files})
    total_duration = sum(a.call.duration_ms for a in attempts)

    outcome_class = outcome.classify(status, attempts, task)
    observed = outcome.observed_difficulty(outcome_class, attempts, budget, quality)
    difficulty_error = round(observed - difficulty.value, 2) if observed is not None else None
    last_tests = attempts[-1].tests if attempts else None
    planning_used = difficulty.mode in (ExecutionMode.LIGHTWEIGHT, ExecutionMode.FULL)

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
        fingerprint=task_fingerprint,
        outcome_class=outcome_class,
        observed_difficulty=observed,
        difficulty_error=difficulty_error,
        resource_forecast=forecast,
        escalation_reasons=escalation_reasons,
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
            fingerprint_key=task_fingerprint.key(),
            fingerprint_category=task_fingerprint.category,
            fingerprint_repo_type=task_fingerprint.repo_type,
            fingerprint_scope=task_fingerprint.scope,
            fingerprint_risk=task_fingerprint.risk,
            confidence=difficulty.confidence,
            quality_level=difficulty.quality_level.value,
            outcome_class=outcome_class.value,
            observed_difficulty=observed,
            difficulty_error=difficulty_error,
            forecast_calls=forecast.expected_calls,
            forecast_basis=forecast.basis,
            planning_used=planning_used,
            tests_ran=bool(last_tests and last_tests.ran),
            tests_passed=last_tests.passed if last_tests else 0,
            tests_failed=last_tests.failed if last_tests else 0,
        ),
    )

    if cancelled:
        on_step("Cancelled by user.")

    return report
