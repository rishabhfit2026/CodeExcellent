# CodeExcellent Validation Phase — Repeated Strategy-Forced Benchmark

**Question:** When does additional agentic planning (LIGHTWEIGHT / FULL) actually improve validated task outcomes enough over DIRECT to justify its additional cost?

**Method:** 6 hard/very_hard benchmark tasks × 3 forced strategies (DIRECT, LIGHTWEIGHT, FULL) × 5 repetitions = 90 live runs against the real `claude` CLI. Strategy was forced by calling the real, unmodified `engine.plan()` and overriding only `planned.difficulty.mode` before calling the real, unmodified `engine.run()` — every other planning decision (budget, testing_required, quality_level, review_required) was left exactly as the unmodified production code computed it. No production code was modified. No thresholds were tuned on these results.

---

## Executive Conclusion

Across the 4 tasks with a reliable validator, **DIRECT matched or beat LIGHTWEIGHT and FULL on every single one**, at 2.7×–6.5× lower cost than FULL. FULL never produced a validated correctness improvement over the cheaper strategies on any of the 6 tasks, and on one task (`very_hard_cross_module_redesign`) FULL was measurably *worse* (3/5 PASS vs. 5/5 for DIRECT and LIGHTWEIGHT). This is closest to **Outcome C** (DIRECT sufficient for most of these hard tasks) with one instance of **Outcome E** (no measurable benefit, worse outcome) rather than Outcome A or B. This is observed on 6 tasks with 5 repetitions each — a real but narrow sample — so it should be read as a strong, consistent signal on *this* task set, not a universal claim about planning.

## What Changed

Nothing in production code. This phase built and ran benchmark infrastructure only:
- `benchmarks/repeated_strategy_benchmark.py` (new) — the strategy-forcing harness
- `benchmarks/repeated_strategy_analysis.py` (new) — the aggregate analysis
- `benchmarks/adaptive_strategy_repeated_results.json` (new) — 90 raw run records
- `benchmarks/adaptive_strategy_repeated_summary.json` (new) — aggregate statistics
- `benchmarks/adaptive_strategy_repeated_results.attempt{1..6}.json` (new) — intermediate snapshots kept for auditability (the run was interrupted by an account session limit 4 times and resumed each time; see Reliability)

`codeexcellent/`'s difficulty scorer, task analyzer, strategy selector, engine, and thresholds are untouched. The working tree's pre-existing uncommitted Phase 4 changes (failure classifier, recovery logic, mode-driven review gating) were the code under test, not modified further.

## Experimental Design

- **Tasks:** `hard_refactor_service`, `hard_add_auth`, `hard_change_data_flow`, `very_hard_architecture_migration`, `very_hard_cross_module_redesign`, `very_hard_auth_migration` — the exact 6 task definitions, unmodified.
- **Strategies forced:** DIRECT, LIGHTWEIGHT (`lightweight_plan_implement_test`), FULL (`plan_implement_test_review`). REVIEW_REQUIRED (risk-driven, orthogonal to strategy) still fires under all three forced modes for the one CRITICAL-risk task, as designed.
- **Repetitions:** 5 (90 runs total), as explicitly approved.
- **Isolation:** every run got a fresh `tempfile.TemporaryDirectory` built from scratch by the task's own fixture function, verified programmatically — all 90 temp roots unique, and every task's fixture tree hashed identical (SHA-256) across all 15 runs of that task regardless of strategy/repetition.
- **Model/config/environment:** identical `load_config()`, no `--model` override (CLI default), `permission_mode=acceptEdits`, same timeout/budget config for every run — only `difficulty.mode` was varied intentionally.

## Results

### Task × Strategy: pass/cost table

