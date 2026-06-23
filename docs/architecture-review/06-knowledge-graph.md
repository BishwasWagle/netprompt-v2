# 6 · The Knowledge Graph — the System's Coordination Hub

The Neo4j Knowledge Graph is the **shared substrate** that ties the two loops and
their models together: the slow LLM planner and the deterministic Runtime Manager
never call each other directly — they coordinate **through the KG**. This document
covers what the KG holds, how each side reads and writes it, how the models use
it, the improvements over Bishwas & Kiran's original KG, and the novelty of the
multi-model design that the KG makes possible.

Companion: [05-evolution-from-original.md](05-evolution-from-original.md) (§3 KG)
· `docs/components/kg-client.md` · `docs/planner/planner-design.md` §5.

> Every claim is grounded in a repo file. KG size and type counts were verified
> against `controller/drone_sfc_kg.json` / `generate_kg.py`.

---

## 6.1 The KG at a glance

A single Neo4j graph generated offline by `controller/generate_kg.py` into
`controller/drone_sfc_kg.json` — **29 nodes, 38 relationships**:

| Node type | Count | Owner | Read by |
|---|---|---|---|
| `Drone` | 10 | strategic (seed) | planner |
| `AgriculturalField` | 5 | strategic (seed) | planner + runtime (`build_envelope`) |
| `SFCTemplate` | 4 | strategic (seed) | planner + runtime |
| `P4PolicyMapping` | 4 | strategic (seed) | planner (candidate set) |
| `ProgrammableSwitch` | 3 | **shared** — seed sets `role`, runtime writes `status` | planner (`allowed_relays`) |
| `CentralController` / `ProgrammableNetworkNode` / `EdgeComputeNode` | 1 each | strategic (seed) | planner topology |
| `Verdict` / `EscalationTicket` / `BaselineSnapshot` / `LastKnownGood` | runtime-authored | **runtime only** | analytics → planner |

Key relations: `REALIZED_BY_P4_POLICY` (4, SFC→policy — the candidate set),
`PRIMARY_PATH`/`BACKUP_PATH`/`CONNECTED_TO_EDGE` (forwarding topology),
`CONNECTED_TO` (19), `ASSIGNED_TO` (10).

The graph splits cleanly into two ownership zones, and that split is the whole
design:

```
        ┌──────────────── STRATEGIC (seeded, read-mostly) ─────────────────┐
        │ SFCTemplate · AgriculturalField · P4PolicyMapping · Drone · infra │
        │ ProgrammableSwitch.role                                          │
        └──────────────────────────────────────────────────────────────────┘
        ┌──────────────── RUNTIME (written by the inner loop) ─────────────┐
        │ ProgrammableSwitch.status · Verdict · EscalationTicket           │
        │ BaselineSnapshot · LastKnownGood   (all tagged updated_by=…)      │
        └──────────────────────────────────────────────────────────────────┘
```

---

## 6.2 The KG as the hub — who reads, who writes

```
                          ┌──────────────────────────┐
        reads strategic   │                          │   reads strategic bounds
     + ProgrammableSwitch │        NEO4J  KG          │   (SFCTemplate +
       .status (relays)   │   strategic │ runtime     │    AgriculturalField)
        ┌─────────────────│   zone      │ zone        │─────────────────┐
        │                 └──────────────────────────┘                 │
        ▼                          ▲          ▲                          ▼
 ┌───────────────┐                 │          │                 ┌────────────────┐
 │ SLOW PLANNER  │   candidate set │          │  writes:        │ RUNTIME MANAGER│
 │ (read-only)   │   SFC↔policy    │          │  switch status, │ (sole writer of│
 │ decision LLM  │─────────────────┘          │  verdicts,      │  runtime state)│
 │ cuda:0        │                            │  snapshots ─────│  monitor+loop  │
 └───────┬───────┘                            │                 └────────┬───────┘
         │  runtime_feedback (per-SFC reliability)                       │
         └───────────── analytics reads Verdict/EscalationTicket ◀───────┘
```

