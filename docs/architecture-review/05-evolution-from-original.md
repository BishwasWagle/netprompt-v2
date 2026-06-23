# 5 · Evolution from the Original NetPrompt — What Changed and Why

This document traces the system from **Bishwas & Kiran's original NetPrompt
Milestone-II** implementation to the current system, across the five pieces the
original was built from — the **slow planner**, the **Knowledge Graph**, the
**selector**, the **test environment** — plus the **architecture/workflow** shift,
the **two models**, and the **LoRA retraining** done to fix decision quality.

Every claim below is grounded in a file in this repo. Metrics were
adversarially verified against the source (24/24 confirmed); where the original
and current differ, both are cited.

> **Provenance.** The original `llm_orchestrator` package, the Neo4j KG-RAG
> context, the fine-tuned `Qwen2.5-1.5B + LoRA` decision model, the validator +
> deterministic fallback, the policy compiler, and the
> `llm_generated_experiment_config.json` artifact are **Bishwas & Kiran's**
> work (`docs/planner-design.md:11-15`). This session's contributions are the
> four planner extensions (§3), the whole `runtime/` MAPE-K inner loop, the
> KG read/write split, the test suite, and the retrain (§7).

---

## 0 · The shift in one picture

```
ORIGINAL (Milestone-II): open-loop, one-shot
  mission ─▶ pick SFC ─▶ deploy once on a torn-down-per-run net ─▶ parse results ─▶ push to KG ─▶ EXIT
            (later: LLM writes llm_generated_experiment_config.json … and STOPS — triggers no deploy)

CURRENT: two-loop, closed
  mission ─▶ SLOW PLANNER (LLM, KG-RAG, constrained) ─▶ DeploymentSpec ─▶ RUNTIME MANAGER (MAPE-K) ─▶ live P4/BMv2
     ▲                                                                          │
     └──────────── analytics: per-SFC reliability ◀── Verdict / EscalationTicket (KG) ◀┘
```

| Dimension | Original (Bishwas & Kiran) | Current |
|---|---|---|
| Control | Open-loop, one-shot decide→deploy→parse→exit | Two-loop MAPE-K with rollback/escalation |
| Planner output | Config artifact, then **stops** | Artifact → normalized `DeploymentSpec` → live episode |
| Selector | Hand-coded Cypher + if/elif ladder | Fine-tuned LLM, 6-key constrained decision |
| Adaptation | None (parse & record) | Tier 0 tune → 1 reroute → 2 LLM-regen, domination-guarded |
| KG | Mixed strategic+operational; status hardcoded | Read-strategic / write-runtime; status monitor-computed |
| Tests | 0 assertions; human-read `.txt` files | 212 unit (454 asserts) + 16 node integration (62 asserts) |
| Feedback | Outcomes pushed to KG, never reused | Closed analytics loop folds verdicts into planner context |

---

## 1 · Architecture & workflow

**Original** — a single-pass, open-loop pipeline: pick an SFC for a scenario,
deploy it once on a BMv2 network that is torn down per run, parse results, push
them to Neo4j, exit. No running control loop, no rollback, no attribution, no
in-envelope adaptation. The later LLM orchestrator terminated by **writing a
config artifact and stopping** — it never triggered or supervised a deployment
(`runtime-manager-design.md`; `planner_adapter.py:1-12` documents the seam).

**Current** — a two-loop control architecture around a KG+Library hub:
- a **slow LLM planner** outer loop (decide *which* SFC) feeding
- a **deterministic MAPE-K `RuntimeManager`** inner loop
  (deploy → monitor → attribute → adapt → commit/rollback/escalate) over a
  **persistent** BMv2 fabric, with a tiered in-envelope adaptation engine
  (tier 0 tune → 1 reroute → 2 LLM regen) guarded by domination + a shared
  per-episode budget.

