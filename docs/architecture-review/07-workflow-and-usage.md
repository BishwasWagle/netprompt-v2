# 7 · Workflow & Usage

Two parts: **Part A — Workflow** explains how the components fit together and
interact (the holistic process and the handoff-by-handoff detail); **Part B —
Usage** is the concrete commands to run it. For *what* each piece is, see
[01-architecture.md](01-architecture.md) and [06-knowledge-graph.md](06-knowledge-graph.md);
for exhaustive flags see [`docs/guides/usage.md`](../guides/usage.md) (Part B mirrors it).

---
---

# Part A · Workflow — how the system works

## A.1 The holistic process (plain-English walkthrough)

One mission, from intent to verdict, told as a story — no commands, just who does
what and why:

1. **A mission arrives** (e.g. *emergency_alert_relay*, with telemetry: bandwidth,
   delay, loss, battery). It needs a Service Function Chain (SFC) deployed on the
   drone network within an SLA.
2. **The slow planner reads the world from the KG.** It pulls the live switch
   topology (which relays are active/standby) and the menu of legal SFC↔policy
   pairs, plus a per-SFC reliability signal from past runs.
3. **The planner decides *which* SFC.** A small LLM, *constrained* so it can only
   emit a legal, complete decision, picks the SFC, policy, path, and relay, and
   writes a deployment artifact. It then **stops** — it never touches the network.
4. **The runtime takes over.** A thin adapter normalizes the artifact into a typed
   `DeploymentSpec` (reconstructing the exact per-switch rule files). The runtime
   *never* re-decides the SFC — that was the planner's job.
5. **The gate vets the binding before anything is installed.** A refused binding
   becomes a `rejected` verdict and the network is never touched.
6. **The runtime deploys and observes.** It installs the rules on the live
   P4/BMv2 switches, then watches a *window* of the network — pinging flows,
   reading switch counters and queue state — and condenses it into one
   `MonitorReport`.
7. **The evaluator attributes and decides.** A 6-stage ladder asks, in order: is
   the fabric healthy? is the SLA met? if not, did *our* change cause it (roll back)
   or is the environment at fault (adapt)? It hands off to the adapt engine when a
   fix is worth trying.
8. **The adapt engine hill-climbs within the SFC's envelope.** Cheapest first: tune
   a queue knob (tier 0), reroute to the backup relay (tier 1), or — last resort —
   ask a *second* LLM to regenerate P4 rules (tier 2). Every candidate is gated,
   applied, re-measured, and kept only if it strictly improves without regressing.
9. **The episode ends in a verdict** — `healthy`, `marginal`, `rollback`,
   `escalated`, `system_fault`, or `rejected` — written to the KG along with
   snapshots and the live switch status.
10. **The loop closes.** Those verdicts are aggregated into a per-SFC reliability
    signal that flows back into the planner's next decision through the KG. The
    fast loop's measured reality conditions the slow loop's future choices.

## A.2 The components and who they talk to

| Component | Role | Talks to |
|---|---|---|
| `llm_orchestrator` (planner) | decide which SFC → artifact | **reads** KG; **calls** decision LLM; **reads** analytics |
| decision LLM (Qwen-1.5B+LoRA, cuda:0) | pick the 6-key decision | driven by `llm_runner` under a KG-derived grammar |
| `planner_adapter` | artifact → `DeploymentSpec` | reads the artifact + (optionally) `kg_client.build_envelope` |
| `RuntimeManager` | orchestrate one episode | `gate`, `monitor`, `evaluator`→`adapt`, `deployer`, `kg_client` |
| `gate` (ValidationGate) | sound pre-deploy + per-candidate checks | called by `RuntimeManager` and `adapt` |
| `monitor` (NetworkMonitor) | live network → `MonitorReport` | samplers → `node_runner` → BMv2/Mininet; writes status via `kg_client` |
| `evaluator` | 6-stage attribution + commit ladder | calls `adapt`, `deployer.rollback` |
| `adapt` engine | tiered hill-climb to the goal | `gate`, `deployer`, `monitor`, optional `regen` |
| `regen` (RegenProposer) | tier-2 P4-rule regeneration | reads `deployer.table_state`; calls regen LLM (cuda:1) |
| `deployer` | install/rollback rules + QoS | `node_runner` → switches/hosts |
| `node_runner` | execute CLI/host commands | `simple_switch_CLI` (thrift), `mnexec` (namespaces) |
| `kg_client` | runtime's Neo4j boundary | reads bounds, writes verdicts/status |
| `analytics` | verdicts → per-SFC reliability | reads KG; feeds the planner |
| Neo4j **KG** | the shared coordination hub | planner reads; runtime writes (see [06](06-knowledge-graph.md)) |

