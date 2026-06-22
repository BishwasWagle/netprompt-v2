# 1 · Clean Architecture Breakdown

## 1.1 The system in one sentence

RuntimeManager is the **inner, mostly-deterministic control loop** of a two-loop
MAPE-K (Monitor–Analyze–Plan–Execute over a Knowledge base) system that keeps a
drone Service Function Chain (SFC) inside its SLA envelope on a live P4/BMv2
network, escalating to a slow LLM planner only when no in-envelope fix exists.

## 1.2 Two loops, two models

```
 mission ─▶ SLOW PLANNER (outer loop) ──artifact──▶ RUNTIME MANAGER (inner loop) ──▶ live P4/BMv2
             │ decision LLM (Qwen Instruct, cuda:0)      │ deterministic ladder
             │ KG-RAG → select SFC + path                │ tier-2 regen LLM (Qwen Coder, cuda:1) optional
             ▼                                            ▼
       llm_generated_experiment_config.json        Verdict + EscalationTicket → KG
```

* **Outer loop** (untouched by this codebase's runtime side): an LLM reads the
  Knowledge Graph + history, picks *which SFC* and *which path*, and writes a JSON
  artifact. It then **stops** — it triggers no deploy.
* **Inner loop** (`runtime/`): takes that artifact, deploys it, observes the live
  network, and drives one *episode* to a terminal verdict using a deterministic
  cost-ordered adaptation ladder. An LLM appears here only as an optional Tier-2
  "regenerate the P4 rules" component — **the Runtime Manager itself is not an
  LLM.**

## 1.3 Component map (`runtime/`)

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

## 1.4 The core data contracts

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

## 1.5 End-to-end data flow of one episode

```
DeploymentSpec
     │
     ▼  RuntimeManager.run_episode()                                  [runtime_manager.py:45]
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 1. gate.check_binding(spec) ───not ok──▶ Verdict("rejected")  (never deploys)│  [:47]
 │ 2. current_tables ← deployer.table_state()  (live switch state for gate L2)  │  [:57]
 │ 3. EvalContext built with a FRESH Budget(N)  ── per-episode budget model     │  [:61]
 │ 4. report ← monitor.observe_window()  ──▶ MonitorReport                      │  [:70]
 │ 5. best-effort KG writes: switch_status, baseline                            │  [:74]
 │ 6. result ← evaluate(spec, report, ctx)                                      │  [:78]
 └───────────────────────────────────────────────────────────────────────────┘
     │
     ▼  evaluate() — the 6-stage ladder                                [evaluator.py:75]
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
     ▼  adapt() — the cost-ordered hill-climb engine                       [adapt.py:174]
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
     ▼  back in run_episode():  on healthy|marginal commit                [runtime_manager.py:83]
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

## 1.6 Architectural strengths (keep these)

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

## 1.7 The "north-star" clean architecture

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
   │ + TableStateProto (optional node methods)        │
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