- **The planner is read-only on the KG.** It never writes operational state (the
  original's hand-driven status writer is gone — §6.5).
- **The runtime is the sole writer of operational state.** Every write is tagged
  `updated_by='runtime-manager'` and is an idempotent `MERGE`
  (`kg_client.py:120-174`).
- **The loop closes through the KG**, two ways:
  1. the runtime monitor writes live `ProgrammableSwitch.status`, which the
     planner reads as `allowed_relays` on its next decision;
  2. the runtime writes `Verdict`/`EscalationTicket`, which the analytics layer
     aggregates into a per-SFC reliability signal folded back into the planner's
     prompt (`runtime_feedback`).

---

## 6.3 How the SLOW PLANNER uses the KG

The planner's KG read layer is `llm_orchestrator/kg_context.py`
(`Neo4jContextClient`). Two reads drive everything:

**1. `get_topology_snapshot()`** (`kg_context.py:31`) — reads `ProgrammableSwitch`
(`id, role, status, thrift_port, device_id`), forwarding links, and historical
`PathDecision` nodes, then **normalizes**:
- `role` → `access_switch` / `primary_relay` / `backup_relay`;
- `status` → `active_relays` / `standby_relays` / `unavailable_relays`
  (`kg_context.py:114-131`).

That status partition is **how the planner knows which relays it may pick** — and
that status is exactly what the runtime monitor writes. So the planner's relay
availability is the fast loop's live observation, surfaced through the KG.

**2. `get_candidate_sfc_policy_set()`** (`kg_context.py:186`) — reads
`(:SFCTemplate)-[:REALIZED_BY_P4_POLICY]->(:P4PolicyMapping)` → the candidate
`(sfc_id, policy_type, p4_program, path_preference)` set. This is the **KG-driven
action menu**, replacing the old in-code list (falls back to
`fallback_candidate_actions()` only if the KG has none).

These two reads become `orchestration_constraints` in the prompt, and — this is
the important part — that **same** constraint set is the source of truth for
*both* the constrained-decoding grammar and the validator:

```
KG candidate set + allowed relays/paths
        │
        ├─▶ decision_grammar.build_decision_gbnf(...)  → GBNF the LLM decodes under
        └─▶ validator.validate_generated_decision(...) → the contract check
                                       ⇒ grammar-valid ⇒ validator-valid by construction
```

So the planner LLM literally **cannot emit an SFC/policy pair the KG does not
realize** — the symbolic KG bounds the neural decoder (more in §6.7).

---

## 6.4 How the RUNTIME MANAGER uses the KG

The runtime's entire Neo4j surface is `runtime/kg_client.py` — a strict
**read-strategic / write-runtime** boundary (`kg_client.py:1-12`):

**Reads (strategic bounds only):**
- `build_envelope(sfc, target_field)` — the **strictest** of the SFCTemplate's and
  the field's own bounds (`min` latency, `max` bandwidth), fused with the
  *runtime-owned* action space from `config.SFC_ACTION_SPACE`
  (legal tiers/paths/knob ranges). The planner authors none of the
  tiers/paths/knobs (`kg_client.py:58-87`).
- `read_field_requirements()` — one bounds-only `Envelope` per field for the
  monitor's per-flow checks (a field missing a bound is skipped, not built with
  `None`).

**Writes (runtime state only, best-effort, idempotent):**
- `write_switch_status()` — the **monitor-computed** status (replaces the old
  hardcoded `SCENARIO_STATE`);
