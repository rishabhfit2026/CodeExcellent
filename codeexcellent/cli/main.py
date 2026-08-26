from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codeexcellent.analyzer import difficulty_scorer, task_analyzer
from codeexcellent.budget import budget_manager
from codeexcellent.claude.claude_engine import ClaudeRunner
from codeexcellent.config.settings import load_config
from codeexcellent.core import memory, repository
from codeexcellent.core.engine import run as run_engine
from codeexcellent.core.models import ExecutionReport

_BANNER = "CodeExcellent"


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * max(24, len(title)))


def _analysis_report(request: str, root: str, config: dict) -> None:
    task = task_analyzer.analyze(request)
    repo = repository.analyze(root, config)
    repo.relevant_files = repository.find_relevant_files(root, request, config)
    difficulty = difficulty_scorer.score(task, repo, config)
    budget = budget_manager.allocate(difficulty, config)

    print(_BANNER)
    _print_header("Task Analysis")
    print(f"Difficulty: {difficulty.value}/10 ({difficulty.band})")
    print(f"Risk: {difficulty.risk_level.value}")
    print(f"Estimated scope: {difficulty.estimated_scope}")
    print(f"Planning required: {'yes' if difficulty.planning_required else 'no'}")
    print(f"Testing required: {'yes' if difficulty.testing_required else 'no'}")
    print(f"Strategy: {difficulty.mode.value}")
    if repo.relevant_files:
        print(f"Relevant files: {', '.join(repo.relevant_files[:8])}")

    _print_header("Budget")
    print(f"Effort: {budget.effort}")
    print(f"Max spend: ${budget.max_budget_usd}")
    print(f"Max Claude calls: {budget.max_claude_calls}")
    print(f"Max retries: {budget.max_retries}")
    print(f"Timeout: {budget.timeout_seconds}s")


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

    _analysis_report(args.prompt, root, config)
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
    config = load_config()
    engine = ClaudeRunner(config)
    available, detail = engine.is_available()
    print(f"Claude CLI: {'OK' if available else 'MISSING'} -- {detail}")
    print(f"Python: {sys.version.split()[0]}")

    root = str(Path(args.root).resolve())
    repo = repository.analyze(root, config)
    print(f"Target directory: {root}")
    print(f"Git repository: {'yes (' + repo.git_branch + ')' if repo.has_git and repo.git_branch else ('yes' if repo.has_git else 'no')}")
    print(f"Detected project types: {', '.join(repo.project_types) or 'none'}")
    return 0 if available else 1


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

    config = load_config()
    if args.show:
        print(json.dumps(config, indent=2))
    else:
        print(f"User config path: {user_config_path()}")
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
    parser = argparse.ArgumentParser(prog="codeexcellent", description="Resource-aware orchestration layer around the Claude CLI")
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

    config_parser = subparsers.add_parser("config", help="Show configuration")
    config_parser.add_argument("--show", action="store_true", help="Print the fully merged configuration")
    config_parser.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        return _interactive()

    root_pre_parser = argparse.ArgumentParser(add_help=False)
    root_pre_parser.add_argument("--root", default=".")
    root_args, argv = root_pre_parser.parse_known_args(argv)

    # Bare invocation with no subcommand and no prompt-looking arg -> REPL.
    known_commands = {"run", "analyze", "doctor", "history", "config", "-h", "--help"}
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
