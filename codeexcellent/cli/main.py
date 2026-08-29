from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

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

_RISK_STYLES = {
    "low": "green",
    "medium": "yellow",
    "high": "dark_orange",
    "critical": "bold red",
}

# The presentation layer's one deliberate exception to the "near-stdlib"
# architecture principle (README) -- confined entirely to this module.
# Rich degrades to plain text automatically when stdout/stderr isn't a real
# terminal (piped/redirected), so scripted use is unaffected. Two consoles,
# not one, so error/warning output still goes to stderr like the plain-print
# version did -- `console.print()` defaults to stdout, which would otherwise
# silently merge error text into piped stdout.
console = Console()
err_console = Console(stderr=True)


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * max(24, len(title)))


def _analysis_report(request: str, root: str, config: dict, planned: PlanResult | None = None) -> PlanResult:
    planned = planned if planned is not None else plan_task(request, root, config)
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

    planned = plan_task(args.prompt, root, config)
    if not planned.blocked_reason and args.loop:
        from codeexcellent.budget import budget_manager

        # Only the budget ceiling changes -- strategy selection stays exactly
        # what the evidence-based selector already chose. Loop mode answers
        # "how long may it keep retrying," not "how it should plan." Applied
        # before printing so the Budget section below shows the real ceiling
        # that will actually be used, not the difficulty band's default.
        planned.budget = budget_manager.allocate_loop(config)

    _analysis_report(args.prompt, root, config, planned=planned)
    if planned.blocked_reason:
        return 1

    if args.loop:
        print(
            f"\nLoop mode: keeps retrying with feedback until the task's own validation "
            f"says done, or the ceiling above is hit."
        )
        if not args.yes:
            answer = input("Continue? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                return 1

    print("\nStarting Claude...")

    report = run_engine(args.prompt, root, config, engine, on_step=lambda msg: print(f"  {msg}"), planned=planned)
    _print_report(report)
    return 0 if report.status == "COMPLETE" else 2


def cmd_analyze(args: argparse.Namespace) -> int:
    root = str(Path(args.root).resolve())
    config = load_config()
    _analysis_report(args.prompt, root, config)
    return 0


def _render_doctor(rows: list[tuple[bool | None, str, str, str | None]]) -> None:
    body = Text()
    for i, (status, label, detail, fix) in enumerate(rows):
        if i:
            body.append("\n")
        icon, style = ("✓", "green") if status is True else ("✗", "bold red") if status is False else ("?", "yellow")
        body.append(f"{icon} ", style=style)
        body.append(f"{label:<20}", style="bold")
        if detail:
            body.append(f" {detail}", style="red" if status is False else "dim")
        if fix:
            body.append(f"\n  → {fix}", style="yellow")
    console.print(Panel(body, title="[bold]Environment check[/bold]", title_align="left", border_style="cyan", padding=(1, 2)))


def cmd_doctor(args: argparse.Namespace) -> int:
    import json
    import shutil as _shutil

    ok = True
    rows: list[tuple[bool | None, str, str, str | None]] = []

    try:
        config = load_config()
        rows.append((True, "Configuration", "loaded and merged successfully", None))
    except (json.JSONDecodeError, OSError) as exc:
        rows.append((False, "Configuration", str(exc), "check the JSON syntax in your ~/.codeexcellent/config.json (or $CODEEXCELLENT_CONFIG)"))
        _render_doctor(rows)
        return 1

    engine = ClaudeRunner(config)
    available, detail = engine.is_available()
    rows.append((available, "Claude CLI", detail, None if available else "install Claude Code and ensure 'claude' is on PATH (https://claude.com/claude-code)"))
    if not available:
        ok = False
    else:
        auth = engine.auth_status()
        if auth is None:
            rows.append((None, "Claude auth", "could not read `claude auth status`", None))
        elif auth.get("loggedIn"):
            rows.append((True, "Claude auth", f"logged in ({auth.get('subscriptionType', 'unknown plan')})", None))
        else:
            rows.append((False, "Claude auth", "not logged in", "run `claude auth login`"))
            ok = False

    rows.append((True, "Python", sys.version.split()[0], None))

    git_path = _shutil.which("git")
    rows.append((bool(git_path), "Git", git_path or "not found", None if git_path else "CodeExcellent still works without git, but loses change-isolation and dirty-state warnings"))

    root = str(Path(args.root).resolve())
    repo = repository.analyze(root, config)
    rows.append((True, "Target directory", root, None))
    if repo.has_git:
        rows.append((True, "Git repository", f"yes ({repo.git_branch})" if repo.git_branch else "yes", None))
    else:
        rows.append((None, "Git repository", "no", None))
    rows.append((True, "Project types", ", ".join(repo.project_types) or "none", None))
    rows.append((True, "Test locations", ", ".join(repo.test_dirs) or "none", None))

    try:
        db_path = memory.db_path(root)
        memory.recent(root, limit=1)
        rows.append((True, "History database", str(db_path), None))
    except OSError as exc:
        rows.append((False, "History database", str(exc), None))
        ok = False

    _render_doctor(rows)
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

    body = Text()
    body.append("Repository  ", style="dim")
    body.append(f"{repo_label}\n", style="bold")
    body.append("Claude CLI  ", style="dim")
    body.append("● ready\n" if available else "● unavailable\n", style="green" if available else "bold red")
    body.append("Git         ", style="dim")
    if repo.has_git and repo.git_branch:
        body.append(f"● {repo.git_branch}", style="green")
    elif repo.has_git:
        body.append("● tracked", style="green")
    else:
        body.append("○ not a repository", style="yellow")

    console.print(Panel(
        body, title=f"[bold]{_BANNER}[/bold] [dim]v{__version__}[/dim]",
        title_align="left", border_style="cyan", padding=(1, 2), width=min(console.width, 64),
    ))
    console.print('[dim]Type a task, [/dim][bold]/help[/bold][dim] for commands, or "exit" to quit.[/dim]\n')


def _print_compact_plan(planned: PlanResult) -> None:
    """The concise per-task summary the interactive loop shows before
    executing (section 3) -- difficulty/risk/confidence/strategy in a
    handful of lines, not the full multi-section report `analyze`/`run`
    print. Full detail is always available via `codeexcellent analyze`.
    """
    difficulty = planned.difficulty
    risk = difficulty.risk_level.value
    risk_style = _RISK_STYLES.get(risk, "white")

    line = Text()
    line.append("difficulty ", style="dim")
    line.append(f"{difficulty.value}/10", style="bold")
    line.append("   risk ", style="dim")
    line.append(risk.upper(), style=risk_style)
    line.append("   confidence ", style="dim")
    line.append(f"{difficulty.confidence}", style="bold")
    line.append("   strategy ", style="dim")
    line.append(_MODE_LABELS.get(difficulty.mode, difficulty.mode.value), style="cyan")
    console.print(line)


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
        icon, style, label = "✓", "bold green", "Task completed"
    elif report.status == "CANCELLED":
        icon, style, label = "✗", "yellow", "Cancelled"
    else:
        icon, style, label = "✗", "bold red", f"Task {report.status.lower()}"

    quality = f"{report.final_quality.score}/10" if report.final_quality else "n/a"
    files = len(report.files_changed)

    body = Text()
    body.append(f"{icon} {label}\n\n", style=style)
    body.append("quality ", style="dim")
    body.append(f"{quality}", style="bold")
    body.append("   files changed ", style="dim")
    body.append(f"{files}", style="bold")
    body.append("   cost ", style="dim")
    body.append(f"${report.total_cost_usd}", style="bold")
    if report.final_quality and report.final_quality.issues:
        for issue in report.final_quality.issues[:3]:
            body.append(f"\n  • {issue}", style="dim")

    border = style.replace("bold ", "")
    console.print(Panel(body, border_style=border, padding=(0, 2), width=min(console.width, 72)))


_SLASH_COMMANDS = {
    "/help": "Show this list of commands",
    "/doctor": "Check environment health without leaving the session",
    "/loop": 'Keep retrying a task until it\'s done, under a much higher ceiling -- usage: "/loop <task>"',
}


def _print_slash_menu() -> None:
    body = Text()
    for i, (cmd, desc) in enumerate(_SLASH_COMMANDS.items()):
        if i:
            body.append("\n")
        body.append(f"{cmd:<10}", style="bold cyan")
        body.append(desc, style="dim")
    body.append("\n\n")
    body.append("exit / quit", style="bold cyan")
    body.append("  End the session", style="dim")
    console.print(Panel(body, title="[bold]Commands[/bold]", title_align="left", border_style="cyan", padding=(1, 2)))


def _run_task_in_session(prompt: str, root: str, config: dict, engine: ClaudeRunner, *, loop: bool = False) -> None:
    """Shared by plain task input and `/loop <task>` -- the only difference
    is which budget the plan runs under.
    """
    with console.status("[cyan]Analyzing task...[/cyan]", spinner="dots"):
        planned = plan_task(prompt, root, config)
    if planned.blocked_reason:
        console.print(f"[bold red]Blocked:[/bold red] {planned.blocked_reason}\n")
        return

    if loop:
        from codeexcellent.budget import budget_manager

        planned.budget = budget_manager.allocate_loop(config)

    _print_compact_plan(planned)
    if loop:
        console.print("[dim]looping until done or the ceiling above is hit[/dim]")
    console.print()

    with console.status("[cyan]Working...[/cyan]", spinner="dots") as status:
        def _on_step(msg: str) -> None:
            shown = _interactive_step(msg)
            if shown:
                status.update(f"[cyan]{shown}[/cyan]")

        report = run_engine(prompt, root, config, engine, on_step=_on_step, planned=planned)

    _print_compact_result(report)
    console.print()


def _handle_slash_command(line: str, root: str, config: dict, engine: ClaudeRunner) -> bool:
    """Returns True if `line` was a recognized slash command (handled here,
    the main loop should just continue) -- False if it wasn't one at all,
    in which case the caller treats it as a normal task.
    """
    if not line.startswith("/"):
        return False

    command, _, rest = line.partition(" ")
    command = command.lower()
    rest = rest.strip()

    if command in ("/help", "/"):
        _print_slash_menu()
        return True

    if command == "/doctor":
        cmd_doctor(argparse.Namespace(root=root))
        console.print()
        return True

    if command == "/loop":
        if not rest:
            console.print('[yellow]Usage:[/yellow] /loop <task description>\n')
            return True
        _run_task_in_session(rest, root, config, engine, loop=True)
        return True

    console.print(f"[yellow]Unknown command[/yellow] {command} -- type [bold]/help[/bold] for a list.\n")
    return True


def _interactive() -> int:
    root = str(Path.cwd())
    config = load_config()
    engine = ClaudeRunner(config)
    _print_startup_banner(root, config, engine)

    available, detail = engine.is_available()
    if not available:
        err_console.print(f"[bold red]Claude CLI is not available:[/bold red] {detail}")
        err_console.print("Run [bold]codeexcellent doctor[/bold] for details.")
        return 1

    while True:
        try:
            console.print("[bold cyan]❯[/bold cyan] ", end="")
            prompt = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if not prompt or prompt.lower() in ("exit", "quit"):
            return 0

        if _handle_slash_command(prompt, root, config, engine):
            continue

        _run_task_in_session(prompt, root, config, engine)


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
    run_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts (dirty repo, loop mode cost)")
    run_parser.add_argument(
        "--loop", action="store_true",
        help="Keep retrying with feedback under a much higher (but still finite) call/cost ceiling "
             "until the task's own validation says done, instead of stopping at the normal "
             "difficulty-band budget. For project-level asks, not quick fixes. See config['loop_mode'].",
    )
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
