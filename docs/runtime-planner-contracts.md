# Runtime ⇄ Planner Contracts (for sign-off)

**From:** Kevin (Runtime Manager / inner loop) · **To:** Kiran (Planner / outer loop)
**Purpose:** agree the *only two* messages that cross our boundary, plus the KG read/write split, so we can build independently. Everything else in [runtime-manager-design.md](runtime-manager-design.md) is runtime-internal.

The single source of truth once agreed: [`runtime/contracts.py`](../runtime/contracts.py) — both sides import it (or mirror its field names exactly).

---

## 1. Handoff IN — how a deployment starts

Two options; **we propose Option 1 (thin)** so the planner surface stays minimal:

**Option 1 — thin handoff (recommended).** The planner only provides:

```
{ sfc: str, target_field: str, correlation_id: str }
```

e.g. `{sfc: "ReliableRelaySFC", target_field: "Field_2", correlation_id: "..."}`. The runtime then resolves the **binding** itself (via the existing mappers) and derives the **envelope** (SLA bounds from `SFCTemplate` + the field's requirements in the KG; action space is runtime-owned). The planner never has to know about rule files, knob ranges, or paths.

**Option 2 — full spec.** The planner supplies the complete `DeploymentSpec` (binding + envelope). More planner work, only worth it if the planner wants to constrain *how* an SFC is bound.

Either way, the internal normalized form on our side is:

```python
@dataclass
class DeploymentSpec:
    sfc: str                # already chosen by the planner — runtime never selects
    binding: dict           # resolved from the mappers (runtime, under Option 1)
    envelope: Envelope      # bounds from KG + runtime-owned action space
    correlation_id: str     # ties deploy → monitor → verdict → escalation
    target_field: str       # which field this deployment serves
```

**Transport question for Kiran:** how does the handoff arrive — (a) planner writes a `PlannedDeployment` node to the KG and runtime polls/subscribes, or (b) direct invocation (CLI/SSH call into the runtime)? We're agnostic; (a) fits the KG-hub architecture best.

## 2. Handoff OUT — escalation + verdicts

**EscalationTicket** (written to the KG when no in-envelope fix exists; consumed by the planner at the human checkpoint):

```python
@dataclass
class EscalationTicket:
    correlation_id: str
    sfc: str
    observed: dict          # the metrics that violated (per-field)
    envelope: Envelope      # what the SFC promised
    trace: list             # every adaptation attempt: tier, candidate, pre/post, why it failed
    reason: str             # "all tiers exhausted" | "budget spent" | "no harm-free config"
```

The `trace` is the payload that matters: it shows the planner exactly what the runtime already tried, so re-planning starts informed. **The runtime never proposes a replacement SFC** — that's the planner's decision.

**Verdicts** (every terminal outcome, aggregated by Results+analytics for the planner's learning loop):

```python
@dataclass
class Verdict:
    correlation_id: str
    outcome: str            # "healthy" | "marginal" | "rollback" | "escalated"
                            #   | "system_fault" | "rejected" (gate-refused binding,
                            #     never deployed)
    tier_reached: int       # 0 none/tune, 1 reroute, 2 LLM-regen — how hard the runtime worked
    headroom: float         # margin at commit (thin margin ⇒ "marginal")
    trace: list
    timestamp: str
```

**`marginal` is a planner signal:** the deploy is committed and running, but precarious (thin margin, or it took LLM rule-regeneration to hold SLA). No action required from the runtime side; the planner may want to re-plan proactively on the slow loop.

## 3. KG split (who writes what)

| | Planner (Kiran) | Runtime (Kevin) |
|---|---|---|
| **Writes** | `SFCTemplate` library, intent/targets, `PlannedDeployment` (if Option 1a) | `ProgrammableSwitch.status` (monitor-computed), `BaselineSnapshot`, `ConfigSnapshot`/`LastKnownGood`, `Verdict`, `EscalationTicket` |
| **Reads** | `Verdict` aggregates, `EscalationTicket` | `SFCTemplate` bounds, `AgriculturalField` requirements, own snapshots |
| **Never touches** | runtime state above | the SFC library; `priority` is read for context but never acted on — when meeting the target would harm a neighbor and no harm-free config exists, we escalate rather than pick a victim |

Note: the runtime **replaces** the hardcoded switch-status writes currently in `update_topology_state.py` with monitor-computed status — `kg_path_selector.py`-style status→path reads keep working unchanged.

## 3b. Update after reviewing your merged work (milestone-II-latest)

Your `llm_orchestrator` already emits `llm_generated_experiment_config.json` with
`selected_sfc`, `selected_policy`, `selected_path`, `deployment_mode`, and the
`p4_json`/`access_rules`/`relay_rules`/`backup_rules` paths — that **is**
essentially the Option-2 handoff. So the integration is even thinner than
proposed: a small adapter maps your artifact into our `DeploymentSpec`
(`selected_sfc → sfc`, the four file paths → `binding`; we derive `envelope`
ourselves). **Two fields we'd ask you to add to the artifact:**
`correlation_id` (any unique run id) and `target_field` (which field the
mission serves). Everything else is covered.

Also noted: you serve **Qwen2.5-1.5B-Instruct + LoRA via transformers/PEFT**
(`requirements-runtime.txt`). Our Tier-2 adaptation model is the same family —
we'll align our serving on the same stack so the node carries one inference
setup.

## 4. Implementation status (runtime side — already built)

So you can see the shapes are real, not proposals: all types in this note exist in
[`runtime/contracts.py`](../runtime/contracts.py) and are exercised by 129 unit
tests on branch `Run-time-Manager`. Facts that affect your side:

- **KG persistence format:** Verdicts, tickets, and snapshots are written with
  nested payloads (traces, envelopes) as **JSON-string properties** (Neo4j can't
  nest maps). Your aggregation/consumption side should `json.loads` the `trace`,
  `observed`, `envelope`, and `payload` properties. Every runtime write carries
  `updated_by: 'runtime-manager'`.
- **Envelope composition (runtime-side, no planner action needed):** bounds =
  strictest of `SFCTemplate` and the target field's KG values; the action space
  (legal tiers/paths/knobs) comes from a runtime-owned registry — consistent
  with §3's rule that the planner never authors it.
- **EscalationTicket nodes** are created with `status: 'open'`; we suggest the
  planner sets it to `'consumed'` (or similar) when re-planning — open to
  whatever convention you prefer (relates to question 3 below).

## 5. Questions needing your answer

1. **Option 1 (thin) or Option 2 (full) for the handoff?** We recommend 1.
2. **Transport:** KG node + poll, or direct invocation?
3. **Escalation acknowledgment:** after we write an `EscalationTicket` and stop, does the planner signal "re-planned, here's the new deployment" purely by issuing a fresh handoff (new `correlation_id`)? (We assume yes — keeps the seam stateless.)
4. Any extra fields you want on `Verdict` for the learning loop (e.g., per-attempt token/cost stats for the LLM tier)?