The two loops are bridged by a **typed contract** — `DeploymentSpec` in,
`EscalationTicket` + `Verdict` out (`docs/runtime-planner-contracts.md:34-76`) —
and a **closed analytics loop** folds runtime verdicts back into the planner's
decision context (`orchestrate.py:55-56`, `analytics.py`).

*Verified live behavior:* an end-to-end planner-artifact episode (D11) produced a
real `Verdict(outcome=escalated, tier_reached=2)` + `EscalationTicket`; a
multi-handoff soak (D14) ran 6 rounds → 6/6 verdicts written
(`docs/runtime-planner-contracts.md:204-236`).

---

## 2 · The slow planner

**Original pipeline** (`llm_orchestrator/`, unchanged in shape):
`build_runtime_input_object` reads the Neo4j topology snapshot + candidate
SFC/policy set + a results-CSV history (KG-RAG, top-k=3 by a hand-weighted
similarity), assembles an input object with `orchestration_constraints`, then
`run_pipeline` prompts the fine-tuned model for a **6-key JSON decision**
(`selected_sfc/policy/path/relay`, `priority_class`, `deployment_mode`),
validates it, **falls back to a deterministic rule oracle** if invalid, and
compiles the artifact (`orchestrate.py`, `validator.py`, `policy_compiler.py`).

**Original limitation:** the 1.5B model emitted only **4 of the 6 keys** then
rambled past the JSON, so the validator rejected it and the deterministic
fallback took over — the LLM was *routinely bypassed*
(`decision_grammar.py:3-7`, `planner-design.md:186`).

The four extensions (this work, `planner-design.md:168-209`):

| # | Extension | Outcome |
|---|-----------|---------|
| 3.1 | **KG topology seed** — add `ProgrammableSwitch` roles + `P4PolicyMapping` so the planner resolves `allowed_relays`/candidates locally | Made it runnable on the consolidated node |
| 3.2 | **Grammar-constrained decoding** — per-request GBNF derived from the *same* `orchestration_constraints` the validator checks | **KEPT** — forces a complete, valid 6-key decision; "grammar-valid ⇒ validator-valid by construction" |
| 3.3 | **Prompt / few-shot** — a mission→SFC rubric + exemplars | **NEGATIVE, reverted** — a 1.5B model isn't promptable out of mode-collapse |
| 3.4 | **LoRA retrain** — distill the oracle into fresh weights | **PARTIAL WIN** — see §7 |

§3.2 is subtle and important: constrained decoding makes the LLM output *always
valid*, so the fallback now fires **only when constrained decoding is off**. The
decision therefore rests on the *adapter* — which is exactly why the retrain (§7)
mattered. New file `decision_grammar.py` + 4 unit tests
(`tests/unit/test_decision_grammar.py`).

---

## 3 · The Knowledge Graph

**Original** — one static Neo4j graph for the testbed, generated offline into
`drone_sfc_kg.json` (`controller/generate_kg.py`, **29 nodes / 38 relationships**:
10 `Drone`, 5 `AgriculturalField`, 4 `SFCTemplate`, 4 `P4PolicyMapping`, 3
`ProgrammableSwitch`, 3 cluster nodes) and bulk-imported with a **destructive**
load (`controller/import_kg.py:22` — `MATCH (n) DETACH DELETE n`). It modeled the
strategic world **and** carried live operational state (switch status, path
decisions) in the *same* graph; a hand-driven script
(`experiments/update_topology_state.py`) wrote switch status from a **hardcoded
`SCENARIO_STATE` table** keyed by scenario name.

**Current** — same strategic graph, now under a strict **read/write contract**
(`runtime/kg_client.py:1-12`):
- **Reads** only strategic state: `SFCTemplate` + `AgriculturalField` bounds.
- **Writes** only runtime state the MAPE-K loop computes: monitor-measured switch
  status (new node types `Verdict`, `EscalationTicket`, `BaselineSnapshot`,
  `LastKnownGood` — `kg_client.py:131-174`).
- **Status is no longer asserted** from a scenario dict — it is *computed* by the
  monitor from live observation (the hardcoded→circular `SCENARIO_STATE` is gone).