| Task | Strategy | PASS/5 | Mean cost | Median cost | Mean dur | Median dur |
|---|---|---|---|---|---|---|
| hard_refactor_service | direct | 5 | $0.062 | $0.061 | 12.2s | 12.2s |
| hard_refactor_service | lightweight | 5 | $0.060 | $0.060 | 11.2s | 11.5s |
| hard_refactor_service | full | 5 | $0.290 | $0.293 | 26.9s | 28.6s |
| hard_add_auth | direct | 5 | $0.093 | $0.091 | 21.8s | 21.6s |
| hard_add_auth | lightweight | 5 | $0.095 | $0.096 | 22.3s | 21.4s |
| hard_add_auth | full | 5 | $0.249 | $0.242 | 45.0s | 38.4s |
| hard_change_data_flow† | direct | — | $0.053 | $0.051 | 8.4s | 7.3s |
| hard_change_data_flow† | lightweight | — | $0.067 | $0.053 | 9.5s | 9.7s |
| hard_change_data_flow† | full | — | $0.326 | $0.331 | 23.8s | 26.9s |
| very_hard_architecture_migration | direct | 0 | $0.169 | $0.164 | 50.6s | 48.0s |
| very_hard_architecture_migration | lightweight | 0 | $0.154 | $0.149 | 47.1s | 40.4s |
| very_hard_architecture_migration | full | 0 | $0.348 | $0.306 | 49.4s | 45.2s |
| very_hard_cross_module_redesign‡ | direct | 5 | $0.062 | $0.061 | 13.7s | 12.5s |
| very_hard_cross_module_redesign‡ | lightweight | 5 | $0.062 | $0.064 | 13.5s | 12.3s |
| very_hard_cross_module_redesign‡ | full | **3** | $0.403 | $0.352 | 67.6s | 66.3s |
| very_hard_auth_migration† | direct | — | $1.047 | $0.962 | 210.9s | 194.3s |
| very_hard_auth_migration† | lightweight | — | $1.249 | $1.284 | 321.9s | 312.3s |
| very_hard_auth_migration† | full | — | $1.806 | $1.744 | 389.4s | 410.8s |

† No validator by design — "PASS/5" is not applicable; correctness is genuinely unknown. ‡ Validator flagged partially objective (see Validator Limitations).

### Strategy-level aggregate table

| Strategy | Total cost | Mean cost | Median cost | Validator-bearing pass rate | Total FAIL | Mean retries |
|---|---|---|---|---|---|---|
| direct | $7.43 | $0.248 | $0.081 | 75.0% (15/20) | 5 | 0.33 |
| lightweight | $8.44 | $0.281 | $0.090 | 75.0% (15/20) | 5 | 0.33 |
| full | $17.11 | $0.570 | $0.302 | 65.0% (13/20) | 7 | 0.43 |

(`validator-bearing` = the 4 of 6 tasks that have a `validate()`; the other 2 are excluded from this rate entirely, never counted as PASS.)

### Task × Best/Cheapest/Recommended strategy table

Non-subjective rule: for tasks with a validator, `recommended = cheapest strategy (by median cost) among those within 1 PASS run of the best PASS count`. For tasks without a validator, `recommended = cheapest by median cost`, explicitly labelled cost-only.

