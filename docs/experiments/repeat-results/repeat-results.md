# Repeat-KRONOS Results

Measured results for the **repeated** Kiran & Bishwas KRONOS experiments
([../repeat-kronos-experiments-plan.md](../repeat-kronos-experiments-plan.md)). Re-measured on
our system under the §0 controls — **we never import the draft's numbers**. Original E1–E4
results live in [../results/](../results/); this file covers the repeat campaign only.

## Run metadata

| Item | Value |
|---|---|
| Host | Linux, 2× Tesla P100-PCIE-16GB (`cuda:0`) |
| Planner | `Qwen2.5-1.5B-Instruct` + LoRA `final_adapter_retrained` (promoted), GBNF-constrained, `--no-4bit` |
| KG | Neo4j `bolt://localhost:7687`, re-seeded (`seed_kg`) before the run |

---

## Build #1 — Table VIII: control-plane timing (re-measured)

**Driver:** `repro/table8_timing.sh 6` → `runtime/tools/table8_to_csv.py`.
**Method:** the 4 **set-A** probes through the instrumented `orchestrate` with
`--repeat-decision 6` (decode 0 = cold incl. CUDA warmup; decodes 1–5 = warm steady-state),
plus one deterministic-fallback probe for the rule-based contrast. Per-stage `perf_counter`
brackets emit a timing sidecar per probe; the scorer aggregates to
[`table_viii_timing.csv`](table_viii_timing.csv) (per-probe) +
[`table_viii_summary.csv`](table_viii_summary.csv) + [`plots/table_viii_timing.png`](plots/table_viii_timing.png).

| Component | Ours (avg ± sd) | Draft Table VIII | Read |
|---|---|---|---|
| **SFC Selection (LLM, warm)** | **11.04 s ± 0.19** | 1.00 s | **11× the draft** — and cold≈warm, so it is genuine **constrained-decode cost on the P100**, not warmup |
| SFC Selection (LLM, cold first call) | 11.47 s ± 0.18 | — | first decode after load (CUDA warmup + constrained decode) |
| SFC Selection (rule-based fallback) | **11 µs** | "~µs" | the honest **LLM-s vs rule-µs** contrast — ~6 orders of magnitude |
| KG Reasoning (Cypher reads) | 29.9 ms ± 0.5 | 7.7–7.8 ms (warm) | same order; ours is cold/near-warm within a process |
| Compile + artifact check | 0.38 ms | — | decision → experiment config |
| Result Writeback (planner config file) | 0.58 ms | 0.54 s | **our planner write is sub-ms** — the draft's 0.54 s was a KG write (see gap) |
| History Build (CSV + input assembly) | 14.9 ms | — | not a draft component |
| Model Load (one-time) | 3.72 s ± 0.02 | — | amortized; **excluded** from steady-state SFC selection |
| KG Update (runtime-side) | **n/a (gap)** | 0.51 s | not in the planner path — see below |

**Findings.**
1. **SFC selection dominates the control plane at ~11 s, not ~1 s.** The cold (11.47 s) ≈ warm
   (11.04 s) gap is small, so this is the **steady-state cost of GBNF-constrained decoding** for a
   1.5B model on the P100 — *not* a warmup artifact. The draft's 1.00 s is **not reproducible**
   on our setup; we report our measured ~11 s. (Consistent with the E1 note of ≈20 s/probe *incl.*
   the 3.72 s model load + tokenizer/import overhead.)
2. **LLM ~s vs rule ~µs is the honest overhead story.** The deterministic fallback decides in
   ~11 µs; the LLM path is ~10⁶× slower. The overhead is **attributable** (it is the LLM), per the
   edits-doc guardrail — not hidden.
3. **KG reasoning is cheap (~30 ms), same order as the draft's 7.7 ms warm.** Defensible; the KG
   is not the bottleneck.
4. **Two draft components don't exist in our planner path.** The draft's "KG Update 0.51 s" and
   "Result Writeback 0.54 s" were **KG writes**; in our architecture the planner only *reads* the
   KG (sub-ms config-file write), and the closed-loop KG write (Verdict / BaselineSnapshot) lives
   in the **runtime** (`runtime/kg_client.py`, exercised in E2/E3), keyed by `correlation_id`. To
   complete the four-component table we must instrument the runtime write path separately — logged
   here as a **gap**, not fabricated.

