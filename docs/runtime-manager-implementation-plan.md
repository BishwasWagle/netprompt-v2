# Runtime Manager — Implementation Plan

**Companion to:** [runtime-manager-design.md](runtime-manager-design.md) (v2). Section references (§) point there.
**Owner:** Kevin. **Planner boundary:** unchanged — nothing here touches Kiran's files or responsibilities.

> **Status (2026-06-12, branch `Run-time-Manager`, 129 unit tests green).**
> The **local track is complete**: M1 ✅ · M2 ✅ · M3 ✅ · M4 ◐ (command/parse
> layer + Deployer protocol done; real Runner remains) · M5 ◐ (computation
> pipeline + kg_client done; sampler wiring remains) · M0 ✅ kit built, not yet
> run · M7 ◐ (regen grammar/prompt/proposer + stub model done; real serving
> remains) · M6 ☐ node-bound · M-K ☐ drafted, awaiting Kiran's sign-off.
> Everything remaining requires the Chameleon nodes.

---

## 1. Guiding constraints (these shape the whole plan)

1. **Two execution environments.** The dev box is Windows; the testbed (Mininet/BMv2/thrift, `tc`, namespaces) lives on the Chameleon nodes. So the plan splits into a **local track** (pure logic: contracts, gate, evaluator, adapt engine — TDD'd against fakes) and a **node track** (deployer, topology holder, monitors, integration). The local track is most of the code and all of the algorithmic risk; the node track is where physical assumptions get verified.
2. **Deterministic before LLM.** Tiers 0–1, the evaluator, and the gate are fully testable with zero model dependency. Tier-2 regen lands last (M6) and the system must already be complete and demonstrable without it.
3. **Fakes-first.** The "dry-run mode" from design Phase 5 is pulled **forward**: `FakeDeployer` + `FakeMonitor` + scenario fixtures are built early (M1) so the engine and evaluator are exercised locally long before the node is touched.
4. **Spike before build.** The §10.7 unknowns (handles, live `table_modify`, namespace access, counter channel) are resolved in a half-day node spike (M0) *before* writing the deployer — its API depends on the answers.
5. **Contracts to Kiran early, in parallel.** `DeploymentSpec`/`EscalationTicket` sign-off (M-K) is independent of all build milestones; start it immediately so it never blocks integration.

---

## 2. Target repo layout

New top-level package (deployed to `network-node` via git; KG reachable at `bolt://controller-node:7687` per existing scripts). Branch: `milestone-III-runtime` off `milestone-II`.

As built (✅ = exists and tested; ◇ = node-half still to be written):

```
runtime/
├── contracts.py            ✅ all dataclasses + goal() + jsonable() + Deployer protocol
├── config.py               ✅ ports/MACs, hysteresis, budget, knob steps + TC templates,
│                           #   SFC_ACTION_SPACE registry, KG endpoint
├── kg_client.py            ✅ injectable driver; envelope composition (strictest bounds
│                           #   + runtime action space); JSON-payload writes      (§8)
├── gate.py                 ✅ ValidationGate L0–L2 (+ L3 on-node)                 (§11)
├── deployer.py             ✅ command/parse layer over an injectable Runner;
│                           #   ◇ real Runner (subprocess/SSH + mnexec) at M4-node (§10)
├── monitors/
│   ├── pipeline.py         ✅ hysteresis, counter math, parsers, exogenous shift,
│   │                       #   status derivation, assemble_report          (§5.2–5.7)
│   └── network_monitor.py  ◇ M5-node: sampler wiring over pipeline.py
├── evaluator.py            ✅ the 6-stage ladder + EvalContext              (§6)
├── adapt.py                ✅ diagnose/propose/engine + regen_proposer seam (§7)
├── regen/                  ✅ grammar.py (GBNF from gate constants), prompt.py
│                           #   (verbatim TEMPLATE), llm_client.py (stub),
│                           #   proposer.py; ◇ real serving client at M7
├── runtime_manager.py      ✅ episode loop (§7.6)
├── fakes.py                ✅ FakeDeployer, FakeMonitor, ScriptedRunner
└── tools/
    ├── launch_network.py   ✅ persistent topology holder (runs on node)    (§10.6)
    ├── spike_s0.sh         ✅ scripted M0 checks
    └── spike_s0.md         ✅ protocol + results table (to fill on node)
tests/
├── unit/                   ✅ 129 tests across 9 files — local, no testbed
└── integration/            ◇ node — against live BMv2 (M4-node onward)
```

---

## 3. Milestones

Effort tags: **S** ≈ a focused session, **M** ≈ a few sessions, **L** ≈ a week-scale chunk. Order is dependency order; M-K runs in parallel from day one.

### M-K · Contract sign-off with Kiran (parallel track) — S
Extract §9 (`DeploymentSpec`, `EscalationTicket`, `Envelope`) + the §8 KG read/write split into a short contracts note; agree on field names, KG node labels, and who creates `DeploymentSpec`. **Exit:** both sides agree; `contracts.py` reflects the agreed schema.
*Status: note drafted ([runtime-planner-contracts.md](runtime-planner-contracts.md)); awaiting Kiran's answers to its §4 questions.*

### M0 · On-node spike — S (needs node access; everything in §10.7)
Bring up the existing multihop topology, pause before teardown, and verify by hand:
1. `table_add` handle output format vs `table_dump` (e.g. `echo "table_add forward_table forward 00:00:00:00:00:0b => 11" | simple_switch_CLI --thrift-port 9090` → "Entry has been added with handle N").
2. `table_modify forward_table forward <h> => 12` during a live ping — flip takes effect, no restart.
3. Host-namespace access out-of-process: `mnexec -a <pid> tc qdisc show` / `ip netns exec`.
4. Counter channel decision (§5.2): veth `/sys/class/net/s1-eth*/statistics/` update granularity vs declaring P4 counters. **Pick one.**
5. Port map confirm (s1 port 11→s2, 12→s3); BMv2 stability over ~1h idle + repeated CLI sessions.

**Exit:** `tools/spike_s0.md` records answers; deployer + monitor APIs are unblocked. *If any item fails (e.g., `table_modify` semantics differ), the fallback is `table_delete`+`table_add` — same API, noted in the spike doc.*
*Status: kit built and syntax-checked (`launch_network.py`, `spike_s0.sh`, `spike_s0.md` with a decision-to-code mapping per check); not yet run on the node.*

### M1 · Contracts + fakes + test harness (local) — M
`contracts.py`, `config.py`, `fakes.py`, and **scenario fixtures**: a small library of synthetic `MonitorReport` sequences encoding each scenario (healthy, causal regression, environment regression, contention harm, path-quality fault, ddil-everything-degraded). `FakeDeployer` tracks applied candidates and supports rollback; `FakeMonitor` replays fixture sequences with candidate-dependent branches (e.g., "if rerouted, backup metrics apply").
**Exit:** fixtures replay deterministically under pytest on Windows.
*Status: done. Fixtures also carry per-scenario deployment envelopes + `spec()` builders, and delegate report assembly to the shared `monitors/pipeline.assemble_report` so fixture and production semantics cannot drift.*

### M2 · Gate L0–L2 (local) — S
`gate.py` against `contracts.py`: CLI-syntax parse (L0), envelope bounds (L1), the blackhole/reachability invariant (L2 — edge MAC routable, no orphaned drone MAC, valid ports).
**Exit (unit):** valid binding passes; out-of-range knob, illegal path, entry-deleting regen, and unknown-table candidates each rejected with the right `reason`.
*Status: done (22 tests incl. the L0-passes/L2-catches layering case).*

### M3 · Evaluator + adapt engine (local — the algorithmic core) — L
`evaluator.py` (6-stage ladder incl. rung-1 fix order, rung-3 `AND NOT exogenous_shift`, stage-5 → adapt, stage-6 headroom) and `adapt.py` (diagnose, propose Tiers 0–1, domination guard, hill-climb, shared budget, `tried` set; Tier-2 = stub returning `None`). `runtime_manager.py` episode loop with §7.6 boundary (any commit → re-baseline + budget reset).
**Exit (unit, against fixtures):** every rung routes correctly; the corrected acceptance traces pass —
- *path-quality fault* (relay_failure + ReliableRelaySFC): Tier-1 reroute → GOAL → commit;
- *contention harm with shaping knob* (BandwidthOptimized target starving a neighbor): Tier-0 `tbf rate ↓` steps → harm-free → commit;
- *contention harm without shaping knob*: exhausts → escalate `"no harm-free config"` (correct outcome per §7.3);
- *ddil*: all tiers fail → escalate `"budget spent"` with full trace;
- *environment regression*: `exogenous_shift=True` → **no rollback**, goes to rung 4;
- oscillation: a candidate that regresses is rolled back and never retried; budget resets only on commit.

*Status: done — all traces pass with exact budget/rollback counts. Engine additionally gained the injectable `regen_proposer` seam and prefers a live `deployer.table_state()` for gate L2.*

### M4 · Node track: topology holder + real deployer — M (needs M0)
`tools/launch_network.py` (extract topology from the experiment script; runs resident, never tears down; cleans `/tmp/bmv2-*.ipc` on start) and `deployer.py` (rules via `simple_switch_CLI` to 9090–92 with handle tracking per M0; `tc` via the M0-verified namespace mechanism; `ConfigSnapshot` capture; deterministic rollback).
**Exit (integration):** deploy LowLatency binding → live-flip to backup → rollback → re_push, all on one uninterrupted network, verified by ping continuity.
*Status: local half done — `deployer.py` (builders, parsers, handle tracking, semantic-diff rollback, `table_state()`) over an injectable Runner, with `ScriptedRunner` tests; `launch_network.py` written. Remaining: the real Runner (subprocess/SSH + the C3-verified namespace mechanism) and the integration exit above.*

### M5 · Node track: real monitors + KG client — M
`network_monitor.py` (per-field metrics from the M0 counter channel + namespace pings; hysteresis; baseline capture; harm/headroom/exogenous-shift computation — on the testbed, exogenous shift can be read from `tc qdisc show` on link interfaces we don't manage), `system_monitor.py` (PID/thrift/table checks → status table §5.5), `kg_client.py` (all §8 reads/writes; **replaces `update_topology_state.py`'s hardcoded writes** — that script stops being called by our loop; the file itself is untouched).
**Exit (integration):** live `MonitorReport` matches induced conditions (kill s2 → `Failed`; congest primary → `Degraded`; harm list populates when a field is squeezed); baseline + status visible in Neo4j.
*Status: local half done — `monitors/pipeline.py` (hysteresis, counter math, parsers, exogenous shift, status derivation, the shared `assemble_report`) and `kg_client.py` (injectable driver, envelope composition, JSON-payload writes). Remaining: sampler wiring (real sysfs paths, namespace ping, thrift liveness) + the integration exit above.*

### M6 · End-to-end deterministic system (node) — M
Wire `runtime_manager.py` over real deployer + monitors + KG. Run the three acceptance scenarios from M3 **live**, plus a soak run (repeated episodes over hours — BMv2 stability per M0 item 5).
**Exit:** design-doc Phase-3/4 exit criteria pass on the live testbed **with Tier-2 still stubbed**. This is the paper's deterministic baseline system.
*Status: not started (node-bound; everything it wires is built and unit-tested).*

### M7 · Tier-2 regen + reproducibility (node + GPU) — L
`regen/`: grammar (the two tables × existing actions — resolves open question §13.3), prompt template (violation, table dump, bounds, prior failures), `llm_client.py` against vLLM/llama.cpp serving Qwen-Coder (pinned revision, greedy, constrained). Gate L3 dry-install if M0 showed it's needed. Phase-5 hardening: structured logging of episodes/verdicts, multi-model comparison harness (Qwen vs DeepSeek-Coder vs Granite/StarCoder baseline) for the paper.
**Exit:** a regen candidate flows propose → gate → apply → observe end-to-end; LLM-down test degrades to escalate-sooner (fail-safe §7.4); comparison table generated.
*Status: plumbing done with a stub model — both exit behaviors above already pass locally (`runtime/regen/`: GBNF generated from gate constants and narrower than the gate, verbatim prompt TEMPLATE, stateless K-cap proposer, `StubLLMClient`). Remaining: the real serving client (vLLM/llama.cpp + pinned Qwen-Coder, `gbnf()` as the guided-decoding constraint), gate L3 if the spike shows it's needed, and the multi-model comparison harness.*

---

## 4. Test strategy

| Layer | Where | What |
|---|---|---|
| Unit | local (Windows, pytest) | gate cases; hysteresis machine; ladder routing per rung; engine traces incl. guards, budget, episode reset; contract (de)serialization |
| Fixture acceptance | local | the six M3 scenario traces — these are the *specification* of behavior and run in CI forever |
| Integration | node | M0 checklist (scripted where possible); M4/M5/M6 exits; fault injection via `tools/inject.py` (kill switch process, `tc`-degrade a link, congest a field) |
| Soak | node | M6: repeated episodes, hours-scale, no BMv2 restart |
| Repro | node+GPU | M7: pinned model, greedy decoding — same inputs → same candidate, twice |

The fixture set doubles as the **paper's evaluation matrix**: each fixture = one row (scenario × SFC × expected verdict/tier).

---

## 5. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Veth counters too coarse / slow for loss measurement | Med | M0 item 4 decides early; fallback = declare P4 counters (mechanical P4 edit + recompile — observability only, not SFC semantics) |
| `table_modify` semantics differ from expectation | Low | M0 item 2; fallback `table_delete`+`table_add` (brief in-flight gap — measure it) |
| BMv2 instability over long runs | Med | M0 item 5 + M6 soak; watchdog in `launch_network.py` restarting a dead switch = a rung-1 system fault the loop already handles (nice demo, actually) |
| Namespace access (`mnexec`) unavailable out-of-process | Low | Fallback: resident `launch_network.py` exposes a tiny local command socket for `tc` ops |
| GPU/serving availability on controller-node | Med | M7 is last and optional-for-baseline; M6 is a complete deterministic system either way |
| Contract drift with Kiran | Med | M-K starts now; `contracts.py` is the single source both sides import |
| Scenario fixtures diverge from real testbed behavior | Med | M6 runs the same scenarios live; divergences feed back into fixtures (and are findings for the paper) |

---

## 6. Definition of done (runtime side of Milestone III)

1. The three live acceptance scenarios pass end-to-end on the persistent testbed (M6).
2. Every terminal outcome writes a `Verdict`/`EscalationTicket` + trace to the KG, and switch status in the KG is monitor-computed — `SCENARIO_STATE` is no longer in the loop.
3. Tier-2 regen demonstrably proposes, is gate-checked, and deploys at least one recovered episode — and the system demonstrably survives the LLM being unavailable (M7).
4. Reproducibility kit: pinned model revision + serving stack, greedy constrained decoding, fixture matrix, soak log — enough for a reviewer to re-run.