- `write_verdict()`, `write_escalation()`, `write_baseline()`,
  `write_last_good()` — persisted as JSON-string properties via
  `contracts.jsonable` (Neo4j can't hold nested maps), `MERGE`d on
  `(correlation_id, timestamp)` so a best-effort retry never duplicates.

Field ids are translated at the boundary (`F<n>` ↔ `Field_<n>`,
`kg_client.py:32-39`) so neither side has to know the other's naming.

---

## 6.5 How the three model surfaces relate to the KG

The system has **three decision-making surfaces**, and they consume the KG very
differently — worth being precise about (it is a feature, not an accident):

| Surface | Model | KG usage |
|---|---|---|
| **Decision** (planner) | `Qwen2.5-1.5B-Instruct` + LoRA, `cuda:0` | **Heavy** — KG topology + candidate set become the prompt context *and* the constrained-decoding grammar + validator constraints. |
| **Regen** (runtime tier-2) | `Qwen2.5-Coder-1.5B-Instruct`, `cuda:1` | **None** — it reads the deployer's **live installed `table_state`**, not the KG, because rule regeneration needs the *actual* switch entries, not strategic state (`runtime/regen/proposer.py` `table_state_fn`). |
| **Deterministic loop** | (no model) | Reads strategic bounds for the `Envelope`; writes all runtime state. |

So the KG grounds the *strategic* model (which SFC), while the *tactical* model
(which P4 rules) is grounded in live switch state from the deployer — each model
reads from the source appropriate to its altitude. The analytics signal
(`Verdict`/`EscalationTicket` aggregated per SFC) flows back only to the
**decision** model's context (`orchestrate.py:55-56`).

---

## 6.6 Improvements over the original KG

| Aspect | Original (Bishwas & Kiran) | Current |
|---|---|---|
| Load | `import_kg.py`: `MATCH (n) DETACH DELETE n` — **wipes the whole graph** (runtime records included), hardcoded creds | `seed_kg.py`: idempotent `MERGE`, creds from `config` (env); **preserves** runtime records; `--reset-strategic` deletes only strategic labels and **never** touches `ProgrammableSwitch` |
| Switch status | **Hardcoded** `SCENARIO_STATE` dict keyed by scenario name (`update_topology_state.py`) — circular: the answer was written in | **Monitor-computed** from live observation, written by the runtime |
| Read/write model | Strategic + operational mixed in one graph, no contract; a hand-driven script wrote status + `PathDecision` | Strict **read-strategic / write-runtime** contract; planner read-only, runtime sole operational writer |
| Operational node types | `PathDecision` only | `Verdict`, `EscalationTicket`, `BaselineSnapshot`, `LastKnownGood` (provenance-tagged, idempotent) |
| Envelope | LLM read raw template bounds | `build_envelope` **fuses strictest** SFCTemplate ∧ field bounds + runtime action space at read time |
| SLA bounds | 20 ms / 50 ms (physically unachievable on the fabric → every episode escalated) | recalibrated to **45 ms / 60 ms** against measured ~40 ms primary / ~52 ms backup |
| Candidate set | in-code list | KG-driven `REALIZED_BY_P4_POLICY` set (in-code only as fallback) |

The deepest change is conceptual: the original treated the KG as a **scratchpad**
(wipe-and-reload, write the scenario's answer in, read it back). The current
system treats it as a **system of record with a write contract** — strategic
truth is seeded and durable, operational truth is computed by the loop that
observes it, and the two never clobber each other.

---

## 6.7 Novelty — a KG-coordinated, multi-model, neuro-symbolic control system

> **Calibration.** This section is **design rationale**, not a research-novelty
> claim. An adversarial, prior-art-grounded assessment ([08-novelty.md](08-novelty.md))
> finds that *none* of the items below is individually novel — each maps to
> established work (e.g. the KG-derived grammar is GENRE/PICARD; the KG-as-hub is the
> blackboard pattern). Read it as *how the system is put together and why*, and see
> [08](08-novelty.md) for what is (and isn't) defensible to claim.

What stands out here is not any one model but the **setup**: two specialized LLMs
and a deterministic control loop, each at a different altitude, coordinated entirely
through a shared symbolic KG.

1. **KG-as-blackboard for a two-loop, multi-model system.** The slow planner and
   the fast runtime never call each other; they communicate through the KG hub
   (strategic reads, runtime writes) plus a typed file/contract handoff
   (`DeploymentSpec`/`EscalationTicket`). The KG is the only shared mutable state,
   with a clean ownership split that makes the coupling auditable.

2. **Neuro-symbolic decoding — the grammar *is* the KG.** The decision LLM decodes
   under a GBNF grammar **built per request from the KG's candidate
   `REALIZED_BY_P4_POLICY` set and live relay availability**. The symbolic graph
   doesn't just inform the prompt; it *constrains the token sampler*, so an
   invalid SFC/policy/path/relay is unrepresentable. This is what lets a weak 1.5B
   model be safe to put in the loop: format and legality are guaranteed by
   construction, leaving only *which* legal option to judge.

3. **Two models, two grounding sources, two GPUs.** The **strategic** model
   (decision, `cuda:0`) is grounded in the KG; the **tactical** model (regen,
   `cuda:1`) is grounded in the deployer's live `table_state`. They are pinned to
   separate GPUs so neither contends, and each reads from the source matched to
   its job — strategic state for "which SFC," live switch entries for "which P4
   rules." A clean separation of *knowledge altitude*.

4. **A closed loop through the KG.** The fast loop's measured reality —
   `ProgrammableSwitch.status` and `Verdict`/`EscalationTicket` history — flows
   back into the slow model's next decision via the KG and the analytics signal.
   The strategic model is therefore conditioned on the tactical loop's outcomes,
   not just static topology.

5. **Symbolic backstop under a neural decision.** The deterministic
   `validator.fallback_decision` oracle is the same rule policy that was distilled
   into the retrained LoRA (§7 of the evolution doc) — so the KG-grounded LLM and
   the symbolic oracle agree by construction, and the oracle remains a backstop
   when constrained decoding is off.

> **Honest caveats (the design's current limits).** (a) The analytics feedback
> *path* is wired, but the 1.5B model doesn't yet *exploit* the signal — it's
> advisory until a feedback-aware retrain. (b) The KG is a single Neo4j instance
> on the synchronous write path (no batching/retry today — see
> [02-critical-problems.md](02-critical-problems.md) P3/S1). (c) The decision
> model generalizes on mission *names*, not telemetry, so KG grounding guarantees
> *legal* choices, not always the *best* one. The novelty is the architecture; the
> model quality is the known frontier.

---

## Key Takeaways

- **The KG is the only shared mutable state, with a strict two-zone ownership contract.** The slow LLM planner and the deterministic Runtime Manager never call each other directly — they coordinate entirely through a single Neo4j graph (29 nodes, 38 relationships, generated offline by `controller/generate_kg.py` into `controller/drone_sfc_kg.json`). The planner is read-only on the KG, while the runtime is the sole writer of operational state; every runtime write is tagged `updated_by='runtime-manager'` and uses an idempotent `MERGE` (`kg_client.py:120-174`). Strategic nodes (SFCTemplate, AgriculturalField, Drone, infra) are seeded and durable; runtime nodes (Verdict, EscalationTicket, BaselineSnapshot, LastKnownGood, plus `ProgrammableSwitch.status`) are computed by the loop, and the two never clobber each other.

- **The grammar IS the KG candidate set — symbolic constraints bound the neural decoder.** The planner reads `(:SFCTemplate)-[:REALIZED_BY_P4_POLICY]->(:P4PolicyMapping)` (`kg_context.py:186`) plus live relay availability, and that *same* constraint set feeds both `decision_grammar.build_decision_gbnf(...)` and `validator.validate_generated_decision(...)`. Because the GBNF grammar is built per request from the KG, the LLM literally cannot emit an SFC/policy/path/relay the graph does not realize — grammar-valid implies validator-valid by construction. This is what makes a weak 1.5B model safe to put in the loop: format and legality are guaranteed, leaving only *which* legal option to judge.

- **Three decision surfaces consume the KG very differently, by design.** The **decision** model (Qwen2.5-1.5B-Instruct + LoRA, `cuda:0`) uses the KG heavily — topology and candidate set become both prompt context and decoding constraints. The **regen** tier-2 model (Qwen2.5-Coder-1.5B-Instruct, `cuda:1`) uses the KG **not at all** — it reads the deployer's live installed `table_state` because rule regeneration needs actual switch entries, not strategic state (`runtime/regen/proposer.py`). The **deterministic loop** uses no model, reading strategic bounds for the `Envelope` and writing all runtime state. Each model reads from the source matched to its altitude: strategic for "which SFC," live switch entries for "which P4 rules."

- **The loop closes through the KG in two concrete directions.** The runtime monitor writes live `ProgrammableSwitch.status`, which the planner reads back as `allowed_relays` on its next decision (the planner's relay availability is literally the fast loop's live observation). Separately, the runtime writes `Verdict`/`EscalationTicket`, which the analytics layer aggregates into a per-SFC reliability signal folded back into the planner's prompt as `runtime_feedback` (`orchestrate.py:55-56`). The strategic model is therefore conditioned on the tactical loop's outcomes, not just static topology.

- **The current KG replaced a scratchpad with a system of record.** The original (Bishwas & Kiran) wiped the whole graph on load (`MATCH (n) DETACH DELETE n`) and hardcoded switch status in a `SCENARIO_STATE` dict keyed by scenario name — circular, since the answer was written in. The current `seed_kg.py` uses idempotent `MERGE`, preserves runtime records, and `--reset-strategic` never touches `ProgrammableSwitch`; status is now monitor-computed from live observation. `build_envelope` fuses the strictest of SFCTemplate and field bounds at read time, and SLA bounds were recalibrated from physically unachievable 20/50 ms to 45/60 ms against measured ~40 ms primary / ~52 ms backup latency.

- **The novelty is the architecture, with model quality as the known frontier.** Two specialized LLMs and a deterministic loop, each at a different knowledge altitude, are coordinated entirely through one shared symbolic KG acting as a blackboard, backed by a typed file/contract handoff (`DeploymentSpec`/`EscalationTicket`) and a symbolic backstop (`validator.fallback_decision`, the same policy distilled into the LoRA). The doc is honest about limits: the analytics feedback path is wired but the 1.5B model doesn't yet exploit the signal (advisory until a feedback-aware retrain), the KG is a single Neo4j instance on the synchronous write path with no batching/retry, and the model generalizes on mission names not telemetry — so KG grounding guarantees *legal* choices, not always the *best* one.

---

*Sources: `controller/{generate_kg,import_kg,select_template}.py`,
`controller/drone_sfc_kg.json`, `runtime/kg_client.py`,
`runtime/tools/seed_kg.py`, `runtime/regen/proposer.py`,
`network/milestone-II-latest/netprompt-milestone-II/llm_orchestrator/kg_context.py`,
`docs/components/kg-client.md`, `docs/planner/planner-design.md`. KG size/type counts
verified against `drone_sfc_kg.json`.*
