# Experiment Results

Recorded results for the experiments in [experiment-design.md](experiment-design.md),
run under the controls in [experiment-validity.md](experiment-validity.md). Only the
off-testbed experiments (**E2a**, **E1**) are reported here; **E2b / E3** require the
live BMv2 fabric and are pending.

## Run metadata (2026-06-24)

| Item | Value |
|---|---|
| Host | Linux `6.8.0-111-generic`, 2× Tesla P100-PCIE-16GB (`cuda:0` used) |
| Libraries | torch 2.3.1+cu121 · transformers 4.46.3 · peft 0.13.2 · transformers-cfg 0.2.7 · neo4j 5.27.0 |
| Planner model | `Qwen2.5-1.5B-Instruct` + LoRA `final_adapter_retrained` (promoted) |
| Base-model revision | HF snapshot `989aa7980e…` — **unpinned in code** (validity §5); recorded here as the resolved snapshot actually used |
| Adapter provenance | git-tracked (`adapter_config` blob `5621db782905`) |
| Decoding | greedy + GBNF constrained (`NETPROMPT_LLM_CONSTRAINED=1`, default), `--no-4bit`, `--device-map cuda:0` |
| KG | Neo4j `bolt://localhost:7687`, re-seeded clean + calibrated: LowLatency=45 / ReliableRelay=60 ms; fields 45/60; s1·s2 active, s3 standby; 4 SFC→P4Policy edges |
| E2a runtime | off-node `FakeDeployer` + scenario `FakeMonitor` (no GPU, no fabric) |

---

## E2a — Runtime Manager behavioral gate (off-node)

Each of the 6 fixture scenarios driven through the full `RuntimeManager.run_episode`
with the canonical per-scenario setup (mirroring `tests/unit/test_{evaluator,runtime_manager}.py`).
**Result: 6/6 reach their designed verdict — PASS (the hard gate).**

| Scenario | Verdict | Tier | Reason | Expected | ✓ |
|---|---|---|---|---|---|
| `healthy` | healthy | 0 | — | commit (healthy) | ✓ |
| `causal_regression` | rollback | 0 | — | rollback to last-good | ✓ |
| `path_quality_fault` | healthy | 1 | — | commit after reroute | ✓ |
| `contention_harm_with_knob` | marginal | 0 | — | harm-free commit | ✓ |
| `contention_harm_no_knob` | escalated | 2 | no harm-free config | escalate | ✓ |
| `ddil` | escalated | 2 | all tiers exhausted | escalate | ✓ |

**Reproduce:** the off-node harness in the §"Reproduce" block, or `pytest tests/unit/test_evaluator.py tests/unit/test_runtime_manager.py -q` (the same scenarios as assertions).

---

## E1 — Slow Planner decision quality (confusion matrix)

8 probes (sets A/B/C from [planner-lora-eval.md](../planner/planner-lora-eval.md))
through the production `llm_orchestrator.orchestrate` CLI, promoted adapter,
constrained-on. Expected = the deterministic oracle (`validator.fallback_decision`).

| ID | Mission (set) | Expected (oracle) | Selected | parse | ✓ |
|---|---|---|---|---|---|
| A1 | `emergency_alert_relay` (A) | ReliableRelaySFC | ReliableRelaySFC | parsed_json | ✓ |
| A2 | `bulk_data_transfer` (A) | BandwidthOptimizedSFC | BandwidthOptimizedSFC | parsed_json | ✓ |
| A3 | `real_time_pest_detection` (A) | LowLatencyVideoSFC | LowLatencyVideoSFC | parsed_json | ✓ |
| A4 | `long_term_soil_monitoring` (A) | EnergyAwareSFC | EnergyAwareSFC | parsed_json | ✓ |
| B1 | `real_time_video` (B) | LowLatencyVideoSFC | BandwidthOptimizedSFC | parsed_json | ✗ |
| B2 | `soil_moisture_survey` (B) | EnergyAwareSFC | BandwidthOptimizedSFC | parsed_json | ✗ |
| C1 | `routine_field_patrol` (C) | LowLatencyVideoSFC | BandwidthOptimizedSFC | parsed_json | ✗ |
| C2 | `routine_field_patrol` (C) | EnergyAwareSFC | BandwidthOptimizedSFC | parsed_json | ✗ |

**Score:** set A **4/4**; sets B+C **0/4** (all default to BandwidthOptimized);
constrained-valid rate **8/8 (100%)**; decision latency ≈ 20 s/probe (incl. model load).

**This reproduces `planner-lora-eval.md` exactly with the promoted adapter.**

