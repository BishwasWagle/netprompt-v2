# RuntimeManager — Senior Engineering Review (June 2026)

A reverse-engineering and code-quality review of the `runtime/` inner-loop control
system and its surrounding repository. The mandate was explicit: **understand the
architecture and data flow, then improve quality, scalability, and maintainability
without changing functionality.**

Baseline at time of review: `pytest tests/unit` → **212 passed in 0.31s** (green).
Every refactor in this review is designed to keep that suite green.

> **Status (kept current).** Two phases of this review's plan have since been
> **applied** to the repo: **Phase 0** (commit `f9eab54`) removed the
> duplicate `network/` archive trees, backup shadows, and the stray harness
> (~664 MB freed; working tree now ~420 MB); **Phase 1** (commit `e5e9d8c`)
> added real `typing.Protocol` classes + centralized vocabulary constants +
> a shared `dimension_margins`. Consequently: (a) issues those phases resolved
> are marked **✅ resolved** where they appear; (b) some `file:line` references
> in docs 01–02 reflect the **review-time snapshot** (Phase 1 shifted line
> numbers in `contracts.py`/`adapt.py`/`evaluator.py`/`runtime_manager.py`) —
> the cited symbol is authoritative, the line may be ±a few. Pre-Phase-0
> duplication counts (e.g. "~1.1 GB", "184→53 .py files") are labelled as the
> review-time figures that motivated Phase 0. See
> [09-summary.md](09-summary.md) for the live implementation-status table.

## Documents

| # | Document | What it covers |
|---|----------|----------------|
| 1 | [01-architecture.md](01-architecture.md) | Clean architecture breakdown — the two-loop MAPE-K model, component responsibilities, and the complete end-to-end episode data flow. |
| 2 | [02-critical-problems.md](02-critical-problems.md) | Critical problem areas across the 5 requested dimensions, severity-ranked, each with `file:line` evidence. |
| 3 | [03-refactoring-strategy.md](03-refactoring-strategy.md) | A sequenced, low-risk-first refactoring plan with test gates. |
| 4 | [04-production-code.md](04-production-code.md) | Drop-in, behavior-preserving production-grade code for the highest-value fixes. |
| 5 | [05-evolution-from-original.md](05-evolution-from-original.md) | How the system evolved from Bishwas & Kiran's original NetPrompt (slow planner, KG, selector, test env) — architecture/workflow shift, the two models, and the LoRA retraining (what was done and how it improved). |
| 6 | [06-knowledge-graph.md](06-knowledge-graph.md) | The KG as the system's coordination hub — how the slow planner and runtime manager (and their models) read/write it, improvements over the original, and the KG-coordinated multi-model design (framed as design rationale; see [08](08-novelty.md) for a calibrated novelty assessment). |
| 7 | [07-workflow-and-usage.md](07-workflow-and-usage.md) | The operational workflow & usage — the six-stage run lifecycle, the three ways to run an episode, the two models, testing, gotchas, and a command cheat-sheet (frames `docs/guides/usage.md`). |
| 8 | [08-novelty.md](08-novelty.md) | Honest, prior-art-grounded novelty assessment — are the claimed novelties (incl. the KG ones) genuine vs the state of the art? — plus six creative-but-realistic edge-network use cases. |
| 9 | [09-summary.md](09-summary.md) | Digest & reading guide — a concise, per-document summary of this whole review set, a master facts table, and current implementation status. Start here to navigate. |

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
quality debt is **not** in the control logic — it was at the edges: a ~1.1 GB
forked-duplicate `network/` archive committed to git (**✅ removed in Phase 0**),
contracts and protocols expressed as prose docstrings rather than
`typing.Protocol`/enums (**✅ added in Phase 1**), and a handful
of still-open hot-path performance costs (per-attempt subprocess storms, per-statement KG
sessions, an un-timed blocking LLM call). All are fixable without touching
behavior.

---

## Key Takeaways

- **The verdict: sound control logic, debt at the edges.** This June 2026 senior engineering review of RuntimeManager concludes the `runtime/` package is genuinely well-engineered — a clean MAPE-K control loop with carefully reasoned safety properties (domination guard, hysteresis, fail-safe escalation), pure/testable computation cores, and injectable I/O seams. The quality debt is explicitly *not* in the control logic but at the periphery, and all of it is fixable without changing behavior.

- **The "edges" are concrete and named.** The biggest debt items were a ~1.1 GB forked-duplicate `network/` archive committed to git (**✅ removed in Phase 0**), contracts and protocols written as prose docstrings instead of `typing.Protocol`/enums (**✅ added in Phase 1**), and a handful of still-open hot-path performance costs (per-attempt subprocess storms, per-statement KG sessions, and an un-timed blocking LLM call — Phase 2).

- **Findings were adversarially verified against the actual code.** The review combined a direct read of every module in `runtime/` with a multi-agent pass that mapped each subsystem and hunted issues across five dimensions, then checked every finding against the real source. The severities reported throughout are the post-verification, corrected values — not raw first-pass guesses.

- **Documented tradeoffs are not bugs.** Because this is unusually well-documented research code, several plausible-sounding findings were deliberately thrown out as intentional, documented design decisions rather than defects — captured in the "What is NOT a problem" appendix of 02-critical-problems.md. The calibration philosophy credits deliberate design instead of flagging it.

- **A green 212-test baseline anchors every refactor.** At review time, `pytest tests/unit` reported 212 passed in 0.31s, and the explicit mandate was to improve quality, scalability, and maintainability *without changing functionality* — so every proposed refactor is designed to keep that suite green.

- **Nine documents, with a clear entry point.** The set spans architecture (01), severity-ranked critical problems with `file:line` evidence (02), a low-risk-first refactoring strategy with test gates (03), drop-in production code (04), the evolution from Bishwas & Kiran's original NetPrompt (05), the knowledge graph as coordination hub (06), the operational workflow & usage (07), an honest novelty assessment with edge-network use cases (08), and a summary/reading guide (09) — start with 09-summary.md to navigate.