**Can claim:** on our P100 + constrained-decoding setup, control-plane overhead is dominated by
LLM SFC selection (~11 s, paid once per decision, attributable), with KG reasoning at ~30 ms and a
~11 µs rule-based baseline. **Cannot claim:** the draft's 1.00 s SFC / 0.54 s writeback figures
(not reproduced / not in the planner path).

**Reproduce:** `repro/table8_timing.sh [repeats]` (needs venv · seeded Neo4j · GPU).

---

## Build #2 — Table IV: KG-driven decision provenance + KG query latency (re-measured)

**Driver:** `repro/table4_provenance.sh 5` → `runtime/tools/table4_to_csv.py`.
**Method:** the 4 **set-A** probes through the real LLM planner. Decision provenance
(selected SFC / path / policy / relay) is read from the `--output` config; per-query KG latency
(`sfc_query` = `get_candidate_sfc_policy_set`, `path_query` = the `PathDecision` query in
`get_topology_snapshot`) is captured warm (`--repeat-context 5`) from the `--save-timings`
sidecar. Aggregated to [`table_iv_decisions.csv`](table_iv_decisions.csv) +
[`plots/table_iv_kg_latency.png`](plots/table_iv_kg_latency.png).

| Mission (set A) | Selected SFC | Path | P4 Policy | Relay | SFC query (ms) | Path query (ms) |
|---|---|---|---|---|---|---|
| real_time_pest_detection | LowLatencyVideoSFC | primary | primary_path_low_latency | s2 | 2.56 | 2.54 |
| long_term_soil_monitoring | EnergyAwareSFC | primary | energy_policy_table_essential_only | s2 | 2.43 | 1.97 |
| emergency_alert_relay | ReliableRelaySFC | backup | backup_path_reliable_relay | s3 | 2.68 | 2.32 |
| bulk_data_transfer | BandwidthOptimizedSFC | primary | bandwidth_policy_table_bulk_marking | s2 | 2.24 | 2.09 |

All **4/4 correct vs the set-A oracle**, all `parsed_json`.

