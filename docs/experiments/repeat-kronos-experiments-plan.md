# Plan — Repeat Kiran & Bishwas's KRONOS Experiments

**Subject.** A repeat plan organized around the **original KRONOS / NetPrompt v2 draft**
experiment battery (Kiran & Bishwas — `NetPrompt_V2.pdf`, §IV–V): Tables IV–VIII, the Fig. 6
confusion matrix, and the §V.D adversarial + counterfactual probes. For *each* of their
experiments this document records **whether our system can repeat it**, **how** (exact tool /
command, or precisely what small tool must be written), the **reframe guardrail** under which the
result stays defensible, and the **current status** (already run / to-build).

**How this differs from the other docs.** [experiment-design.md](experiment-design.md) is
organized around *our* E1–E4 structure and folds the draft into it; this document inverts that
view — it starts from *their* experiment list and asks, one by one, "can we repeat it, and what
do we skip?" The basis for every framing call is
[kronos-draft-experiment-edits.md](kronos-draft-experiment-edits.md) (the adopt / reframe / cut /
verify determination). Code-feasibility verdicts below were ground-truthed against the live
source and adversarially verified (file:line cited where load-bearing); the draft's experiment
inventory, table values, and framings were cross-checked against `NetPrompt_V2.pdf`
(`docs/design/NetPrompt_V2.pdf`) directly.

**Sources.** [kronos-draft-experiment-edits.md](kronos-draft-experiment-edits.md) ·
[experiment-design.md](experiment-design.md) ·
[experiment-validity.md](experiment-validity.md) ·
[results/experiment-results.md](results/experiment-results.md) (what we have already run) ·
[planner-lora-eval.md](../planner/planner-lora-eval.md) (the set A/B/C decision accuracy).

> **Path note.** The planner code (`orchestrate`, `kg_context`, `validator`, `prompt_builder`,
> `decision_grammar`, `llm_runner`) lives under
> `$NETPROMPT_ROOT/llm_orchestrator/` where
> `NETPROMPT_ROOT=network/milestone-II-latest/netprompt-milestone-II` (see
> `deploy/gpu-node/gpu-node.env`). `repro/e1.sh` `cd`s there before invoking
> `python -m llm_orchestrator.orchestrate`. `controller/` and `runtime/tools/` are at the repo
> root. Code citations below use this convention.

---

## 1. Bottom line

**There is no Kiran-&-Bishwas experiment our system genuinely *cannot* perform.** Grounding the
eight *reported* experiments (Tables IV–VIII, Fig. 6, §V.D adversarial + counterfactual) against
the code, every one is either **already run**, **runnable as-is**, or **runnable after a *small*
tooling addition** — an instrumentation patch, a synthetic-graph generator, an extra harness arm,
or a perturbation/sweep wrapper. *None* is blocked by missing hardware, a missing capability, or
data that cannot be produced. (A ninth, "historical-performance-aware path selection," is promised
in the abstract/intro but **has no result in the paper itself** — see §3.)

So the honest "skip" list is **not** a list of impossible experiments. It is two things:

1. **One experiment to de-prioritize on cost/value grounds** — **Table V** (KG-scalability
   sweep). It needs net-new synthetic-KG tooling and yields only *Neo4j Cypher query latency on
   a small synthetic graph* — "drop if effort > value" per [experiment-design.md §6.1](experiment-design.md).
2. **A set of *claims*, not experiments, the system cannot substantiate** — telemetry-reasoning
   generalization, throughput-as-goodput, sub-10 % loss fidelity, and "the loop *learns*." We
   **reframe** these per the edits doc rather than run them as the draft framed them (§4).
3. **One experiment the *paper itself never reported*** — the "historical-performance-aware
   path-selection" experiment promised in the abstract/intro/§IV has **no result in §V**. We have
   no result for it either, so it is **build-or-drop**, not "repeat" (§3, last entry).

### Verdict at a glance

