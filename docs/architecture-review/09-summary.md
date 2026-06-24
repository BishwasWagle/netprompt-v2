# 9 · Review Set — Digest & Reading Guide

A one-screen summary of every document in `docs/architecture-review/`: what each
one is for, what's inside, the headline takeaways, and (where relevant) its
implementation status. Read this first to decide which document you need.

---

## How to read this set

```
00-README ──▶ 09-summary (you are here)
   │
   ├─ Understand the system ........ 01-architecture · 06-knowledge-graph
   ├─ See what's wrong / debt ...... 02-critical-problems
   ├─ Plan & apply fixes ........... 03-refactoring-strategy · 04-production-code
   ├─ Run / operate the system ..... 07-workflow-and-usage
   ├─ Novelty vs the state of the art  08-novelty
   └─ Understand how it got here ... 05-evolution-from-original
```

- **New to the codebase?** 01 → 06 → 05.
- **Reviewing / triaging debt?** 02 → 03 → 04.
- **Running or operating it?** 07-workflow-and-usage (lifecycle + commands).
- **Asking "is any of this novel"?** 08-novelty (honest, prior-art-grounded).
- **Ready to run experiments?** [`../design/experiment-validity.md`](../design/experiment-validity.md) (validity audit + go/no-go), [`../design/experiment-design.md`](../design/experiment-design.md) (the 3 experiments), and [`../design/experiment-results.md`](../design/experiment-results.md) (recorded E2a/E1 results).
- **Writing the paper / status report?** 05 → 06 → 08 (metrics + defensible framing).

**Baseline that anchors everything:** `pytest tests/unit` → **212 passed in
~0.3 s**. Every applied change kept it green.

---

## 00 · README — index & verdict
**Purpose:** entry point; one-paragraph verdict + how the review was produced.
**Inside:** the document table, the multi-agent + adversarial-verification method,
and the headline conclusion.
**Takeaway:** the `runtime/` control logic is genuinely well-engineered — the debt
is at the *edges* (a forked-duplicate archive, docstring-only contracts, a few
hot-path costs), all fixable without changing behavior.

---

## 01 · Architecture breakdown
**Purpose:** reverse-engineered architecture + complete data flow.
**Inside:**
- The **two-loop MAPE-K** model (slow LLM planner → deterministic `RuntimeManager`).
- A component map of every `runtime/` module and the core data contracts.
- An **end-to-end episode trace** — `DeploymentSpec` → gate → `observe_window` →
  6-stage evaluator ladder → tiered `adapt` → commit/rollback/escalate — naming
  the contract type crossing each boundary.
- The architectural *strengths* to preserve, and a "north-star" target shape.
**Takeaway:** one engine, one goal, pure/testable cores, safety-by-construction
(domination guard, hysteresis, fail-safe escalation). Don't rewrite the loop.

---

## 02 · Critical problem areas
**Purpose:** the issues, across the 5 requested dimensions, severity-ranked with
`file:line` evidence.
**Inside (the 4 HIGH items):**
1. **~1.1 GB of forked-duplicate `network/` archive** committed to git.
2. **`observe_window()` subprocess storm** on every adapt attempt.
3. **Blocking, un-timed LLM inference** in the loop.
4. **Magic-string vocabularies** (outcomes / switch-status / metrics) duplicated
   across 6 files (✅ fixed in Phase 1 — the M1 refactor, commit `e5e9d8c`).
Plus MEDIUM/LOW items — the margin-formula dup (✅ D2, Phase 1) and docstring-only
protocols (✅ A1, Phase 1) are resolved; in-memory `last_good` and per-statement KG
sessions remain open (Phase 2/3) — and an **appendix of 3 *cleared* claims**
(the shared `Budget`, the `policy_type` sniff, the Fake/real divergence) that are
documented, deliberate design — not bugs.
**Takeaway:** calibrated, not alarmist; severities are post-adversarial-verification.

---

## 03 · Refactoring strategy
**Purpose:** a sequenced, low-risk-first plan with test gates.
**Inside:** four phases —
- **Phase 0 — hygiene** (delete duplicates/shadows; `.gitignore`) — zero code risk.
- **Phase 1 — typing & vocabularies** (constants, margin helper, `_EPS`, Protocols).
- **Phase 2 — performance** (parallel pings, regen warmup+timeout, KG batching,
  `table_state`/liveness guards) — each defaulted to today's exact behavior.
- **Phase 3 — scalability** (KG-backed `last_good`, config split) — the only phase
  that adds a new code path; opt-in.
**Status:** **Phase 0 ✅ done** (commit `f9eab54`), **Phase 1 ✅ done**
(commit `e5e9d8c`). Phases 2–3 remain proposed.

