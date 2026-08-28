from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from codeexcellent import __version__
from codeexcellent.claude.claude_engine import ClaudeRunner
from codeexcellent.config.settings import load_config
from codeexcellent.core import memory, repository
from codeexcellent.core.engine import plan as plan_task
from codeexcellent.core.engine import run as run_engine
from codeexcellent.core.models import ExecutionMode, ExecutionReport, PlanResult

_BANNER = "CodeExcellent"

_MODE_LABELS = {
    ExecutionMode.DIRECT: "Direct",
    ExecutionMode.LIGHTWEIGHT: "Lightweight planning",
    ExecutionMode.FULL: "Full planning + review",
    ExecutionMode.REVIEW_REQUIRED: "Direct + mandatory review",
}


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * max(24, len(title)))


def _analysis_report(request: str, root: str, config: dict) -> PlanResult:
    planned = plan_task(request, root, config)
    difficulty, budget, forecast = planned.difficulty, planned.budget, planned.forecast

    print(_BANNER)
    _print_header("Task Analysis")
    if planned.blocked_reason:
        print(f"BLOCKED: {planned.blocked_reason}")
        return planned

    print(f"Difficulty: {difficulty.value}/10 ({difficulty.band}) [confidence {difficulty.confidence}, {difficulty.basis}]")
    print(f"Risk: {difficulty.risk_level.value}")
    print(f"Quality level: {difficulty.quality_level.value}")
    print(f"Estimated scope: {difficulty.estimated_scope}")
    print(f"Planning required: {'yes' if difficulty.planning_required else 'no'}")
    print(f"Testing required: {'yes' if difficulty.testing_required else 'no'}")
    print(f"Strategy: {difficulty.mode.value}")
    if difficulty.reasons:
        print("Reasons:")
        for reason in difficulty.reasons:
            print(f"  - {reason}")
    if planned.repo.relevant_files:
        print(f"Relevant files: {', '.join(planned.repo.relevant_files[:8])}")

    _print_header("Budget")
    print(f"Effort: {budget.effort}")
    print(f"Max spend: ${budget.max_budget_usd}")
    print(f"Max Claude calls: {budget.max_claude_calls}")
    print(f"Max retries: {budget.max_retries}")
    print(f"Timeout: {budget.timeout_seconds}s")

    _print_header("Resource forecast")
    print(f"Basis: {forecast.basis} (n={forecast.sample_size} similar past task(s))")
    print(f"Expected Claude calls: ~{forecast.expected_calls}")
    print(f"Expected retries: ~{forecast.expected_retries}")

    return planned


def _print_report(report: ExecutionReport) -> None:
    _print_header("Result")
    print(f"Status: {report.status}")
    if report.final_quality:
        print(f"Quality: {report.final_quality.score}/10")
        if report.final_quality.issues:
            print("Issues:")
            for issue in report.final_quality.issues:
                print(f"  - {issue}")
    print(f"Claude calls: {len(report.attempts)}")
    print(f"Files changed: {', '.join(report.files_changed) if report.files_changed else '(none)'}")
    last_tests = report.attempts[-1].tests if report.attempts else None
    if last_tests and last_tests.ran:
        print(f"Tests: {last_tests.passed} passed, {last_tests.failed} failed ({last_tests.command})")
    if report.observed_difficulty is not None:
        sign = "+" if (report.difficulty_error or 0) >= 0 else ""
        print(f"Observed difficulty: {report.observed_difficulty}/10 (predicted {report.difficulty.value}, error {sign}{report.difficulty_error})")
    print(f"Total cost: ${report.total_cost_usd}")
    print(f"Total duration: {report.total_duration_ms / 1000:.1f}s")
    print(f"\nCodeExcellent: {report.status}")


