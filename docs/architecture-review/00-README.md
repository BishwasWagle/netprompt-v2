# RuntimeManager — Senior Engineering Review (June 2026)

A reverse-engineering and code-quality review of the `runtime/` inner-loop control
system and its surrounding repository. The mandate was explicit: **understand the
architecture and data flow, then improve quality, scalability, and maintainability
without changing functionality.**

Baseline at time of review: `pytest tests/unit` → **212 passed in 0.31s** (green).
Every refactor in this review is designed to keep that suite green.

## Documents

| # | Document | What it covers |
|---|----------|----------------|
| 1 | [01-architecture.md](01-architecture.md) | Clean architecture breakdown — the two-loop MAPE-K model, component responsibilities, and the complete end-to-end episode data flow. |
| 2 | [02-critical-problems.md](02-critical-problems.md) | Critical problem areas across the 5 requested dimensions, severity-ranked, each with `file:line` evidence. |
| 3 | [03-refactoring-strategy.md](03-refactoring-strategy.md) | A sequenced, low-risk-first refactoring plan with test gates. |
| 4 | [04-production-code.md](04-production-code.md) | Drop-in, behavior-preserving production-grade code for the highest-value fixes. |

## How this review was produced

The analysis combined a direct read of every module in `runtime/` with a
multi-agent review that mapped each subsystem, hunted issues across five
dimensions, and **adversarially verified every finding against the actual code**.
That verification pass matters here: this is unusually well-documented research
code, and several plausible-sounding findings were *correctly thrown out* because
they describe deliberate, documented design decisions (see
[§ "What is NOT a problem"](02-critical-problems.md#appendix-what-is-not-a-problem)).
Severities below are the post-verification, corrected values.

## One-paragraph verdict

The `runtime/` package is genuinely well-engineered: a clean MAPE-K control loop
with carefully reasoned safety properties (domination guard, hysteresis, fail-safe
escalation), pure/testable computation cores, and injectable I/O seams. The
quality debt is **not** in the control logic — it is at the edges: a ~1.1 GB
forked-duplicate `network/` archive committed to git, contracts and protocols
expressed as prose docstrings rather than `typing.Protocol`/enums, and a handful
of hot-path performance costs (per-attempt subprocess storms, per-statement KG
sessions, an un-timed blocking LLM call). All are fixable without touching
behavior.