---

## 04 · Production-grade code
**Purpose:** drop-in, behavior-preserving before/after for the top fixes.
**Inside:** 8 refactors — **M1** vocabularies, **D2** margin helper, **P1** parallel
pings, **P3** regen warmup+deadline, **A1** Protocols, **D1/D3/D5** deletions,
**D4** scrape module — each with an explicit "why behavior is unchanged" argument.
**Status:** the M1/D2/A1 refactors and the D1/D3/D5 deletions were **applied** in
Phases 0–1. P1/P3 (performance) remain ready-to-apply proposals.
**Takeaway:** every change is verified against the 212-test baseline; the doc is
the reference for the remaining Phase 2 work.

---

## 05 · Evolution from the original
**Purpose:** how the system grew from Bishwas & Kiran's original NetPrompt to today.
**Inside:** original → current across the **slow planner**, **KG**, **selector**,
**test environment**, **architecture/workflow**, the **two models**, and the
**LoRA retraining** — the centerpiece.
**Verified headline numbers:**
- Selector: hardcoded Cypher + if/elif ladder → constrained 6-key LLM decision.
- Tests: **0 assertions** (human-read `.txt`) → **212 unit / 454 asserts +
  16 node-integration / 62 asserts**.
- Retrain: distilled the `fallback_decision` oracle into a fresh LoRA — **640
  balanced examples**, r=16/α=32 (**18.46 M params, 1.18%**), 3 epochs fp32,
  **final loss 0.0066** → broke the always-LowLatency collapse (**4/4 known
  missions**; 0/4 telemetry-only — the open gap). Promoted 2026-06-19.
**Takeaway:** open-loop one-shot → closed-loop self-adapting; honest scorecard of
what shipped vs what's still open.

---

## 06 · The Knowledge Graph
**Purpose:** the KG as the system's coordination hub.
**Inside:**
- The KG (**29 nodes / 38 relationships**) split into a **strategic** zone (seeded,
  read-mostly) and a **runtime** zone (written by the inner loop).
- How the **slow planner** reads it (topology status → `allowed_relays`;
  `REALIZED_BY_P4_POLICY` → the candidate set that drives *both* the
  constrained-decoding grammar and the validator).
- How the **runtime** reads strategic bounds (`build_envelope`) and writes runtime
  state only (status/verdicts/snapshots), idempotent + provenance-tagged.
- How the **three model surfaces** relate to it — decision LLM is KG-grounded; the
  regen LLM reads the deployer's live `table_state`, **not** the KG; the loop is
  deterministic.
- Improvements (destructive import → idempotent seed; hardcoded status →
  monitor-computed) and a **novelty** section: a KG-coordinated, multi-model,
  **neuro-symbolic** design where the grammar *is* the KG candidate set.
**Takeaway:** the KG is the only shared mutable state, with a clean read/write
ownership contract — that contract is the architecture.

---

## 07 · Workflow & usage
**Purpose:** how to actually run and operate the system.
**Inside:**
- The **six-stage lifecycle** (seed KG → launch testbed → plan → episode → verify →
  feedback), with a stage/component/command table.
- The full **end-to-end run** with real, ground-truthed commands, and the **three
  ways to run an episode** (scenario / planner / soak) picked by intent.
- The **two models** (swap adapters, retrain, regen tools), the **testing
  workflow** (212 unit / 16 integration), the closed **feedback loop**,
  **operational gotchas**, and a **command cheat-sheet**.
**Takeaway:** frames the operational lifecycle and the *why*; cross-links
[`docs/guides/usage.md`](../guides/usage.md) as the canonical flag-level reference. The inner
loop runs with no LLM by default (Tier-2 stubbed → escalate).

---

## 08 · Novelty assessment & edge-network use cases
**Purpose:** answer "is any of this genuinely novel vs the state of the art?" — and
where the system could realistically apply.
**Inside:**
- An adversarial assessment of **10 candidate novelties** (prior-art research + a
  skeptic pass). Verdict: **none is individually novel** — 3 are established SOTA,
  7 are known patterns in a specific instantiation, **0** reached moderate/notable.
- The closest **prior art per claim** (GENRE/PICARD for the KG-derived grammar,
  Geng et al. for GBNF decoding, Reflexion for feedback, Symbiotic Agents for the
  two-loop, shielding for the safety wrap, P4SC/P4-SFC for the testbed).
- The **defensible residual** (a systems/integration + real-testbed contribution,
  framed soberly) and **recommended paper framing**.
- **Six edge-network use cases** (MEC slicing, tactical mesh, IIoT, V2X, LEO/NTN,
  security SFC/SASE) — creative but realistic, each mapped to the real mechanisms.