- The seed is now **idempotent** `MERGE` that *preserves* runtime records
  (`runtime/tools/seed_kg.py`), not a `DETACH DELETE` wipe.
- `build_envelope` fuses **strictest of SFCTemplate AND field requirements** with
  a runtime-owned action space (`kg_client.py:58-87`) — a read-time fusion the
  original never did.
- **SLA bounds recalibrated to the measured fabric**: field/SFC latency
  `20 ms / 50 ms → 45 ms / 60 ms` (the originals were below the ~40 ms primary /
  ~52 ms backup floor, so every episode escalated; `generate_kg.py:48-53`).

---

## 4 · The selector

**Original** — a hand-coded, deterministic chooser (no LLM), in two forms:
- `controller/select_template.py` — a single Cypher query with a **hardcoded**
  `latency_requirement = 20` returning the lowest-latency `SFCTemplate`,
  `LIMIT 1` (SFC id only).
- `controller/run_selected_sfc.py:56-66` — a hardcoded **if/elif ladder** over a
  field's latency/bandwidth/priority mapping to one of four templates.
- Plus published **baselines** for comparison: `baseline_static` (always emits
  `LowLatencyVideoSFC`) and `baseline_rule_based`.

**Current** — a fine-tuned **LLM planner** (`Qwen2.5-1.5B-Instruct + LoRA`) emits
one **atomic 6-key decision** from full mission + telemetry + KG topology +
KG-RAG history + runtime feedback. The old rule ladder survives only as
(a) the deterministic `validator.fallback_decision` oracle, and (b) the **label
source distilled into the retrained LoRA** (§7). The original static/rule
selectors are kept as **formal baselines** contrasted against the proposed
KG-driven NetPrompt.

> **Honest tradeoff:** the LLM's selection is *far* slower —
> ~**1.0 s** (proposed, `comparative_results/`) vs ~**3 µs** for the rule-based
> baseline. The LLM buys mission-/context-sensitivity and KG grounding at a
> ~10⁵× latency cost; acceptable because selection is the *slow* loop (per
> mission/re-plan, not per control cycle).

---

## 5 · The test environment

**Original** — standalone Mininet experiment scripts driven by bash auto-runners:
build a network, install rules, run iperf/ping, **dump raw output to `.txt`**, tear
the network down. **No automated pass/fail** — an operator read the result files.
`grep -c assert` over all 11 experiment files returns **0**. Rule installs were
fire-and-forget (no output check). The original used a **single OVS switch**
(`sfc_experiment.py`) or single P4 switch.

**Current** — two tiers:
- **Off-testbed pytest unit suite: 212 tests / 454 assertions** across 19 files,
  built on deterministic test doubles (`FakeDeployer`/`FakeMonitor`/
  `ScriptedRunner`) + **6 scenario fixtures** (`runtime/fixtures.py`), so engine
  and evaluator logic is tested without `sudo` Mininet.
- **Node-gated integration suite: 16 tests / 62 assertions** across 6 files (all
  `skipif(not _testbed_up())`) that drive a **resident 3-switch BMv2 fabric**
  (`launch_network.py`, s1=9090/s2=9091/s3=9092) and assert on real switch state
  + live traffic.