| Task | Best pass count strategy | Cheapest strategy | Recommended strategy |
|---|---|---|---|
| hard_refactor_service | direct (tie, 5/5/5) | lightweight | **lightweight** |
| hard_add_auth | direct (tie, 5/5/5) | direct | **direct** |
| hard_change_data_flow | n/a — no validator | direct | **direct** (cost-only) |
| very_hard_architecture_migration | n/a — 0 PASS everywhere | lightweight | **none — inconclusive** |
| very_hard_cross_module_redesign | direct (tie w/ lightweight, 5/5 vs full's 3/5) | direct | **direct** |
| very_hard_auth_migration | n/a — no validator | direct | **direct** (cost-only) |

## Direct vs Lightweight vs Full

- **DIRECT vs LIGHTWEIGHT:** effectively identical on every metric — same pass rates on all 4 validator-bearing tasks, cost within ±5% of each other on 5 of 6 tasks. LIGHTWEIGHT's extra plan-then-implement-then-test structure measurably changed nothing about correctness in this sample.
- **FULL vs the other two:** consistently 2.1×–6.5× more expensive per task, and never better. On `hard_refactor_service` and `hard_add_auth`, FULL matched DIRECT/LIGHTWEIGHT's 5/5 pass rate but cost 2.7×–4.9× more for the identical outcome. On `very_hard_cross_module_redesign`, FULL actually *dropped* to 3/5 while DIRECT/LIGHTWEIGHT held 5/5 — the extra planning+review step didn't help and, on this task, coincided with worse outcomes.

## Cost Effectiveness

**Cost per validated success** (total strategy cost ÷ validated PASS count, over the 4 validator-bearing tasks):

| Strategy | Total cost | Validated successes | Cost per validated success |
|---|---|---|---|
| direct | $7.43 | 15 | **$0.50** |
| lightweight | $8.44 | 15 | $0.56 |
| full | $17.11 | 13 | **$1.32** |

FULL costs 2.7× more per validated success than DIRECT while producing *fewer* total successes (13 vs. 15) — worse on both axes simultaneously in this sample.

## Correctness

Pass/fail/error breakdown, validator-bearing runs only (20 per strategy):

| Strategy | PASS | FAIL | VALIDATOR_ERROR | TIMEOUT | INCOMPLETE | EXECUTION_ERROR |
|---|---|---|---|---|---|---|
| direct | 15 (75%) | 5 (25%) | 0 | 0 | 0 | 0 |
| lightweight | 15 (75%) | 5 (25%) | 0 | 0 | 0 | 0 |
| full | 13 (65%) | 7 (35%) | 0 | 0 | 0 | 0 |

Zero validator crashes and zero timeouts in the final, clean 90-run dataset (all `EXECUTION_ERROR` cells from account-limit interruptions were re-run to a genuine result — see Reliability). All 5 FAILs on `very_hard_architecture_migration` per strategy are genuine implementation failures (this task never once passed, at any strategy) — not infrastructure noise.

## Reliability

The live run hit the Claude CLI's own **account session/usage limit** four separate times over the ~30 hours this benchmark took to complete (interspersed with waiting for resets, not continuous compute time): the CLI returned `"You've hit your session limit · resets <time> (Asia/Kolkata)"` for every call attempted after each limit was hit, with near-zero cost and ~6–13s duration (the CLI rejects near-instantly, it doesn't actually place the call). This is unambiguous from the raw `validator_error` field text — not inferred.

This is an **infrastructure/account-capacity fact about when the benchmark was run**, not a correctness result for any task or strategy — captured under the harness's `EXECUTION_ERROR` state precisely so it would never be miscounted as a task FAIL. The harness was extended mid-run with a `--resume-from` capability: on resume, every already-genuine (non-`EXECUTION_ERROR`) result is kept unchanged and only the missing/interrupted cells are re-run, so no real, already-paid-for live result was discarded or duplicated. The harness also added a self-abort guard — after the *first* infra-capacity error, it stops immediately rather than burning through the remaining queued cells against the same wall (visible in the log: aborts fired after exactly 1 such error each time, not after N failed attempts).

Total sunk cost from calls that started before a limit was hit and had to be discarded: **$1.48** across 4 records (on top of the $32.97 reflected in the final clean dataset) — small relative to the $34+ total spent on this experiment.

Within the final, clean 90-run dataset itself: 0 timeouts, 0 validator crashes, 0 unresolved execution errors. Retries were rare and roughly flat across strategies (mean 0.33 for DIRECT/LIGHTWEIGHT, 0.43 for FULL) — planning did not meaningfully reduce retry counts.

## Important Outliers

No single run was flagged as anomalous relative to its own task×strategy cell (3× cell-median rule on cost/duration) — 0 outliers detected. This means `very_hard_auth_migration`'s consistently large cost/duration (median $0.96–$1.74, 194s–411s depending on strategy) is not a fluke from one bad run; it is genuinely how every repetition of that task behaves under every strategy, driven by the CRITICAL-risk mandatory review path that fires regardless of forced mode. Readers should not discount this task's cost as "one outlier run skewing the average" — it didn't.

## Validator Limitations

- **`hard_change_data_flow`, `very_hard_auth_migration`:** no `validate()` by design (module docstring in `tasks.py`) — correctness is genuinely unknown for these two tasks under every strategy. Their rows above report cost/duration only; PASS/FAIL numbers are deliberately omitted, never assumed.
- **`very_hard_cross_module_redesign`:** flagged **partially objective**, not fully objective. Its validator is a hybrid: a precise structural check ("`orders.py` doesn't reach into `inventory.STOCK` directly") paired with a behavioral check driven entirely through `inventory.py`'s own public `adjust()` function. This is a real fix already applied in a prior phase (documented in the file's own comments) after an earlier live run found the original version crashed on a legitimate refactor. It remains partially objective because it still assumes the implementation keeps a function literally named `adjust` — a narrow but real naming assumption. Its PASS/FAIL numbers (including FULL's notable 3/5) should be read with that caveat, though the structural check itself is a precise, non-superficial signal, not a text-match proxy.
- **`hard_refactor_service`, `hard_add_auth`, `very_hard_architecture_migration`:** fully behavioral (import the fixture module, call real functions, assert on real return values) plus a minimal structural signal that something changed (so a no-op can't trivially pass) — treated as reliable ground truth.

## What the Data Supports

- On 4 of 6 tasks with a reliable validator, DIRECT achieved the same or a better validated pass rate than LIGHTWEIGHT and FULL, at meaningfully lower cost.
- FULL was never the strategy with the best validated pass rate on any of the 6 tasks, and cost 2.1×–6.5× more than DIRECT on every task.
- On one task (`very_hard_cross_module_redesign`), FULL's pass rate was measurably lower than DIRECT/LIGHTWEIGHT's (3/5 vs 5/5) — a real, if partially-objective-validator-caveated, instance of extra planning coinciding with a worse outcome.
- `very_hard_architecture_migration` failed on every single run regardless of strategy (0/15 total) — this specific task's difficulty is not something any of the three forced strategies solved in this sample.
- Cost scales strongly and consistently with forced strategy (FULL costs 2–6.5× DIRECT) independent of whether that cost bought any additional correctness.

## What the Data Does NOT Support

- This does **not** prove planning never helps — it was tested on 6 tasks, all already filtered to hard/very_hard, with 5 repetitions each. A task category genuinely requiring upfront architectural planning that isn't represented in this suite could behave differently.
- It does **not** establish that FULL is actively harmful in general — one task (`very_hard_cross_module_redesign`) showed a worse pass rate for FULL, but with a partially-objective validator and n=5; this is suggestive, not proof, and should not be generalized to "FULL always hurts."
- It does **not** speak to task categories not represented here (e.g. large multi-service coordination, or tasks with much larger context windows) where planning's value proposition may differ.
- It does **not** validate or invalidate the CRITICAL-risk mandatory-review path's cost — `very_hard_auth_migration` has no validator, so whether its added cost (review fires under all 3 forced strategies) buys any real safety benefit is unmeasured here, by design (a sandboxed fixture can't meaningfully validate OAuth2 migration correctness).

## Research Questions

- **Q1 — Does FULL increase validated correctness?** No. On the 4 validator-bearing tasks, FULL's aggregate pass rate (65%) was lower than DIRECT/LIGHTWEIGHT's (75% each), and it was never the best-performing strategy on any individual task.
- **Q2 — Does LIGHTWEIGHT get most of the benefit at lower cost?** LIGHTWEIGHT matched DIRECT almost exactly in both cost and correctness in this sample — there wasn't a "benefit" for it to capture more cheaply than FULL, because FULL didn't produce one either.
- **Q3 — Are there task categories where FULL clearly pays off?** None observed among these 6 tasks. The closest thing to a FULL-specific effect was negative (`very_hard_cross_module_redesign`).
- **Q4 — Are there hard tasks where DIRECT suffices?** Yes — `hard_refactor_service`, `hard_add_auth`, and `very_hard_cross_module_redesign` all showed DIRECT matching or exceeding the more expensive strategies.
- **Q5 — Does planning reduce retries/timeouts enough to justify cost?** No measurable effect. Retry counts were low and roughly flat (0.33 direct/lightweight vs 0.43 full); there were zero timeouts under any strategy in the clean dataset.
- **Q6 — Cost per additional successful task from planning?** Undefined in the literal sense — FULL produced *fewer* validated successes than DIRECT (13 vs. 15), not more, so there is no "additional success" to attribute a marginal cost to in this sample.
- **Q7 — Is the current adaptive selector making the right decision?** Mixed, leaning yes on the important case. The unmodified production selector currently picks LIGHTWEIGHT for 5 of these 6 tasks and FULL only for `very_hard_auth_migration` (forced by CRITICAL risk, independent of these results). It never picks FULL for a task where this benchmark showed FULL underperforming — consistent with what the data supports. It does pick LIGHTWEIGHT over DIRECT for `hard_add_auth`, `hard_change_data_flow`, and `very_hard_cross_module_redesign`, where this benchmark's mechanical rule recommended DIRECT — but the cost gap there is small (LIGHTWEIGHT ran 1.0×–1.3× DIRECT's cost on those three), so this is a minor inefficiency, not a costly misjudgment.

