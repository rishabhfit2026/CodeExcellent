from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codeexcellent import __version__
from codeexcellent.claude.claude_engine import ClaudeRunner
from codeexcellent.config.settings import load_config
from codeexcellent.core import memory, repository
from codeexcellent.core.engine import plan as plan_task
from codeexcellent.core.engine import run as run_engine
from codeexcellent.core.models import ExecutionReport, PlanResult

_BANNER = "CodeExcellent"


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
            f"mode={r.mode:<24} status={r.status:<10} calls={r.claude_calls} cost=${r.cost_usd}"
        )
        if r.raw_cost_usd is not None:
            line += f"  | raw: cost=${r.raw_cost_usd} success={r.raw_success}"
        print(line)

    totals = report.totals()
    _print_header("Summary")
    print(f"Tasks: {totals['tasks']}")
    print(f"Success rate: {totals['success_rate'] * 100:.0f}%")
    print(f"Avg Claude calls: {totals['avg_claude_calls']}")
    print(f"Avg cost: ${totals['avg_cost_usd']}")
    print(f"Total cost: ${totals['total_cost_usd']}")
    print(f"Avg quality: {totals['avg_quality']}/10")

    compare = report.compare_totals()
    if compare:
        _print_header("CodeExcellent vs raw Claude (A/B)")
        print(f"CodeExcellent total cost: ${compare['codeexcellent_total_cost_usd']}")
        print(f"Raw Claude total cost:    ${compare['raw_total_cost_usd']}")
        print(f"CodeExcellent avg calls per task: {compare['codeexcellent_avg_calls']}")

    if report.mode == "mock":
        print("\nNote: mock mode validates CodeExcellent's own decisions (difficulty/strategy/budget/call count) "
              "at zero cost. It does not judge real code quality -- use --live for that.")


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


def _interactive() -> int:
    print(_BANNER)
    print('Interactive mode. Type a task, or "exit" to quit.\n')
    root = str(Path.cwd())
    config = load_config()
    engine = ClaudeRunner(config)
    available, detail = engine.is_available()
    if not available:
        print(f"Claude CLI is not available: {detail}", file=sys.stderr)
        return 1

    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt or prompt.lower() in ("exit", "quit"):
            return 0

        _analysis_report(prompt, root, config)
        print("\nStarting Claude...")
        report = run_engine(prompt, root, config, engine, on_step=lambda msg: print(f"  {msg}"))
        _print_report(report)


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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        return _interactive()

    root_pre_parser = argparse.ArgumentParser(add_help=False)
    root_pre_parser.add_argument("--root", default=".")
    root_args, argv = root_pre_parser.parse_known_args(argv)

    # Bare invocation with no subcommand and no prompt-looking arg -> REPL.
    known_commands = {"run", "analyze", "doctor", "history", "config", "benchmark", "-h", "--help"}
    if not argv:
        return _interactive()

    if argv[0] not in known_commands and not argv[0].startswith("-"):
        # `codeexcellent "fix the bug"` is shorthand for `codeexcellent run "fix the bug"`.
        argv = ["run", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = root_args.root
    if not getattr(args, "command", None):
        return _interactive()

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