- Rule installation is now **verified** — the loader fails loudly if any
  `table_add` was rejected (vs the original's unchecked `subprocess`).

| Metric | Original | Current |
|---|---|---|
| Unit tests / assertions | 0 / 0 | 212 / 454 |
| Integration tests / assertions | 0 / 0 | 16 / 62 (node-gated) |
| Network model | ephemeral single-switch | resident 3-switch BMv2/P4 |
| Pass/fail | human reads `.txt` | executable assertions |

---

## 6 · The two models

| | **Decision model** (planner) | **Tier-2 regen model** (runtime) |
|---|---|---|
| Base | `Qwen2.5-1.5B-Instruct` | `Qwen2.5-Coder-1.5B-Instruct` |
| Adapter | LoRA (`final_adapter` → `final_adapter_retrained`) | none (base, pinned revision) |
| Device | `cuda:0`, FP16, 4-bit **off** on P100 | `cuda:1` (no contention with the planner) |
| Decoding | greedy + GBNF grammar-constrained (default-on) | greedy + GBNF, default-**stubbed** (`StubLLMClient`) |
| Role | *select which SFC/policy/path* | *regenerate P4 rules* when tier 0/1 fail |
| Pinned for repro | adapter path via `NETPROMPT_LLM_ADAPTER` | `NETPROMPT_REGEN_REVISION=2e1fd397…` |

The decision model is **not** the Runtime Manager; the regen model is a component
the deterministic RM *may call* at tier 2 (`docs/components/README.md:40`,
`gpu-node.env:17,32-35`).

---

## 7 · The LoRA retraining (the centerpiece)

### 7.1 Why
The original planner LoRA (`final_adapter`) **mode-collapsed to
`LowLatencyVideoSFC` for every mission** (`planner-lora-eval.md:44-57`). The two
cheaper fixes were exhausted: constrained decoding (§3.2) fixed *format* but not
*choice*; prompt/few-shot (§3.3) didn't move the choice (the base model behaves
identically). Decision *quality* needed **new weights**.

### 7.2 The oracle — where labels come from
The deterministic `validator.fallback_decision` already encodes the correct
mission/telemetry → SFC policy. The retrain **distills it** into the LoRA, so
every training target is already validator-valid and grammar-valid by
construction (`train_decision_lora.py:31,88`):

| Condition (first match wins) | SFC · path · mode |
|---|---|
| emergency mission / loss ≥ 3 / unavailable relay | ReliableRelaySFC · backup · multihop |
| soil mission / battery < 40 | EnergyAwareSFC · primary · single_switch |
| pest mission / delay ≤ 10 | LowLatencyVideoSFC · primary · multihop |
| bandwidth ≥ 30 | BandwidthOptimizedSFC · primary · single_switch |
| else | ReliableRelaySFC · backup · multihop |

### 7.3 What exactly was done (verified against `train_decision_lora.py` + configs)
- **Dataset — generate-then-bin, balanced:** for a target SFC, sample telemetry
  that lands on its oracle branch (`_telemetry_for`), use a **named mission 50% /
  generic 50%** (forces telemetry use, not just name memorization), label with
  `fallback_decision`, bin by returned SFC, keep an equal count per SFC. Final:
  **640 examples, 160/SFC**, `--seed 0` (`train_decision_lora.py:77-99`).
- **Loss on the decision only:** prompt tokens masked to `-100`; EOS appended so
  the model learns to *stop* at the closing brace (the original rambled because
  it never learned to stop) (`train_decision_lora.py:102-107`).
- **Model + fresh LoRA (drop-in compatible):** base `Qwen2.5-1.5B-Instruct`;
  `r=16, lora_alpha=32, lora_dropout=0.05, bias=none, CAUSAL_LM`, 7 target modules
  (`q,k,v,o,gate,up,down_proj`) → **18,464,768 trainable (1.18% of 1.56B)**. Both
  `final_adapter` and `final_adapter_retrained` share these hyperparameters
  (verified in both `adapter_config.json`), so the new adapter is a one-flag swap.
- **Training:** manual loop, **batch 1 + grad-accum 8**, AdamW **lr 2e-4**,
  grad-clip 1.0, **3 epochs**, `max_len 2600`, **fp32** (P100 is sm_60: no bf16,
  fp16 Adam underflows; weights are dtype-agnostic so inference still runs FP16).
  ~1.8 s/example → **~52 min** on one Tesla P100-16GB
  (`train_decision_lora.py:141-178`, `planner-lora-retrain.md:88-104,215`).
- **Final mean loss `0.0066`** (loss fell `0.33 → 0.17 → 0.12` within the first
  240 examples).