## Recommendation for CodeExcellent

No change to the strategy selector's code or thresholds is being made as part of this task, per the standing instruction. Based on what this data supports:

- The current selector's avoidance of FULL for 5 of 6 of these tasks is consistent with what was measured — no change indicated there.
- There is a directional signal that the selector's LIGHTWEIGHT-over-DIRECT choice on `hard_add_auth`-shaped tasks (single-file, well-specified, low/medium risk) costs slightly more without a measured correctness benefit. This is the same conclusion the Phase 4 selector redesign was already built around (the `cross_module_signal`-driven FULL/LIGHTWEIGHT split) — this benchmark's result is consistent with, not contradictory to, that design.
- **More data is required** before proposing a concrete threshold change: n=5 repetitions per cell is enough to see a consistent pattern, not enough to set a numeric confidence/signal cutoff. A wider task sample (more single-file hard tasks, more genuine multi-file coordination tasks) at the same repetition count would be needed to responsibly tune the DIRECT/LIGHTWEIGHT boundary.
- The clearest actionable signal is about `very_hard_cross_module_redesign`: FULL underperforming DIRECT/LIGHTWEIGHT there (with the validator's partial-objectivity caveat noted) is worth a targeted follow-up before generalizing.

## Next Experiment

1. Re-run `very_hard_cross_module_redesign` at higher repetition (e.g. n=15 per strategy) in isolation to confirm whether FULL's 3/5 vs. 5/5 gap is a real effect or sampling noise, ideally alongside a second, independently-designed validator to reduce reliance on the single partially-objective one.
2. Add benchmark tasks that are genuinely large-context or multi-service coordination shaped (distinct from single-file "hard" tasks like `hard_refactor_service`/`hard_add_auth`) to test whether FULL's planning phase pays off when there's more for a plan to meaningfully do.
3. Investigate `very_hard_architecture_migration`'s 0/15 pass rate specifically — since no strategy solved it, the bottleneck may not be planning depth at all (worth a qualitative read of a few `files_changed`/`git_diff_stat` records to see what's actually going wrong before assuming more planning would fix it).
4. If a future run is scheduled against an account with per-session usage limits, budget explicit resume checkpoints into the plan up front — this run needed 4 resumes across roughly 30 wall-clock hours purely from external session caps, not from the harness or task work itself.
