# Runtime ⇄ Planner Contracts (now an internal integration spec)

> **Ownership update (2026-06-17).** Kiran's network node expired; **we now own the
> entire stack** (orchestrator/planner code, KG, testbed, runtime). This was a
> two-party contract "for sign-off"; it is now our **internal integration spec**.
> The "must not modify planner files" boundary (design §1.2) is lifted — we maintain
> the orchestrator too — and every question that was "for Kiran" is ours to decide
> (resolutions tracked in §6). The local Neo4j (`bolt://localhost:7687`) is the only
> KG. The original two-party framing is kept below for provenance.

**From:** Kevin (Runtime Manager / inner loop) · **To:** ~~Kiran (Planner / outer loop)~~ now self.
**Purpose:** the *two* messages that cross the planner↔runtime seam, plus the KG read/write split. Everything else in [runtime-manager-design.md](runtime-manager-design.md) is runtime-internal.

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
[`runtime/contracts.py`](../runtime/contracts.py) and are exercised by the unit
suite on branch `Run-time-Manager`. Facts that affect your side:

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

---

## 6. Integration status — runtime-side handoff adapter BUILT (2026-06-16)

The inbound handoff is wired on the runtime side, consuming your existing artifact
**without touching any planner code** (your decision kernel — `validator` /
`prompt_builder` / `kg_context` / `policy_compiler` — is untouched). What landed:

- [`runtime/planner_adapter.py`](../runtime/planner_adapter.py) — turns your
  `outputs/llm_generated_experiment_config.json` into our normalized
  `DeploymentSpec`. Maps `selected_sfc → sfc`, the four rule/JSON paths → `binding`,
  derives the `envelope` (from the KG, action space runtime-owned), and threads the
  two runtime-supplied fields (below). 15 unit tests against synthetic *and* the
  real committed artifact; full suite **199 green**.
- [`runtime/tools/run_from_planner.py`](../runtime/tools/run_from_planner.py) — CLI.
  Default is a **dry** run (normalize → gate `check_binding`, no deploy); `--no-kg`
  gives a fully offline structural check. Verified end-to-end on this node against
  the real artifact (`ReliableRelaySFC`, backup path) → gate **PASS**.

**Decisions we adopted on our side (you can still steer 1–3):**

1. **Transport = file** for now: we read the artifact you already write. It's the
   lowest-coupling option and needs zero changes from you. We can move to a KG
   `PlannedDeployment` node + poll later (question 2) — purely a runtime-side swap
   behind the same adapter.
2. **Two fields are runtime-supplied at invocation**, because the artifact doesn't
   carry them (confirmed by grep — neither `correlation_id` nor `target_field`
   appears anywhere in your output):
   - `correlation_id` — we auto-generate `plan-<sfc>-<8hex>` if you don't supply one;
   - `target_field` — passed in by whoever invokes the runtime (your artifact has
     `mission_type` / `priority_class` / `connected_drone_ids`, but no single field).
   **If you can add either field to the artifact, we'll consume it directly** — until
   then the runtime fills them in. This is the one place a small planner-side add
   would tighten the contract.
