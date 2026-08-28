# CodeExcellent

An adaptive coding-agent CLI built around the Claude CLI. Install it, `cd`
into a project, and run `codeexcellent` for an interactive coding-agent
session — CodeExcellent decides *when, why, and how much* of Claude's
reasoning a given task actually needs, learns from what actually happened,
and gets out of the way.

The guiding principle is **quality per resource, not minimum tokens**:
CodeExcellent is designed to use the minimum sufficient intelligence and
validation to complete a task correctly, not to minimize resource use at
the expense of correctness. A one-line rename gets one cheap call at low
effort, no plan, no review. A payment-code one-liner gets a mandatory
review even though it's short, because risk — not just difficulty — drives
how much validation a task gets. A multi-file architecture migration gets a
plan, a larger budget, tests, and a review pass. Every prediction is
checked against what actually happened, and that history feeds back into
the next prediction for a similar task.

```
Task → estimate difficulty (heuristic + history) → forecast resources
     → select a strategy → allocate a budget → call Claude → validate
     → stop → record what actually happened
```

## Why this architecture

- **Python 3.10+, stdlib only.** No web framework, no ORM, no ML library, no
  third-party CLI toolkit. The whole system is: parse text, score it, shell
  out to a CLI, read JSON back, run tests, write SQLite rows. Anything
  heavier would be optimizing for the wrong thing given the actual problem
  size — the "adaptive" layer is a transparent statistical blend over real
  history, not machine learning, by design (see [Adaptive estimation](#adaptive-estimation-prediction-vs-reality)).
- **One package per pipeline stage** (`analyzer/`, `budget/`, `claude/`,
  `quality/`, `core/`, `benchmark/`), each independently unit-testable
  against the dataclasses in `core/models.py`. `core/engine.py` is the only
  module that knows the full pipeline order; `engine.plan()` is the single
  function both `run` and `analyze` call for everything decided before a
  Claude call is made, so the two can never show different numbers for the
  same task (a real bug in an earlier version: `analyze` used to compute
  difficulty directly and skip strategy selection entirely).
- **`CodingEngine` is an abstract interface** (`claude/engine.py`);
  `ClaudeRunner` is its only implementation today. A future engine for a
  different CLI/model plugs in without touching the orchestrator.
- **No fake token accounting.** `--output-format json` on the installed
  Claude CLI genuinely returns `total_cost_usd` and `usage.{input,output}_tokens`
  per call — that's what's recorded. Nothing here estimates or fabricates
  usage the CLI doesn't report, and CodeExcellent never claims unused
  capacity from one task can be "transferred" to another — it just avoids
  spending it in the first place.

## Requirements

- Python 3.10+ (Linux, macOS, or Windows — pure Python, no OS-specific code)
- The `claude` CLI installed and authenticated (`claude --version` and
  `claude auth status` should both work — `codeexcellent doctor` checks
  both). CodeExcellent shells out to it; it does not call any API directly.

## Installation

**From PyPI** (once published):

```bash
pip install codeexcellent
```

**From source**, as a global command via [pipx](https://pipx.pypa.io) (recommended —
isolates it from your other Python projects):

```bash
pipx install .
```

**From source**, into a virtualenv for development:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"   # editable install + pytest/build/twine
```

Either way, `codeexcellent` becomes a global command (or `.venv/bin/codeexcellent`
/ `source .venv/bin/activate && codeexcellent` for the venv install). Verify
with `codeexcellent doctor`.

**Uninstall:**

```bash
pipx uninstall codeexcellent   # if installed via pipx
pip uninstall codeexcellent    # if installed via pip
```

This removes the package and the `codeexcellent` command cleanly. It does
not touch `~/.codeexcellent/config.json` (your settings) or any project's
`.codeexcellent/history.db` (your task history) — those are your data, not
installation artifacts, and are left in place. Delete them manually if you
want a fully clean slate.

**Building a release** (for maintainers):

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m build          # produces dist/*.whl and dist/*.tar.gz
.venv/bin/python -m twine check dist/*
.venv/bin/python -m twine upload dist/*   # publish to PyPI
```

## Usage

The primary experience is the bare `codeexcellent` command, run from inside
a project:

```
$ cd my-project
$ codeexcellent

CodeExcellent v0.2.0
Repository: my-project
Claude CLI: ✓
Git: ✓ (main)

Type a task, or "exit" to quit.

> Fix the authentication bug
Difficulty: 6.1/10  Risk: MEDIUM  Confidence: 0.78
Strategy: Lightweight planning

Implementing...
Testing...

✓ Task completed
Quality: 9.0/10 | Files changed: 2 | Cost: $0.14
```

Each turn is analyzed, classified, and executed with a strategy sized to
that specific task — you don't need to know or think about the internal
orchestration; type what you want done. `codeexcellent doctor` (see below)
is worth running once after install to confirm the Claude CLI and git are
detected correctly.

One-shot execution (no REPL) is also fully supported, for scripting or a
single task:

```bash
codeexcellent "fix the login bug"
codeexcellent run "fix the login bug"       # equivalent, explicit form

# See the difficulty/strategy/budget/forecast without executing anything
codeexcellent analyze "Migrate JWT auth to OAuth"

# Environment check: CLI, auth, git, config validity, history DB
codeexcellent doctor

# Past runs for the current project, including prediction-vs-reality
codeexcellent history

# Inspect merged configuration, or scaffold a starter override file
codeexcellent config --show
codeexcellent config --init

# Representative task suite, zero-cost by default (see Benchmarking below)
codeexcellent benchmark

# Version
codeexcellent --version
```

`--root <path>` targets a different repository (works before or after the
subcommand). `run` accepts `-y/--yes` to skip the pre-existing-changes
confirmation prompt. `--debug` (or `$CODEEXCELLENT_DEBUG=1`) shows the full
Python traceback on an unexpected error instead of a one-line message —
useful when filing a bug report.

Interactive mode shows a condensed per-task summary (difficulty/risk/
confidence/strategy, then short progress phrases, then a one-line result) --
`analyze`/`run`'s fuller multi-section breakdown (reasons, budget, resource
forecast) is always available on demand via `codeexcellent analyze "..."`.

## Execution modes

- **`direct`** — no plan, no review. For tasks where planning's estimated
  benefit doesn't exceed its cost (one extra Claude call + its context).
- **`lightweight`** — implement, then test.
- **`full`** — plan (a separate, read-only, JSON-schema-constrained call),
  implement, test, review.
- **`review_required`** — no plan phase, but a Claude review is mandatory
  regardless of how the difficulty number comes out. This exists because
  risk and difficulty aren't the same axis: a one-line change to payment
  logic is CRITICAL risk even if it only touches one file (see
  [Quality levels & risk](#quality-levels--risk-aware-execution)).

The choice isn't a raw difficulty threshold — see `analyzer/strategy_selector.py`.
Planning is genuinely cost-aware: its estimated benefit (from difficulty,
low confidence, architecture impact, risk) is weighed against its cost
before it's used at all.

## Adaptive estimation: prediction vs. reality

Every run is scored twice:

1. **Predicted difficulty** (`analyzer/difficulty_scorer.py`) — a heuristic
   0–10 blend of task-text signals (complexity, scope, risk, testing/
   architecture impact, ambiguity) and repo signals (file count, language/
   framework mix), with a risk floor so a short but dangerous request is
   never scored trivially.
2. **Observed difficulty** (`core/outcome.py`), computed *after* the run
   from what actually happened: retries beyond the first call, the quality
   shortfall, and how long it took relative to budget.

`codeexcellent history` shows both, plus the error between them. Every row
also records observability metadata beyond the prediction itself: strategy
used, whether planning ran, actual resource usage (cost, duration, calls,
retries, files changed), and test outcome (ran/passed/failed counts) — no
source code, diffs, or file contents, only the task description and these
metrics. That history is also the training data for
`analyzer/adaptive_estimator.py`: for
a task whose `TaskFingerprint` (category + repo type + scope + risk — never
the request text or source code) matches at least `min_samples_for_blend`
(default 3) past runs, the heuristic estimate is blended toward the observed
average, weighted by a confidence that grows with sample size and shrinks
with variance. Below that sample count, the heuristic stands unchanged —
this is a transparent statistical blend, not machine learning; the docstring
in that file is the whole algorithm.

Not every outcome is valid training signal (`core/outcome.py`'s
`OutcomeClass`): a Claude CLI timeout or crash is classified as
`infra_failure` and excluded, because it says nothing about how hard the
task actually was. Only `success`, `task_difficulty_failure`, and
`ambiguous_requirement` outcomes feed the estimator.

Confidence also drives allocation directly (`budget/budget_manager.py`'s
`allocate_adaptive`): a low-confidence estimate gets a conservative margin
added so an uncertain prediction doesn't under-provision the run; a
high-confidence one is left at the already-tight band default.

`budget/resource_forecaster.py` does the same historical-vs-heuristic split
for *expected* Claude calls/retries/duration, shown in the "Resource
forecast" section of `analyze`/`run` output before any call is made.

## Quality levels & risk-aware execution

Risk (`analyzer/risk_classifier.py`) has four levels — `low` / `medium` /
`high` / `critical` — the last triggered either by a high risk-keyword
density or specific combinations (payment/billing keywords; destructive
verbs like drop/delete/truncate paired with database/production/users).
Risk maps to a `QualityLevel` (`trivial` → `critical`), which drives:

- the minimum quality score required to call a task complete
  (`quality.min_pass_score_by_level` in config — 5.0 for trivial, 9.0 for
  critical), and
- whether a structured Claude review call is mandatory
  (`quality.mandatory_review_levels`), independent of the difficulty-based
  threshold.

## Context intelligence

`core/repository.py`'s `find_relevant_files` is a bounded, tiered search
run once per task, never re-run mid-task:

1. **Path match** — keyword overlap with file paths (cheap, no reads).
2. **Content match** — if path matching doesn't fill the result budget, a
   capped scan (300 files, 100KB/file) of file *contents* for the same
   keywords, so e.g. "fix JWT expiration" can match a function inside
   `token_service.py` even if the filename doesn't mention JWT.
3. **Dependency/test pull-in** — for the strongest matches, their direct
   imports (regex-based, Python/JS/TS) and same-named test files are added,
   capped at `context.max_dependency_files` (default 3).

`core/context.py` reads only that shortlist, truncated and capped
(`context.max_bytes_per_file`, `context.max_total_bytes`), and preserves the
tier ordering — primary matches always outrank pulled-in dependencies within
the budget. Retries don't re-send this context at all: they run inside
Claude's own resumed session (`--resume`), so a retry prompt
(`core/retry.py`) carries only new feedback (issues found, test failures),
not a restatement of context the session already has.

## Security & privacy

CodeExcellent operates locally: it shells out to the `claude` CLI (which
handles its own auth via `claude auth login`) and reads/writes files inside
the target repository. It never uploads repository contents anywhere on its
own, and it never introduces a separate credential store of its own.

Files that commonly hold secrets — `.env` and variants (except
`.env.example`/`.env.sample`/`.env.template`), private keys (`.pem`, `.key`,
`id_rsa` and friends), and credential stores (`.npmrc`, `.netrc`,
`credentials.json`) — are excluded from automatic context selection
entirely, regardless of keyword score, so a task like "fix the production
config" can't accidentally pull `.env.production`'s contents into a Claude
prompt (`core/repository.py::_is_sensitive_path`). This only affects
*automatic* pre-selection; Claude's own Read/Glob/Grep tools can still open
such a file directly if a task genuinely requires it — CodeExcellent isn't a
sandbox around Claude's tool access, only around what it proactively feeds
into the prompt.

`codeexcellent doctor`'s auth check reports login status and subscription
tier only — never the account email or org ID, even though the underlying
`claude auth status` returns them. History (`core/memory.py`) stores task
descriptions and outcome metadata (difficulty, cost, test pass/fail counts,
etc.), never file contents or diffs.

## Reliability

Every run reaches one of five terminal statuses: `COMPLETE`, `INCOMPLETE`
(quality never satisfied, retries/budget exhausted), `FAILED` (the Claude
call itself kept erroring), `BLOCKED` (pre-flight: CLI unavailable, or the
repository exceeds `repository.hard_file_ceiling`, checked before spending
anything), or `CANCELLED` (Ctrl-C during a run — caught, the partial result
is still recorded, not a crash).

## How a task is handled end-to-end

1. **TaskAnalyzer** (`analyzer/task_analyzer.py`) scores the raw request
   text — complexity, scope, risk, testing/architecture signal, ambiguity,
   and a `category` (rename/small_change/large_refactor/general) used in the
   fingerprint.
2. **RepositoryAnalyzer** (`core/repository.py`) inspects the target repo
   and produces the relevant-file shortlist above. The whole repo is never
   sent anywhere.
3. **DifficultyScorer + AdaptiveDifficultyEstimator** blend the heuristic
   score with matching history, as described above.
4. **StrategySelector** picks `direct` / `lightweight` / `full` /
   `review_required`, cost-aware and risk-aware.
5. **BudgetManager** allocates `--effort` / `--max-budget-usd` plus
   orchestrator-side call/retry/timeout limits, confidence-adjusted, and
   escalates progressively (one band at a time) on retry — never straight to
   maximum.
6. **ResourceForecaster** estimates expected calls/retries/duration for
   display before execution.
7. **ClaudeRunner** (`claude/claude_engine.py`) invokes `claude -p ...
   --output-format json` in the target repo's working directory, using only
   flags confirmed present in the installed CLI's `--help` — `--effort`,
   `--max-budget-usd`, `--permission-mode`, `--allowedTools`, `--json-schema`,
   `--resume`. `--json-schema` calls (planning, review) use the CLI's own
   `structured_output` field rather than re-parsing text.
8. **TestRunner** (`core/test_runner.py`) runs the project's own test suite
   directly (pytest/npm/go/cargo) — never via Claude.
9. **QualityChecker** scores the result heuristically against the
   quality-level-appropriate threshold, plus a mandatory/threshold-gated
   Claude review.
10. **StopController** decides whether to stop. Remaining budget is never by
    itself a reason to continue.
11. **OutcomeClassifier** (`core/outcome.py`) labels why the run ended the
    way it did and computes observed difficulty, for the history to learn
    from.
12. **TaskMemory** (`core/memory.py`) records the fingerprint, prediction,
    forecast, and actual outcome to `<root>/.codeexcellent/history.db`.

## Benchmarking

```bash
codeexcellent benchmark                    # mock engine, zero cost, always safe to run
codeexcellent benchmark --category hard    # just one category
codeexcellent benchmark --live             # real Claude CLI calls -- asks for confirmation, costs money
codeexcellent benchmark --live --compare   # + a raw "just call Claude directly" comparison per task
```

`codeexcellent/benchmark/tasks.py` defines 16 tasks spanning trivial through
very_hard. Each `BenchmarkTask` carries a task description, a fixture-repo
generator, and an `expected_behavior` string describing correct completion;
14 of the 16 also have a `validate(root) -> (passed, message)` function that
imports the fixture module in-process and calls its real functions,
asserting on actual return values — behavioral, not text-matching (e.g. the
trivial-rename validator calls `greet("World")` and checks it returns
`"Hello, World"`, and that the parameter isn't still named `nam`). Every
validator was verified against three synthetic states before being
trusted — the untouched baseline fails, a hand-written correct fix passes,
and a distinct incorrect fix fails — see `tests/test_benchmark_validators.py`.
That process caught a real bug class: for "preserve behavior while
restructuring" tasks, checking behavior preservation alone trivially passes
a complete no-op (unchanged code obviously preserves its own behavior), so
those validators pair it with a minimal structural signal that something
actually changed. The remaining 2 tasks deliberately have no validator, with
the reason documented at each — one has no fixed implementation contract to
assert on (any validator would false-negative a differently-shaped but
correct fix), the other's only seemingly-objective check turned out to be
satisfiable by a no-op too. Adding a `validate` to a task without one is a
self-contained addition to `tasks.py`.

The default mock engine (`benchmark/mock_engine.py`) makes no real Claude
calls — it validates CodeExcellent's own decision-making (difficulty,
strategy, call count) across the whole suite at zero cost, but it doesn't
produce correct code, so its quality scores and `validated` results aren't
meaningful (the report says so explicitly). `--live` runs the real thing
and is the only mode whose numbers say anything about actual code quality,
actual validation-check pass rate, or actual cost; `--compare` additionally
calls Claude directly with no orchestration on the same fixture, for an
honest A/B of CodeExcellent vs. "just use Claude" — never run
automatically, always behind an explicit confirmation. Each task runs in
its own fresh temporary directory, destroyed after use, and a `--compare`
run's raw-Claude execution gets an entirely separate directory from
CodeExcellent's own — the two systems (and consecutive tasks) cannot leak
state into each other.

The report distinguishes correctness (`status`, `validated`), efficiency
(`cost_usd`, `duration_ms`, `claude_calls`, `retries` — all real numbers
from the CLI's own accounting, never estimated tokens), quality (test pass/
fail counts, `quality_score`, `files_changed`), and prediction (difficulty,
confidence, risk, strategy, `planning_used`) as separate fields — a
validator only ever answers "did the expected behavior become true," never
"did CodeExcellent think this was hard." `report.totals()` and
`report.by_difficulty()` never average a metric over tasks it doesn't apply
to (e.g. `test_pass_rate` only counts tasks that actually ran tests).

A note on labels vs. reality: the benchmark task *names* ("hard_...",
"very_hard_...") are my own subjective difficulty labels when writing the
suite. The heuristic scorer doesn't always agree — e.g. "change the data
pipeline to process in batches" scores low because none of its words hit the
keyword lists, while "migrate app.py into a package" scores medium rather
than very-high because only one dimension (task complexity, from the word
"migrate") is elevated and the weighted blend pulls it down. That's a real,
known gap in the keyword-heuristic layer, left as-is rather than tuned to
match my own benchmark labels — doing so would be curve-fitting to the test
data. It's also exactly the gap the adaptive estimator is positioned to
close over time from real history, once a project has run enough similar
tasks.

### Live benchmark results (measured, 2026-08-26)

A real `--live --compare` run on the 5 easiest, most reliably-validated
tasks (the 3 trivial tasks plus `easy_validation`/`easy_helper` — all with
behaviorally-verified `validate()` functions that had already passed
baseline-fail/correct-pass/incorrect-fail testing). Each task ran twice from
an identical starting fixture, in separate isolated directories: raw
`claude -p` with no orchestration, and `codeexcellent` end-to-end. This was
a measurement-only run — no orchestration logic was changed before or
because of it, and none of these numbers have been used to tune anything.

| Task | Predicted difficulty | Confidence | Risk | Strategy | Planning used | CE calls/retries | CE cost | CE duration | Raw cost | Raw duration | CE validated | Raw validated |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trivial_rename | 0.68 (trivial) | 0.75 | low | direct | no | 1 / 0 | $0.0496 | 7,122ms | $0.0608 | 12,921ms | ✅ | ✅ |
| trivial_typo | 1.12 (trivial) | 0.75 | low | direct | no | 1 / 0 | $0.0492 | 6,882ms | $0.0535 | 12,686ms | ✅ | ✅ |
| trivial_constant | 1.63 (trivial) | 0.75 | low | direct | no | 1 / 0 | $0.0494 | 6,760ms | $0.0605 | 12,036ms | ✅ | ✅ |
| easy_validation | 1.36 (trivial†) | 0.75 | low | direct | no | 1 / 0 | $0.0496 | 7,366ms | $0.0616 | 24,516ms | ✅ | ✅ |
| easy_helper | 2.08 (easy) | 0.75 | low | direct | no | 1 / 0 | $0.0498 | 6,413ms | $0.0616 | 13,350ms | ✅ | ✅ |

† `easy_validation` is filed in the "easy" benchmark category, but the
heuristic scorer itself predicted the "trivial" band on this run — the same
labels-vs-reality gap noted above, reproduced here on live data.

No task in this batch triggered `testing_required`, so tests_passed/failed
aren't reported here (not applicable, not 0/0 — that would misleadingly
imply tests ran and none passed).

**Aggregates:**

| | CodeExcellent | Raw Claude |
|---|---|---|
| Correctness | 5/5 | 5/5 |
| Validated pass rate | 100% | 100% |
| Total cost | $0.2476 | $0.2981 |
| Avg cost/task | $0.0495 | $0.0596 |
| Avg duration/task | 6,909ms | 15,102ms |
| Avg quality score | 10.0/10 | n/a (not scored) |

**What this does and doesn't show:**
- Both systems got all 5 tasks correct on this sample — n=5 is too small to
  conclude anything about relative correctness in general; it only shows
  both work on simple tasks.
- CodeExcellent was cheaper (~17% less total cost) and faster (~54% less
  average duration) than calling Claude directly on every single task in
  this batch. These are real measured differences, not estimates — but they
  are five simple, `direct`-strategy tasks; CodeExcellent's planning/review/
  retry machinery never activated here, so this says nothing about
  medium/hard/very_hard tasks where it would.
- Two configuration differences are known, not speculative: CodeExcellent
  sets `--effort low` for trivial-band tasks and sends a curated context;
  raw Claude uses CLI defaults for both. Turn counts, context size actually
  consumed, and token counts were not captured for either side, so *why*
  the gap exists isn't confirmed here — only that it exists in this sample.
- Raw Claude's internal call/turn count isn't captured by `run_raw` (a
  single CLI invocation isn't decomposed into "calls" the way
  CodeExcellent's orchestration loop is), so `claude_calls`/`retries` are
  CodeExcellent-side-only metrics as measured — not a like-for-like
  comparison point.

## Configuration

Defaults live in `codeexcellent/config/defaults.json`: difficulty bands,
scoring weights, planning thresholds, per-band budgets, quality thresholds
(flat and per-quality-level), adaptive estimator sample/blend settings,
confidence thresholds and margins, strategy planning cost, context/repo
scan limits. Override any subset at `~/.codeexcellent/config.json` (or
`$CODEEXCELLENT_CONFIG`) — only the keys you set are merged over the
defaults. `codeexcellent config --init` scaffolds an empty starter file at
that path; `codeexcellent config --show` prints the fully merged result.

## Cross-platform notes

CodeExcellent is pure Python with no OS-specific code paths, and is tested
on Linux; macOS and Windows should work identically since every subprocess
call resolves its executable explicitly (`core/platform_utils.py`) rather
than assuming a Unix binary name or shell behavior — e.g. it runs tests with
`sys.executable` rather than a hardcoded `python3`, and resolves commands
like `npm` and `claude` to their full path so an npm-installed `.cmd`/`.ps1`
shim on Windows is found the same way a `.exe`/`.sh` would be on Linux/macOS.

## Testing

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

All 166 tests run against a mocked `CodingEngine`/`subprocess.run` and a
temp-directory SQLite history — no Claude subscription or API access is
required, and nothing shells out to the real `claude` binary during the
test suite. This includes packaging tests (`tests/test_packaging.py`) that
check the entry point, version wiring, and license file; cross-platform
executable-resolution tests (`tests/test_platform_utils.py`,
`tests/test_test_runner.py`); interactive-mode tests
(`tests/test_interactive.py`) that script stdin against a stub engine;
top-level error-handling tests (`tests/test_error_handling.py`) proving a
raw traceback never reaches the user outside `--debug`; benchmark framework
tests (`tests/test_benchmark.py`) covering isolation and metric/report
correctness; and per-task baseline/correct/incorrect validator tests
(`tests/test_benchmark_validators.py`). Beyond the unit suite, the pipeline
has also been verified against the real Claude CLI end-to-end
(trivial and CRITICAL-risk tasks via `analyze`, a real `run` against a
scratch repo, a full interactive-session smoke test), and the packaging
itself has been verified by building the sdist/wheel (`python -m build`,
`twine check`) and installing/uninstalling both into a throwaway virtualenv
from scratch.

## Current scope / what's next

This covers the spec's Phase 1–9 (audit, structured history,
prediction-vs-reality tracking, adaptive estimation, resource forecasting,
strategy selection, context intelligence, progressive allocation with
reasons, quality/risk-aware stopping) plus Phase 10–12 (benchmark/A-B
harness, `doctor`/CLI explainability, docs). Deliberately not built, to
avoid overengineering: an actual ML difficulty model (the spec explicitly
asks for statistical blending first, and there isn't yet enough real
project history to justify more), and a second `CodingEngine`
implementation to prove out the abstraction beyond Claude.

Packaging (v0.2.0): PyPI-ready `pyproject.toml` with full metadata, an MIT
license, a global `codeexcellent` command verified via a from-scratch
build/install/uninstall cycle, `--version`, `config --init`, and explicit
cross-platform executable resolution.

This release (v0.2.1): interactive mode is the primary UX (a startup banner,
a concise per-turn summary instead of the full `analyze` report, `--debug`
error handling), a real double-computation fix (`engine.run()` can now reuse
a caller's already-computed `plan()` instead of silently redoing the repo
scan and adaptive-history lookup), a security fix (`.env`/key/credential
files are now excluded from automatic context selection, regardless of
keyword match), phase-7 observability fields (`planning_used`, test pass/
fail counts) in history, and `expected_behavior`/`validate()` fields on the
benchmark task structure. None of this touched the difficulty/strategy/
budget decision logic in `analyzer/`, `budget/`, or `quality/` — it's CLI
UX, a resource-efficiency fix, and data-hygiene/observability work. See
[CHANGELOG.md](CHANGELOG.md) for the version history.