### 7.4 How it improved — the result
Evaluated with constrained decoding (the production setting), so every output is a
valid 6-key decision; the test is purely *which* SFC
(`planner-lora-eval.md:42-58`):

| Probe set | Original | Retrained |
|---|---|---|
| **(A)** known mission names | 1 mode (always LowLatency) | **4/4 correct** |
| **(B)** novel mission names | always LowLatency | 0/4 (→ BandwidthOptimized) |
| **(C)** generic names, telemetry-only | always LowLatency | 0/4 (→ BandwidthOptimized) |

- **What worked:** the retrain **broke the always-LowLatency collapse** — across
  the four known mission types the model now picks correctly
  (emergency→Reliable, bulk→Bandwidth, pest→LowLatency, soil→Energy). A
  categorical improvement over "1 of 4 SFC types ever reachable."
- **What didn't:** it learned **mission-name → SFC** more than **telemetry
  reasoning** — for novel/generic missions where the answer depends on numbers it
  defaults to BandwidthOptimized. Likely cause: telemetry digits sit deep in a
  ~2400-token prompt a 1.5B model under-attends to, and name is the easier signal.

### 7.5 Why it was promoted (the subtle, correct reason)
Under the **production default (constrained decoding ON)** the grammar makes the
LLM output *always valid*, so the fallback is **bypassed** and the LLM's choice
ships. That means the *original* adapter ships its mode-collapsed LowLatency for
every non-LowLatency mission, while the **retrained adapter is the only one
correct under constrained-on**. **Promoted 2026-06-19**:
`gpu-node.env` sets `NETPROMPT_LLM_ADAPTER → final_adapter_retrained`; rollback is
a one-line env change (original preserved + tracked)
(`planner-lora-eval.md:73-108`).

### 7.6 The closed learning loop
`build_runtime_input_object` now folds a per-SFC reliability signal — aggregated
from the runtime's `Verdict`/`EscalationTicket` history — back into every decision
as `input_object.runtime_feedback` (`orchestrate.py:55-56`, `analytics.py`).
*Honest caveat:* the feedback **path** is wired and robust, but the current 1.5B
model doesn't yet *exploit* the signal (it wasn't trained on the field), and
escalation-rate conflates "wrong SFC" with "unachievable SLA on this fabric" — so
today it is advisory. Truly acting on it needs a feedback-aware retrain or a
stronger model.

---

## 8 · Honest scorecard

| Improvement | Status |
|---|---|
| Open-loop → two-loop MAPE-K with rollback/escalation | ✅ shipped, live-verified (D11/D14) |
| Hand-coded selector → constrained LLM planner | ✅ shipped |
| Mode-collapsed planner → mission-sensitive on known taxonomy | ✅ 4/4 known (retrain) |
| Telemetry-only / novel-mission generalization | ⚠️ open (0/4 — needs telemetry-weighted retrain or larger model) |
| KG: hardcoded status → monitor-computed; read/write split | ✅ shipped |
| Tests: 0 assertions → 212 unit + 16 integration | ✅ shipped |
| Closed analytics feedback loop | ◑ path wired; model doesn't yet exploit it |
| LLM selection latency (~1 s vs ~3 µs rule-based) | ⚠️ inherent cost of the slow loop |

**Net:** the system went from a one-shot, open-loop, human-graded pipeline with a
mode-collapsed selector to a closed-loop, self-adapting, test-covered control
system with a planner that is correct on the known mission taxonomy. The two
honest remaining gaps are telemetry generalization in the 1.5B decision model and
a feedback-aware retrain to *act* on the (already-wired) analytics loop.

---

## Key Takeaways

