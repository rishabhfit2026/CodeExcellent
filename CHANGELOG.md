# Changelog

## 0.2.1

**Interactive mode is now the primary UX** (no changes to difficulty/
strategy/budget decision logic):
- Bare `codeexcellent` shows a startup banner (version, repository name,
  Claude CLI / git status) and, per turn, a concise summary (difficulty/
  risk/confidence/strategy → short progress phrases → one-line result)
  instead of the full multi-section `analyze`/`run` report. That fuller
  report is unchanged and still available on demand via
  `codeexcellent analyze "..."`.
- `engine.run()` now accepts an optional pre-computed `plan()` result, so
  the interactive loop (which already displays the plan) doesn't pay for a
  second repo scan + adaptive-history lookup per turn. Callers that don't
  pass one behave exactly as before.
- Top-level error handling: an unexpected exception now prints one line and
  exits non-zero instead of a raw traceback, unless `--debug` (or
  `$CODEEXCELLENT_DEBUG=1`) is set. `KeyboardInterrupt` is now caught during
  task analysis too, not only during the Claude-call loop.

**A real security fix**: files that commonly hold secrets (`.env` and
variants, private keys, `.npmrc`/`.netrc`/credential stores) are now
excluded from automatic context selection entirely, regardless of keyword
match — a task like "fix the production config" could previously
substring-match and pull `.env.production`'s contents into the Claude
prompt. This only affects automatic pre-selection; Claude's own tools can
still open such a file directly if genuinely needed.

**Observability (phase 7)**: history now also records `planning_used` and
test pass/fail counts per task, additive to the existing schema.

**Benchmark framework**: `BenchmarkTask` gained `expected_behavior` (set for
all 15 tasks) and an optional `validate(root) -> (passed, message)`
programmatic correctness check (filled in for one task per difficulty
band), reported as `validated`/`validation_message` per result and a
`validated_pass_rate` in the summary — a corpus of ~15 hardcoded phrases
matching against pass/fail states isn't itself proof of anything; this is
what closes the gap between "a file changed" and "the task was actually
done correctly" for `--live` runs.

Also fixed along the way: `--root`/`--debug` extraction, and a hermeticity
fix to the `doctor` packaging test (it was making a real, unmocked
`claude auth status` call, which is what the "no real Claude subscription
required" testing claim was supposed to guarantee).

## 0.2.0

**Packaging / production readiness** (no orchestration logic changed):
- PyPI-ready `pyproject.toml`: full metadata (classifiers, keywords, URLs,
  MIT license), `dynamic` version sourced from `codeexcellent.__version__`,
  a `dev` extra (`pytest`, `build`, `twine`).
- `LICENSE` (MIT) and `MANIFEST.in` for sdist completeness.
- `codeexcellent --version` / `-v`.
- `codeexcellent config --init` to scaffold a starter user config file.
- `doctor` gained a config-validity check.
- Cross-platform executable resolution (`core/platform_utils.py`): fixed a
  real bug where the test runner hardcoded `python3` (not guaranteed on
  Windows) and invoked `npm` by bare name (fails on Windows, where `npm`
  resolves to `npm.cmd` and a non-shell subprocess launch won't find it
  without going through `shutil.which` first). The same fix was applied to
  the Claude CLI invocation and the benchmark harness's raw-Claude call.
- Verified end-to-end: built sdist + wheel (`python -m build`), validated
  with `twine check`, installed each into a from-scratch virtualenv,
  confirmed the global command and `defaults.json` packaging work, then
  confirmed `pip uninstall` removes the command and package files cleanly
  while leaving user data (`~/.codeexcellent/config.json`, per-project
  `.codeexcellent/history.db`) untouched.

**V2 — adaptive intelligence** (this version's orchestration work):
- Prediction-vs-reality tracking: `TaskFingerprint`, `OutcomeClass`, and
  observed-difficulty reconstruction from actual run signals, stored in the
  history DB.
- `AdaptiveDifficultyEstimator`: blends the heuristic difficulty toward the
  historical average for matching-fingerprint tasks once enough samples
  exist, confidence-weighted.
- `ResourceForecaster` and confidence-aware budget allocation.
- `StrategySelector`: cost-aware planning decisions and a `REVIEW_REQUIRED`
  mode that CRITICAL risk forces regardless of the difficulty number.
- Risk/quality-level tiers (`analyzer/risk_classifier.py`) driving pass
  thresholds and mandatory review.
- Context intelligence v2: content-aware fallback search plus bounded
  dependency/test-file pull-in.
- `codeexcellent benchmark`: a 15-task representative suite, zero-cost mock
  mode by default, `--live`/`--compare` for real Claude A/B comparison.
- `doctor` gained auth-status, git-binary, and history-DB checks.

## 0.1.0

Initial MVP: CLI (`run`/`analyze`/`doctor`/`history`/`config`), TaskAnalyzer,
RepositoryAnalyzer, DifficultyScorer, BudgetManager, `ClaudeRunner` (the real
Claude CLI integration via `--output-format json`), TestRunner,
QualityChecker, StopController, RetryManager, and SQLite-backed TaskMemory.