def cmd_run(args: argparse.Namespace) -> int:
    root = str(Path(args.root).resolve())
    config = load_config()

    engine = ClaudeRunner(config)
    available, detail = engine.is_available()
    if not available:
        print(f"Claude CLI is not available: {detail}", file=sys.stderr)
        return 1

    repo_probe = repository.analyze(root, config)
    if repo_probe.has_git and repo_probe.git_dirty_files and not args.yes:
        print(f"Warning: {len(repo_probe.git_dirty_files)} file(s) already modified/untracked before this run:")
        for f in repo_probe.git_dirty_files[:10]:
            print(f"  {f}")
        answer = input("Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 1

    planned = _analysis_report(args.prompt, root, config)
    if planned.blocked_reason:
        return 1
    print("\nStarting Claude...")

    report = run_engine(args.prompt, root, config, engine, on_step=lambda msg: print(f"  {msg}"))
    _print_report(report)
    return 0 if report.status == "COMPLETE" else 2


def cmd_analyze(args: argparse.Namespace) -> int:
    root = str(Path(args.root).resolve())
    config = load_config()
    _analysis_report(args.prompt, root, config)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import json
    import shutil as _shutil

    ok = True

    try:
        config = load_config()
        print("Configuration: OK -- loaded and merged successfully")
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Configuration: INVALID -- {exc}")
        print("  Fix: check the JSON syntax in your ~/.codeexcellent/config.json (or $CODEEXCELLENT_CONFIG).")
        return 1

    engine = ClaudeRunner(config)
    available, detail = engine.is_available()
    print(f"Claude CLI: {'OK' if available else 'MISSING'} -- {detail}")
    if not available:
        print("  Fix: install Claude Code and ensure 'claude' is on PATH (https://claude.com/claude-code).")
        ok = False
    else:
        auth = engine.auth_status()
        if auth is None:
            print("Claude auth: UNKNOWN -- could not read `claude auth status`")
        elif auth.get("loggedIn"):
            print(f"Claude auth: OK -- logged in ({auth.get('subscriptionType', 'unknown plan')})")
        else:
            print("Claude auth: NOT LOGGED IN")
            print("  Fix: run `claude auth login`.")
            ok = False

    print(f"Python: {sys.version.split()[0]}")

    git_path = _shutil.which("git")
    print(f"Git: {'OK -- ' + git_path if git_path else 'MISSING'}")
    if not git_path:
        print("  Note: CodeExcellent still works without git, but loses change-isolation and dirty-state warnings.")

    root = str(Path(args.root).resolve())
    repo = repository.analyze(root, config)
    print(f"Target directory: {root}")
    print(f"Git repository: {'yes (' + repo.git_branch + ')' if repo.has_git and repo.git_branch else ('yes' if repo.has_git else 'no')}")
    print(f"Detected project types: {', '.join(repo.project_types) or 'none'}")
    print(f"Detected test locations: {', '.join(repo.test_dirs) or 'none'}")

    try:
        db_path = memory.db_path(root)
        memory.recent(root, limit=1)
        print(f"History database: OK -- {db_path}")
    except OSError as exc:
        print(f"History database: INACCESSIBLE -- {exc}")
        ok = False

    return 0 if ok else 1


def _print_benchmark_report(report) -> None:
    _print_header(f"Benchmark results ({report.mode} mode)")
    for r in report.results:
        line = (
            f"[{r.category:<10}] {r.task_id:<32} difficulty={r.predicted_difficulty:<5} "
            f"risk={r.risk:<8} mode={r.mode:<24} status={r.status:<10} "
            f"calls={r.claude_calls} retries={r.retries} cost=${r.cost_usd}"
        )
        if r.tests_ran:
            line += f"  | tests={r.tests_passed}p/{r.tests_failed}f"
        if r.validated is not None:
            line += f"  | validated={'yes' if r.validated else 'no (' + (r.validation_message or '') + ')'}"
        if r.raw_cost_usd is not None:
            line += f"  | raw: cost=${r.raw_cost_usd} success={r.raw_success}"
            if r.raw_num_turns is not None:
                line += f" num_turns={r.raw_num_turns}"
            if r.raw_validated is not None:
                line += f" validated={'yes' if r.raw_validated else 'no'}"
        print(line)

    totals = report.totals()
    _print_header("Summary")
    print(f"Tasks: {totals['total_tasks']} ({totals['successful_tasks']} successful)")
    print(f"Success rate: {totals['success_rate'] * 100:.0f}%")
    print(f"Avg agent calls: {totals['average_agent_calls']}  |  Avg retries: {totals['average_retries']}")
    print(f"Avg resource usage: ${totals['average_resource_usage_usd']}  |  Total: ${totals['total_resource_usage_usd']}")
    print(f"Avg duration: {totals['average_duration_ms']:.0f}ms")
    print(f"Avg quality: {totals['average_quality']}/10")
    if "validated_tasks" in totals:
        print(f"Validated pass rate: {totals['validated_pass_rate'] * 100:.0f}% ({totals['validated_tasks']} task(s) with an automated check)")
    if "tasks_with_tests_run" in totals:
        print(f"Test pass rate: {totals['test_pass_rate'] * 100:.0f}% ({totals['tasks_with_tests_run']} task(s) that ran tests)")

    by_difficulty = report.by_difficulty()
    if by_difficulty:
        _print_header("By difficulty band")
        for band in ("trivial", "easy", "medium", "hard", "very_hard"):
            if band not in by_difficulty:
                continue
            stats = by_difficulty[band]
            print(
                f"{band:<10} tasks={stats['tasks']:<3} success={stats['success_rate'] * 100:.0f}%  "
                f"avg_calls={stats['average_agent_calls']}  avg_cost=${stats['average_resource_usage_usd']}  "
                f"avg_duration={stats['average_duration_ms']:.0f}ms"
            )

    compare = report.compare_totals()
    if compare:
        _print_header("CodeExcellent vs raw Claude (A/B)")
        print(f"CodeExcellent total cost: ${compare['codeexcellent_total_cost_usd']}  |  avg duration: {compare['codeexcellent_avg_duration_ms']:.0f}ms")
        print(f"Raw Claude total cost:    ${compare['raw_total_cost_usd']}  |  avg duration: {compare['raw_avg_duration_ms']:.0f}ms")
        print(f"CodeExcellent avg calls per task: {compare['codeexcellent_avg_calls']}")
        if "validated_tasks" in compare:
            print(
                f"Validated pass rate -- CodeExcellent: {compare['codeexcellent_validated_pass_rate'] * 100:.0f}%  "
                f"vs raw Claude: {compare['raw_validated_pass_rate'] * 100:.0f}%  ({compare['validated_tasks']} task(s) with an automated check)"
            )

    if report.mode == "mock":
        print("\nNote: mock mode validates CodeExcellent's own decisions (difficulty/strategy/budget/call count) "
              "at zero cost. It does not judge real code quality or pass any 'validated' checks -- use --live for that.")


def cmd_benchmark(args: argparse.Namespace) -> int:
    from codeexcellent.benchmark import runner
    from codeexcellent.benchmark.mock_engine import MockBenchmarkEngine
    from codeexcellent.benchmark.tasks import ALL_TASKS

    config = load_config()
    tasks = ALL_TASKS
    if args.category:
        tasks = [t for t in ALL_TASKS if t.category == args.category]
        if not tasks:
            categories = sorted({t.category for t in ALL_TASKS})
            print(f"No benchmark tasks in category '{args.category}'. Choices: {', '.join(categories)}", file=sys.stderr)
            return 1

    if args.compare and not args.live:
        print("--compare requires --live (a mock A/B comparison can't demonstrate real efficiency).", file=sys.stderr)
        return 1

    if args.live:
        call_note = " x2 (CodeExcellent + a raw-Claude comparison)" if args.compare else ""
        print(f"This will make real Claude CLI calls for {len(tasks)} task(s){call_note}, incurring real cost.")
        if not args.yes:
            answer = input("Continue? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                return 1
        engine_factory = lambda: ClaudeRunner(config)
        mode = "live"
    else:
        engine_factory = MockBenchmarkEngine
        mode = "mock"
        print("Running in mock mode (no cost, no real Claude calls). Pass --live to benchmark against the real CLI.\n")

    report = runner.run_suite(
        config, engine_factory, mode, tasks=tasks, compare=args.compare,
        on_step=lambda msg: print(f"  {msg}"),
    )
    _print_benchmark_report(report)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    root = str(Path(args.root).resolve())
    rows = memory.recent(root, limit=args.limit)
    if not rows:
        print("No task history yet.")
        return 0
    for row in rows:
        print(
            f"[{row['created_at']}] {row['status']:<10} "
            f"difficulty={row['predicted_difficulty']:<5} band={row['band']:<10} "
            f"cost=${row['cost_usd']:<7} calls={row['claude_calls']} "
            f"quality={row['quality_score']} :: {row['request'][:60]}"
        )
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    import json

    from codeexcellent.config.settings import user_config_path

    path = user_config_path()

    if args.init:
        if path.exists() and not args.yes:
            answer = input(f"{path} already exists. Overwrite with an empty template? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{\n  \"_comment\": \"Override any subset of defaults.json's keys here.\"\n}\n")
        print(f"Created {path}")
        return 0

    config = load_config()
    if args.show:
        print(json.dumps(config, indent=2))
    else:
        print(f"User config path: {path}")
        print(f"{'(exists)' if path.exists() else '(not created yet -- run `codeexcellent config --init`)'}")
        print("(Create this file with any subset of defaults.json's keys to override them.)")
    return 0


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def _print_startup_banner(root: str, config: dict, engine: ClaudeRunner) -> None:
    available, _ = engine.is_available()
    repo = repository.analyze(root, config)
    repo_label = Path(root).name or root

    print(f"{_BANNER} v{__version__}")
    print(f"Repository: {repo_label}")
    print(f"Claude CLI: {_mark(available)}")
    print(f"Git: {_mark(repo.has_git)}" + (f" ({repo.git_branch})" if repo.has_git and repo.git_branch else ""))
    print('\nType a task, or "exit" to quit.\n')


def _print_compact_plan(planned: PlanResult) -> None:
    """The concise per-task summary the interactive loop shows before
    executing (section 3) -- difficulty/risk/confidence/strategy in a
    handful of lines, not the full multi-section report `analyze`/`run`
    print. Full detail is always available via `codeexcellent analyze`.
    """
    difficulty = planned.difficulty
    print(f"Difficulty: {difficulty.value}/10  Risk: {difficulty.risk_level.value.upper()}  Confidence: {difficulty.confidence}")
    print(f"Strategy: {_MODE_LABELS.get(difficulty.mode, difficulty.mode.value)}")


def _interactive_step(msg: str) -> str | None:
    """Maps engine.run()'s internal on_step messages to short, user-facing
    progress phrases for the interactive loop; returns None to suppress a
    message entirely (internal reasoning/decision text belongs in `analyze`
    /`run`'s fuller output, not in every turn of a conversation).
    """
    if msg.startswith("Calling Claude"):
        # "attempt 1," (with the comma) avoids "attempt 1" matching "attempt 11".
        return "Implementing..." if "attempt 1," in msg else "Retrying..."
    if msg == "Running tests...":
        return "Testing..."
    if msg == "Running quality review...":
        return "Reviewing..."
    if msg == "Cancelled by user." or msg.startswith("BLOCKED"):
        return msg
    return None


def _print_compact_result(report: ExecutionReport) -> None:
    if report.status == "COMPLETE":
        print(f"\n{_mark(True)} Task completed")
    elif report.status == "CANCELLED":
        print(f"\n{_mark(False)} Cancelled")
    else:
        print(f"\n{_mark(False)} Task {report.status.lower()}")

    quality = f"{report.final_quality.score}/10" if report.final_quality else "n/a"
    files = len(report.files_changed)
    print(f"Quality: {quality} | Files changed: {files} | Cost: ${report.total_cost_usd}")
    if report.final_quality and report.final_quality.issues:
        for issue in report.final_quality.issues[:3]:
            print(f"  - {issue}")


def _interactive() -> int:
    root = str(Path.cwd())
    config = load_config()
    engine = ClaudeRunner(config)
    _print_startup_banner(root, config, engine)

    available, detail = engine.is_available()
    if not available:
        print(f"Claude CLI is not available: {detail}", file=sys.stderr)
        print("Run `codeexcellent doctor` for details.", file=sys.stderr)
        return 1

    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt or prompt.lower() in ("exit", "quit"):
            return 0

        planned = plan_task(prompt, root, config)
        if planned.blocked_reason:
            print(f"Blocked: {planned.blocked_reason}\n")
            continue

        _print_compact_plan(planned)
        print()

        def _on_step(msg: str) -> None:
            shown = _interactive_step(msg)
            if shown:
                print(f"{shown}")

        report = run_engine(prompt, root, config, engine, on_step=_on_step, planned=planned)
        _print_compact_result(report)
        print()


def build_parser() -> argparse.ArgumentParser:
    # --root is deliberately NOT registered here: argparse gives a shared
    # dest a fresh default every time a (sub)parser's own parse pass starts,
    # which silently clobbers a value already set by an enclosing parser.
    # main() extracts --root itself in a separate pre-pass instead, so it
    # works whether it appears before or after the subcommand.
    parser = argparse.ArgumentParser(prog="codeexcellent", description="Adaptive, resource-aware orchestration layer around the Claude CLI")
    parser.add_argument("-v", "--version", action="version", version=f"codeexcellent {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Execute a coding task")
    run_parser.add_argument("prompt", help="The task to perform")
    run_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation about pre-existing uncommitted changes")
    run_parser.set_defaults(func=cmd_run)

    analyze_parser = subparsers.add_parser("analyze", help="Show difficulty/budget analysis without executing")
    analyze_parser.add_argument("prompt", help="The task to analyze")
    analyze_parser.set_defaults(func=cmd_analyze)

    doctor_parser = subparsers.add_parser("doctor", help="Check environment health")
    doctor_parser.set_defaults(func=cmd_doctor)

    history_parser = subparsers.add_parser("history", help="Show past task executions")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.set_defaults(func=cmd_history)

    config_parser = subparsers.add_parser("config", help="Show or scaffold configuration")
    config_parser.add_argument("--show", action="store_true", help="Print the fully merged configuration")
    config_parser.add_argument("--init", action="store_true", help="Create a starter user config file at the user config path")
    config_parser.add_argument("-y", "--yes", action="store_true", help="With --init, overwrite an existing user config without asking")
    config_parser.set_defaults(func=cmd_config)

    benchmark_parser = subparsers.add_parser("benchmark", help="Run the representative task benchmark suite")
    benchmark_parser.add_argument("--live", action="store_true", help="Use the real Claude CLI (incurs real cost); default is a zero-cost mock engine")
    benchmark_parser.add_argument("--compare", action="store_true", help="Also run each task via raw Claude (no orchestration) for an A/B comparison; requires --live")
    benchmark_parser.add_argument("--category", choices=["trivial", "easy", "medium", "hard", "very_hard"], help="Only run tasks in this category")
    benchmark_parser.add_argument("-y", "--yes", action="store_true", help="Skip the cost confirmation prompt for --live")
    benchmark_parser.set_defaults(func=cmd_benchmark)

    return parser


def _dispatch(argv: list[str]) -> int:
    if not argv:
        return _interactive()

    # --root and --debug are deliberately NOT registered on the main/sub
    # parsers: argparse gives a shared dest a fresh default every time a
    # (sub)parser's own parse pass starts, which silently clobbers a value
    # already set by an enclosing parser. Extracted here instead, so both
    # work whether they appear before or after the subcommand.
    global_pre_parser = argparse.ArgumentParser(add_help=False)
    global_pre_parser.add_argument("--root", default=".")
    global_pre_parser.add_argument("--debug", action="store_true")
    global_args, argv = global_pre_parser.parse_known_args(argv)

    # Bare invocation with no subcommand and no prompt-looking arg -> REPL.
    known_commands = {"run", "analyze", "doctor", "history", "config", "benchmark", "-h", "--help"}
    if not argv:
        return _interactive()

    if argv[0] not in known_commands and not argv[0].startswith("-"):
        # `codeexcellent "fix the bug"` is shorthand for `codeexcellent run "fix the bug"`.
        argv = ["run", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = global_args.root
    if not getattr(args, "command", None):
        return _interactive()

    return args.func(args)


def main(argv: list[str] | None = None) -> int:
    """Never lets an unexpected exception reach the user as a raw Python
    traceback (section 10) -- prints a one-line message and exits non-zero
    instead, unless --debug (or $CODEEXCELLENT_DEBUG=1) is set, in which case
    the real traceback is shown so a developer can actually diagnose it.
    argparse's own --help/--version/usage-error exits (SystemExit) and a
    Ctrl-C during input() (already handled inside `_interactive()`) are
    unaffected -- this only catches what would otherwise be a crash.
    """
    argv = sys.argv[1:] if argv is None else argv
    debug = "--debug" in argv or os.environ.get("CODEEXCELLENT_DEBUG") == "1"

    try:
        return _dispatch(argv)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        if debug:
            raise
        print(f"\nUnexpected error: {exc}", file=sys.stderr)
        print("Run with --debug (or set CODEEXCELLENT_DEBUG=1) for the full traceback.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