3. **Canonical per-switch binding (was "path remap"; see D4' below).** We take only
   the planner's *decisions* from the artifact — the SFC and the path — and
   reconstruct the per-switch rule files from the SFC's canonical names under the
   runtime tree root. `policy_type` is preserved **verbatim** (the deployer reads
   `"…backup…"` out of it to select the active path).

### 6a. Resolved (post-ownership-change, 2026-06-17)

- **Relay-slot semantics — RESOLVED (D4').** The artifact labels rule files by the
  *active relay* (a backup deployment sets `relay_rules` **and** `backup_rules` to
  the *s3* file). But `deploy()` installs all three switches every time
  (s1←access, s2←relay, s3←backup) and picks the path from `policy_type` — so the
  s2 slot must hold the *s2* file, else s2 is loaded with s3's rules (a real bug,
  not inert: s2's table is installed regardless of active path). `planner_adapter`
  now **reconstructs canonical paths** (`<prefix>_s{1,2,3}_rules.txt`), so
  `relay_rules → s2` file even when the artifact says s3. Verified on the real
  artifact: `relay_rules` → `…_s2_rules.txt`, `backup_rules` → `…_s3_rules.txt`,
  path = backup, gate **PASS**.
- **KG populated — DONE (D12).** The local Neo4j is seeded with the strategic nodes
  (`SFCTemplate` × 4, `AgriculturalField` × 5, drones). `kg_client.build_envelope`
  now resolves a real envelope live (e.g. `ReliableRelaySFC`/`Field_2` →
  `lat≤50ms, bw≥20mbps`) instead of `LookupError`.
- **Non-destructive strategic seed — DONE (D12b).**
  [`runtime/tools/seed_kg.py`](../runtime/tools/seed_kg.py) MERGEs the strategic graph
  idempotently (KG creds from `runtime.config`, not hardcoded) — unlike the legacy
  `controller/import_kg.py`, which opens with `MATCH (n) DETACH DELETE n` and would
  wipe runtime records. Proven: a planted `Verdict` canary survives a re-seed.
  `--reset-strategic` does a clean reseed that deletes only strategic labels (never
  `Verdict`/snapshots/`ProgrammableSwitch`).
- **Field-id convention.** Planner/KG ids are `Field_<n>`; the runtime-internal form
  (host_map + monitor requirement keys) is `F<n>`. The adapter normalizes
  `target_field` to `F<n>` so the spec is runtime-canonical (`build_envelope` maps it
  back to `Field_<n>` for KG reads).
- **`--deploy` driver — WIRED (D9).**
  [`run_from_planner.py`](../runtime/tools/run_from_planner.py) `--deploy` builds the
  spec (KG envelope), pre-flight-gates the binding, then reuses the M5/M6-tested
  `build_and_run` to deploy + run one episode + write the verdict/snapshots to the KG.
- **Live end-to-end episode — DONE (D11, 2026-06-17).** Ran the real planner artifact
  (`ReliableRelaySFC`, backup) through `--deploy` on the resident testbed (3× BMv2 +
  iperf load). Full chain executed: artifact → KG envelope (`lat≤50/bw≥20`) → gate
  PASS → deploy(backup) → live monitor → adapt ladder (tune→reroute→regen) → verdict.
  **KG records written** under the run's `correlation_id`: `Verdict`
  (outcome=`escalated`, tier_reached=2), `EscalationTicket` (reason="all tiers
  exhausted"), `BaselineSnapshot`, and monitor-computed `ProgrammableSwitch.status`
  (s1=Degraded, s2=Standby, s3=Degraded — the live replacement for the hardcoded
  `update_topology_state.py`). The **escalation is the expected outcome**: F2's ≤50 ms
  bound is borderline-unachievable on the ~52 ms backup path (the documented
  bounds-vs-hardware gap, design §6), ReliableRelay has no tune knobs, and Tier-2
  regen is stubbed in this assembly — so the ladder legitimately exhausts. The point
  proven is the **integration**, not a healthy verdict.
  *Live-only fix this run surfaced:* the KG carries all 5 fields but a partial testbed
  realizes only the host_map's fields, so `_run_live` restricts the monitor's
  requirements to host_map fields (else it probes a hostless field → KeyError).
- **Escalation-ack — RESOLVED.** Stateless fresh-handoff: a new deployment is just a
  new artifact with a new `correlation_id`. No ack node.
- **`target_field` / `correlation_id`** — stay runtime-supplied at invocation. Since
  we own the orchestrator now, adding them to the artifact is an optional polish.

**Still open:** a multi-handoff integration soak (D14 — several artifacts back-to-back,
KG-write resilience); and the outer-loop learning/verdict-consumption (deferred — not
needed for the runtime to run real deployments). The single-episode chain (D1–D12, D9,
**D11**) is complete and live-verified.
