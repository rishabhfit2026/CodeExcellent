# Changelog

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
