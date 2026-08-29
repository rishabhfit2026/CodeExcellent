# Investigation: making CodeExcellent the primary CLI

## The actual goal

Not "beat Claude on a benchmark." The goal is adoption: someone who already has
the `claude` CLI installed and working reaches for `codeexcellent` in their
terminal *by default*, because it gives better quality per unit of effort/cost
spent — not because it's always cheaper, and not because it's always smarter,
but because it spends resources adaptively instead of uniformly.

This is the project's own stated principle (README): **quality per resource,
not minimum tokens.** Effort and quality are the axis that matters, not raw
speed or raw cost in isolation. A benchmark result is only useful insofar as
it's evidence for that axis.

## Where we actually stand (as of the public compare benchmark, 48 runs)

- **Trivial → hard (12 of 16 tasks): a real, measured win.** Same correctness
  as raw Claude, cheaper on 11 of 12. This is the part of the pitch that's
  already true and already provable by anyone who reruns it.
- **very_hard (3 of 16 tasks): mixed.**
  - `very_hard_architecture_migration` — tie (both fail every time). Not a
    CodeExcellent-specific weakness.
  - `very_hard_auth_migration` — CodeExcellent spends ~10x more (mandatory
    review on a CRITICAL-risk task) with **zero validator to show it's worth
    it**. This is the shakiest part of the current story — not because the
    review is a bad idea, but because we can't currently prove it does
    anything.
  - `very_hard_cross_module_redesign` — a real, twice-observed regression
    (also showed up in the earlier internal strategy-forced benchmark under
    the FULL strategy). CodeExcellent is cheaper here AND passes less often.
    This is the one finding that actually needs fixing, not just explaining.

Full data: `benchmarks/public_compare_results.json` /
`benchmarks/public_compare_summary.json`. Report: the published "CodeExcellent
vs Claude" artifact.

## Plan

### 1. Close the `cross_module_redesign` regression
Highest-leverage fix because it's already diagnosed and observed twice. Needs
root-cause investigation into why the LIGHTWEIGHT/FULL path underperforms
DIRECT specifically on this task's shape (cross-module interface coupling) —
not a threshold tweak, an actual understanding of what's going wrong in the
generated fix.

### 2. Give `very_hard_auth_migration` a real validator
Right now this is CodeExcellent's single most expensive behavior (10x cost)
with no evidence behind it. Either the mandatory review catches something raw
Claude misses (great, now provable) or it doesn't (then the cost isn't
justified as designed). A synthetic sandboxed OAuth2 migration can't be
validated against a real provider, so this needs a validator that checks a
meaningful structural/behavioral proxy without being gameable — same bar as
the fixes already applied to the other validators in this suite.

### 3. Re-run and re-verify — don't just claim the fix worked
Same benchmark, same isolation discipline (fresh fixture per run, hash-verified),
same non-forced adaptive strategy selection. A claimed fix that hasn't been
re-measured the same rigorous way isn't a fix yet, it's a hypothesis.

### 4. Ship the current honest version now, in parallel — don't gate on 1-3
The trivial-through-hard win is real today. Publishing it now, with the
very_hard gap disclosed honestly, gets real usage and real feedback sooner
than waiting for a cleaner story. Real users on real (non-synthetic) repos
will surface things this 16-task fixture suite can't.

### 5. Distribution
Highest-signal audience: people already running `claude` CLI directly —
Claude Code communities, r/ClaudeAI, Anthropic's Discord, Show HN — since
they're one step from adoption and already understand exactly what's being
compared.

## Immediate next step (in progress)

Dogfooding: actually use `codeexcellent` as the daily-driver CLI, not just
benchmark it in isolated fixtures. Real friction on a real project is the
fastest way to find out if "quality per resource" holds up outside a
controlled benchmark, and it's the same experience a new user would have.
