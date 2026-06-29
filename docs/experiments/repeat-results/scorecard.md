# Scorecard — KRONOS draft vs. our re-measured results

A single consolidated view of the Kiran & Bishwas KRONOS / NetPrompt v2 draft claims against what
**we re-measured on our system** (per the [repeat plan](../repeat-kronos-experiments-plan.md), under
the §0 controls, never importing the draft's numbers). Per-experiment detail is in
[repeat-results.md](repeat-results.md); the original E1–E4 results are in [../results/](../results/).

**Verdict legend.** ✅ **Reproduces** — our measurement matches the draft's (within hardware).
⚠️ **Re-scoped** — the experiment reproduces *structurally*, but the draft's *claim/framing* is
corrected. ❌ **Not reproduced** — our measurement contradicts the draft's value/claim.
◑ **Pending** · ⏭ **Skip candidate**.

---

## 1. The scorecard

| Experiment (draft) | Draft claim / value | Our re-measured result | Verdict | Where |
|---|---|---|---|---|
| **Table IV** — KG query latency | SFC 1.20–2.64 ms, path 1.82–3.20 ms | SFC **2.2–2.7 ms**, path **2.0–2.5 ms** | ✅ Reproduces | build #2 |
| **Table IV** — decision provenance | scenario → SFC/path/policy | set-A **4/4** (SFC/path/policy/relay) | ⚠️ Re-scoped (name-driven, not telemetry) | build #2 |
| **Table V** — KG scalability | 4.37 → 7.80 ms to 200 drones | — | ⏭ Skip candidate (needs synthetic-KG tooling) | build #7 |
| **Table VI** — RTT / loss / tput | topology-equiv, 3 arms × scenarios | E3 **36/36**; proposed in-SLA both faults | ⚠️ Re-scoped (RTT headline; tput=offered load; coarse loss) | E3 (prior) |
| **Table VII** — NoKG ablation | NoKG loss 2.0 / 6.67 % | backup_fault: proposed **35 ms healthy**; nokg/rule **132 ms** | ✅ Reproduces (re-scoped: RTT not loss %) | build #5 |
| **Table VIII** — SFC selection | ~**1.00 s** | **11.04 s ± 0.19** | ❌ Not reproduced (P100 + constrained decode) | build #1 |
| **Table VIII** — KG reasoning | 7.70–7.80 ms (warm) | **~30 ms** (cold/near-warm) | ✅ Reproduces (order) | build #1 |
| **Table VIII** — KG update / writeback | 0.51 s / 0.54 s | planner write **sub-ms**; KG write is runtime-side | ⚠️ Re-scoped (architecture gap) | build #1 |
| **Table VIII** — rule-based contrast | "~µs" | **~11 µs** | ✅ Reproduces | build #1 |
| **Fig. 6** — accuracy (known taxonomy) | 88.9–100 % per class | set-A **100 %** (perfect diagonal) | ✅ Reproduces (set-A) | build #6 |
| **Fig. 6** — error structure | errors "semantically similar" | **mode-collapse to BandwidthOptimized** | ❌ Not reproduced (collapse, not semantic) | build #6 |
| **§V.D(2)** — adversarial robustness | "remained robust" | **20/20** stable & valid | ⚠️ Re-scoped (robust *by insensitivity*) | build #3 |
| **§V.D(3)** — counterfactual | SFC "changed appropriately" | name **yes** (4/4); telemetry **no** (flat) | ⚠️ Re-scoped (name, not telemetry generalization) | build #4 |
| **Abstract** — historical-path selection | claimed | **no result in the paper**; not wired to the decision | ◑ Build-or-drop | build #8 |
| **Abstract** — packet loss "below 4 %" | < 4 % | KRONOS battery row is **10 %** (self-contradiction) | ❌ Not reproduced / drop threshold | build #5 / E3 |
| **Prose** — throughput "10.47 Mbps despite migrating" | goodput framing | access-link **offered load**, not goodput | ⚠️ Re-scoped | E3 |
| **Prose** — closed loop "supports learning" | loop improves decisions | loop **functions** (writes/reads), does not improve | ⚠️ Re-scoped | (validity §8) |
| **Prose** — DDIL maintained-SLA | a KRONOS win | infeasible → **honest escalation** | ⚠️ Re-scoped | E3 |

---

## 2. The through-line

The split is consistent across every build: **the KG / P4 / constrained-decoding *machinery*
reproduces; the LLM's claimed *reasoning and generalization* does not.**

**✅ What holds (the substrate is solid):**
- **KG / Cypher latency** — Table IV query latency reproduces (2–3 ms); Table VIII KG-reasoning is
  the same order (~ms); the KG is genuinely not the bottleneck.
- **RTT comparison** — the E3 topology-equivalent result stands (proposed is the only arm in-SLA
  across both relay-fault locations).
- **Known-taxonomy decision accuracy** — Fig. 6 set-A is a perfect diagonal; provenance is 4/4.
- **Decision validity / format** — §V.D(2) 20/20 valid; constrained decoding guarantees it.
- **Honest overhead contrast** — LLM ~s vs rule ~µs is real and attributable.

**❌ What does not hold (the LLM-reasoning claims):**
- **SFC-selection latency** — ~11 s on the P100, not ~1 s.
- **Telemetry reasoning / generalization** — §V.D(3) flat to telemetry; Fig. 6 off-taxonomy
  collapses to BandwidthOptimized; the planner keys on the mission *name*.
- **"Semantically similar" error structure** — our errors mode-collapse into one column, not graceful
  semantic confusion.
- **"Robust reasoning"** — §V.D(2) stability is *insensitivity to context*, the favourable face of
  the same limitation.

**⚠️ What must be re-scoped (measurement semantics):**
- Throughput is **offered load**, not goodput; sub-10 % loss is below resolution; the abstract's
  "below 4 %" is contradicted by KRONOS's own 10 % battery row.
- The draft's "KG Update / Result Writeback" seconds are **runtime-side KG writes**, not the
  planner path (our planner write is sub-ms).
- The closed loop **functions** but does not **improve** decisions; DDIL success = honest escalation.

**Net:** after re-measuring, the defensible KRONOS story is **KG-grounded, always-valid SFC/path
decisions on the known taxonomy, with millisecond KG reasoning and a real P4/BMv2 adaptation loop**
— *not* telemetry-reasoning generalization, goodput guarantees, or sub-resolution loss. The
honest contributions survive; the overreaching claims are re-scoped or dropped.

---

## 3. Remaining work

| Build | Experiment | Status | Needs |
|---|---|---|---|
| #7 | Table V — KG scalability | skip candidate (low value) | synthetic-KG generator |
| #8 | Historical-path A/B | build-or-drop (design ready) | Design A is planner-side; Design B needs 3 edits |

All other experiments (Tables IV, VI, VII, VIII, Fig. 6, §V.D(2)/(3)) are done — builds #1–#6 + E3.

---

*See also: [repeat-results.md](repeat-results.md) (per-build detail),
[../repeat-kronos-experiments-plan.md](../repeat-kronos-experiments-plan.md) (the plan + build list),
[../kronos-draft-experiment-edits.md](../kronos-draft-experiment-edits.md) (the adopt/reframe/cut
determination), [../results/experiment-results.md](../results/experiment-results.md) (original E1–E4).*