## A.3 Component interaction map

```
  mission ─▶ ┌───────────────────────── SLOW PLANNER (llm_orchestrator) ─────────────────────────┐
             │ kg_context ──reads──▶ NEO4J KG ◀──reads── history_retriever (KG-RAG over results) │
             │     │  topology+candidates        analytics.feedback_for_planner ◀── Verdict hist  │
             │     ▼                                              │                                │
             │ prompt_builder (orchestration_constraints) ◀───────┘  runtime_feedback              │
             │     │                 │                                                              │
             │     ▼                 ▼                                                              │
             │ decision_grammar   llm_runner (Qwen-1.5B+LoRA, cuda:0, GBNF-constrained) ──▶ validator│
             │   (GBNF from KG)                                                  └─ fallback oracle  │
             │     └──────────────────────▶ policy_compiler ─▶ artifact_checker ─▶ ARTIFACT (json)  │
             └───────────────────────────────────────────────────────────┬───────────────────────-─┘
                                                                          ▼
                                            planner_adapter.spec_from_artifact ─▶ DeploymentSpec
                                                                          │
  ┌───────────────────────────── RUNTIME MANAGER.run_episode ────────────▼──────────────────────────┐
  │  gate.check_binding ──reject──▶ Verdict("rejected")  (no deploy)                                  │
  │  deployer.table_state() ─(live tables)─┐                                                          │
  │  monitor.observe_window() ─▶ MonitorReport ──▶ evaluator.evaluate (6-stage ladder)                │
  │        │                                              │  rung4/5                                  │
  │        │ samplers→node_runner→BMv2                    ▼                                           │
  │        │  ping/counters/qdisc/liveness        adapt(): diagnose ─▶ propose(tier0/1/2) ─▶ gate.check│
  │        │                                              │                    │ tier2: regen (cuda:1)│
  │        │                                       deployer.apply ─▶ node_runner ─▶ switches/hosts    │
  │        │                                       monitor.observe_window (re-measure) ─▶ keep/rollback│
  │        ▼                                              ▼                                           │
  │  kg_client.write_switch_status / write_verdict / write_baseline / write_escalation / write_last_good│
  └──────────────────────────────────────────────┬──────────────────────────────────────────────────┘
                                                  ▼ (best-effort, idempotent MERGE)
                                              NEO4J KG ──────▶ (back to analytics → planner)  [closed loop]
```

## A.4 The interactions in detail (handoff by handoff)

**1 · Planner ↔ KG (read-only).** `Neo4jContextClient.get_topology_snapshot()`
returns switches normalized into access/primary/backup relays and partitions them
into `active/standby/unavailable` by their live `status`;
`get_candidate_sfc_policy_set()` returns the legal `(sfc, policy, p4_program,
path)` tuples from `(:SFCTemplate)-[:REALIZED_BY_P4_POLICY]->(:P4PolicyMapping)`.
The planner never writes the KG.

**2 · Inside the planner (one pipeline).** `kg_context` (world) + `history_retriever`
(KG-RAG: top-k similar past runs from the results CSV) + `analytics.feedback_for_planner`
(per-SFC reliability) feed `prompt_builder.build_llm_input_object`, which also emits
`orchestration_constraints` — the single source of truth shared by the next two
steps. `decision_grammar.build_decision_gbnf` turns those constraints into a GBNF;
`llm_runner` decodes the LLM *under* that grammar (so output is a complete, legal
6-key decision); `validator.validate_generated_decision` is then a by-construction
pass (or, if constrained decoding is off and output is invalid, the deterministic
`fallback_decision` oracle takes over). `policy_compiler` maps the decision to a P4
program + per-switch rule paths; `artifact_checker` confirms the files exist; the
artifact is written.

**3 · Artifact → `DeploymentSpec`.** `planner_adapter.spec_from_artifact` takes the
planner's *decisions* (SFC name + `policy_type`, verbatim) and **reconstructs** the
canonical per-switch binding (`s1←access_rules`, `s2←relay_rules`, `s3←backup_rules`)
from the SFC's canonical filenames — it does *not* trust the artifact's literal rule
paths. It normalizes the field id to `F<n>` and resolves the `Envelope`
(`kg.build_envelope` = strictest SFC ∧ field bounds + the runtime-owned action
space). Output: a typed `DeploymentSpec`.

