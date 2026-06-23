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
`orchestrate` writes an artifact → `run_from_planner --deploy` runs a live episode →
`Verdict`/snapshots to the KG → `analytics` folds reliability back into the next
decision. This mirrors the **existing comparative harness**
(`run_comparative_experiments.sh`): **3 arms × 6 scenarios.**

**Independent variables:**
- **Arm:** `baseline_static` (fixed LowLatency, no adaptation) · `baseline_rule_based`
  (rule selection, no in-envelope adaptation) · **proposed NetPrompt** (LLM decision +
  tiered runtime adaptation).
- **Scenario:** `baseline · low_latency · congestion · battery_depletion · relay_failure · ddil`
  (the harness's netem-driven conditions).

**Dependent variables:** end-to-end per-field RTT / loss / throughput vs requirement;
**SLA-attainment / commit rate**; orchestration overhead (decision + deploy + adapt);
SFC-selection appropriateness; outcome distribution; (closed-loop) whether
`runtime_feedback` reflects prior verdicts.

**Protocol.**
```bash
# one scenario, proposed arm (repeat per arm × scenario; baselines via the published scripts):
sudo mn -c
sudo -E python3 runtime/tools/launch_network.py --p4-json <matching .json> --rules-dir <rules> --sfc <sfc> --scenario <s>   # resident
# start representative iperf load (validity §7); then:
python -m llm_orchestrator.orchestrate --mission <M> ... --output /tmp/plan.json
sudo -E python -m runtime.tools.run_from_planner --artifact /tmp/plan.json --target-field <F> --deploy --correlation-id e3-<arm>-<s>
# baselines: network/.../baselines/baseline_{static,rule_based}_experiment.py --scenario <s>
# aggregate: python -m llm_orchestrator.analytics   (+ the comparative parse scripts for RTT/loss/throughput tables)
```

**Baseline / comparison.** The headline table is **proposed vs static vs rule-based**
per scenario on the measured metrics — the same shape as the milestone-II
`comparative_results/` (RTT, packet loss, throughput, orchestration overhead, SFC
decisions). The proposed arm's advantage is expected on the *adaptive* scenarios
(relay_failure → reroute-and-commit; congestion → tune; ddil → honest escalate)
where the static arm cannot recover.

**Success criteria.** On the calibrated fabric, the proposed arm **commits**
(healthy/marginal) on scenarios the static arm fails, with comparable or better
measured RTT/loss on the adaptive scenarios, at a bounded orchestration overhead;
`ddil` correctly **escalates** in all arms (it is genuinely infeasible — escalating
is the *correct* outcome, not a loss). Expect a **marginal-biased** mix (thin
headroom by design).

**Can claim:** end-to-end, the KG-grounded LLM planner + tiered runtime keeps flows
in-SLA on the calibrated fabric better than non-adaptive baselines, with a working
(if not yet exploited) feedback loop. **Cannot claim** (validity §8): goodput
guarantees; sub-10% loss fidelity; that the feedback loop *improves* decisions;
cross-host reproducibility without pinned revisions.

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

*See also: [experiment-validity.md](experiment-validity.md) (the controls + go/no-go),
[../architecture-review/05-evolution-from-original.md](../architecture-review/05-evolution-from-original.md)
(the baselines + retrain provenance), [planner-lora-eval.md](../planner/planner-lora-eval.md)
(the E1 probe matrix), `runtime/fixtures.py` (the E2 scenario models),
`network/.../run_comparative_experiments.sh` (the E3 harness shape).*
