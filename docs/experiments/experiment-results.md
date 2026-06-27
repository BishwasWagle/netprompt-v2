# Experiment Results

Recorded results for the experiments in [experiment-design.md](experiment-design.md),
run under the controls in [experiment-validity.md](experiment-validity.md). All four
are now reported: **E2a** + **E1** (off-testbed), **E2b** (M5/M6 live foundation), and
**E3** (the full 3-arm × 4-scenario comparative on the live fabric).

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

*Reproduce:* see [Reproduce → E2b](#e2b--live-readiness-m5--m6-on-the-fabric).

---

## E3 — Whole system, 3 arms × 4 scenarios (live, topology-equivalent)

The full design-doc E3, **as-built** with the fault-injection harness
(`runtime/tools/e3_compare.py` + `e3_measure.py`; see
[experiment-design.md §E3 "As-built design"](experiment-design.md) for why this
replaced the published `run_comparative_experiments.sh`). All three arms run on the
*identical* 3-switch fabric (clean `low_latency` access, 5 Mbit/drone load); the only
differences are *which SFC is chosen* and *whether the runtime adapts*. After a healthy
baseline a relay-link fault is injected (the M6 mechanism). Bounds are calibrated +
satisfiable (**70 ms / 5 Mbps / 20 %** — latency is the gate, bw met at 15 Mbit offered).
These calibrated bounds (`e3_measure` `CAL_REQ`) **supersede the KG-seeded per-SFC latency**
(LowLatency = 45 / ReliableRelay = 60 ms in the metadata row) so the SLA is met on either
relay but blown by the +100 ms fault; only the **legal path/tier action space** is taken
from the KG per SFC (`build_envelope`: LowLatency = primary-only; ReliableRelay = primary +
backup).

**4 scenarios × 3 arms × 3 repeats = 36 cells, 36/36 succeeded.** Variance is ~0
(`±≤0.1 ms`) because the netem fault is deterministic — these are mean ± population sd
over 3 reps, not a jitter estimate.

| Scenario | Arm | SFC | Outcome | Tier | Final path | F1 RTT (ms) | SLA-met |
|---|---|---|---|---|---|---|---|
| `healthy` | static | LowLatency | met | — | primary | 30.1 ± 0.0 | 3/3 |
| `healthy` | rule | LowLatency | met | — | primary | 30.1 ± 0.0 | 3/3 |
| `healthy` | **proposed** | LowLatency | healthy | 0 | primary | 30.1 ± 0.0 | 3/3 |
| `primary_fault` | static | LowLatency | **violated** | — | primary | 120.2 ± 0.0 | 0/3 |
| `primary_fault` | rule | ReliableRelay | met | — | backup | 47.3 ± 0.1 | 3/3 |
| `primary_fault` | **proposed** | ReliableRelay | healthy | 0 | backup | 47.1 ± 0.0 | 3/3 |
| `backup_fault` | static | LowLatency | met *(dodged)* | — | primary | 30.1 ± 0.0 | 3/3 |
| `backup_fault` | rule | ReliableRelay | **violated** | — | backup | 132.1 ± 0.1 | 0/3 |
| `backup_fault` | **proposed** | ReliableRelay | **healthy** | **1** | **primary** | **35.0 ± 0.0** | **3/3** |
| `ddil` | static | LowLatency | violated | — | primary | 120.1 ± 0.1 | 0/3 |
| `ddil` | rule | ReliableRelay | violated | — | backup | 132.2 ± 0.0 | 0/3 |
| `ddil` | **proposed** | ReliableRelay | **escalated** | **2** | backup | 132.3 ± 0.1 | 0/3 |

**The thesis, demonstrated.** `proposed` is the **only arm in-SLA across both single-fault
locations**, and the only one that fails *correctly* when infeasible:

- **`primary_fault` isolates SFC selection.** `static` (LowLatency, primary) is stuck on
  the +100 ms primary → 120 ms, violated. `rule` and `proposed` both pick ReliableRelay
  (deploys backup) and dodge the fault for free (47 ms) — **no adaptation** (proposed at
  tier 0). This is the *planner's* value: choosing a more robust SFC.
- **`backup_fault` isolates runtime adaptation.** `rule` and `proposed` pick the **same
  SFC** (ReliableRelay, backup); the fault hits both. `rule` (no adapt) stays stuck on the
  degraded backup → 132 ms, violated. **`proposed` reroutes backup→primary (tier 1)** →
  35 ms, healthy. The *only* difference is adaptation. (`static` dodges by luck — its
  primary path wasn't hit.)
- **`ddil` is honest infeasibility.** Both relays +100 ms: every arm misses; `proposed`
  **escalates at tier 2** ("all tiers exhausted") with a ticket — the *correct* outcome,
  not a loss.

No single static choice is robust to both fault locations (static fails `primary_fault`;
rule fails `backup_fault`); only the adaptive arm is.

**Orchestration overhead.** `e3_measure` wall: static/rule ≈ **15.7 s** (deploy + 1 window);
proposed ≈ **24.9 s** even at **tier 0** (healthy/`primary_fault` — no adaptation), so the
bulk of the proposed overhead is the **episode's multiple observe windows** (attribution),
*not* the reroute. The reroute (`backup_fault`) adds ≈ **6 s** (→ 31.4 s) and only **1 of 4**
proposed scenarios actually reroutes; `ddil` (exhaust all tiers → escalate) ≈ 28.9 s. The
LLM SFC decision is a separate ≈ 20 s step (E1) — bounded, paid once per decision.

**Can claim (from this data):** end-to-end on the calibrated fabric, the proposed pipeline
keeps the target in-SLA across both relay-fault locations — via SFC choice where a static
pick fails, and via **tier-1 reroute** where a non-adaptive arm with the *same* SFC cannot
— and escalates honestly when infeasible. **Cannot claim:** real-world variance (netem is
deterministic → ~0 sd); goodput (bw is offered load); sub-10 % loss fidelity
(`ping_count=2`, loss read 0 % throughout); off-taxonomy generalization (E1: 0/4).

**Reproduce:** see [Reproduce → E3](#e3--whole-system-3-arms--4-scenarios-live) (driver
invocation, CSV/figure export, and the full preconditions).

---

## E4 — Tier-2 regen correction (deliberately-broken SFC corpus)

The premade SFC rule files are safe; E4 feeds the Tier-2 regen subsystem a corpus of
**broken** s1 forward-table scripts ([`regen_corpus/`](../../regen_corpus/), 16 items) and
measures the three nested correctness layers the M7 design separates (regen-llm.md): a rule
can be **grammar-valid**, **gate-safe** (no blackhole), and/or **recovery-capable** (restores
the route) — independently. Two item kinds:

- **reject** — a bad *candidate* the guardians must refuse: 8 **syntactic** (caught by
  `grammar.validate()`, the proposer's pre-filter) + 4 **runtime** (grammar-valid but the
  `ValidationGate` rejects at **L2**: blackhole / duplicate / dangling handle).
- **recover** — a faulty *installed* table (a drone mis-ported or dropped); the regen must
  emit a corrective `table_modify` that is grammar-valid, gate-accepted, and recovers.

Three arms — `gate` (deterministic safety, all reject items), `stub` (deterministic recovery
*machinery*, the synthesized correct fix), `real` (the live Qwen2.5-Coder-1.5B on `cuda:1`,
the model-capability frontier).

| Group | Arm | n | grammar-valid | gate-safe | recovers | note |
|---|---|---|---|---|---|---|
| reject · syntactic | gate | 8 | — | — | — | **8/8 refused** (by `grammar`) |
| reject · runtime | gate | 4 | — | — | — | **4/4 refused** (by gate **L2**) |
| recover | **stub** | 4 | 4/4 | 4/4 | **4/4** | validate→gate→recover machinery works (given the right line) |
| recover | **real** | 4 | 4/4 | 3/4 | **0/4** | safe, but never the exact corrective row |

**The honest finding (reproduces the M7 size-sweep).** The *safety* property is total: every
broken script is refused, and the 1.5B Coder stays grammar-valid (4/4) and almost always
blackhole-safe (3/4) — an unsafe rule never reaches the network. But **exact recovery is the
model frontier: 0/4.** The real model emits well-formed `table_modify` lines that target the
wrong handles (1–2) or echo the already-broken port, never the exact corrective row, so the
route is never restored; the `stub` arm proves the gap is the model, not the machinery (4/4
recovery given the right line — it exercises validate→gate→recover, not generation).

**Can claim:** the gate/grammar refuse 12/12 deliberately-broken scripts at the expected
layer; the recovery path is correct end-to-end (stub 4/4); a small Coder is safe-but-not-
recovery-capable (real 0/4), matching the M7 finding. **Cannot claim:** that the *model*
repairs faults (it doesn't, at 1.5B) — E4 measures that frontier, it doesn't close it.

**Reproduce:** see [Reproduce → E4](#e4--regen-correction-over-the-bad-sfc-corpus).

---

## Status

| Experiment | State |
|---|---|
| E2a (runtime behavioral gate) | ✅ 6/6 PASS |
| E1 (planner decision quality) | ✅ 4/4 set A · 0/4 B/C · 8/8 valid — reproduces the eval; VERIFY resolved |
| E2b (runtime on the live fabric) | ◑ foundation verified — M5/M6 6/6 live; full real-monitor soak still pending |
| E3 (end-to-end vs baselines) | ✅ 36/36 cells (3 arms × 4 scenarios × 3 reps) — proposed in-SLA across both fault locations (tier-1 reroute on `backup_fault`), honest escalate on `ddil` |
| E4 (regen correction) | ✅ safety total — 12/12 broken scripts refused, stub recovery 4/4; real Coder 4/4 grammar · 3/4 gate-safe · **0/4 recovery** (model frontier, per M7) |
| Validity readiness gate (validity §9) | ✅ complete (unit 212 · E2a 6/6 · E1 · M5/M6 6/6 live) |

---

## CSV exports (for result tables & figures)

The Markdown tables above are generated from CSVs under `docs/experiments/results/`, so
they can be re-plotted or pasted into a spreadsheet without re-deriving anything:

| File | Shape | Feeds |
|---|---|---|
| `e2a_gate.csv` | one row per fixture scenario — verdict/tier/reason vs the designed verdict, `pass` | the **E2a gate table** above |
| `e2b_integration.csv` | one row per node-gated test — `outcome` + `time_s` | the **E2b M5/M6 table** above |
| `e1_confusion.csv` | one row per probe — `expected_sfc` vs `selected_sfc`, parse status | the **E1 confusion matrix** above |
| `e3_summary.csv` | one row per scenario × arm — mean ± population-sd over repeats, SLA-met count | the **E3 table** above |
| `e3_cells.csv` | one row per scenario × arm × repeat (F1/F2 flattened) — raw | raw per-run inspection |
| `e3_flows.csv` | one row per flow per cell (tidy/long, `is_target` flag) | bar/scatter figures (pandas `groupby`) |
| `e4_corpus.csv` | one row per corpus item × arm — grammar/gate/recover or reject-layer, `pass` | the **E4 table** above |
| `e4_summary.csv` | one row per group × arm — caught/grammar/gate/recovery rates | the **E4 table** above |

`e3_compare` **auto-writes** the E3 CSVs next to its `--out` JSONL after every campaign
(best-effort — a CSV error never loses the JSONL). Each `repro/*.sh` also emits its
experiment's CSV. To (re)generate directly:

```bash
python3 -m runtime.tools.results_to_csv e2a --outdir docs/experiments/results   # runs the off-node gate
python3 -m runtime.tools.results_to_csv e2b --junit /tmp/e2b_junit.xml --outdir docs/experiments/results
python3 -m runtime.tools.results_to_csv e1  --indir /tmp --outdir docs/experiments/results
python3 -m runtime.tools.results_to_csv e3  --in /tmp/e3_full.jsonl --outdir docs/experiments/results
```

**Figures** (`docs/experiments/results/plots/`) are rendered from those CSVs by
`plot_results` (matplotlib, headless — `pip install matplotlib`):

```bash
python3 -m runtime.tools.plot_results --resultsdir docs/experiments/results
```

| Figure | Shows |
|---|---|
| `e3_rtt_by_scenario_arm.png` | F1 RTT (mean ± sd) per scenario × arm vs the 70 ms SLA line — proposed is the only arm under the bound across both fault locations |
| `e3_sla_met_by_scenario_arm.png` | SLA-met repeats (of 3) per scenario × arm |
| `e3_overhead_by_scenario_arm.png` | orchestration wall time per scenario × arm |
| `e1_accuracy_by_set.png` | planner accuracy by probe set (A known-taxonomy 4/4 vs B/C held-out 0/4) |

The committed CSVs are from the **2026-06-24** E3 run (36/36 cells) and the E1 probe
set; the summary reproduces the E3 table exactly (e.g. `backup_fault`/proposed
35.0 ms at tier 1, `ddil`/proposed escalate at tier 2).

---

## Reproduce

Each experiment is an executable script under [`repro/`](../../repro/) — run it directly instead
of copy-pasting. The scripts self-locate the repo root and source
`deploy/gpu-node/gpu-node.env`, so they work from any directory and resolve the venv python
themselves (override with `NETPROMPT_PY`); each (re)seeds the KG as needed.

| Experiment | Run | Needs |
|---|---|---|
| E2a | `repro/e2a.sh` | venv only |
| E1  | `repro/e1.sh` | venv · seeded Neo4j · GPU |
| E2b | `repro/e2b.sh` | testbed · passwordless `sudo` |
| E3  | `repro/e3.sh [repeats] [out.jsonl]` | testbed · `sudo` · GPU |
| E4  | `repro/e4.sh [arms]` | venv only (`gate,stub`); GPU for the `real` arm |

### E2a — runtime behavioral gate (off-node)

The 6 fixture scenarios through `run_episode` (no GPU/fabric); the same scenarios as
the unit assertions (canonical setups: `tests/unit/test_{evaluator,runtime_manager}.py`).
Runs the pytest gate, then emits `docs/experiments/results/e2a_gate.csv`.

```bash
repro/e2a.sh
```

### E1 — planner confusion matrix (8 probes)

Promoted adapter, constrained-on. Writes `/tmp/e1_<ID>.json` per probe (the IDs match
`results_to_csv`'s `E1_PROBES`) and emits `docs/experiments/results/e1_confusion.csv`.

```bash
repro/e1.sh
```

### E2b — live readiness: M5 + M6 on the fabric

Stands up a resident `low_latency` BMv2 fabric (3 switches, thrift 9090/9091/9092), waits
for it, runs the node-gated M5/M6 suites against it, then tears it down — the two-shell
flow automated in one process (fabric log in `/tmp/e2b_fabric.log`). Emits
`docs/experiments/results/e2b_integration.csv` from the run's JUnit output.

```bash
repro/e2b.sh
```

### E3 — whole system, 3 arms × 4 scenarios (live)

Runs the comparative campaign (each cell self-launches + tears down the fabric), then
writes the cells/flows/summary CSVs and renders the figures into `docs/experiments/results/`.
Args default to `3 /tmp/e3_full.jsonl`.

```bash
repro/e3.sh 3 /tmp/e3_full.jsonl
```

**Preconditions:** (1) passwordless `sudo` — the driver launches/measures/tears down BMv2 +
Mininet (the script checks `sudo -n` first); (2) a running, **freshly seeded** Neo4j on
`localhost:7687` (sourcing `gpu-node.env` exports `NETPROMPT_KG_URI=bolt://localhost:7687`,
reconciling `config.py`'s `controller-node` default with the driver's `localhost` default);
(3) the GPU with the promoted Qwen adapter on `cuda:0` for the proposed arm. The driver
re-seeds the KG per cell and runs the episode with `kg=None`, so no cell pollutes the
planner's history.

### E4 — regen correction over the bad-SFC corpus

Runs the broken-SFC corpus ([`regen_corpus/`](../../regen_corpus/)) through the regen
guardians + corrector and writes `e4_corpus.csv` + `e4_summary.csv`. Default arms `gate,stub`
are deterministic (no GPU); add `real` for the live Qwen2.5-Coder frontier (loads the Coder
on `cuda:1`). The `tests/unit/test_regen_corpus.py` guard keeps the corpus honest in CI.

```bash
repro/e4.sh                 # gate + stub (deterministic, committed)
repro/e4.sh gate,stub,real  # + the live model arm (needs the GPU; downloads the Coder once)
```