### VERIFY resolved (the KRONOS Fig-6 provenance question)
The promoted `final_adapter_retrained` is *itself* **4/4 on the known taxonomy and
0/4 on telemetry-only** — reproduced live. So the draft's **Fig-6 (88.9–100%) is
consistent with set-A / known-taxonomy accuracy of this same adapter**; no
different/condition-trained model need be posited. Combined with the *forced*
argument (the model was trained on telemetry — `train_decision_lora.py:85-86` — yet
fails it), **Fig-6 must be labelled "known-taxonomy accuracy," never "telemetry
generalization."** → [kronos-draft-experiment-edits.md §0](kronos-draft-experiment-edits.md)
is now **RESOLVED**.

**Honest limit:** this reproduced the *direction* (4/4 set A, 0/4 B/C) with **one
mission per class**, not the full held-out distribution behind the exact 88.9–100%
percentages — enough to settle provenance/direction, not to re-derive those figures.

---

## E2b (foundation) — live readiness: M5 + M6 integration on the fabric

A resident `low_latency` BMv2 fabric (`launch_network.py`, 3 switches on thrift
9090/9091/9092) was stood up and the node-gated integration suites run with real
traffic. **6/6 PASSED in 243 s (~4 min)** — then the fabric was torn down (`mn -c`).

| Test | Confirms |
|---|---|
| M5 `congest_primary_reports_degraded` | monitor measures a degraded primary correctly on real traffic |
| M5 `squeeze_neighbour_populates_harm_list` | displaced-harm detection on measured flows |
| M5 `kill_s2_reports_failed_and_stays_sound` | single-relay kill → `Failed` but system stays sound (no false fault) |
| M6 `relay_fault_reroutes_and_commits_live` | full loop: exogenous fault → **reroute → commit** on live switches |
| M6 `ddil_both_paths_degraded_escalates_live` | infeasible → **honest escalate** on real hardware |
| M6 `contention_harm_tunes_target_down_and_commits_live` | harm → **tune-down → commit** with real `tc` actuation |

**Significance.** This closes the two things E2a (off-node) could not: **measurement
correctness on real traffic** (validity §2) and that the runtime's **behavioral
outcomes reproduce on real hardware** (E2b success criterion) — the live M6 verdicts
mirror the E2a model-mode results (reroute-commit / escalate / tune-commit). With
E2a + E1 + this, the **validity readiness gate (validity §9) is complete**; only the
short real-monitor soak and E3 remain before collecting comparative data.

*Reproduce:* launch `low_latency` fabric → `sudo -E env NETPROMPT_TREE_ROOT=$NETPROMPT_ROOT
… pytest tests/integration/test_m5_monitor_node.py tests/integration/test_m6_acceptance_node.py -v`
→ `sudo mn -c`.

---

## Status

| Experiment | State |
|---|---|
| E2a (runtime behavioral gate) | ✅ 6/6 PASS |
| E1 (planner decision quality) | ✅ 4/4 set A · 0/4 B/C · 8/8 valid — reproduces the eval; VERIFY resolved |
| E2b (runtime on the live fabric) | ◑ foundation verified — M5/M6 6/6 live; full real-monitor soak still pending |
| E3 (end-to-end vs baselines) | ⏳ pending — needs the fabric + the 3-arm comparative harness |
| Validity readiness gate (validity §9) | ✅ complete (unit 212 · E2a 6/6 · E1 · M5/M6 6/6 live) |

---

## Reproduce

```bash
cd ~/Run-time-Manager && source deploy/gpu-node/gpu-node.env
python -m runtime.tools.seed_kg                       # re-seed clean + calibrated KG

# E2a — off-node behavioral gate (no GPU/fabric): the 6 scenarios through run_episode
#   (canonical setups: see tests/unit/test_{evaluator,runtime_manager}.py)
pytest tests/unit/test_evaluator.py tests/unit/test_runtime_manager.py -q

# E1 — planner confusion matrix (8 probes, promoted adapter, constrained-on)
cd "$NETPROMPT_ROOT"
for probe in "emergency_alert_relay 20 25 2 80" "bulk_data_transfer 80 40 1 90" \
             "real_time_pest_detection 40 6 1 80" "long_term_soil_monitoring 15 50 1 25" \
             "real_time_video 40 10 1 80" "soil_moisture_survey 15 50 1 25" \
             "routine_field_patrol 20 5 1 80" "routine_field_patrol 15 50 1 20"; do
  set -- $probe
  python -m llm_orchestrator.orchestrate --mission "$1" --bandwidth "$2" --delay "$3" \
    --loss "$4" --battery "$5" --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
    --device-map cuda:0 --no-4bit --output /tmp/e1.json
  python3 -c "import json;d=json.load(open('/tmp/e1.json'));x=d.get('decision',d);print('$1',x['selected_sfc'],x['llm_parse_status'])"
done
```
