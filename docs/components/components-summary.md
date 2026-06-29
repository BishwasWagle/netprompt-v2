# Component Summary

A one-page tour of every significant component in the system — grouped by the two loops and the
two models, with each component's role, responsibilities, interface, and the single most important
gotcha. For the full reference cards see the per-component docs linked under each entry (and the
[Component Reference README](README.md)).

## Two loops, two models

```
mission ─▶ SLOW PLANNER (outer loop) ─▶ artifact ─▶ RUNTIME MANAGER (inner loop) ─▶ live P4/BMv2
            └ decision LLM (cuda:0)                   └ deterministic; tier-2 regen LLM (cuda:1) optional
```

The **Slow Planner** (outer loop) reads the Knowledge Graph + history and decides *which* SFC and
path, then writes a JSON artifact and stops. The **Runtime Manager** (inner loop) takes that
artifact, deploys it to the live P4/BMv2 network, and drives one episode to a terminal verdict via
a cost-ordered adaptation ladder — escalating only when no in-envelope fix exists. The **Knowledge
Graph** is the shared hub: the planner reads strategic state; the runtime is the sole writer of
operational state.

| Group | Components |
|---|---|
| **Runtime Manager** (inner loop) | Runtime Manager · Validation Gate · Deployer · Monitors · Evaluator · Adaptation Engine · KG Client |
| **Slow Planner** (outer loop) | Slow Planner · Slow-Planner Analytics |
| **The two models** | Planner LLM (decision) · Regen LLM (Tier-2 code) |

---

## Runtime Manager — inner loop (mostly deterministic)

### Runtime Manager
*Orchestrates one deployment's episode — gate, deploy, observe, evaluate, adapt — and records verdicts and snapshots to the KG.*

Owns the episode lifecycle: runs the pre-deploy binding gate, provisions a fresh per-episode
`Budget` and `EvalContext` to the evaluator, and on a commit promotes last-known-good and
re-baselines. It does **not** own attribution (evaluator), the tier ladder/guards (adapt engine),
live re-install (deployer), or observation/hysteresis (monitor). Mostly deterministic — the only
non-determinism is the optional Tier-2 regen LLM (defaults to `None`).

**Does:**
- Pre-deploy gate rejects invalid specs; refused specs are logged to the KG and never touch the network.
- Refreshes `current_tables` from the deployer each episode so the Tier-2 gate sees live switch tables, not stale values.
- Builds `EvalContext` with a fresh per-episode `Budget`, observes one window, writes switch status + baseline snapshot, then evaluates.
- On any commit: ends the episode, re-captures last-good, calls `monitor.rebaseline()`, persists the new last-good + re-baselined snapshot.

**Interface.** `RuntimeManager.run_episode(spec, timestamp) -> EvalResult` (`runtime/runtime_manager.py`); CLIs: `run_episode.py` (scenario-driven), `run_from_planner.py` (planner-driven), `soak.py` (repeated episodes + kill injection).
**Gotcha.** KG writes are best-effort — transient Neo4j failures are swallowed/counted but never abort an episode; `Budget` is per-episode by construction (any deviation breaks the budget-bounded escalation guarantee).
[Full card → runtime-manager.md](runtime-manager.md)

### Validation Gate
*The sound, pre-deploy authority that proves a binding or adapt candidate is safe before it touches the data plane.*

Owns the deterministic, conservative, fast structural checks (L0–L2) that guarantee a config
installs cleanly, stays in-envelope, and cannot blackhole a flow — this is what makes an LLM
acceptable in a live control loop (the model proposes, the gate is the authority). It does **not**
decide SLA satisfaction or talk to the KG.

**Does:**
- Two entry points: `check_binding()` validates a full `DeploymentSpec` pre-deploy; `check()` validates tune/reroute/regen candidates before apply.
- **L0 syntax/grammar:** parses as `simple_switch_CLI`, references known tables, validates MAC/IPv4 keys, egress is a real switch port.
- **L1 envelope:** tier ∈ `legal_tiers`, knob ∈ `knob_ranges`, path ∈ `legal_paths`.
- **L2 safety invariants:** simulates commands against a `current_tables` copy; asserts all required drone MACs stay forwarded and the edge stays reachable.
- **L3 dry-install** (optional, node-only): catches real install errors (DUPLICATE_ENTRY / handle drift) simulation can't; OFF by default.