- **The core shift is open-loop one-shot to closed-loop MAPE-K.** The original (Bishwas & Kiran's Milestone-II) decided one SFC, deployed once on a per-run torn-down BMv2 net, parsed results, pushed to the KG, and exited — and its later LLM orchestrator literally just wrote `llm_generated_experiment_config.json` and stopped without deploying. The current system is a two-loop architecture: a slow LLM planner feeds a deterministic MAPE-K `RuntimeManager` (deploy → monitor → attribute → adapt → commit/rollback/escalate) over a persistent fabric, bridged by a typed `DeploymentSpec`-in / `Verdict`+`EscalationTicket`-out contract.

- **The selector went from a hand-coded if/elif ladder to a constrained LLM.** The original chooser was pure Cypher with a hardcoded `latency_requirement = 20` plus an if/elif ladder over four templates (`run_selected_sfc.py:56-66`). The current planner is a fine-tuned `Qwen2.5-1.5B + LoRA` emitting one atomic 6-key decision; the old ladder survives only as the deterministic `validator.fallback_decision` oracle and as the label source distilled into the retrained LoRA. This buys mission/context-sensitivity at a roughly 10⁵× latency cost (~1.0 s vs ~3 µs), acceptable only because selection is the slow loop.

- **Grammar-constrained decoding is what made the LLM's choice load-bearing.** Extension §3.2 derives a per-request GBNF from the same `orchestration_constraints` the validator checks, so "grammar-valid ⇒ validator-valid by construction." Crucially, with constrained decoding on (the production default) the deterministic fallback is bypassed entirely — so the LLM's choice actually ships, which is exactly why the retrain mattered.

- **Test rigor jumped from zero to 212 + 16 tests.** The original had standalone Mininet scripts dumping raw output to `.txt` for a human to read — `grep -c assert` over all 11 experiment files returns 0. The current suite is 212 unit tests / 454 assertions (on fake deployers/monitors, no sudo) plus 16 node-gated integration tests / 62 assertions driving a resident 3-switch BMv2 fabric (s1=9090/s2=9091/s3=9092), and rule installs now fail loudly if any `table_add` is rejected.

- **The LoRA retrain broke mode-collapse but only halfway.** The original adapter collapsed to `LowLatencyVideoSFC` for every mission, so it was retrained by distilling the oracle into fresh weights: 640 examples (160/SFC, 50% named / 50% generic missions), loss masked to the decision only, 3 epochs in ~52 min on a Tesla P100, reaching a final mean loss of 0.0066. The result was a partial win — 4/4 correct on known mission names (emergency→Reliable, bulk→Bandwidth, pest→LowLatency, soil→Energy), but 0/4 on novel and telemetry-only probes, where it defaults to BandwidthOptimized.

- **The retrain was promoted precisely because the fallback no longer fires.** Since constrained-on bypasses the deterministic oracle, the original adapter would ship its collapsed LowLatency for every non-LowLatency mission, making the retrained adapter the only one correct in production. It was promoted 2026-06-19 via `gpu-node.env` (`NETPROMPT_LLM_ADAPTER → final_adapter_retrained`), with rollback being a one-line env change since the original is preserved.

- **The honest open gaps are telemetry generalization and an unexploited feedback loop.** The 1.5B model learned mission-name→SFC more than telemetry reasoning (digits sit deep in a ~2400-token prompt it under-attends), so number-dependent missions still need a telemetry-weighted retrain or a larger model. The closed analytics loop folds per-SFC reliability from `Verdict`/`EscalationTicket` history back into `runtime_feedback`, but the path is only wired (◑) — the model wasn't trained on the field and escalation-rate conflates "wrong SFC" with "unachievable SLA," so today it is merely advisory.

---

*Sources: `docs/planner-design.md`, `docs/planner-lora-retrain.md`,
`docs/planner-lora-eval.md`, `docs/runtime-planner-contracts.md`,
`docs/future-work.md`, `controller/{generate_kg,import_kg,select_template,run_selected_sfc}.py`,
`network/milestone-II-latest/netprompt-milestone-II/{llm_orchestrator,train_decision_lora.py}`,
`runtime/{kg_client,planner_adapter,fixtures,fakes}.py`, `deploy/gpu-node/gpu-node.env`.
All metrics adversarially verified (24/24 confirmed) against these files.*