**4 · `RuntimeManager.run_episode` — the conductor.** In order: (a)
`gate.check_binding(spec)` — a refusal short-circuits to `Verdict("rejected")` with
no deploy; (b) refresh `current_tables` from `deployer.table_state()` (the live
switch view the gate's L2 needs); (c) build an `EvalContext` carrying a **fresh
`Budget(N)`** — the per-episode budget; (d) `monitor.observe_window()` →
`MonitorReport`; (e) `evaluate(spec, report, ctx)`; (f) on a `healthy`/`marginal`
commit, promote `deployer.capture()` to `last_good` and call `monitor.rebaseline()`.
KG writes happen along the way, best-effort.

**5 · Monitor ↔ samplers ↔ `node_runner` ↔ BMv2.** `observe_window` runs `M`
probes; each probe reads switch-port counters (sysfs), pings each flow from its
drone namespace (`node_runner.run_host` → `mnexec ping`), and samples queue
discipline (`tc`). `SystemMonitor.liveness()` checks each switch's process (`pgrep`)
and thrift socket. The pure functions in `pipeline.py` (hysteresis, throughput
aggregation, exogenous-shift detection, status derivation) assemble the
`MonitorReport`. Switch *status* here is computed from observation — it is later
written to the KG, where the planner reads it back (the closed loop).

**6 · evaluator ↔ adapt ↔ deployer ↔ gate.** The 6-stage ladder calls `adapt()` at
rung 4 (target failing) or rung 5 (neighbor harmed). One adapt step:
`diagnose(report)` → `Diagnosis`; `propose(diag, tier, env, deployer.state, tried,
regen_proposer)` → `Candidate`; `gate.check(cand, env, live_tables)` →
`GateResult`; `deployer.capture()` (pre-image); `deployer.apply(cand)` (which drives
`node_runner`); `budget.spend()`; `monitor.observe_window()` re-measures; then
`goal`/`dominates`/`improves` decide keep-or-`deployer.rollback(pre)`. The gate is
the authority on every candidate; the budget bounds applied attempts.

**7 · adapt ↔ regen ↔ regen LLM (tier 2).** When tiers 0/1 are exhausted,
`propose` calls the `regen_proposer` (`RegenProposer.__call__`): it reads the live
`table_state` (not the KG), builds a prompt showing what's installed *now*, and
calls the code LLM (`LocalHFClient`, Qwen-Coder on `cuda:1`, GBNF-constrained) to
regenerate P4 rules. The result is *still* gated before anything is applied; a
K-cap (`REGEN_MAX_REJECTS`) and the fail-safe (`None` → escalate) bound it. By
default this seam is stubbed (`regen_proposer=None`).

**8 · deployer ↔ `node_runner` ↔ switches/hosts.** The deployer is pure
command/parse/state: it builds `table_add/modify/delete` and `tc` commands,
`node_runner` executes them (`simple_switch_CLI` over thrift; `mnexec` into host
namespaces), and the deployer parses the CLI output back into `TableEntry` state.
Rollback restores entry *semantics* (key/action/args), re-parsing fresh handles.

**9 · `kg_client` ↔ Neo4j (write side).** `RuntimeManager` calls `write_switch_status`,
`write_verdict`, `write_escalation`, `write_baseline`, `write_last_good` — all
best-effort (a Neo4j blip can never abort an episode), idempotent `MERGE`, tagged
`updated_by='runtime-manager'`, with complex payloads JSON-encoded via `jsonable`.

**10 · analytics ↔ KG ↔ planner (closing the loop).** `analytics` reads the
`Verdict`/`EscalationTicket` history, aggregates a per-SFC reliability signal, and
`feedback_for_planner` folds it into the planner's next `input_object.runtime_feedback`
(disable with `NETPROMPT_PLANNER_FEEDBACK=0`).

## A.5 The lifecycle in six stages

```
                                    ┌──────────── prerequisites ────────────┐
                                    │ source gpu-node.env · venv · Neo4j up  │
                                    └────────────────────────────────────────┘
                                                     │
 (1) SEED KG ──▶ (2) LAUNCH TESTBED ──▶ (3) PLAN (slow loop) ──▶ (4) EPISODE (fast loop) ──▶ (5) VERIFY
  generate_kg       launch_network         orchestrate              run_from_planner            KG records
  + seed_kg         (resident BMv2)        → plan.json artifact     deploy→observe→adapt→commit  + teardown
      ▲                                                             /rollback/escalate           │
      └───────────────── (6) analytics: per-SFC reliability ◀── Verdict/EscalationTicket ─────────┘
```

| # | Stage | Component | What happens |
|---|-------|-----------|--------------|
| 0 | Prereqs | `gpu-node.env`, venv, Neo4j | env (KG creds, devices, adapter), FP16/no-4bit on P100 |
| 1 | Seed KG | `generate_kg.py` → `seed_kg` | build the strategic graph JSON, MERGE it in non-destructively |
| 2 | Launch testbed | `launch_network.py` | bring up the **resident** 3-switch BMv2/P4 fabric (s1/s2/s3) |
| 3 | Plan | `llm_orchestrator.orchestrate` | decision LLM picks SFC/policy/path → writes `plan.json` |
| 4 | Episode | `run_from_planner` (or `run_episode`) | normalize → gate → deploy → observe → adapt → commit/rollback/escalate |
| 5 | Verify | KG / pytest | inspect `Verdict`/snapshots; tear the testbed down |
| 6 | Feedback | `llm_orchestrator.analytics` | aggregate verdicts → per-SFC reliability → next plan's context |

(1)–(2) are one-time setup per environment; (3)–(5) are the per-mission run; (6)
closes the loop.

---
---

# Part B · Usage — how to run it

## B.1 Prerequisites (every session)

```bash
cd ~/Run-time-Manager
source deploy/gpu-node/gpu-node.env     # NETPROMPT_ROOT/TREE_ROOT, KG creds, devices, promoted adapter
# venv: ~/netprompt-venv  ·  Neo4j: bolt://localhost:7687 (neo4j / netprompt123) must be running
```

> **P100 note:** FP16, no bitsandbytes. `gpu-node.env` sets `NETPROMPT_LLM_USE_4BIT=0`
> + `NETPROMPT_LLM_DEVICE_MAP=cuda:0`; sourcing the env is enough (the orchestrator
> defers to it when `--4bit`/`--device-map` are omitted).

## B.2 End-to-end run

**(1) Seed the KG** — strategic graph (planner topology + runtime bounds), idempotent.
`generate_kg.py` (re)builds the JSON; `seed_kg` MERGEs it without wiping runtime records:

```bash
cd ~/Run-time-Manager/controller && ~/netprompt-venv/bin/python generate_kg.py   # -> drone_sfc_kg.json
cd ~/Run-time-Manager && ~/netprompt-venv/bin/python -m runtime.tools.seed_kg     # MERGE, keeps runtime records
```

**(2) Launch the resident testbed** — *match the P4 program to the SFC you'll deploy*.
The deployer installs table *rules*, not the P4 *program* (fixed at switch launch), so a
mismatch fails the install handle-count check:

```bash
sudo -E python3 runtime/tools/launch_network.py \
  --p4-json "$NETPROMPT_ROOT/compiled_p4/reliable_relay.json" \
  --rules-dir "$NETPROMPT_ROOT/p4_multihop_rules" \
  --sfc reliable_relay --scenario baseline      # resident; hold it in the background
# readiness: echo 'table_dump forward_table' | simple_switch_CLI --thrift-port 9090   # rows on 9090/9091/9092
```

**(3) Plan** — the slow planner reads the KG, decides, and writes a deployable artifact.
The situation is the `--mission/--bandwidth/--delay/--loss/--battery` flags:

```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python -m llm_orchestrator.orchestrate \
  --mission emergency_alert_relay --bandwidth 20 --delay 25 --loss 2 --battery 80 \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --device-map cuda:0 --no-4bit --output /tmp/plan.json
# inspect: selected_sfc + decision.llm_parse_status (parsed_json = the LLM's decision was used)
```

**(4) Run an episode** driven by that artifact. Start with the dry run (gate check, no
network), then the live deploy:

```bash
cd ~/Run-time-Manager
# dry (no testbed): normalize -> gate check only, no deploy
~/netprompt-venv/bin/python -m runtime.tools.run_from_planner \
  --artifact /tmp/plan.json --target-field F2 --no-kg
# live: deploy + one episode + write verdict/snapshots to the KG (needs testbed + traffic)
sudo -E ~/netprompt-venv/bin/python -m runtime.tools.run_from_planner \
  --artifact /tmp/plan.json --target-field F2 --deploy
```

Representative traffic for a live episode (detached so it survives the run) — an `iperf`
server on the edge and UDP flows from the drones, so the monitor sees real throughput:

```bash
EDGE=$(pgrep -f 'mininet:edge')
sudo setsid mnexec -a "$EDGE" iperf -s -u </dev/null >/dev/null 2>&1 &
for d in d4 d5 d6 d7 d8 d9 d10; do P=$(pgrep -f "mininet:$d\b")
  sudo setsid mnexec -a "$P" iperf -u -c 10.0.0.100 -b 5M -t 600 </dev/null >/dev/null 2>&1 & done
```

**(5) Verify + tear down.** The run writes KG records keyed by `correlation_id`
(`Verdict`, `EscalationTicket`, `BaselineSnapshot`, `ProgrammableSwitch.status`); teardown
uses bracketed patterns so `pkill` won't match itself:

```bash
sudo pkill -9 -f "[l]aunch_network"; sudo pkill -9 "[s]imple_switch"; sudo pkill -f "[i]perf"; sudo mn -c
```

## B.3 Three ways to run an episode — pick by intent

| Mode | Command | When to use |
|------|---------|-------------|
| **Scenario-driven** | `runtime.tools.run_episode --scenario path_quality_fault --monitor {model\|real}` | exercise the loop against a fixture; `model` = instant scenario monitor (no testbed), `real` = live monitor over the fabric. Scenarios: `healthy`, `causal_regression`, `path_quality_fault`, `contention_harm_with_knob`, `contention_harm_no_knob`, `ddil` |
| **Planner-driven** | `runtime.tools.run_from_planner --artifact plan.json --target-field F2 --deploy` | true end-to-end from a real planner artifact (§B.2) |
| **Soak** | `runtime.tools.soak --minutes 60 --kill-every 20 --kill s3 [--with-regen]` | repeated episodes + injected switch kills + watchdog recovery; `--with-regen` wires the real Tier-2 model |

> **No LLM required for the inner loop.** By default Tier-2 is stubbed
> (`regen_proposer=None`), so the RM escalates instead of loading a model — the
> deterministic tiers (tune/reroute) exercise the whole loop. The regen model is
> wired only by `soak --with-regen` and the M7 live tests.

## B.4 The two models

**Decision model** (planner, `Qwen2.5-1.5B-Instruct` + LoRA, `cuda:0`). Constrained
decoding (default ON) forces a complete, valid 6-key decision; the adapter is a one-flag swap:

```bash
export NETPROMPT_LLM_CONSTRAINED=1          # =0 disables -> invalid output falls to the rule oracle
# promote / roll back the adapter (default set in gpu-node.env):
NETPROMPT_LLM_ADAPTER=".../final_adapter_retrained"   # promoted (mission-appropriate on known taxonomy)
NETPROMPT_LLM_ADAPTER=".../final_adapter"             # rollback (mode-collapsed under constrained-on)
# per-run override: orchestrate.py --adapter-path .../final_adapter
# retrain (oracle distillation, ~55 min fp32 on a P100):
~/netprompt-venv/bin/python train_decision_lora.py --neo4j-password netprompt123 \
  --out netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained --per-class 160 --epochs 3 --device cuda:0
```

**Tier-2 regen model** (`Qwen2.5-Coder-1.5B-Instruct`, `cuda:1`) — not a CLI; it's the
`RegenProposer` plugged into the adapt engine, pinned for reproducibility in `gpu-node.env`
(`NETPROMPT_REGEN_REVISION=2e1fd397…`). Tools around it:

```bash
~/netprompt-venv/bin/python -m runtime.tools.regen_manifest    # reproducibility manifest (model+rev, decoding, hashes)
~/netprompt-venv/bin/python -m runtime.tools.regen_compare --models <0.5B,1.5B,3B>   # the paper's comparison table
```

See [05 §7](05-evolution-from-original.md) for the retrain story and
[06 §6.5](06-knowledge-graph.md) for which model reads the KG vs live switch state.

## B.5 Testing & verification

```bash
# offline, fast — the green baseline every change must preserve:
~/netprompt-venv/bin/python -m pytest tests/unit -q                       # 212 passed (~0.3s)
# node-gated integration (needs the resident testbed + traffic):
sudo -E env NETPROMPT_TREE_ROOT="$NETPROMPT_ROOT" NETPROMPT_KG_URI=bolt://localhost:7687 \
  NETPROMPT_KG_PASS=netprompt123 ~/netprompt-venv/bin/python \
  -m pytest tests/integration/test_m6_acceptance_node.py -v
```

- **Unit tier (212 tests / 454 asserts):** off-testbed against `FakeDeployer`/`FakeMonitor`/
  `ScriptedRunner` + 6 scenario fixtures — no `sudo`, no Mininet.
- **Integration tier (16 tests / 62 asserts):** all `skipif(not _testbed_up())`; drives the
  real BMv2 fabric and asserts on live switch state / traffic.

## B.6 The closed feedback loop

```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python -m llm_orchestrator.analytics \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123   # report; --json / --write-kg
```

Aggregates the runtime's `Verdict`/`EscalationTicket` history into a per-SFC reliability
signal, folded into every planner decision as `input_object.runtime_feedback` (disable with
`NETPROMPT_PLANNER_FEEDBACK=0`). The path is wired and robust; the current 1.5B model doesn't
yet *exploit* it (advisory) — see [05 §7.6](05-evolution-from-original.md).

## B.7 Operational gotchas (the ones that bite)

1. **Match the P4 program to the SFC** at launch, or deploy fails with
   `N table_add lines but N-1 handles` (the program is fixed at switch launch; only rules
   are installed live).
2. **FP16 / no-4bit on the P100** — `gpu-node.env` handles it; don't force bitsandbytes.
3. **Live episodes need traffic** — start the detached `iperf` flows (§B.2) or the monitor
   reads an idle/unreachable network.
4. **Seed with `seed_kg`, not the legacy `import_kg.py`** — the latter does `DETACH DELETE`
   and wipes runtime records; the former is idempotent `MERGE`.
5. **Teardown uses bracketed patterns** so `pkill` doesn't match itself; finish with `sudo mn -c`.

## B.8 Command cheat-sheet

| Goal | Command |
|------|---------|
| Seed KG | `python -m runtime.tools.seed_kg` |
| Launch testbed | `sudo -E python3 runtime/tools/launch_network.py --p4-json … --rules-dir … --sfc … --scenario …` |
| Plan | `python -m llm_orchestrator.orchestrate --mission … --output plan.json` |
| Episode (planner) | `sudo -E python -m runtime.tools.run_from_planner --artifact plan.json --target-field F2 --deploy` |
| Episode (scenario) | `sudo -E python -m runtime.tools.run_episode --scenario … --monitor real` |
| Soak | `sudo -E python -m runtime.tools.soak --minutes 60 --kill-every 20 --kill s3 [--with-regen]` |
| Unit tests | `python -m pytest tests/unit -q` |
| Analytics | `python -m llm_orchestrator.analytics --neo4j-password netprompt123` |
| Retrain LoRA | `python train_decision_lora.py --neo4j-password netprompt123 --out … --per-class 160 --epochs 3` |
| Regen manifest / compare | `python -m runtime.tools.regen_manifest` · `… regen_compare --models …` |
| Teardown | `sudo pkill -9 -f "[l]aunch_network"; sudo pkill -9 "[s]imple_switch"; sudo mn -c` |

---

## Key Takeaways

- **Two parts: workflow (how it fits together) vs usage (how to run it).** Part A is the conceptual model — the holistic 10-step process, a component-interaction map, and a handoff-by-handoff breakdown of who calls whom and what data crosses each boundary. Part B is the concrete, ground-truthed commands.

- **The components coordinate through narrow, typed handoffs — not a tangle.** The planner reads the KG and emits an artifact; `planner_adapter` turns it into a `DeploymentSpec`; `RuntimeManager` conducts `gate → monitor → evaluator → adapt → deployer`, each a single clear interface. The only shared mutable state is the KG, and even there the read/write ownership is split (see [06](06-knowledge-graph.md)).

- **The fast loop is a strict call hierarchy with re-measurement at its core.** `evaluator` calls `adapt`, which loops `diagnose → propose → gate → deployer.apply → monitor.observe_window → keep-or-rollback`. Every applied candidate is re-measured before it's kept, and the gate authorizes every change — that's what makes a weak Tier-2 LLM safe to include.

- **The loop genuinely closes through the KG.** Monitor-computed switch status and `Verdict`/`EscalationTicket` history are written to the KG and read back by the planner (relay availability + per-SFC reliability), so the slow loop is conditioned on the fast loop's measured reality.

- **The everyday entry points: `seed_kg` → `launch_network` → `orchestrate` → `run_from_planner --deploy`, gated by `pytest tests/unit -q` (212 green).** Three ways to run an episode (scenario / planner / soak) cover off-testbed exercise, true end-to-end, and stress; the inner loop needs no LLM by default.

- **`docs/guides/usage.md` remains the canonical flag reference.** This document frames the workflow and the why; `usage.md` carries the exhaustive flags and per-LLM detail.
