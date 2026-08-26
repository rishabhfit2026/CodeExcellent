# CodeExcellent

An adaptive, resource-aware orchestration layer around the Claude CLI.
CodeExcellent does not replace Claude's reasoning or coding ability — it
decides *when, why, and how much* of it a given task actually needs, learns
from what actually happened, and gets out of the way.

```
Task → estimate difficulty (heuristic + history) → forecast resources
     → select a strategy → allocate a budget → call Claude → validate
     → stop → record what actually happened
```

A one-line rename gets one cheap call at low effort, no plan, no review. A
payment-code one-liner gets a mandatory review even though it's short. A
multi-file architecture migration gets a plan, a larger budget, tests, and a
review pass. Every prediction is checked against what actually happened, and
that history feeds back into the next prediction for a similar task.

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

- Python 3.10+
- The `claude` CLI installed and authenticated (`claude --version` and
  `claude auth status` should both work — `codeexcellent doctor` checks
  both). CodeExcellent shells out to it; it does not call any API directly.

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

# See the difficulty/strategy/budget/forecast without executing anything
codeexcellent analyze "Migrate JWT auth to OAuth"

# Environment check: CLI, auth, git, config validity, history DB
codeexcellent doctor

# Past runs for the current project, including prediction-vs-reality
codeexcellent history

# Inspect merged configuration
codeexcellent config --show

# Representative task suite, zero-cost by default (see Benchmarking below)
codeexcellent benchmark
```

`--root <path>` targets a different repository (works before or after the
subcommand). `run` accepts `-y/--yes` to skip the pre-existing-changes
confirmation prompt.

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

`codeexcellent history` shows both, plus the error between them. That
history is also the training data for `analyzer/adaptive_estimator.py`: for
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

`codeexcellent/benchmark/tasks.py` defines 15 representative tasks, three
each at trivial/easy/medium/hard/very_hard (section 20 of the spec this was
built from). The default mock engine (`benchmark/mock_engine.py`) makes no
real Claude calls — it validates CodeExcellent's own decision-making
(difficulty, strategy, call count) across the whole suite at zero cost, but
it does not produce correct code, so its quality scores aren't meaningful.
`--live` runs the real thing and is the only mode whose numbers say anything
about actual code quality or actual cost; `--compare` additionally calls
Claude directly with no orchestration on the same fixture, for an honest A/B
of CodeExcellent vs. "just use Claude" — never run automatically, always
behind an explicit confirmation.

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

## Configuration

Defaults live in `codeexcellent/config/defaults.json`: difficulty bands,
scoring weights, planning thresholds, per-band budgets, quality thresholds
(flat and per-quality-level), adaptive estimator sample/blend settings,
confidence thresholds and margins, strategy planning cost, context/repo
scan limits. Override any subset at `~/.codeexcellent/config.json` (or
`$CODEEXCELLENT_CONFIG`) — only the keys you set are merged over the
defaults.

## Testing

```bash
.venv/bin/pytest -q
```

All 74 tests run against a mocked `CodingEngine`/`subprocess.run` and a
temp-directory SQLite history — no Claude subscription or API access is
required, and nothing shells out to the real `claude` binary during the
test suite. Beyond the unit suite, the pipeline has also been verified
against the real Claude CLI end-to-end (trivial and CRITICAL-risk tasks via
`analyze`, a real `run` against a scratch repo).

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
