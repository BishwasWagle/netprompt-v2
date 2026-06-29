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