**Findings.**
1. **The KG query latencies reproduce the draft cleanly.** Ours: SFC query **2.24–2.68 ms**, path
   query **1.97–2.54 ms** — squarely inside the draft's Table IV ranges (SFC 1.20–2.64 ms, path
   1.82–3.20 ms). Unlike the SFC-selection cost (Build #1: 11 s vs the draft's 1 s), the **Cypher
   latencies are defensible and reproducible** — the KG is genuinely millisecond-scale.
2. **The decision provenance matches the draft's SFC/path/policy mappings** — LowLatency·primary·s2,
   Energy·primary·s2, Reliable·backup·s3 — but via the **mission name** (set A), which the planner
   gets right (4/4), *not* via telemetry/condition reasoning.
3. **Provenance guardrail honored.** The draft's Table IV condition rows
   (congestion/relay/DDIL → ReliableRelay) are the **telemetry case the planner fails 0/4** — they
   are **not** run here as LLM "KG reasoning." The `emergency_alert_relay`→ReliableRelay·backup row
   reproduces the *same SFC/path* the draft attributes to those conditions, but it is **name-driven
   (set A)**; a true condition→SFC mapping must be attributed to the deterministic rule oracle, not
   the LLM. So we adopt the table's *structure* and the (honest) query latencies, and re-scope the
   "emerged from KG reasoning" claim.

**Can claim:** on the seeded KG, the planner emits KG-grounded, valid set-A decisions
(SFC/path/policy/relay, 4/4) with warm Cypher query latency ~2–3 ms — reproducing the draft's
Table IV query-latency ranges. **Cannot claim:** that the condition→SFC rows emerged from LLM
telemetry reasoning (the planner is 0/4 off-taxonomy — those are rule-grounded).

**Reproduce:** `repro/table4_provenance.sh [repeat_context]` (needs venv · seeded Neo4j · GPU).

---

## Build #3 — §V.D(2): adversarial robustness of the set-A decision (re-measured)

**Driver:** `repro/vd2_robustness.sh` → `runtime/tools/e1_robustness.py`.
**Method:** load the planner once; per set-A mission, build the clean input, then apply 5
deterministic context perturbations and check whether `selected_sfc` stays stable. Perturbations
touch only the *evidence* (telemetry / topology / advisory / serialization) — never the action
space (`orchestration_constraints` + `candidate_sfc_policy_set`), so the correct answer is always
still selectable. Output: [`vd2_robustness.csv`](vd2_robustness.csv) (per mission×perturbation) +
[`vd2_summary.csv`](vd2_summary.csv) + [`plots/vd2_robustness.png`](plots/vd2_robustness.png).

| Perturbation | Kind | Decision-stability | Valid |
|---|---|---|---|
| misleading_advisory (recommends the wrong SFC) | evidence | **4/4** | 4/4 |
| stale_kg (active/standby swapped, relay marked down) | evidence | **4/4** | 4/4 |
| conflicting_telemetry (telemetry contradicts the name) | near-circular | **4/4** | 4/4 |
| noisy_topology (junk links, inflated counts, fake relays) | evidence | **4/4** | 4/4 |
| format_shift (stringified numbers, reordered keys) | guarantee | **4/4** | 4/4 |

**Result: 20/20 decisions stable, 20/20 valid** — reproduces the draft's "remained robust."

**Findings (and the honest caveat).**
1. **The decision is perfectly stable under all 5 perturbations.** Even `misleading_advisory` and
   `stale_kg`, which actively recommend/imply the *wrong* path/SFC, do not flip the choice — and
   `noisy_topology` doesn't either.
2. **But this is robustness *by insensitivity*, not by reasoning.** The planner keys on the mission
   *name* and largely ignores telemetry/topology/advisory context (the same trait behind 0/4
   off-taxonomy, [E1](../results/experiment-results.md)). It correctly ignores *misleading* context
   here — but it would equally ignore *corrective* context. So "robust" is the favorable face of the
   §3 limitation, not independent evidence of reasoning.
3. **Two probes are low-information by design** (as the plan flagged): `format_shift` validity is a
   **constrained-decoding guarantee** (every output is a valid 6-key decision regardless), and
   `conflicting_telemetry` is **near-circular** (the planner ignores telemetry, so it trivially
   "survives"). Reported as such, not as wins.

**Can claim:** the set-A decision is stable and validator-compliant under misleading advisory,
stale KG, conflicting telemetry, noisy topology, and format shift (20/20) — it is not derailed by
noisy context. **Cannot claim:** that this reflects robust *reasoning* — it reflects name-keying /
context-insensitivity; format-validity is a decoding guarantee, and the telemetry probe is
near-circular.

**Reproduce:** `repro/vd2_robustness.sh` (needs venv · seeded Neo4j · GPU).

---

## Build #4 — §V.D(3): counterfactual sensitivity, planner vs oracle (re-measured)

**Driver:** `repro/vd3_counterfactual.sh` → `runtime/tools/e1_counterfactual.py`.
**Method:** vary one signal at a time, hold the rest fixed, and contrast the planner's
`selected_sfc` with the deterministic oracle (`validator.fallback_decision`) on the same input.
Telemetry sweeps fix the mission to `bulk_data_transfer` (no oracle name-trigger) so telemetry
alone drives the oracle. Output: [`vd3_counterfactual.csv`](vd3_counterfactual.csv) +
[`vd3_summary.csv`](vd3_summary.csv) + [`plots/vd3_counterfactual.png`](plots/vd3_counterfactual.png).

| Sweep | Planner distinct SFCs | Oracle distinct SFCs | Planner tracks it? | Oracle agreement |
|---|---|---|---|---|
| **mission_name** (set A) | **4** | 4 | **yes** | **4/4** |
| delay_ms (3→100) | 1 (flat: Bandwidth) | 2 | **no** | 3/5 |
| loss_percent (0→10) | 1 (flat: Bandwidth) | 2 | **no** | 1/5 |
| battery_percent (90→10) | 1 (flat: Bandwidth) | 2 | **no** | 2/5 |

Overall planner-oracle agreement **10/19**.

**Findings.**
1. **"Changes appropriately" holds only for the mission-name counterfactual.** Varying the name
   across set A, the planner produces 4 distinct, correct SFCs and **agrees with the oracle 4/4** —
   genuine sensitivity to the mission objective.
2. **The planner is flat to *telemetry* counterfactuals.** Holding the name fixed and sweeping
   delay / loss / battery, the planner never changes (always BandwidthOptimized), while the oracle
   responds (LowLatency at delay≤10; Reliable at loss≥3; Energy at battery<40). This is the
   §3 limitation expressed as a counterfactual: the planner does **not** track telemetry.
3. **So the draft's "SFC changed appropriately when … telemetry … was modified" does not hold as a
   generalization claim** — it holds for the *name*, not the numbers. Reported here strictly as
   per-episode **oracle-agreement**, never as planner generalization (edits-doc §6.2). *(Minor: the
   oracle treats `loss=0` as missing via its `or 999` idiom → Reliable, which is why loss-sweep
   agreement is 1/5; an oracle quirk, not a planner effect.)*

**Can claim:** the planner is counterfactually sensitive to the mission objective (4/4 distinct,
oracle-agreeing) and counterfactually **insensitive** to telemetry (flat where the oracle moves) —
a clean, honest characterization. **Cannot claim:** telemetry-driven counterfactual reasoning /
generalization (the draft's framing) — that is contradicted here, as in E1.

**Reproduce:** `repro/vd3_counterfactual.sh` (needs venv · seeded Neo4j · GPU).

---

## Build #6 — Fig. 6: SFC decision confusion matrix (re-measured)

**Driver:** `repro/fig6_confusion.sh` → `runtime/tools/fig6_confusion.py`; probe set
`repro/fig6_probes.json` (28 probes, 7/class: 1 set-A known name + ~3 set-B novel + ~3 set-C
generic, generated + validated by the `fig6-probe-generation` workflow). Output:
[`fig6_confusion.csv`](fig6_confusion.csv) + [`fig6_summary.csv`](fig6_summary.csv) +
[`fig6_probes.csv`](fig6_probes.csv) + [`plots/fig6_confusion.png`](plots/fig6_confusion.png).

**Set-A (known taxonomy) — the Fig-6 analog: a perfect 4×4 identity (4/4, 100% diagonal).**

Full held-out set (row-normalized %, true × predicted):

| true \ predicted | LowLat | Reliable | Energy | Bandwidth |
|---|---|---|---|---|
| LowLatencyVideo | 14.3 | 0 | 0 | **85.7** |
| ReliableRelay | 0 | 14.3 | 0 | **85.7** |
| EnergyAware | 0 | 0 | 14.3 | **85.7** |
| BandwidthOptimized | 0 | 0 | 0 | **100.0** |

Per-set accuracy: **set-A 4/4 (100%)**, set-B/C **6/24 (25%)**, full **10/28 (35.7%)**.

**Findings.**
1. **On the known taxonomy the matrix is a clean diagonal (100%).** This is the defensible reading
   of Fig. 6 — *known-taxonomy (set-A) decision accuracy* (settled in [E1](../results/experiment-results.md)
   + §0). Our set-A is 100% (deterministic greedy + constrained), at least as strong as the draft's
   88.9–100% — but on the *known names*, not generalization.
2. **The full held-out set collapses to BandwidthOptimized.** Every off-taxonomy (set-B/C) probe —
   regardless of true class — is predicted BandwidthOptimized (the off-taxonomy default,
   [planner-lora-eval](../planner/planner-lora-eval.md)). So the 3 non-Bandwidth classes score 1/7
   each (only their set-A name), and Bandwidth scores 7/7 only because the default *is* Bandwidth.
3. **The draft's "errors concentrated among semantically similar SFCs" is NOT reproduced.** Our
   errors are not semantically distributed — they pile into **one column (the mode)**. The model
   fails by **mode-collapse off the known names**, not by graceful semantic confusion. This is a
   sharper, more honest characterization than the draft's.

**Can claim:** 100% set-A decision accuracy presented as a confusion matrix (the Fig-6 analog,
known-taxonomy). **Cannot claim:** the draft's generalization framing or "semantically similar"
error structure — off the known names the planner mode-collapses to BandwidthOptimized.

**Reproduce:** `repro/fig6_confusion.sh` (needs venv · seeded Neo4j · GPU).