**Interface.** `ValidationGate.check_binding(spec) -> GateResult`, `check(cand, env, current_tables) -> GateResult` (`runtime/gate.py`); contracts from `runtime/contracts.py`, bounds from `runtime/config.py`.
**Gotcha.** L2 keys the working set by `(table, handle)` not handle alone (BMv2 handles aren't unique across tables) — keying by handle alone collapsed entries and produced false blackhole rejections; port args are matched as exact strings.
[Full card → validation-gate.md](validation-gate.md)

### Deployer
*Live re-installs table rules and host QoS over a persistent BMv2 network, tracks config state, and handles rollback.*

Owns all command construction, output parsing, and state tracking for the data plane: installs and
modifies switch table rules and host `tc` qdiscs, applies optimization candidates (tune/reroute/
regen), and restores prior configs. It does **not** decide what to deploy, whether it's safe, or
whether it met SLA.

**Does:**
- Two mutation surfaces: switch tables via `run_cli` (`table_add/modify/delete`) and host namespaces via `run_host` (`tc`, `ip`, `arp`).
- Reroute is a 5-part ordered action to prevent blackholes (install relay edge-MAC → repoint s1 → bind path → re-arp drones).
- Tracks state by table **key** (not handle) so it survives switch restarts (handles get re-numbered on reboot).
- Idempotent deploy with `_reset_switch`; `dry_install` is the gate's L3 hook (test-applies live, catches real errors, rolls back).
- Agnostic to command delivery via an injected `Runner` (live `NodeRunner` or `ScriptedRunner` for off-node tests).

**Interface.** `Deployer.deploy(spec)`, `apply(cand)`, `rollback(snapshot)`, `re_push`, `recover_switch`, `dry_install`, `capture`, `table_state`, `state` (`runtime/deployer.py`).
**Gotcha.** The testbed P4 program must match the deployment config — a mismatch surfaces as a handle-count mismatch (N `table_add` lines but N−1 handles) because BMv2 silently rejects duplicate keys.
[Full card → deployer.md](deployer.md)

### Monitors
*Passive-first network monitor (noisy) + system liveness (sound), smoothed by K-of-M hysteresis into the `MonitorReport` the loop acts on.*

Owns real-time measurement of per-field RTT/loss/throughput, per-switch status derivation, baseline
capture, and exogenous-shift detection. Computes `ProgrammableSwitch.status` from observation
(replacing hardcoded tables). It passes signals to the loop but does **not** decide adaptation,
deploy, or write to the KG directly.

**Does:**
- Passive-first: throughput from root-namespace veth counters (never perturbed by tunes), RTT/loss from drone-namespace ping, liveness from process + thrift reachability.
- K-of-M hysteresis per flow so noise can't trigger false adaptation (one probe = counters around a ping burst; window = M probes).
- Status derivation: Failed → Standby → Active / Degraded, with a `path_sla_ok` verdict post-hysteresis.
- Exogenous-shift detection (`qdisc_shifted`): baseline-vs-current `tc` on unmanaged links → signals "reroute, don't roll back."
- Unreachable ping = 10 s RTT + 100% loss; system-fault only on s1 Failed OR both relays Failed.

**Interface.** `runtime/monitors/pipeline.py` (`HysteresisTracker`, `derive_switch_status`, `qdisc_shifted`, `assemble_report`), `network_monitor.py` (`NetworkMonitor`, `NodeSampler`), `system_monitor.py`.
**Gotcha.** Unreachable ping reads as a **violation**, not healthy; a dead switch's empty counter dict is falsy-but-valid (code uses `is None`, not `or`); missing port / degenerate `dt<=0` yields rate 0.0 to avoid crashes.
[Full card → monitors.md](monitors.md)

### Post-Deploy Evaluator
*Attributes the post-deploy `MonitorReport` through a 6-stage ladder to a terminal Verdict (healthy/marginal/rollback/system_fault) + optional EscalationTicket.*

Owns post-deploy attribution and the commit gate: is the system sound? was SLA met? was a
regression *caused by our change* (rollback) or *the environment* (adapt)? commit or escalate? It
delegates adaptation mechanics to the adapt engine and owns no monitoring, mutation, or KG writes.

**Does:**
- Six-stage ladder: system sound → SLA met → caused regression (rollback) → in-envelope fix (adapt) → displaced harm (adapt) → headroom-based commit.
- `REGRESSION_EPS` (0.02) deadband prevents spurious rollbacks from monitor jitter; the exogenous-shift guard attributes environment-coincident regressions outward (adapt, not rollback).
- Tier-2 LLM rule synthesis forces a **marginal** verdict regardless of headroom; escalations carry full context (metrics, envelope, trace, reason).

**Interface.** `evaluate(spec, report, ctx) -> EvalResult`; `EvalContext(deployer, monitor, gate, budget, last_good, …, regen_proposer, timestamp)`; `commit_outcome(headroom, tier_reached) -> str`.
**Gotcha.** Tier-2 adaptations always yield marginal verdicts (signaling inherent precariousness); the stage-3 `REGRESSION_EPS` deadband is critical to keep measurement noise from triggering false rollbacks.
[Full card → evaluator.md](evaluator.md)

### Unified Adaptation Engine
*Cost-ordered tier ladder (tune → reroute → regen) that hill-climbs to `target_sla_met` AND `no_displaced_harm`, or escalates.*

Owns in-envelope recovery: diagnose the worst violation, propose one candidate per tier, gate it,
re-observe. Serves both the target-failing and neighbour-harmed callers. It does **not** own
attribution, live mutation, gate rules, or the Tier-2 LLM itself (calls an injected `regen_proposer`
seam).

**Does:**
- Three tiers: Tier 0 tune (deterministic knob step), Tier 1 reroute (deterministic primary↔backup flip), Tier 2 regen (LLM, stubbed by default).
- Guards: `dominates` rejects when the target fails or harm count rises; `improves` requires strict progress on some axis.
- Shared episode budget: `spend(1)` only on *applied* attempts; gate-rejects/capacity-skips bounded by a finite tried-set for termination.
- `regen_proposer` defaults to `None` (escalate-sooner fail-safe): an unavailable/hanging LLM degrades capability, not safety.

**Interface.** `runtime/adapt.py` (`Budget`, `diagnose`, `propose`, `dominates`, `improves`, `adapt`); `runtime/regen/proposer.py` (`RegenProposer`, the Tier-2 seam).
**Gotcha.** A headroom rise with the target margin intentionally dropping is correct (harm-relief gives back the target's grab to lift a neighbour); grids are integer-indexed to avoid float drift.
[Full card → adaptation-engine.md](adaptation-engine.md)

### KG Client
*The runtime's Neo4j read/write boundary: reads strategic bounds, writes runtime records, translates field ids.*

Owns the runtime's entire Neo4j surface. Reads strategic state (`SFCTemplate` bounds, per-field
requirements) and writes runtime state **only** (switch status, verdicts, tickets, snapshots). It
does **not** touch the SFC library or planner-owned topology, nor compute the action space (that's
runtime-owned via config).

**Does:**
- Builds envelopes by merging the strictest bounds (min latency / max bandwidth) from `SFCTemplate` + `AgriculturalField` with the runtime-owned action space (legal tiers/paths/knob ranges).
- Writes verdicts, escalation tickets, baseline snapshots, last-known-good — all tagged `updated_by='runtime-manager'`.
- Idempotent writes via `MERGE` on `correlation_id`/`timestamp`/key to survive transient-error retries.
- Translates field ids at the boundary (`F<n>` runtime ↔ `Field_<n>` KG); lazy-loads the driver in prod, accepts an injected driver in tests.

**Interface.** `KGClient.connect()`, `build_envelope(sfc, target_field)`, `read_field_requirements()`, `read_last_good(sfc)`, `write_{switch_status,verdict,escalation,baseline,last_good}()` (`runtime/kg_client.py`).
**Gotcha.** `read_last_good` returns a raw dict keyed by SFC name only (two deployments share one slot) and isn't consumed by the live loop today — the deployer holds its own `ConfigSnapshot` last-good.
[Full card → kg-client.md](kg-client.md)

---

## Slow Planner — outer loop (LLM-driven)

### Slow Planner
*The outer-loop KG-RAG orchestrator: read Neo4j topology + run history → prompt the decision LLM → validate/fallback → compile a deployable artifact.*

Owns the end-to-end planning pipeline: assemble the LLM input from KG + telemetry + history, invoke
the decision model, enforce the decision contract, and compile the chosen SFC/policy into a concrete
deployment artifact (P4 JSON + rule-file paths). It does **not** own the decision model, the inner
loop, or the KG's contents.

**Does:**
- Assembles input from a Neo4j topology snapshot, candidate SFC/P4 policy sets, normalized results CSV, and the top-3 historical runs by similarity.
- Invokes the model, parses to a 6-key decision, validates against the contract; swaps to a deterministic rule-based fallback on invalid output.
- Compiles the validated decision into an `experiment_config` artifact (P4 JSON + rule files) with artifact verification before deploy.
- Derives `orchestration_constraints` (allowed SFC ids / policy types / paths / relays) as the single source of truth for the validator and the grammar.

**Interface.** `build_runtime_input_object()`, `run_pipeline()` → `{raw_model_output, decision, validation, experiment_config, artifact_check}`, `compile_llm_decision_to_experiment_config()` (`llm_orchestrator/orchestrate.py` + `kg_context`, `history_retriever`, `prompt_builder`, `validator`, `policy_compiler`).
**Gotcha.** The fallback is deterministic rule-based (not the LLM) and is masked when constrained decoding is on; topology/candidates come live from Neo4j every call — an **empty KG silently degrades** to `fallback_candidate_actions()`.
[Full card → slow-planner.md](slow-planner.md)

### Slow-Planner Analytics
*Aggregates the Runtime Manager's verdict/escalation history into reliability signals that feed back into planning.*

A read-only consumer of the inner loop's KG records (`Verdict`/`EscalationTicket`). Owns aggregation
and reporting; produces the per-SFC reliability signal that closes the loop via `runtime_feedback`.
It makes no decisions, writes no verdicts, and never touches the network.

**Does:**
- Reads `Verdict` (outcome/tier/headroom/timestamp) + `EscalationTicket` (sfc/reason) nodes.
- Aggregates outcome distribution, adapt-tier histogram, commit-headroom stats, escalations by SFC/reason.
- Best-effort per-SFC attribution (join on `EscalationTicket.sfc` or parse `plan-<sfc>-<hex>` correlation ids).
- Exports `collect()`, `reliability_summary()`, `feedback_for_planner()` (closed-loop entry, toggled by `NETPROMPT_PLANNER_FEEDBACK`), `format_report()`.

**Interface.** `analytics.py`: `collect(run_cypher)`, `reliability_summary(stats)`, `feedback_for_planner(run_cypher)`, `format_report(stats)`; CLI with `--json` / `--write-kg`.
**Gotcha.** Verdicts have no SFC field, so attribution is best-effort; escalation-rate ≠ SFC-unsuitability (it can mean unachievable SLA bounds), and the current model doesn't yet exploit the feedback signal.
[Full card → planner-analytics.md](planner-analytics.md)

---

## The two models

### Planner LLM — the decision model
*Fine-tuned Qwen2.5-1.5B-Instruct + LoRA that emits 6-key orchestration decisions under grammar-constrained decoding (`cuda:0`).*

A subsystem of the Slow Planner that owns decision-model inference: load base + LoRA, build the
constrained-decoding logits processor, generate, and parse to a decision dict. It does **not**
assemble input, validate, run fallback, compile artifacts, or regenerate P4 rules.

**Does:**
- Emits the 6-key decision: `selected_sfc`, `selected_policy`, `selected_path`, `selected_relay`, `priority_class`, `deployment_mode`.
- Grammar-constrained decoding (per-request GBNF) makes the output validator-valid by construction.
- Greedy deterministic decoding (`do_sample=False`); promoted default adapter is `final_adapter_retrained` (original preserved as rollback).
- Retrained via `train_decision_lora.py` distilling a balanced dataset labeled by the fallback oracle.

**Interface.** `LLMOrchestrator(...).load() / .generate_raw(input_object)`, `parse_model_decision`, `build_decision_gbnf`, `validate_generated_decision`, `fallback_decision` (`llm_orchestrator/llm_runner.py`).
**Gotcha.** Constrained decoding is ON by default and a grammar-valid decision bypasses the fallback entirely; `build_decision_gbnf` raises → unconstrained when there are no candidate pairs/relays (an unseeded KG silently loses the constraint). Known gap: generic/telemetry-only missions default to BandwidthOptimized.
[Full card → planner-llm.md](planner-llm.md)

### Regen LLM — the Tier-2 code model
*Grammar-constrained Qwen2.5-Coder-1.5B that generates candidate BMv2 table rules (`cuda:1`).*

A Tier-2 leaf component that owns **only** candidate generation for P4 table rules: given a
violation, envelope, and current table state, it proposes replacement `table_modify`/`table_add`
lines. It does **not** own the adapt ladder, gate, apply/observe, or escalation — those stay with
the Runtime Manager. It is a fail-safe leaf: unavailable/erroring degrades to escalate-sooner.

**Does:**
- Lazy-loads the pinned FP16 model on first call with an `IncrementalGrammarConstraint` logits processor; resets parser state per call.
- Grammar built from the same constants the gate enforces (deliberately narrower — no `table_delete`).
- `RegenProposer` is stateless across calls, K-capped at `REGEN_MAX_REJECTS=3`; generation exceptions are caught and treated as rejected attempts (never crashes).
- Defaults to a stub client (escalates immediately); the real client is wired only by `soak --with-regen` and the M7 live tests.

**Interface.** `LocalHFClient(...).generate(prompt)`, `StubLLMClient`, `RegenProposer(client, table_state_fn, switch, max_rejects).__call__(diag, env, state, exclude) -> Candidate|None` (`runtime/regen/`).
**Gotcha.** Default-stubbed in `run_episode`/`run_from_planner` (Tier 2 escalates with no model wired); the pinned `REGEN_REVISION` is part of the reproducibility manifest — changing it shifts the manifest hash; vLLM is unsupported on the P100.
[Full card → regen-llm.md](regen-llm.md)

---

*See also: the [architecture review](../architecture-review/01-architecture.html) (whole-system
design, KG hub, workflow, novelty), and the [Component Reference README](README.md) for the full
per-component cards.*