| # | Their experiment (draft) | Can we repeat? | Status | Maps to |
|---|---|---|---|---|
| Table IV | KG-driven SFC/path/policy per scenario + KG query latency | **Build small tool** | ✅ **done (build #2)** — set-A 4/4; SFC query 2.2–2.7 ms, path 2.0–2.5 ms (**reproduces draft**) | E1 / E3 |
| Table V | KG-reasoning scalability, 10→200 synthetic drones | **Build small tool** *(de-prioritize)* | Not started | E1 adjunct |
| Table VI | Topology-equivalent RTT/loss/throughput, 3 arms × scenarios | **Yes, as-is** | ✅ done (E3, 36/36) | E3 |
| Table VII | KG ablation (full vs NoKG) | **Build small tool** | Not run | E3 ablation |
| Table VIII | Control-plane timing breakdown | **Build small tool** | ✅ **done (build #1)** — SFC sel **11.04 s** (not 1 s), KG ~30 ms, rule ~11 µs; KG-update gap | E1 / E3 |
| Fig. 6 / §V.D(1) | Confusion matrix, 4 SFC classes | **Yes** (set-A); build tool for exact % | ✅ done (direction); exact % to add | E1 |
| §V.D(2) | Adversarial robustness (5 perturbations) | **Build small tool** | Not started | E1 sub-study |
| §V.D(3) | Counterfactual sensitivity | **Build small tool** | Not started | E1 |
| Abstract/§IV | Historical-performance-aware path selection | **Build, or drop the claim** | Claimed in paper, **no result** | E3 / closed loop |

---

## 2. Feasibility legend

- **Yes, as-is** — tooling exists and the experiment has been (or can immediately be) run with a
  documented command.
- **Build small tool** — conceptually runnable on the current stack; a *specific, small* tool
  must be written first (named per entry). No new hardware, capability, or unproducible data.
- **Cannot perform** — a hard blocker (missing hardware / capability / data). **No experiment
  falls here.**

Two cross-cutting rules from the edits doc apply to every entry: **adopt the *structure*, never
import the draft's *values*** (re-measure ourselves), and **report set A / B / C separately —
never a single blended accuracy**.

---

## 3. The repeat plan, experiment by experiment

### Table IV — KG-driven SFC / path / policy decision per scenario  ·  *Build small tool*

- **They measured:** per-scenario `selected_sfc` + relay path + P4 policy, plus two Cypher
  KG-query-latency columns (SFC-template query, path/relay query). Headline: baseline/low-lat →
  LowLatencyVideo·primary; battery → EnergyAware·primary; congestion/relay/DDIL →
  ReliableRelay·backup; SFC-query 1.20–2.64 ms, path-query 1.82–3.20 ms.
- **Decision half — already done.** `orchestrate` already emits the full decision dict
  (`selected_sfc` / `selected_policy` / `selected_path` / `selected_relay`); E1 ran it
  2026-06-24 (set A 4/4, B/C 0/4, 8/8 valid — [results §E1](results/experiment-results.md)).
- **What to build (small):** (1) the two **latency columns** don't exist — wrap the SFC query
  (`get_candidate_sfc_policy_set`) and the path branch of `get_topology_snapshot` in
  `llm_orchestrator/kg_context.py` with `time.perf_counter()` and surface them into the
  `orchestrate` result dict; (2) extend `runtime/tools/results_to_csv.py` (e1 writer) to emit
  `selected_path, selected_policy, sfc_query_ms, path_query_ms` (the CSV currently keeps only
  `selected_sfc` + parse status). ~6-line patch + 4 CSV columns.
- **Reframe (edits doc §4):** keep the query-latency columns but label them **"Neo4j Cypher
  query latency,"** never "reasoning latency." The condition rows (congestion/relay/DDIL) are the
  telemetry-driven case the planner **fails 0/4** — attribute them to the **rule/deterministic
  oracle** ("rule-grounded selection"), not "emerged from KG reasoning." Pin + report adapter id,
  base-model revision, and `NETPROMPT_LLM_CONSTRAINED`.

### Table V — KG-reasoning scalability, 10→200 synthetic drones  ·  *Build small tool (de-prioritize)*

- **They measured:** Cypher latency vs strategic-graph size (10/50/100/200 drones, 20 runs, warm
  cache), broken into **four sub-measurements** — SFC query, path query, drone-context retrieval,
  and KG writeback (the PDF table shows SFC/Path/Context/Total columns, with Total = sum of the
  three reads). Headline: total 4.37 → 7.80 ms, < 8 ms at every size.
- **Why it needs tooling:** `controller/generate_kg.py` **hardcodes 10 drones** (the
  `range(1, 11)` loops at lines 25/134/141/208; fields fixed at line 44) — there is no size
  parameter, and no latency-sweep harness exists. The query layer itself works (`kg_context`
  queries over a live Neo4j via `KGClient`).
- **What to build (two small scripts):** (1) a **synthetic-KG generator** parameterized by drone
  count — either add `--drones N` to `generate_kg.py` (generalize the four hardcoded ranges) or a
  new `runtime/tools/gen_synthetic_kg.py` emitting `drone_sfc_kg`-style JSON; (2) a
  **latency-sweep harness** (`runtime/tools/kg_scale.py`) that for each N ∈ {10,50,100,200} seeds
  via `python -m runtime.tools.seed_kg --source <N.json>`, warms the cache, then times 20 runs
  with `perf_counter` over **all four sub-measurements** (SFC query / path query / context
  retrieval / writeback) and prints mean ± sd per size. Needs a live Neo4j only.
- **Reframe / cost call:** label contexts **synthetic**, rename to **"KG query latency"**, attach
  **no SLA/perf claim**. This is the **lowest-priority** adjunct (it measures Neo4j query time on
  a tiny graph, not "reasoning"). Per [design §6.1](experiment-design.md), **drop if effort >
  value** — this is the one experiment reasonable to **skip** for the next round.

### Table VI — Topology-equivalent RTT / loss / throughput, 3 arms × scenarios  ·  *Yes, as-is (done)*

- **They measured:** RTT / loss / throughput for KRONOS / Rule-Based / Static SFC across **6
  scenarios** (Baseline, Low Latency, Battery Depletion, Congestion, Relay Failure, DDIL) on a
  topology-equivalent fabric. Headline (flagged invalid by the edits doc): ~10.47 Mbps throughput
  "despite migrating traffic"; KRONOS loss lower than baselines under congestion/relay/DDIL
  (2.67 / 1.67 / 3.33 % vs Static's 6.67 / 6.67 / 10.0 %).
  - **PDF cross-check — two anomalies the plan must not import as wins:** (i) **0 % loss is *not*
    "everywhere"** in Table VI (that is Table VII). KRONOS shows real loss — and the **Battery
    Depletion row is KRONOS 4.75 Mbps / 10.00 % loss, *worse* than both baselines** (3.33 %); the
    EnergyAwareSFC throughput throttle is presented as intentional, but the 10 % loss also
    **contradicts the abstract's "packet-loss below 4 %."** (ii) The clean uniform values (10.50
    across all arms at baseline; KRONOS exactly 10.47 across three scenarios) read as
    single-run/synthesized — re-measure with repeats + std-dev or mark illustrative.
- **Already done.** Our E3 (`runtime/tools/e3_compare.py` + `e3_measure.py`) realizes this:
  **36/36 cells** (3 arms × 4 scenarios × 3 reps), all on the identical clean `low_latency`
  3-switch fabric (the topology-equivalent control). `proposed` is the only arm in-SLA across
  both relay-fault locations (tier-1 reroute on `backup_fault`), honest escalate on `ddil`
  ([results §E3](results/experiment-results.md)).
- **Repeat command:** `repro/e3.sh 3 /tmp/e3_full.jsonl` (testbed + `sudo` + GPU).
- **Scenario gap (intended, not a reduction):** we run **4 relay-fault scenarios**, not their 6
  netem ones, because the netem scenarios degrade the *access* links (a reroute can't fix them)
  and don't differentiate runtime adaptation — see [design §E3 "As-built design"](experiment-design.md).
  The 4 (`healthy` / `primary_fault` / `backup_fault` / `ddil`) deliberately isolate SFC
  selection vs runtime adaptation vs infeasibility.
- **Reframe (edits doc §4/§5):** throughput → **"offered load,"** not goodput, not a headline;
  loss → **coarse bounds** (ours read 0 % at `ping_count=2`, below resolution — raise to ≥ 20 for
  any sub-10 % figure); **RTT is the honest headline dimension**; report N + std-dev (ours is
  ~0 sd because netem is deterministic — state that). DDIL = honest escalation, not a win.

### Table VII — KG ablation (full vs NoKG)  ·  *Build small tool*

- **They measured:** resilience of full KRONOS vs a "NoKG" arm (NoKG saw loss under relay-failure
  / DDIL where full did not; quoted 2.0 % / 6.67 %).
- **PDF cross-check — what the paper's NoKG actually was:** §V.C defines NoKG as removing KG
  storage, graph traversal, historical observations, topology-aware path reasoning, **and** KG-RAG
  validation, with decisions "derived from predefined scenario-specific mappings and static
  configuration lookups." That is functionally **our `rule` arm** (and the paper even calls its
  rule baseline a "Rule-Based No-KG framework"). So our **existing `rule` arm already approximates
  the paper's NoKG** — the cleaner contribution is to *also* add a candidate-set-only ablation
  (below) and report both, rather than re-deriving the rule arm under a new name.
- **Why it needs tooling:** the E3 harness has **no NoKG arm** (arms hardcoded to
  static/rule/proposed in `e3_compare.py` / `e3_measure.py`). The KG-vs-fallback switch lives in
  the orchestrator (`use_fallback_candidates` is already plumbed at `orchestrate.py`), not the
  harness.
- **What to build (small):** add a `nokg` arm to `e3_compare.py` + `e3_measure.py` whose SFC comes
  from the orchestrator in **fallback mode** (`use_fallback_candidates=True`) or against an
  unseeded Neo4j, then deploy through the same M6 fault path.
- **Critical guardrail — define "NoKG" operationally first.** An empty/unseeded KG is **not a
  clean single-factor ablation**: it drops the candidate set **and** topology grounding **and**
  changes the GBNF grammar's candidate source at once. The **clean single-factor arm** is
  **candidate-set-only** (`use_fallback_candidates=True`, topology preserved). State exactly what
  NoKG disables. Report loss **qualitatively** ("NoKG saw loss where full did not"), not 2.0 % /
  6.67 % (below resolution at `ping_count=2`). Optional add-on per [design §E3](experiment-design.md).

### Table VIII — Control-plane timing breakdown  ·  *Build small tool*

- **They measured (verified against the PDF Table VIII — *four* components, not three):**
  **SFC Selection 1.00 s** (± 0.002), **KG Update 0.51 s** (± 0.002), **Result Writeback 0.54 s**
  (± 0.004), and **KG Reasoning (warm cache) 7.70–7.80 ms**. Note: the **0.51 s is *KG Update***
  (writing decision/state back to the graph), **not** KG reasoning — *KG reasoning is the
  millisecond-scale Cypher read*. The paper's own takeaway: graph traversal is ms-scale; the
  ~1 s+ cost is the LLM + closed-loop writeback, not the KG.
- **Why it needs tooling:** every stage runs as one **uninstrumented** pipeline. The only timing
  today is aggregate wall-time in `e3_measure.py` (covers deploy+baseline+fault+episode, not the
  planner stages); `orchestrate`/`llm_runner`/`kg_context` have **no `perf_counter`**.
- **What to build (small) — four brackets to match the four rows:** (1) around `generate_raw` →
  `sfc_selection_s`; (2) around the KG **read** queries in `build_runtime_input_object`
  (`get_topology_snapshot` + `get_candidate_sfc_policy_set`) → `kg_reasoning_ms`; (3) around the
  KG **write-back** path (the closed-loop `Verdict`/snapshot writes keyed by `correlation_id`) →
  `kg_update_s`; (4) around `compile_llm_decision...` + the `--output` file write →
  `result_writeback_s`. Emit all four into the result dict. Run E1 probes with a **warm model** so
  SFC selection excludes the one-time model-load (the ~20 s/probe in results includes load).
  Aggregate mean ± sd.
- **Reframe (edits doc §4):** name SFC selection as **LLM inference (~1 s) vs rule-based ~µs** —
  overhead attributable, not hidden. Keep the **KG-reasoning = ms-scale** point explicit (it is
  the paper's strongest honest claim here) and report which regime was measured (warm ~7.7 ms vs
  cold). *(Honesty note: the original authors' instrumented runner emitted these columns into
  legacy `comparative_results_clean*.csv`; our patch re-derives them live.)*

### Fig. 6 / §V.D(1) — Confusion matrix, 4 SFC classes  ·  *Yes (set-A); build small tool for exact %*

- **They measured:** per-class decision accuracy over the 4 SFC classes on a same-distribution
  held-out set. Headline: LowLatency 99.5 % · ReliableRelay 92.6 % · EnergyAware 100 % ·
  Bandwidth 88.9 %.
- **Direction — already done.** `repro/e1.sh` reproduces it as **set-A accuracy**: 4/4 set A, 0/4
  B/C, 8/8 constrained-valid — and this **settles the Fig-6 provenance VERIFY**: Fig-6 is
  **known-taxonomy (set-A) accuracy, not telemetry generalization** ([results §E1, "VERIFY
  resolved"](results/experiment-results.md); [edits §0 RESOLVED](kronos-draft-experiment-edits.md)).
- **What to build (optional, for the exact percentages):** our probe set is **1 mission per
  class** (8 probes), enough for the direction but not to re-derive 88.9–100 %. To match the full
  matrix, write a **held-out probe generator** (N missions per SFC class) + a per-class confusion
  aggregator in `results_to_csv.py`.
- **Reframe (edits doc §4):** label it **same-distribution held-out over the *known* 4-class
  taxonomy (set A)**; ensure no surrounding sentence reads it as telemetry generalization.

### §V.D(2) — Adversarial robustness (5 perturbations)  ·  *Build small tool*

- **They measured:** decision robustness under 5 context perturbations — misleading advisory,
  stale KG, conflicting telemetry, noisy topology, format-shift. Headline: "remained robust,"
  outputs stayed parseable / validator-compliant.
- **Why it needs tooling:** all 5 perturbation surfaces exist as separable fields in the
  assembled `input_object`, but **no perturbation flag/harness exists** (the CLI exposes only
  mission/telemetry/observed/adapter).
- **What to build (small, ~1 file):** `runtime/tools/e1_robustness.py` — call
  `orchestrate.build_runtime_input_object` for the clean set-A input, apply 5 deterministic dict
  transforms, re-run `orchestrate.run_pipeline(cfg, perturbed, check_artifacts=False)`, and record
  `selected_sfc` stability + validator result + parse status. Everything downstream (run_pipeline,
  grammar, validator) is reused unchanged. No testbed.
- **Reframe (edits doc §4):** frame as **decision-stability of the *known* (set-A) decision under
  noisy context**, not a quality win. JSON parseability / validator compliance is a
  **constrained-decoding guarantee check**, not a metric. **Two probes are low-information by
  design:** format-shift is pre-decided by the GBNF guarantee, and **conflicting-telemetry is
  near-circular** (the planner ignores telemetry — it trivially "survives"). Report both with that
  caveat.

### §V.D(3) — Counterfactual sensitivity  ·  *Build small tool*

- **They measured:** hold all signals fixed, change one, check the SFC changes "appropriately"
  (errors concentrated among semantically similar SFCs).
- **Why it needs tooling:** mechanism = repeated single-field `orchestrate` calls (each signal is
  its own CLI flag) + scoring vs `validator.fallback_decision`. The engine and oracle exist; **no
  counterfactual driver does**.
- **What to build (small):** `repro/e1-d5.sh` + a scorer (modeled on `e1.sh`) that varies one
  field while holding the rest fixed and compares `selected_sfc` to the oracle per call.
- **Reframe (edits doc §6.2 — LET GO as generalization):** do **not** claim the planner
  generalizes (contradicts 0/4 on telemetry-only). Only two defensible forms: (a) restrict
  counterfactuals to **mission-name** changes (set A); or (b) present strictly as a **per-episode
  oracle-agreement check** (`selected_sfc` vs `validator.fallback_decision`). Never a blended
  accuracy.

### Historical-performance-aware path selection (abstract/intro/§IV claim)  ·  *Claimed in the paper, no result — build or drop*

- **They claimed (not measured):** the abstract, intro, and §IV.* all state *"Historical-
  performance-aware path-selection experiments additionally demonstrate the use of graph-
  maintained operational knowledge during relay-path decisions."* **PDF cross-check: §V contains
  no such experiment** — no table or figure shows a relay-path decision that *changed because of*
  a logged prior verdict. §V shows path *migration* (primary→backup by SFC choice / adaptation),
  which is **not** the same as history-driven selection. This is the edits-doc **P0 #2 / P2 #10**
  item, and it is the one place where the paper as written is ahead of its own results.
- **Status:** **not started — and we have no result either.** The closed feedback loop *is* wired
  in our system (verdicts/snapshots are written to the KG keyed by `correlation_id`), but the
  1.5B model does not exploit recorded history to alter a decision (validity §8).
- **What to build (if we want to back the claim):** an episode where a **logged prior verdict
  demonstrably changes the next path choice** — e.g. seed the KG with a poor historical
  RTT/verdict on the primary relay, then show the planner/path logic selects the backup it would
  *not* have selected without that history; A/B with the history present vs absent. This is a real
  experiment to design, not a one-line patch — bounded by whether the history actually feeds the
  decision today.
- **Reframe / decision:** **either build the above, or drop the sentence from the abstract/intro**
  (edits-doc recommendation). Do **not** claim history-driven path selection on the strength of
  the migration results. Until backed, treat the loop as **functioning** (writes/reads verdicts),
  not as **improving** decisions — same guardrail as the "loop learns" row in §4.
- **Design drafted:** [historical-path-ab-design.md](historical-path-ab-design.md) — the wiring is
  **plumbed-but-ignored** (the runtime writes no per-path history; the path decision binds from
  `allowed_*` sets / deterministic fallback, never from history), so the as-is A/B is **expected
  null**. The design gives both a cheap as-is A/B (Design A) and the 3-edit close-the-loop version
  that actually backs the claim (Design B), with a run-A-then-decide gate.

---

## 4. What we skip — and what we *reframe* instead of running

The user's question was "skip what the system cannot perform." Grounded against the code, the
answer splits cleanly:

**(a) One experiment reasonable to skip / de-prioritize:** **Table V** (KG-scalability sweep) —
not impossible, but it needs net-new synthetic-KG tooling and returns only Neo4j query latency on
a synthetic graph with no SLA meaning. **Drop if effort > value.** Everything else is worth
repeating.

**(b) Claims the system cannot substantiate — reframe, never run as framed** (per
[edits doc §5/§6.2/§6.3](kronos-draft-experiment-edits.md)). These are *not* experiments to skip;
they are headline framings to drop:

| Draft claim | Why it can't stand | What we report instead |
|---|---|---|
| Planner **telemetry-reasoning generalization** (Table IV condition rows as "KG reasoning"; §V.D(3) counterfactual generalization; any blended Fig-6 accuracy) | planner is 0/4 on telemetry-only missions — it keys on the mission *name* ([planner-lora-eval](../planner/planner-lora-eval.md)) | set A separately; condition→SFC = rule-grounded; counterfactual = oracle-agreement |
| **Throughput as goodput** ("~10.47 Mbps despite migrating traffic") | it is access-link **offered load**, measured upstream of the bottleneck (validity §2.1) | relabel "offered load"; resilience carried by RTT (+ coarse loss) |
| **Sub-10 % loss fidelity** + abstract **"packet-loss below 4 %"** (1.67/2.67/3.33/6.67 %; KRONOS battery row is 10 %) | below resolution at `ping_count=2` (loss quantizes to ~{0,50,100} %); the "below 4 %" abstract claim is also self-contradicted by the 10 % battery row | coarse bounds, or re-measure at `ping_count` ≥ 20; drop the numeric threshold |
| **Closed loop *improves* decisions** | feedback is wired but the 1.5B model doesn't exploit it (validity §8) | the loop **functions** (writes/reads verdicts), not that it learns |
| **DDIL as a maintained-SLA win** | DDIL is genuinely infeasible | **honest escalation** is the correct outcome |

**(c) What our design *adds* that the draft lacks (keep):** the **Runtime Manager** story (E2) —
verdict taxonomy, causal-regression rollback, contention/domination guard, cost-ordered tier
ladder, fail-safe escalation, soak/recovery. The draft has no equivalent; it is the
differentiator and is already validated (E2a 6/6 gate; M5/M6 6/6 live).

---

## 5. Tooling to build (prioritized)

Ordered by value-per-effort. All are small; none needs new hardware.

1. ✅ **Table VIII per-stage timing** — **DONE.** `perf_counter` brackets in `orchestrate.py`
   (+ `--save-timings` / `--repeat-decision`); driver `repro/table8_timing.sh` + scorer
   `runtime/tools/table8_to_csv.py` → [`repeat-results/`](repeat-results/repeat-results.md). Measured
   warm SFC selection **11.04 s ± 0.19** (not the draft's 1 s), rule-based **11 µs**, KG reasoning
   **30 ms**. Remaining: instrument the **runtime-side KG-update write** for the 4th component.
2. ✅ **Table IV latency + provenance columns** — **DONE.** Per-query timing in `kg_context.py`
   (`run_cypher(label=...)` → `last_query_ms`, surfaced into `orchestrate` timings; warm via
   `--repeat-context`); driver `repro/table4_provenance.sh` + scorer `runtime/tools/table4_to_csv.py`
   → [`repeat-results/`](repeat-results/repeat-results.md). Set-A 4/4 provenance; SFC query
   **2.2–2.7 ms**, path query **2.0–2.5 ms** — **reproduces the draft's Table IV ranges**.
3. **§V.D(2) robustness harness** — `runtime/tools/e1_robustness.py` (5 dict transforms over the
   assembled `input_object`).
4. **§V.D(3) counterfactual driver** — `repro/e1-d5.sh` + oracle-agreement scorer.
5. **Table VII NoKG arm** — `nokg` arm in `e3_compare.py`/`e3_measure.py` + operational NoKG
   definition (candidate-set-only). *(Needs the testbed.)*
6. **Fig. 6 exact %** — held-out probe generator (N/class) + per-class confusion aggregator.
   *(Optional — direction already settled.)*
7. **Table V scalability** — synthetic-KG generator + `kg_scale.py` sweep. *(Lowest priority;
   skip candidate.)*
8. **Historical-path-selection experiment** — KG-seeded prior-verdict A/B that changes the next
   path choice. Design: [historical-path-ab-design.md](historical-path-ab-design.md). *Run Design A
   first (cheap, no testbed, expected null at the orchestrate boundary); if null, build Design B
   (3 edits to close the loop on the path axis) or drop the abstract sentence.*

---

## 6. Execution order

```
Already in hand:  Table VI (E3 36/36)  ·  Fig. 6 direction (E1 set A)  ·  E2 (our addition)

Slow-planner, no testbed (fast — seeded KG + GPU + promoted adapter, constrained-on):
   ├─ build #1 Table VIII timing      → re-run E1 probes warm → per-stage breakdown
   ├─ build #2 Table IV latency/prov  → re-run E1 probes       → provenance + Cypher latency
   ├─ build #3 §V.D(2) robustness     → 5 perturbations over set-A input
   ├─ build #4 §V.D(3) counterfactual → mission-name sweep / oracle-agreement
   └─ build #6 Fig. 6 exact %         → held-out probe set (optional)

On the fabric (testbed + sudo + GPU):
   ├─ build #5 Table VII NoKG arm     → re-run E3 with {static, rule, proposed, nokg}
   └─ build #8 Historical-path A/B    → only if we choose to back the abstract claim (else drop it)

De-prioritize / skip:
   └─ build #7 Table V scalability    → only if effort < value
```

Run each slow-planner build under the [§0 controls](experiment-design.md) (seed + verify the
calibrated KG; `source gpu-node.env`; pin adapter id + base revision + `NETPROMPT_LLM_CONSTRAINED`;
explicit `--correlation-id`; score every decision against `validator.fallback_decision`; report
set A / B / C separately). Re-measure all values ourselves — **adopt the structure, never import
the draft's numbers.**

---

*See also: [kronos-draft-experiment-edits.md](kronos-draft-experiment-edits.md) (the adopt /
reframe / cut determination), [experiment-design.md](experiment-design.md) (E1–E4 design + §6
reconciliation), [experiment-validity.md](experiment-validity.md) (measurement controls),
[results/experiment-results.md](results/experiment-results.md) (what is already run),
[planner-lora-eval.md](../planner/planner-lora-eval.md) (set A/B/C accuracy).*
