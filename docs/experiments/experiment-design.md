# Experiment Design

Three experiments, designed as a **unit → integration → system** progression that
mirrors a test pyramid:

1. **E1 — Slow Planner** in isolation (does it *decide* the right SFC?).
2. **E2 — Runtime Manager** in isolation (does it *adapt* correctly and safely?).
3. **E3 — Whole system** end-to-end (does planner→runtime, on the live fabric,
   beat the baselines?).

Each isolates one subsystem so a failure is attributable, then E3 composes them.
All three obey the controls in
[experiment-validity.md](experiment-validity.md) — read that first; this document
references its preconditions rather than repeating them.

> **Why this order.** E1 and E2 decouple the two loops so their behaviors are
> measured independently and cleanly (E1 needs no testbed for the decision; E2
> can run with a deterministic `model` monitor *or* the real fabric). E3 only adds
> value once each half is characterized — otherwise an end-to-end miss is
> unattributable (was it a bad decision or a bad adaptation?).

---

## 0. Shared setup & metric definitions

**Preconditions (every experiment that touches the fabric or the KG)** — full list
in [experiment-validity.md §7](experiment-validity.md):
seed + **verify** the calibrated KG; `source deploy/gpu-node/gpu-node.env`; pin the
planner base-model revision + the promoted `final_adapter_retrained`; measure with
`--monitor real`; keep continuous `iperf` load through baseline *and* observation;
pass an explicit `--correlation-id`; record decision provenance + `kg_write_failures`.

**Metric dictionary (used across experiments):**

| Metric | Definition | Caveat (from validity audit) |
|---|---|---|
| **Decision accuracy** | `selected_sfc` == oracle (`validator.fallback_decision`) | report set A/B/C separately; never blended |
| **Constrained-valid rate** | fraction of outputs that are complete 6-key JSON (`llm_parse_status == parsed_json`) | always 1.0 with constrained-on — report as a *guarantee check*, not a quality metric |
| **Verdict outcome** | `healthy \| marginal \| rollback \| escalated \| system_fault \| rejected` | calibrated bounds bias the mix toward *marginal* |
| **Tier reached** | 0 tune · 1 reroute · 2 regen | Tier-2 stubbed unless `--with-regen` |
| **RTT / loss** | per-field, from a drone-ns ping | loss coarse at `ping_count=2`; first-drone proxy |
| **Throughput** | per-field, from sysfs counters | **access-link offered load, not goodput** |
| **Orchestration overhead** | wall-time of decision + deploy + adapt | LLM decision ~1 s vs rule-based ~µs |
| **Recovery rate** (soak) | injected switch kills that the watchdog restores | operational; needs no-hang run |

**Recording.** Every run writes `Verdict`/`EscalationTicket`/`BaselineSnapshot` to
the KG keyed by `correlation_id`; aggregate with
`python -m llm_orchestrator.analytics`. Persist the run config (adapter id,
revisions, device, constrained flag, scenario, traffic profile) alongside results.

---

## E1 — Slow Planner (decision quality in isolation)

**Objective.** Does the KG-RAG + constrained-decoding planner select the
*mission-appropriate* SFC, and how does it compare to the deterministic baselines
and to its own pre-retrain self?

**Hypotheses.**
- **H1a:** the retrained adapter picks the correct SFC on the **known mission
  taxonomy** (set A) at a rate far above the mode-collapsed original.
- **H1b:** constrained decoding makes **100%** of outputs valid 6-key decisions
  (so the deterministic fallback never fires under the production default).
- **H1c:** the planner does **not** generalize to telemetry-only missions (set C)
  — a documented limitation to quantify, not hide.

**What it isolates.** The decision only — no deploy, no adaptation. Needs the
seeded KG (for topology + candidate set + RAG history); needs **no testbed**.

**Independent variables (arms):**
- **Decision policy:** `baseline_static` (always LowLatency) · `baseline_rule_based`
  (the if/elif ladder) · `proposed-LLM` with **original** adapter · `proposed-LLM`
  with **retrained** adapter.
- **Mission set:** **A** = the 4 known names (emergency→Reliable, bulk→Bandwidth,
  pest→LowLatency, soil→Energy) · **B** = novel names · **C** = generic names where
  only telemetry disambiguates.

