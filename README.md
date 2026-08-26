# CodeExcellent

A resource-aware orchestration layer around the Claude CLI. CodeExcellent
does not replace Claude's reasoning or coding ability — it decides *how
much* of it a given task actually needs, then gets out of the way.

```
Task → estimate difficulty → allocate a budget → call Claude → validate → stop
```

A one-line rename gets one cheap call at low effort. A multi-file
architecture migration gets a plan, a larger budget, tests, and a review
pass. Neither gets more than it needs.

## Why this architecture

- **Python 3.10+, stdlib only.** No web framework, no ORM, no third-party
  CLI toolkit. The whole system is: parse text, score it, shell out to a
  CLI, read JSON back, run tests, write a SQLite row. Anything heavier would
  be optimizing for the wrong thing given the actual problem size.
- **One package per pipeline stage** (`analyzer/`, `budget/`, `claude/`,
  `quality/`, `core/`), each independently unit-testable with the real
  `Budget`/`DifficultyScore`/`ClaudeCallResult` dataclasses in
  `core/models.py`. `core/engine.py` is the only module that knows the full
  pipeline order — swapping any one stage doesn't require touching the
  others.
- **`CodingEngine` is an abstract interface** (`claude/engine.py`);
  `ClaudeRunner` is its only implementation today. A future engine for a
  different CLI/model plugs in without touching the orchestrator.
- **No fake token accounting.** `--output-format json` on the installed
  Claude CLI genuinely returns `total_cost_usd` and `usage.{input,output}_tokens`
  per call — that's what's recorded. Nothing here estimates or fabricates
  usage the CLI doesn't report.

## Requirements

- Python 3.10+
- The `claude` CLI installed and authenticated (`claude --version` should
  work). CodeExcellent shells out to it; it does not call any API directly.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install pytest   # only needed to run the test suite
```

Then either use `.venv/bin/codeexcellent`, or activate the venv
(`source .venv/bin/activate`) and just use `codeexcellent`.

## Usage

```bash
# Interactive mode
codeexcellent

# One-shot
codeexcellent "Add pagination to the users API"
codeexcellent run "Add pagination to the users API"

# See the difficulty/budget estimate without executing anything
codeexcellent analyze "Migrate JWT auth to OAuth"

# Environment check
codeexcellent doctor

# Past runs for the current project (stored in <root>/.codeexcellent/history.db)
codeexcellent history

# Inspect merged configuration
codeexcellent config --show
```

`--root <path>` targets a different repository (works before or after the
subcommand). `run` accepts `-y/--yes` to skip the pre-existing-changes
confirmation prompt.

## How a task is handled

1. **TaskAnalyzer** (`analyzer/task_analyzer.py`) scores the raw request
   text on complexity, scope, risk, testing signal, architecture impact, and
   ambiguity using keyword/shape heuristics — no repo access yet.
2. **RepositoryAnalyzer** (`core/repository.py`) inspects the target repo:
   project type, languages, frameworks, test locations, git state, and a
   keyword-ranked shortlist of files actually relevant to the request. The
   whole repo is never sent anywhere.
3. **DifficultyScorer** (`analyzer/difficulty_scorer.py`) blends both into
   one 0–10 score using configurable weights, with a risk floor (a
   dangerous-sounding short request is never scored trivially) and picks an
   execution mode: `direct`, `lightweight`, or `full` (plan → implement →
   test → review).
4. **BudgetManager** (`budget/budget_manager.py`) maps the difficulty band
   to real Claude CLI levers — `--effort` and `--max-budget-usd` — plus
   orchestrator-side limits (call count, retries, timeout). Budgets escalate
   progressively on retry rather than starting maximal.
5. **ContextManager** (`core/context.py`) reads only the shortlisted files,
   truncated and capped, and renders the prompt context.
6. **ClaudeRunner** (`claude/claude_engine.py`) invokes `claude -p ...
   --output-format json` in the target repo's working directory so Claude's
   own Edit/Write tools modify the real files.
7. **TestRunner** (`core/test_runner.py`) runs the project's own test suite
   directly (pytest/npm/go/cargo, best-effort detection) — never via Claude,
   so validating a change never costs a Claude call.
8. **QualityChecker** (`quality/quality_checker.py`) scores the result
   heuristically (files actually changed, scope discipline, tests passing).
   For harder/riskier tasks (configurable threshold) it also runs one
   structured Claude review call via `--json-schema`.
9. **StopController** (`core/stop_controller.py`) decides whether to stop.
   Remaining budget is never by itself a reason to continue — only whether
   quality is actually satisfied, and whether retrying is still allowed.
10. **RetryManager** (`core/retry.py`) builds the next prompt with concrete
    feedback (issues found, test failures) — never resends the same prompt.
11. **TaskMemory** (`core/memory.py`) records every run to
    `<root>/.codeexcellent/history.db` for `codeexcellent history`.

## Configuration

Defaults live in `codeexcellent/config/defaults.json` (difficulty bands,
scoring weights, planning thresholds, per-band budgets, quality threshold,
allowed Claude tools, context size caps). Override any subset at
`~/.codeexcellent/config.json` (or `$CODEEXCELLENT_CONFIG`) — only the keys
you set are merged over the defaults.

## Testing

```bash
.venv/bin/pytest -q
```

All 40 tests run against a mocked `CodingEngine`/`subprocess.run` — no
Claude subscription or API access is required. Nothing shells out to the
real `claude` binary during the test suite.

## Current scope / what's next

This is the Phase 1–5 MVP from the project plan: CLI, task analysis,
difficulty scoring, budget allocation, the Claude runner, test running, and
quality/stop gating are all wired end-to-end and have been verified against
the real `claude` CLI. Not yet built (deliberately, to avoid overengineering
a first version): a learned difficulty estimator from history (`history`
already collects the data it would train on), content-aware (not just
path-based) relevant-file search, and a second `CodingEngine` implementation
to prove out the abstraction.
