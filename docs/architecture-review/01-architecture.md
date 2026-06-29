# 1 · Clean Architecture Breakdown

This breakdown is organized into three parts:

* **[§1.1 Whole System](#11-whole-system)** — the two-loop MAPE-K shape and how
  the loops coordinate.
* **[§1.2 Slow Planner](#12-slow-planner--the-outer-loop)** — the outer loop:
  the LLM that decides *which* SFC, then stops.
* **[§1.3 Runtime Manager](#13-runtime-manager--the-inner-loop)** — the inner
  loop and the primary subject of this review: the deterministic control loop
  that deploys, observes, adapts, and commits.

---

## 1.1 Whole System

### 1.1.1 The system in one sentence

The system is a **Knowledge Graph–driven, two-loop MAPE-K**
(Monitor–Analyze–Plan–Execute over a Knowledge base) orchestrator for drone-edge
networks: a **slow LLM planner** (outer loop) does KG-RAG over a Neo4j Knowledge
Graph to decide *which* Service Function Chain (SFC) and relay path; a
**mostly-deterministic RuntimeManager** (inner loop) deploys that decision onto a
live P4/BMv2 network and holds the SFC inside its SLA envelope through a
cost-ordered adaptation ladder, escalating back to the planner only when no
in-envelope fix exists; and the **Knowledge Graph** is the shared hub the planner
reads (strategic state) and the runtime writes back to (operational verdicts) —
closing the loop.

> **Scope.** This review's primary subject is the inner loop (`RuntimeManager`,
> §1.3); the outer-loop planner is summarized in §1.2 and the Knowledge Graph is
> detailed in [06-knowledge-graph.md](06-knowledge-graph.md).

### 1.1.2 Two loops, two models

```
 mission ─▶ SLOW PLANNER (outer loop) ──artifact──▶ RUNTIME MANAGER (inner loop) ──▶ live P4/BMv2
             │ decision LLM (Qwen Instruct, cuda:0)      │ deterministic ladder
             │ KG-RAG → select SFC + path                │ tier-2 regen LLM (Qwen Coder, cuda:1) optional
             ▼                                            ▼
       llm_generated_experiment_config.json        Verdict + EscalationTicket → KG
```

* **Outer loop** — the [Slow Planner](#12-slow-planner--the-outer-loop): an LLM
  reads the Knowledge Graph + history, picks *which SFC* and *which path*, and
  writes a JSON artifact. It then **stops** — it triggers no deploy.
* **Inner loop** — the [Runtime Manager](#13-runtime-manager--the-inner-loop)
  (`runtime/`): takes that artifact, deploys it, observes the live network, and
  drives one *episode* to a terminal verdict using a deterministic cost-ordered
  adaptation ladder. An LLM appears here only as an optional Tier-2 "regenerate
  the P4 rules" component — **the Runtime Manager itself is not an LLM.**

The two LLMs are deliberately separate — different model, different GPU,
different grounding source (see [05](05-evolution-from-original.md) §6 for the
full model table):

| | **Decision model** (planner) | **Tier-2 regen model** (runtime) |
|---|---|---|
| Base | `Qwen2.5-1.5B-Instruct` + LoRA | `Qwen2.5-Coder-1.5B-Instruct` (base) |
| Device | `cuda:0` | `cuda:1` (no contention) |
| Grounded in | the **KG** (strategic) | the deployer's live **`table_state`** (tactical) |
| Role | *select which SFC/policy/path* | *regenerate P4 rules* when tier 0/1 fail |

### 1.1.3 How the two loops coordinate

The slow planner and the runtime manager **never call each other directly**.
They coordinate through two channels:

1. **A typed file/contract handoff.** The planner writes its artifact; the
   runtime's [`planner_adapter.py`](#124-the-handoff--artifact--deploymentspec-then-it-stops)
   normalizes it into a `DeploymentSpec` (planner → runtime), and the runtime
   emits a `Verdict` + optional `EscalationTicket` (runtime → planner) when the
   episode terminates.
2. **The Knowledge Graph as a shared hub.** The planner reads strategic state
   (SFC templates, field bounds, relay availability) and is *read-only*; the
   runtime is the *sole writer* of operational state (switch status, verdicts,
   snapshots). The loop closes through the KG: the runtime's measured
   `ProgrammableSwitch.status` becomes the planner's `allowed_relays`, and
   aggregated `Verdict`/`EscalationTicket` history becomes the planner's
   `runtime_feedback`. Full treatment in
   [06-knowledge-graph.md](06-knowledge-graph.md).

---

## 1.2 Slow Planner — the outer loop

> This review's mandate is the inner loop; the planner is summarized here for
> context and covered in depth in [05-evolution-from-original.md](05-evolution-from-original.md)
> (§2 planner, §4 selector, §6 models, §7 retrain) and
> [06-knowledge-graph.md](06-knowledge-graph.md) (§6.3 how the planner uses the
> KG). The planner's decision kernel (`validator` / `prompt_builder` /
> `kg_context` / `policy_compiler`) is Bishwas & Kiran's original
> `llm_orchestrator/`, extended — not rewritten — this session.

### 1.2.1 What it does

The slow planner is the **outer loop**: given a mission, it reads the Knowledge
Graph (topology + candidate SFC/policy set) plus a KG-RAG results history, and
decides *which* SFC and *which* path to deploy. It emits a single JSON artifact
(`outputs/llm_generated_experiment_config.json`) and then **stops** — it triggers
no deploy and supervises no episode.

Selection is deliberately the *slow* loop: it runs per mission / re-plan, not per
control cycle (~1 s for the LLM decision vs ~3 µs for the old rule-based
baseline; the LLM buys mission-/context-sensitivity and KG grounding at that
cost — [05](05-evolution-from-original.md) §4).

### 1.2.2 The decision pipeline

`build_runtime_input_object` (`llm_orchestrator/orchestrate.py`) assembles the
model input from KG-grounded sources, then `run_pipeline` prompts the fine-tuned
model for a decision, validates it, and compiles the artifact:

```
KG topology snapshot (kg_context.get_topology_snapshot)   → allowed relays/paths
KG candidate set    (kg_context.get_candidate_sfc_policy_set, REALIZED_BY_P4_POLICY) → action menu
KG-RAG history (top-k=3) + runtime_feedback (per-SFC reliability)
        │
        ▼  assemble orchestration_constraints
   run_pipeline → fine-tuned LLM → 6-key JSON decision
        │   {selected_sfc, selected_policy, selected_path, selected_relay,
        │    priority_class, deployment_mode}
        ├─ valid?   ─▶ policy_compiler → llm_generated_experiment_config.json
        └─ invalid? ─▶ validator.fallback_decision (deterministic rule oracle) ─▶ compile
```

The load-bearing detail is that the **same** `orchestration_constraints` feed
*both* the constrained-decoding grammar (`decision_grammar.build_decision_gbnf`)
and the validator (`validator.validate_generated_decision`):

```
KG candidate set + allowed relays/paths
        ├─▶ build_decision_gbnf(...)         → GBNF the LLM decodes under
        └─▶ validate_generated_decision(...) → the contract check
                                   ⇒ grammar-valid ⇒ validator-valid by construction
```

So the LLM literally **cannot emit an SFC/policy pair the KG does not realize**,
and with constrained decoding on (the production default) the deterministic
fallback is **bypassed** — the LLM's choice ships. That is exactly why decision
*quality* rested on the LoRA retrain ([05](05-evolution-from-original.md) §3.2,
§7; [06](06-knowledge-graph.md) §6.3, §6.7).

### 1.2.3 The decision model

`Qwen2.5-1.5B-Instruct` + LoRA (`final_adapter_retrained`), on `cuda:0`, FP16,
greedy decoding under a per-request GBNF grammar (default-on). It is distinct in
every dimension from the runtime's optional Tier-2 regen model
(`Qwen2.5-Coder-1.5B-Instruct`, `cuda:1`, grounded in live `table_state` rather
than the KG). **The decision model is not the Runtime Manager**; the regen model
is a component the deterministic RM *may call* at tier 2. Full model table and
the retrain story: [05](05-evolution-from-original.md) §6–7.

### 1.2.4 The handoff — artifact → DeploymentSpec, then it stops

The planner writes `llm_generated_experiment_config.json` and stops. On the
runtime side, [`planner_adapter.py`](runtime/planner_adapter.py) is the seam that
normalizes that artifact into a typed `DeploymentSpec` **without modifying any
planner code**:

* It preserves the planner's *real* decisions — `selected_sfc` + `policy_type`,
  carried verbatim — and **reconstructs** the canonical per-switch binding
  (`<prefix>_s{1,2,3}_rules.txt` + `<prefix>.json`) rather than trusting the
  artifact's literal rule paths (the orchestrator labels rule files by the
  *active relay*, which would load s2 with s3's rules — see the module
  docstring).
* It supplies the two fields the artifact does not carry — `target_field`
  (drives envelope derivation + the monitor's host map) and `correlation_id`
  (ties deploy → monitor → verdict → escalation).
* The runtime **never re-selects** the SFC or path (design §1.2).

From the `DeploymentSpec`, control passes to the inner loop ([§1.3](#13-runtime-manager--the-inner-loop)).

---

## 1.3 Runtime Manager — the inner loop

### 1.3.1 Component map (`runtime/`)

| Layer | Module | Responsibility |
|-------|--------|----------------|
| **Contracts** | `contracts.py` | Every dataclass that crosses a boundary: `DeploymentSpec`, `MonitorReport`, `FlowMetrics`, `Candidate`, `Diagnosis`, `Verdict`, `EscalationTicket`, `ConfigSnapshot`, `BaselineSnapshot`, `Envelope`, `AdaptResult`. Plus `goal()` and `jsonable()`. |
| **Config** | `config.py` | Testbed topology (ports, MACs, switch port maps), `tc` templates, hysteresis params, adaptation tunables, and the runtime-owned per-SFC action space. |
| **Orchestration** | `runtime_manager.py` | `run_episode()` — the episode lifecycle: pre-deploy gate → baseline → evaluate → commit/rollback/escalate, with best-effort KG writes. |
| **Analyze** | `evaluator.py` | The 6-stage post-deploy attribution + commit ladder. |
| **Plan/Execute** | `adapt.py` | The unified cost-ordered adaptation engine (tier 0 tune → 1 reroute → 2 regen) with the domination guard, hill-climb, `tried` set, and episode `Budget`. |
| **Validate** | `gate.py` | Sound, cheapest-first pre-deploy authority: L0 syntax · L1 envelope · L2 blackhole-invariant simulation · L3 dry-install (node-only). |
| **Execute (I/O)** | `deployer.py` | Pure command/parse/state layer over an injectable runner: install rules, TUNE via `tc`, multi-switch REROUTE, deterministic rollback. |
| **Execute (I/O)** | `node_runner.py` | The real on-node runner: `simple_switch_CLI` over thrift + `mnexec` into Mininet namespaces. |
| **Monitor** | `monitors/network_monitor.py` | Wires real samplers (ping, sysfs counters, `tc`, liveness) to the pure pipeline; produces `MonitorReport`, manages baseline/hysteresis. |
| **Monitor (pure)** | `monitors/pipeline.py` | Deterministic, off-node-testable core: hysteresis, throughput math, parsers, exogenous-shift detection, status derivation, report assembly. |
| **Monitor (I/O)** | `monitors/system_monitor.py` | Process/thrift liveness probes. |
| **KG boundary** | `kg_client.py` | Neo4j reads (envelope/requirements) + best-effort runtime-state writes (verdicts, snapshots, switch status). |
| **Tier-2 LLM** | `regen/` | `proposer.py` (the engine seam), `prompt.py`, `grammar.py` (GBNF), `llm_client.py` (`StubLLMClient` + `LocalHFClient`). |
| **Planner edge** | `planner_adapter.py` | Normalizes the planner's JSON artifact into a typed `DeploymentSpec` without modifying any planner code. |
| **Test doubles** | `fakes.py`, `fixtures.py` | Off-node `FakeDeployer`/`FakeMonitor`/`ScriptedRunner` and scenario models. |
| **Tools** | `tools/` | `soak.py`, `run_episode.py`, `run_from_planner.py`, `launch_network.py`, etc. |

### 1.3.2 The core data contracts

The whole loop is choreographed through a small set of immutable-ish dataclasses,
which is a real strength — boundaries are explicit and timestamps are always
caller-supplied (deterministic under test). The key ones:

* **`DeploymentSpec`** `{sfc, binding, envelope, correlation_id, target_field}` —
  the planner→runtime handoff. The runtime *never* re-selects the SFC.
* **`Envelope`** — SLA bounds (`max_latency_ms`, `min_bandwidth_mbps`,
  `max_loss_percent`) **plus** the runtime-owned legal action space
  (`legal_tiers`, `legal_paths`, `knob_ranges`).
* **`MonitorReport`** — the single contract every evaluator rung and the adapt
  engine read: `target`/`non_target` `FlowMetrics`, `target_sla_met`,
  `displaced_harm`, `vs_baseline`, `exogenous_shift`, `headroom`, `switch_status`,
  `path_confidence`.
* **`Candidate`** `{kind, params}` — one adaptation step; frozen + tuple params so
  it is hashable into the engine's `tried` set.
* **`Verdict`** — the terminal outcome recorded to the KG: one of
  `healthy | marginal | rollback | escalated | system_fault | rejected`.

### 1.3.3 End-to-end data flow of one episode

```
DeploymentSpec
     │
     ▼  RuntimeManager.run_episode()                                  [runtime_manager.py:48]
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 1. gate.check_binding(spec) ───not ok──▶ Verdict("rejected")  (never deploys)│  [:50]
 │ 2. current_tables ← deployer.table_state()  (live switch state for gate L2)  │  [:60]
 │ 3. EvalContext built with a FRESH Budget(N)  ── per-episode budget model     │  [:64]
 │ 4. report ← monitor.observe_window()  ──▶ MonitorReport                      │  [:73]
 │ 5. best-effort KG writes: switch_status, baseline                            │  [:77]
 │ 6. result ← evaluate(spec, report, ctx)                                      │  [:81]
 └───────────────────────────────────────────────────────────────────────────┘
     │
     ▼  evaluate() — the 6-stage ladder                                [evaluator.py:77]
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ rung 1  system_sound?        no ─▶ Verdict("system_fault")               │
   │ rung 2  target_sla_met?      yes ─▶ jump to commit path (rung 5)         │
   │ rung 3  regressed vs baseline (deadbanded) AND not exogenous?            │
   │             yes ─▶ deployer.rollback(last_good) ─▶ Verdict("rollback")   │
   │ rung 4  adapt(...) ─▶ AdaptResult                                        │
   │             fail ─▶ EscalationTicket + Verdict("escalated")             │
   │ rung 5  displaced_harm? ─▶ adapt() to relieve (Option B)                 │
   │             fail ─▶ Verdict("escalated", "no harm-free config")          │
   │ rung 6  commit_outcome(headroom, tier) ─▶ Verdict("healthy"|"marginal")  │
   └─────────────────────────────────────────────────────────────────────────┘
     │
     ▼  adapt() — the cost-ordered hill-climb engine                       [adapt.py:165]
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ while budget.remaining() > 0:                                            │
   │   diag ← diagnose(report)            (worst violation; target outranks)   │
   │   cand ← propose(diag, tier, env, deployer.state, tried, regen_proposer) │
   │          tier 0 tune → tier 1 reroute → tier 2 regen (LLM seam)          │
   │   gate.check(cand, env, live_tables) ──reject──▶ record, try next         │
   │   deployer.apply(cand); budget.spend(); post ← observe_window()          │
   │   goal(post)?            ─▶ success                                       │
   │   dominates ∧ improves?  ─▶ keep (hill-climb)   else deployer.rollback()  │
   └─────────────────────────────────────────────────────────────────────────┘
     │
     ▼  back in run_episode():  on healthy|marginal commit                [runtime_manager.py:86]
        last_good ← deployer.capture();  monitor.rebaseline()
        best-effort KG writes: verdict, last_good, escalation, re-baseline
     │
     ▼
   EvalResult{ verdict, ticket? }
```

**Contract types crossing each boundary:** `DeploymentSpec` → (`GateResult`) →
`MonitorReport` → `FlowMetrics`/`Diagnosis`/`Candidate`/`GateResult` →
`AdaptResult`(+`AttemptRecord` trace) → `Verdict`/`EscalationTicket` →
`EvalResult`; `ConfigSnapshot` is captured/rolled-back as an opaque snapshot and
`BaselineSnapshot` is persisted to the KG.

### 1.3.4 Architectural strengths (keep these)

These are deliberate and good; the refactors in this review are careful **not** to
disturb them:

1. **Pure core / impure shell.** `pipeline.py`, the deployer's parsers/builders,
   and the whole adapt/evaluate logic are pure and unit-tested off-node against
   fakes; only the thin sampler/runner layer does I/O.
2. **One engine, one goal.** Both "target failing" and "neighbor harmed" enter the
   *same* `adapt()` with `GOAL = target_sla_met AND no displaced_harm`. No
   duplicated control logic between the two.
3. **Safety by construction.** Domination guard (never accept a regression),
   strict-progress hill-climb, finite quantized candidate grids + `tried` set
   (termination), per-episode budget, fail-safe escalation when Tier-2 returns
   `None`.
4. **Best-effort recording.** KG writes can never abort an episode — recording is
   not the loop's job. (The mechanism has a cost; see Problem #11.)
5. **Sound gate.** Conservative-by-default: without table state it *refuses*
   regen rather than hoping.

### 1.3.5 The "north-star" clean architecture

The target end-state this review's refactors move toward — same behavior, sharper
seams:

```
        ┌────────────── contracts.py ──────────────┐
        │ dataclasses + Protocols + closed-vocab     │   ← typed seams, not docstrings
        │ enums/constants (Outcome, SwitchStatus…)   │
        └───────────────────────────────────────────┘
                 ▲              ▲              ▲
   ┌─────────────┘   ┌──────────┘   └───────────────┐
   │ DeployerProto   │ MonitorProto   │ GateProto    │   ← every collaborator typed
   │ + TableStateCapable (optional node methods)        │
   └──────────────────────────────────────────────────┘
        ▲                                   ▲
   topology/mechanism config        control-policy object   ← config split: ports/MACs/tc
   (ports, MACs, tc templates)      (budget, τ, action space)   vs tunable policy, injected
```

The control loop itself does not move. What changes is that the *contracts* it
already depends on informally become checkable, and the one global `config`
module splits into "physical mechanism" (topology) and "control policy"
(tunables) so two configurations can coexist without monkeypatching globals.
See [03-refactoring-strategy.md](03-refactoring-strategy.md).

---

## Key Takeaways

- **It's the deterministic inner loop of a two-loop MAPE-K system.** A slow LLM planner (Qwen Instruct on cuda:0) does KG-RAG to pick *which* SFC and path, writes `llm_generated_experiment_config.json`, and then stops — it never deploys. RuntimeManager (`runtime/`) takes that artifact, deploys it to the live P4/BMv2 network, and drives one episode to a terminal verdict via a cost-ordered ladder. The Runtime Manager itself is *not* an LLM; an LLM only appears as an optional Tier-2 "regenerate the P4 rules" component (Qwen Coder on cuda:1).

- **The doc is split into three altitudes: Whole System, Slow Planner, Runtime Manager.** §1.1 frames the two-loop shape and how the loops coordinate (a typed `DeploymentSpec`/`Verdict` contract plus the KG as shared hub — the two never call each other directly). §1.2 covers the outer loop — the planner reads KG topology + candidate set + KG-RAG history, emits a constrained 6-key decision, and stops, handing off via `planner_adapter.py`. §1.3 is the inner loop and the subject of this review.

- **The planner's choice is load-bearing because the grammar *is* the KG.** The same `orchestration_constraints` feed both the GBNF grammar the LLM decodes under and the validator, so grammar-valid ⇒ validator-valid by construction and the LLM cannot emit an SFC/policy the KG doesn't realize. With constrained decoding on (production default) the deterministic fallback is bypassed and the LLM's choice ships — which is why decision quality rested on the LoRA retrain ([05](05-evolution-from-original.md) §7).

- **Everything crosses boundaries as typed contracts.** The whole inner loop is choreographed through a small set of immutable-ish dataclasses in `contracts.py` (`DeploymentSpec`, `MonitorReport`, `Candidate`, `Diagnosis`, `Verdict`, `EscalationTicket`, `Envelope`, etc.), with timestamps always caller-supplied so behavior is deterministic under test. A `DeploymentSpec` is the planner→runtime handoff, and `Verdict` is the terminal outcome — one of `healthy | marginal | rollback | escalated | system_fault | rejected`.

- **An episode runs as nested ladders, each with file/line anchors.** `run_episode()` (`runtime_manager.py:48`) gates first (rejecting without ever deploying), captures live table state, builds a fresh per-episode `Budget(N)`, observes a window into a `MonitorReport`, then calls `evaluate()` (`evaluator.py:77`). That 6-stage ladder checks system soundness → SLA met → regression-vs-baseline rollback → `adapt()` → displaced-harm relief → commit, and `adapt()` (`adapt.py:165`) is the cost-ordered hill-climb (tier 0 tune → 1 reroute → 2 regen LLM) that loops while budget remains.

- **One engine serves both failure modes, against a single goal.** Whether the target SFC is failing or a neighbor is harmed, both enter the *same* `adapt()` with `GOAL = target_sla_met AND no displaced_harm` — no duplicated control logic. Diagnosis ranks the worst violation with the target outranking, and the runtime *never* re-selects the SFC (that was the planner's job).

- **Pure core / impure shell is a deliberate, preserved strength.** `pipeline.py`, the deployer's parsers/builders, and all of the adapt/evaluate logic are pure and unit-tested off-node against fakes (`FakeDeployer`, `FakeMonitor`, `ScriptedRunner`); only a thin sampler/runner layer (`node_runner.py`, samplers) touches real I/O via `simple_switch_CLI` over thrift and `mnexec`.

- **Safety is built in by construction, not bolted on.** A domination guard never accepts a regression, strict-progress hill-climb plus finite quantized candidate grids and a `tried` set guarantee termination, a per-episode budget bounds work, and Tier-2 returning `None` triggers fail-safe escalation. The gate is conservative-by-default — without live table state it *refuses* regen rather than hoping — and KG writes are best-effort so recording can never abort an episode.

- **The north-star is sharper seams, same behavior.** The refactors target turning the contracts the loop already depends on informally into checkable Protocols (`DeployerProto`, `MonitorProto`, `GateProto`, optional `TableStateCapable`) plus closed-vocabulary enums, and splitting the one global `config` module into "physical mechanism" (ports, MACs, tc templates) versus injected "control policy" (budget, τ, action space) so two configurations can coexist without monkeypatching globals. The control loop itself does not move (see `03-refactoring-strategy.md`).