**Dependent variables:** decision accuracy vs oracle; reachable-SFC count (mode-collapse
detector); constrained-valid rate; decision latency.

**Protocol.**
```bash
# per (arm × mission), constrained-on, retrained adapter is the default:
python -m llm_orchestrator.orchestrate \
  --mission <MISSION> --bandwidth <BW> --delay <DELAY> --loss <LOSS> --battery <BATT> \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --device-map cuda:0 --no-4bit --adapter-path <ADAPTER> --output /tmp/e1.json
# read selected_sfc + decision.llm_parse_status; compare to validator.fallback_decision(input_object)
```
Reuse the probe matrix already established in
[planner-lora-eval.md](../planner/planner-lora-eval.md) (sets A/B/C) and the
published baselines under `network/.../baselines/`.

**Success criteria.** Retrained ≥ 3/4 correct on set A and reaches all 4 SFC types
(vs the original's 1); constrained-valid rate = 1.0; set C accuracy reported with
the failure mode (defaults to BandwidthOptimized), not averaged into a headline.

**Can claim:** the LLM makes mission-sensitive, KG-grounded, always-valid decisions
on the known taxonomy, beating both baselines there. **Cannot claim:** telemetry
reasoning, or a single blended accuracy across A+B+C.

**Adopted from the KRONOS draft (§6).** The draft's full slow-planner battery — the
Table IV decisions, Table V scalability, the Fig. 6 confusion matrix, and the §V.D
adversarial / counterfactual probes — is catalogued and scoped in **E1-draft** below;
§6 carries the values we do *not* import.

---

## E1-draft — Slow-planner experiments currently in the KRONOS draft

The KRONOS draft already runs a battery of slow-loop (planner-side) experiments. This
section catalogues them **as they exist in the draft**, maps each to its role in E1, and
records the draft's headline result and the guardrail under which we re-run / report it —
the concrete instantiation of E1's "decision quality in isolation." All are **slow
planner**: KG-RAG decision + constrained decoding, no runtime adaptation, no live fabric
(except the one provenance check in E1-d1). The adopt / let-go / cut rationale lives in §6.

| ID | Draft source | What it measures | Draft headline result | E1 role / status |
|---|---|---|---|---|
| **E1-d1** | Table IV · §V.A | per-scenario selected SFC + relay path + P4 policy + KG query latency | baseline/low-lat → LowLatencyVideo·primary; battery → EnergyAware·primary; congestion/relay/DDIL → ReliableRelay·backup; SFC-query 1.20–2.64 ms, path-query 1.82–3.20 ms | decision-provenance table — **VERIFY provenance** (§6.0) |
| **E1-d2** | Table V · §V.A | KG-reasoning latency vs graph size (10/50/100/200 synthetic drones, 20 runs) | total reasoning 4.37 → 7.80 ms; < 8 ms at every size | **adopt (lowest priority)** — "KG query latency"; needs net-new synthetic-KG tooling; drop if effort > value (§6.1) |
| **E1-d3** | Fig. 6 · §V.D | decision accuracy — confusion matrix over the 4 SFC classes (same-distribution held-out) | LowLatencyVideo 99.5% · ReliableRelay 92.6% · EnergyAware 100% · BandwidthOptimized 88.9% | **adopt** — set-A accuracy presentation |
| **E1-d4** | §V.D | robustness — decision under misleading advisory · stale KG · conflicting telemetry · noisy topology · format-shift | "remained robust"; outputs stayed parseable / validator-compliant / policy-consistent | **adopt** — robustness sub-study (note: *conflicting-telemetry* probe is near-circular — §6.1) |
| **E1-d5** | §V.D | counterfactual — does the SFC change when one signal changes, others fixed | "changed appropriately"; errors concentrated among semantically similar SFCs | **reframe / let-go** as generalization (§6.2) |
| **E1-d6** | Table VIII | planner-side control-plane cost | SFC selection ≈ 1.00 s (LLM inference); KG reasoning 7.70–7.80 ms (warm cache) | report as E1 decision latency (LLM ≈1 s vs rule ≈µs) |

**How we re-run them (under the §0 controls).** E1-d1/d3/d4/d5 need only the seeded KG +
the pinned `final_adapter_retrained` with `NETPROMPT_LLM_CONSTRAINED=1` — no testbed; E1-d2
needs only synthetic graph contexts. Reuse the probe matrix in
[planner-lora-eval.md](../planner/planner-lora-eval.md) (sets A/B/C) for the decision rows,
score every decision against `validator.fallback_decision`, and log `selected_sfc` /
`llm_parse_status` / adapter id per the §0 metric dictionary.

**Guardrails — why each is scoped the way it is:**
- **E1-d1 — provenance first.** The condition rows (congestion/relay/DDIL → ReliableRelay)
  are the telemetry-driven case the promoted adapter **fails** (0/4 on sets B/C,
  [planner-lora-eval §2](../planner/planner-lora-eval.md)). Either confirm the table came
  from a model trained on those condition labels, or attribute the condition→SFC mapping to
  the deterministic logic — do not report it as LLM "KG reasoning." Keep the query-latency
  columns (honest Cypher latencies). (validity §3)
- **E1-d2 — keep as is.** Synthetic, latency-only; the lowest-risk planner result. Mark the
  contexts synthetic; never attach an SLA / performance claim.
- **E1-d3 — label it set A.** "Same-distribution held-out" *is* the known 4-mission
  taxonomy. Present the matrix as decision accuracy on the known taxonomy, **not**
  generalization. (validity §3)
- **E1-d4 — robustness of the *known* decision.** Format-validity is **guaranteed** by
  constrained decoding, so JSON parseability / validator compliance is a *guarantee check*,
  not a quality metric — frame the win as decision-stability under noisy context.
- **E1-d5 — do not claim telemetry generalization.** If the counterfactuals vary telemetry,
  a "changed appropriately" result contradicts 0/4 on telemetry-only missions. Restrict the
  set to **mission-name** changes (set A), or present it as an **oracle-agreement** check.
  (validity §3, §8; §6.2)
- **E1-d6 — name the LLM cost.** ≈1 s per decision is LLM inference (vs rule-based ≈µs);
  report it as overhead honestly rather than burying it.

**Can claim (from this battery):** mission-sensitive, KG-grounded, always-valid SFC
decisions on the known taxonomy, with sub-8 ms KG reasoning to 200 drones and decision
stability under noisy context. **Cannot claim:** telemetry / condition generalization, or
any single blended accuracy across the held-out + adversarial + counterfactual sets.

---

## E2 — Runtime Manager (adaptation efficacy + safety in isolation)

**Objective.** Given a deployment, does the inner loop *attribute correctly* and
*adapt within the envelope* — committing, rolling back, or escalating exactly as the
design specifies — and does it stay **safe** (never blackhole, never accept a
regression)?

**What it isolates.** The runtime, decoupled from the planner: scenarios are driven
by fixtures (`run_episode --scenario`) or by a hand-built `DeploymentSpec`, not by an
LLM. Two sub-modes:
- **E2a (logic / behavioral spec):** `--monitor model` — the 6 deterministic
  scenario models. Fast, no testbed; verifies the evaluator + adapt engine reach the
  **expected verdict** for each scenario. This is the runtime's acceptance test.
- **E2b (measured efficacy):** `--monitor real` on the resident BMv2 fabric with
  live traffic — verifies the same outcomes hold when metrics are *measured* and
  actions *actuate* real switches/`tc`.

**The behavioral matrix (E2a acceptance criteria — from `fixtures.py`):**

| Scenario | Injected condition | **Expected verdict** | Tier |
|---|---|---|---|
| `healthy` | clean deploy | **commit (healthy)** | — |
| `causal_regression` | our bad queue tune, env unchanged | **rollback** to last-good | — |
| `path_quality_fault` | primary relay degrades (exogenous) | **commit** after **reroute** | 1 |
| `contention_harm_with_knob` | target starves neighbor, has a knob | **harm-free commit** after rate-down | 0 |
| `contention_harm_no_knob` | target starves neighbor, no knob | **escalate** (no harm-free config) | exhausted |
| `ddil` | both paths degraded, exogenous | **escalate** (all tiers fail) | exhausted |

**Independent variables:** scenario (6) × monitor mode (model/real) × adaptation policy
(**static** = no adaptation, accept first reading · **tiered** = the engine). Optionally
`--with-regen` to add Tier-2 (see validity §6 — needs a watchdog).

**Dependent variables:** verdict-matches-expected (boolean per scenario); tier reached;
adaptation overhead (attempts, wall-time); for the robustness sub-study, recovery rate.

**Protocol.**
```bash
# E2a — behavioral spec (no testbed):
for s in healthy causal_regression path_quality_fault contention_harm_with_knob contention_harm_no_knob ddil; do
  python -m runtime.tools.run_episode --scenario "$s" --monitor model --correlation-id e2a-"$s"
done
# E2b — measured, on the resident fabric (launch_network up + iperf flowing):
sudo -E python -m runtime.tools.run_episode --scenario path_quality_fault --monitor real --correlation-id e2b-pqf
# Robustness/soak — injected kills + watchdog recovery:
sudo -E python -m runtime.tools.soak --minutes 60 --kill-every 20 --kill s3 --correlation-id e2-soak
```

**Baseline / comparison.** "static" (no in-envelope adaptation) vs "tiered": the
contrast is how many scenarios the tiered engine **commits** that static would
**escalate/fail** — i.e. the value the adaptation ladder adds. Report the
commit/rollback/escalate distribution per policy.

**Success criteria.** E2a: **6/6** scenarios reach their expected verdict (this is the
hard gate — it is the design's behavioral contract). E2b: the *direction* of each
outcome reproduces on real hardware (exact margins differ); soak: `kg_write_failures
== 0`, recoveries == injected_kills, no `system_fault` from a recoverable single-relay
kill.

**Can claim:** the runtime attributes correctly (system-fault vs causal rollback vs
exogenous adapt), adapts cost-ordered within the envelope, and is safe by
construction (gate + domination guard + fail-safe escalation). **Cannot claim:**
end-to-end goodput (validity §2); telemetry/decision quality (that's E1).

---

## E3 — Whole system (end-to-end, vs baselines)

**Objective.** Does the full **planner → runtime** pipeline, on the live P4/BMv2
fabric with real traffic, keep flows in-SLA better than the static and rule-based
baselines — and does the closed analytics loop function?

**What it composes.** E1's decision + E2's adaptation, end to end:
the planner picks the SFC → a live episode deploys + adapts → `Verdict`/snapshots to
the KG. **3 arms × 4 scenarios**, all on the *same* 3-switch fabric.

### As-built design (revised from pilots — `docs/experiments/experiment-results.md`)

The first plan reused the published `run_comparative_experiments.sh` and its six
netem conditions. **Two live pilots disproved that approach** and the design was
revised to the harness actually built (`runtime/tools/e3_compare.py` +
`e3_measure.py`):

1. **The published harness is the old milestone-II pipeline** (single-switch,
   self-contained baselines; `update_topology_state.py` + `path_aware_sfc_selector.py`
   for "proposed") with a hardcoded missing `BASE` path — it does **not** exercise the
   `runtime/` Runtime Manager. Rejected.
2. **`launch_network --scenario` degrades the drone→s1 *access* links; the relay paths
   stay clean** ([launch_network.py:147-156](../../runtime/tools/launch_network.py)).
   So a reroute (primary↔backup) can't fix an access-link fault — the netem scenarios
   don't differentiate runtime adaptation. **Faults must be injected on the relay
   links** (the M6 mechanism).
3. **Offered-load ≥ bw bound on a sub-bound link floods it** (pilot: 45 Mbit into a
   20 Mbit congestion link → 4.2 s RTT). Use **sane 5 Mbit/drone load + calibrated,
   satisfiable bounds** (70 ms / 5 Mbps / 20 %, the M6 calibration) so **latency is the
   gate**, not offered load (validity §2.1).
4. **The reroute-capable SFC (ReliableRelay) deploys on the *backup* path by default.**
   So a *primary* fault is dodged by SFC choice alone (no adaptation) — `proposed`
   committed at **tier 0**, merely tying `rule`. Isolating runtime adaptation needs a
   fault on the arm's *active* path. → two complementary fault scenarios.

**Topology-equivalent arms** (KRONOS Table VI control, realized): all three arms run on
the *identical* 3-switch fabric, clean `low_latency` access profile, 5 Mbit/drone load;
the only differences are *which SFC is chosen* and *whether the runtime adapts*.

- **`static`** — fixed LowLatencyVideoSFC (deploys primary), **no adaptation**.
- **`rule`** — the published if/elif ladder (`baseline_rule_based`'s `select_sfc_if_else`;
  a relay issue → ReliableRelaySFC, deploys backup), **no adaptation**.
- **`proposed`** — the LLM planner's pick (`orchestrate`, emergency mission → ReliableRelay)
  + the **full tiered RuntimeManager episode** (observe → tune → reroute → verdict).

**Scenarios (relay-fault injected after a healthy baseline):**

| Scenario | Fault | Isolates | Expected |
|---|---|---|---|
| `healthy` | none | control | all arms commit |
| `primary_fault` | s1-eth11 +100 ms | **SFC selection** | static (primary) ❌ ; rule/proposed dodge via backup ✓ (no adapt) |
| `backup_fault` | s1-eth12 +100 ms | **runtime adaptation** | static dodges (primary clean) ; rule (backup) ❌ ; **proposed reroutes backup→primary, tier 1** ✓ |
| `ddil` | both relays +100 ms | infeasibility | all arms fail / proposed **escalates** (honest) |

The two fault scenarios disentangle the pipeline's two values: `primary_fault` shows the
planner's **SFC-selection** robustness; `backup_fault` shows the runtime's **adaptation**
(same SFC as `rule`, only adaptation differs). *No single static choice is robust to both
fault locations; only the adaptive arm is.*

**Dependent variables:** per-field RTT / loss / throughput vs requirement; SLA-attainment;
verdict + tier-reached + final path; orchestration overhead (decision + deploy + adapt).

**Protocol.**
```bash
source deploy/gpu-node/gpu-node.env
python3 -m runtime.tools.e3_compare --repeats 3 --out /tmp/e3_full.jsonl
# per cell: teardown → reseed KG → pick SFC (static fixed / rule ladder / proposed via
#   orchestrate) → launch clean-access fabric w/ that SFC's P4 program → 5M/drone iperf →
#   e3_measure (deploy → baseline → inject relay fault → measure or full episode) → teardown
```

**Success criteria.** `proposed` is the **only arm SLA-met across every fault location**:
it dodges `primary_fault` (correct SFC), **reroutes** on `backup_fault` (tier-1 commit
where `rule` — same SFC — stays violated), and **escalates** on `ddil` (honest). `static`
fails `primary_fault`; `rule` fails `backup_fault`. RTT is the headline; bw is offered-load
context only.

**Pilot evidence (1 rep, live):** `primary_fault` → {static *violated* 120 ms, rule *met*
47 ms, proposed *met* tier 0 47 ms}; `backup_fault` → {static *met* 30 ms (dodged), rule
*violated* 132 ms, **proposed *healthy* tier 1, rerouted backup→primary, 35 ms**}. The full
4×3×3 matrix (with variance) is in `experiment-results.md`.

**Can claim:** end-to-end, on the calibrated fabric, the proposed arm is the only one that
keeps the target in-SLA across both relay-fault locations — dodging via SFC choice where a
static pick fails and **rerouting** where a non-adaptive arm with the same SFC cannot — and
escalates honestly when infeasible. **Cannot claim** (validity §8): goodput guarantees;
sub-10 % loss fidelity (`ping_count=2`); that the feedback loop *improves* decisions;
generalization beyond known-taxonomy missions (E1: 0/4 off-taxonomy); cross-host
reproducibility without pinned revisions.

**Adopted from the KRONOS draft (§6).** E3 realizes the draft's **topology-equivalent
control** (all arms on identical fabric/access/load + injected fault, separating
orchestration effect from topology-induced latency — Table VI). The **NoKG ablation**
(Table VII) and **control-plane timing breakdown** (Table VIII) remain optional add-ons;
RTT is the headline measured dimension, throughput shown only as offered load and loss only
at coarse bounds (§6.3).

---

## E4 — Tier-2 regen correction (broken-SFC corpus, in isolation)

**Question.** When the regen subsystem is handed *broken* SFC rules — not the safe premade
ones — does it (a) refuse the unsafe ones, and (b) regenerate a correct fix? E4 isolates the
Tier-2 regen LLM the way E1 isolates the planner: off the live loop, against a fixed corpus.

**Corpus** ([`regen_corpus/`](../../regen_corpus/), 16 items, guarded by
`tests/unit/test_regen_corpus.py`). Two kinds: **reject** items — a bad candidate that must be
refused (8 *syntactic*, caught by `grammar.validate()`; 4 *runtime*, grammar-valid but
gate-**L2** blackhole/duplicate/dangling) — and **recover** items — a faulty installed table
(mis-port / drop) the regen must repair with a corrective `table_modify`.

**Three correctness layers, reported separately** (never blended — like E1's set A vs B/C):
*grammar-valid* (passes the GBNF/`validate()`), *gate-safe* (L0+L2 accept, no blackhole),
*recovers* (restores the route, `_recovers` oracle). **Arms:** `gate` (deterministic safety),
`stub` (deterministic recovery machinery), `real` (live Qwen2.5-Coder-1.5B on `cuda:1`).

**Expected & found** (see results §E4): safety is total (12/12 refused, real model 4/4
grammar · 3/4 gate-safe); **exact recovery is the model frontier — 0/4 at 1.5B**, with `stub`
4/4 proving the gap is the model, not the pipeline. This is the regen analogue of E1's
known-taxonomy-vs-generalization split: the *machinery* is correct; the small *model* is
safe-but-not-yet-capable. Driver `runtime/tools/e4_regen.py`; reproduce `repro/e4.sh`.

---

## 4. Threats to validity & how each experiment controls them

| Threat | Affects | Control |
|---|---|---|
| Synthesized vs measured data | E2b, E3 | `--monitor real`; never report `--monitor model` as measured |
| Unachievable SLA → all-escalate | E2b, E3 | re-seed + verify calibrated KG (validity §4) |
| Throughput = offered load | E3 | attribute contention to latency/loss; don't claim goodput |
| Coarse loss (`ping_count=2`) | E2b, E3 | raise `ping_count` for loss-sensitive runs, or coarse bounds only |
| Planner wrong-but-valid off-taxonomy | E1, E3 | restrict to set A or report A/B/C separately + log provenance |
| Non-reproducible cross-host | all | pin planner+regen revision, device, dtype; explicit `--correlation-id` |
| Regen hang on long soaks | E2 (`--with-regen`), E3 | external watchdog + warm-load; default no-LLM loop otherwise |

---

## 5. Execution order & readiness gate

```
Readiness (validity §9): seed+verify KG → pytest tests/unit (212) → on-node M5/M6 → short real-monitor soak (sensible verdict MIX)
   │
   ├─ E1  (planner, no testbed)         ── decision accuracy vs oracle + baselines
   ├─ E2a (runtime logic, no testbed)   ── 6/6 expected verdicts  ← hard gate
   ├─ E2b (runtime, on fabric)          ── outcomes reproduce on real hardware
   └─ E3  (end-to-end, on fabric)       ── proposed vs static vs rule-based × 6 scenarios
```

E1 and E2a need only the seeded KG and the venv (fast, no `sudo`). E2b and E3 need
the resident fabric + traffic. Run E2a as a **gate**: if the runtime doesn't pass
its 6/6 behavioral spec off-node, an on-fabric miss is unattributable.

---

## 6. Reconciliation with the KRONOS (NetPrompt v2) draft

The KRONOS draft (`NetPrompt_V2.pdf`, §IV–V) already reports a battery of experiments —
Tables IV–VIII and the Fig. 6 confusion matrix. This section maps each onto E1/E2/E3 and
sorts it into one of three buckets: **adopt** (bring it over and run it under the §0 +
validity controls), **let go** (don't carry it forward as a *claim*), or **remove/reframe
in the draft** (the number or framing as written is not defensible per
[experiment-validity.md](experiment-validity.md)). It closes with what our design adds
that the draft is missing (E2).

> **One contradiction drives most of the calls below.** The draft's **Table IV** maps the
> *condition* scenarios (congestion, relay-failure, DDIL) to ReliableRelaySFC, and the
> **Fig. 6** matrix scores 88.9–100% per SFC class — both implying the planner reasons from
> telemetry/condition. But our planner eval found the promoted adapter is **4/4 only on the
> four known mission *names*** and **0/4 on telemetry/condition-only missions** (it defaults
> to BandwidthOptimized) — [planner-lora-eval §2–3](../planner/planner-lora-eval.md).
>
> **This is not an open "either/or" — one branch is forced.** `train_decision_lora.py:85-86`
> trains the retrained adapter on **50% generic missions explicitly "to force telemetry
> use,"** oracle-labelled — i.e. the model *was* trained on the telemetry/condition case and
> **still** scores 0/4 on it. So a 88.9–100% Fig. 6 **cannot** be telemetry generalization;
> it is necessarily on **name-correlated, same-distribution held-out data — i.e. set A.** The
> only remaining question is *whether Fig. 6 came from the promoted `final_adapter_retrained`
> or a different/earlier adapter*, settled cheaply by **reproducing the confusion matrix
> under E1's A/B/C split** (the recommended way to retire this VERIFY). Either way, **no
> Table IV / Fig. 6 value may be imported as a telemetry-reasoning result** — we adopt the
> *structure* of these experiments, never the *values*.
>
> **✅ RESOLVED (2026-06-24).** E1 reproduced the matrix with the promoted adapter — set A
> 4/4, B/C 0/4, 8/8 valid ([experiment-results.md](experiment-results.md)). The promoted
> adapter itself yields the high known-taxonomy accuracy; Fig-6 is **set-A accuracy, not
> generalization**. Relabel accordingly.

### 6.1 Adopt — bring over into E1/E2/E3

| Draft artifact | Maps to | How we adopt it | Guardrail (validity §) |
|---|---|---|---|
| **Table IV** — KG-driven SFC + path decision per scenario | E1 + E3 (decision provenance) | the per-scenario `selected_sfc` / path / policy provenance table | label by set A; attribute condition-only rows to the rule oracle, or to a model *verified* on those labels (§3) |
| **Table V** — KG-reasoning scalability, 10–200 synthetic drones (4.37→7.80 ms) | **new E1 adjunct — lowest priority** | rename to **"KG query latency"**: a synthetic-graph Neo4j query-latency sweep | **needs net-new tooling** (`generate_kg.py` hardcodes 10 drones — a synthetic-KG generator must be written); the result is *Neo4j query time on a small graph*, not "reasoning" — report query latency only, never an SLA/perf claim; **drop if effort > value** |
| **Table VI** — topology-equivalent RTT/loss/throughput, 3 arms × 6 scenarios | E3 headline | the 3-arm × 6-scenario shape **and** the *topology-equivalent control* (all arms on identical relay/forwarding) | RTT is the honest dimension; throughput = offered load; coarse loss only (§2) |
| **Table VII** — KG ablation (full vs NoKG) | E3 ablation arm | a NoKG arm isolating the KG's contribution to resilience | **define "NoKG" operationally first** — an empty/unseeded KG routes the planner to `fallback_candidate_actions()` (`kg_context.py:294`), which *also* drops topology grounding and changes the grammar's candidate source, so it isn't a clean single-factor ablation. State precisely what NoKG disables (candidate set only? topology? grammar source?). Report qualitative/coarse resilience, not precise loss % (§2.2) |
| **Table VIII** — control-plane timing (SFC 1.00 s / KG 0.51 s / writeback 0.54 s) | E3 overhead DV | the orchestration-overhead breakdown | report LLM ≈1 s vs rule ≈µs honestly (metric dict) |
| **Fig. 6 + §V.D(1)** — held-out confusion matrix, 4 SFC classes | E1 set-A accuracy | the confusion-matrix presentation of decision accuracy | "same-distribution held-out" = the *known taxonomy*; this is set A, **not** generalization (§3) |
| **§V.D(2)** — adversarial robustness (misleading advisory · stale KG · conflicting telemetry · noisy topology · format-shift) | **new E1 robustness sub-study** | perturb the context, check the set-A decision still holds | robustness of the *known* decision; format-validity is a constrained-decoding **guarantee check**, not a quality metric. **Caveat: the "conflicting telemetry" probe is near-circular** — since the planner keys on the mission *name* and ignores telemetry (0/4, §3), it will trivially "survive" telemetry conflict; frame it as *"robust to telemetry noise because (by the §3 limitation) it ignores telemetry,"* not a robustness win |

### 6.2 Let go — do not carry forward as a claim

| Draft artifact | Why we let it go | Keep instead |
|---|---|---|
| **§V.D(3) counterfactual** telemetry-sensitivity, framed as the planner *generalizing* | contradicts 0/4 on telemetry-only missions ([planner-lora-eval §2–3](../planner/planner-lora-eval.md); validity §3, §8) | at most an **oracle-agreement check** per episode — not a planner-generalization claim |
| **Throughput as a performance/resilience headline** ("~10.47 Mbps despite migrating traffic") | throughput is access-link **offered load**, not goodput (§2.1) | resilience carried by packet-loss + RTT (coarse); throughput shown only as offered load |
| **Closed-loop *learning improves decisions*** | feedback is wired but the 1.5B model doesn't exploit it; escalation-rate conflates wrong-SFC with unachievable-SLA (§8) | claim the loop **functions** (writes/reads verdicts), not that it **improves** decisions |
| **Set B/C as a positive planner result** | the planner learned names, not telemetry ([planner-lora-eval §3](../planner/planner-lora-eval.md)) | report A/B/C **separately** as a documented limitation; never a blended accuracy (E1) |

### 6.3 Remove or reframe in the draft

| In the draft | Problem | Fix in the draft |
|---|---|---|
| Table VI **throughput column** + "maintained throughput near 10.47 Mbps" narrative | reads as goodput; it is offered load measured *upstream* of the shared bottleneck (§2.1) | relabel the column **"offered load"**; drop the goodput/resilience reading; don't make throughput a headline |
| **Fine-grained loss %** — Table VI (1.67 / 2.67 / 3.33 / 6.67%), Table VII (2.0 / 6.67%) | below measurement resolution at `ping_count=2` (loss quantizes to ~{0, 50, 100}% per probe) (§2.2) | report **coarse loss bounds**, or raise `ping_count`≥20 and re-measure before quoting any sub-10% figure |
| **Uniform / too-clean values** — 10.50 across all arms (baseline); KRONOS exactly 10.47 across congestion/relay/DDIL; KRONOS 0.00% loss everywhere | read as single-run or synthesized; no variance reported | reproduce under `--monitor real` with repeats + std-dev, or mark explicitly **"illustrative / single-run"** (§2.4, §5) |
| **Table IV** condition rows (congestion/relay/DDIL → ReliableRelay) framed as the LLM "emerged from KG reasoning" | that is exactly the telemetry-driven case the planner **fails** (§3; [planner-lora-eval §2](../planner/planner-lora-eval.md)) | attribute condition-only selections to the **rule/deterministic** path logic, or verify the model on those labels first |
| **DDIL** "0% loss / 10.47 Mbps" read as a KRONOS **win** | DDIL is genuinely infeasible; maintaining-SLA is the wrong success criterion | reframe DDIL success as **honest escalation** in all arms (E3 success criteria; §8) |

### 6.4 What our design adds that the draft lacks (keep — do not drop)

The draft has **no equivalent of E2**. It models relay adaptation only as path migration
(primary → backup) and reports no verdict semantics. Our **Runtime Manager** contribution —
the verdict taxonomy (commit / rollback / escalate / system-fault), **causal-regression
rollback**, the **contention/domination guard**, the **cost-ordered tier ladder**, fail-safe
escalation, and the **soak/recovery** robustness study — is exactly what distinguishes
NetPrompt v2 from a "KG picks an SFC" story. E2a's **6/6 behavioral spec is the hard gate**
(§5). The reconciliation flows *toward* the draft here: it should **gain** an E2-style
section, not lose anything to it.

---

*See also: [experiment-validity.md](experiment-validity.md) (the controls + go/no-go),
[../architecture-review/05-evolution-from-original.md](../architecture-review/05-evolution-from-original.md)
(the baselines + retrain provenance), [planner-lora-eval.md](../planner/planner-lora-eval.md)
(the E1 probe matrix), `runtime/fixtures.py` (the E2 scenario models),
`network/.../run_comparative_experiments.sh` (the E3 harness shape),
`NetPrompt_V2.pdf` (the KRONOS draft §6 reconciles).*
