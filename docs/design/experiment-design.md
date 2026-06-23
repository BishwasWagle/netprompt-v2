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

**Adopted from the KRONOS draft (§6).** E1 absorbs three of the draft's planner-side
artifacts, *re-run under these controls*: the Fig. 6 **confusion matrix** becomes E1's
set-A accuracy presentation; the §V.D **adversarial-robustness probes** (misleading
advisory · stale KG · conflicting telemetry · noisy topology · format-shift) become an
E1 robustness sub-study on the known taxonomy; and the Table V **KG-reasoning
scalability** sweep (10–200 synthetic drones) becomes an E1 decision-latency adjunct.
See §6.1 for the guardrails and §6 for the values we do *not* import.

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

**Adopted from the KRONOS draft (§6).** E3 takes over the draft's **topology-equivalent
control** (all arms on identical relay path + forwarding, to separate orchestration effect
from topology-induced latency — Table VI), adds the **NoKG ablation** as a fourth arm
(Table VII), and reports the **control-plane timing breakdown** (SFC-select / KG-update /
writeback — Table VIII) as the overhead DV. RTT is the headline measured dimension;
throughput is shown only as offered load and loss only at coarse bounds — see §6.3 for
the columns we remove/reframe.

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
> to BandwidthOptimized) — [planner-lora-eval §2–3](../planner/planner-lora-eval.md). So
> either the KRONOS numbers came from a **different model trained on condition labels** (then
> the set A/B/C taxonomy in E1 must be re-pinned to *that* model before importing any of its
> values), or the condition→SFC decisions came from the **deterministic oracle** (then they
> must be labeled as such, not "emerged from KG reasoning"). **Verify which — against the
> actual training set — before importing any Table IV / Fig. 6 number.** Until then we adopt
> the *structure* of these experiments, never the *values*.

### 6.1 Adopt — bring over into E1/E2/E3

| Draft artifact | Maps to | How we adopt it | Guardrail (validity §) |
|---|---|---|---|
| **Table IV** — KG-driven SFC + path decision per scenario | E1 + E3 (decision provenance) | the per-scenario `selected_sfc` / path / policy provenance table | label by set A; attribute condition-only rows to the rule oracle, or to a model *verified* on those labels (§3) |
| **Table V** — KG-reasoning scalability, 10–200 synthetic drones (4.37→7.80 ms) | **new E1 scalability adjunct** | a synthetic-graph query-latency sweep; cheap, needs no fabric | mark **synthetic**; report query latency only — never an SLA/performance claim |
| **Table VI** — topology-equivalent RTT/loss/throughput, 3 arms × 6 scenarios | E3 headline | the 3-arm × 6-scenario shape **and** the *topology-equivalent control* (all arms on identical relay/forwarding) | RTT is the honest dimension; throughput = offered load; coarse loss only (§2) |
| **Table VII** — KG ablation (full vs NoKG) | E3 ablation arm | a NoKG arm isolating the KG's contribution to resilience | qualitative/coarse resilience, not precise loss % (§2.2) |
| **Table VIII** — control-plane timing (SFC 1.00 s / KG 0.51 s / writeback 0.54 s) | E3 overhead DV | the orchestration-overhead breakdown | report LLM ≈1 s vs rule ≈µs honestly (metric dict) |
| **Fig. 6 + §V.D(1)** — held-out confusion matrix, 4 SFC classes | E1 set-A accuracy | the confusion-matrix presentation of decision accuracy | "same-distribution held-out" = the *known taxonomy*; this is set A, **not** generalization (§3) |
| **§V.D(2)** — adversarial robustness (misleading advisory · stale KG · conflicting telemetry · noisy topology · format-shift) | **new E1 robustness sub-study** | perturb the context, check the set-A decision still holds | robustness of the *known* decision; format-validity is a constrained-decoding **guarantee check**, not a quality metric (metric dict) |

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