**Takeaway:** the doc-06 novelty language is *design rationale*, not a research
claim; sell the integration and the safety discipline, not any single technique.

---

## Master facts table (all verified)

| Fact | Value | Source |
|---|---|---|
| Unit tests / assertions | 212 / 454 | `pytest --co`, `grep assert` |
| Integration tests / assertions (node-gated) | 16 / 62 | `pytest --co tests/integration` |
| Assertions in original experiment scripts | 0 | `grep -c assert` |
| Working-tree duplication removed (Phase 0) | ~664 MB | `du -sh` of deleted trees |
| KG size | 29 nodes / 38 relationships | `drone_sfc_kg.json` |
| BMv2 fabric | 3 switches (s1/s2/s3, thrift 9090–9092) | `launch_network.py` |
| Decision model | Qwen2.5-1.5B-Instruct + LoRA, cuda:0 | `gpu-node.env` |
| Regen model | Qwen2.5-Coder-1.5B-Instruct, cuda:1 (pinned rev) | `gpu-node.env` |
| Retrain dataset / loss | 640 examples (160/SFC) / final loss 0.0066 | `train_decision_lora.py`, `planner-lora-retrain.md` |
| Retrain result | 4/4 known missions; 0/4 telemetry-only | `planner-lora-eval.md` |
| SLA bound recalibration | 20/50 ms → 45/60 ms | `generate_kg.py` |

---

## Current implementation status

| Item | Status |
|---|---|
| Phase 0 — repo hygiene | ✅ committed (`f9eab54`) |
| Phase 1 — typing & vocabularies | ✅ committed (`e5e9d8c`) |
| Phase 2 — performance | proposed ([04](04-production-code.md) P1/P3) |
| Phase 3 — scalability | proposed ([03](03-refactoring-strategy.md)) |
| Review documents (00–09) | committed |

*This digest is generated from the review documents it summarizes; if those
change, update the relevant section here.*

---

## Key Takeaways

- **Read this digest first, then jump to the document you need.** `09-summary.md` is a one-screen reading guide to the nine docs in `docs/architecture-review/`. Follow the routing it gives: new to the codebase → 01 → 06 → 05; triaging debt → 02 → 03 → 04; running or operating it → 07-workflow-and-usage; asking whether any of it is novel → 08-novelty; writing the paper or status report → 05 → 06 → 08, where the verified metrics and the honest framing live.

- **The single most important conclusion: don't rewrite the loop.** The `runtime/` control logic (a two-loop MAPE-K model: slow LLM planner → deterministic `RuntimeManager`) is genuinely well-engineered, with safety-by-construction (domination guard, hysteresis, fail-safe escalation). The debt was at the *edges* — a ~1.1 GB forked-duplicate archive (✅ removed in Phase 0), docstring-only contracts and magic-string vocabularies (✅ fixed in Phase 1), and a few still-open hot-path costs (Phase 2) — all fixable without changing behavior.

- **Implementation status: Phases 0 and 1 are committed; Phases 2 and 3 are still proposed.** Phase 0 (repo hygiene, removing ~664 MB of working-tree duplication) landed in commit `f9eab54`, and Phase 1 (typing & vocabularies — constants, margin helper, `_EPS`, Protocols) landed in `e5e9d8c`. Phase 2 (performance: parallel pings, regen warmup+timeout — the P1/P3 fixes in doc 04) and Phase 3 (scalability: KG-backed `last_good`, config split — doc 03) remain ready-to-apply but unapplied.

- **Every claim is verified and every applied change kept the tests green.** The baseline that anchors everything is `pytest tests/unit` → 212 passed in ~0.3 s (212 tests / 454 assertions), up from 0 assertions in the original human-read `.txt` scripts. Severities in doc 02 are post-adversarial-verification — calibrated, not alarmist — and even include an appendix of 3 *cleared* claims that turned out to be deliberate design rather than bugs.

- **The headline research results live in docs 05 and 06.** The LoRA retrain distilled the `fallback_decision` oracle into 640 balanced examples (r=16/α=32, 18.46 M params / 1.18%), hit final loss 0.0066, and broke the always-LowLatency collapse — 4/4 known missions but 0/4 telemetry-only (the open gap), promoted 2026-06-19. The KG is 29 nodes / 38 relationships and is the only shared mutable state, governed by a clean read/write ownership contract that the doc calls "the architecture."

- **This file is generated from the docs it summarizes — keep it in sync.** As its closing note states, the digest is derived from the nine review documents it covers (00–08), so if those change the relevant section here must be updated. The master facts table and the current-implementation-status table give you the verified numbers and commit hashes at a glance without re-reading the source docs.
