# Runtime Manager — Implementation Plan

**Companion to:** [runtime-manager-design.md](runtime-manager-design.md) (v2). Section references (§) point there.
**Owner:** Kevin. **Planner boundary:** unchanged — nothing here touches Kiran's files or responsibilities.

> **Status (2026-06-15, branch `Run-time-Manager`, on the Chameleon network-node, 166 unit + 10 integration tests green).**
> Local track complete and **node bring-up complete (M0–M6)**: M1 ✅ · M2 ✅ · M3 ✅ ·
> **M0 ✅ RUN & PASSED** (spike findings folded into the design doc) · **M4 ✅**
> (real `NodeRunner` + Deployer fixes, the full deploy→flip→rollback→re_push exit
> verified live) · **M5 ✅** (real `network_monitor`/`system_monitor` sampler,
> induced-fault exit tests, `kg_client` wired into the loop — all live) ·
> **M6 ✅** (all **3 acceptance scenarios pass LIVE** — reroute-and-commit,
> ddil-escalate, contention-harm-tune-commit; watchdog + soak harness built &
> validated by the **≥1h soak run: 351 episodes, 314 healthy / 37 marginal, 0
> escalations, 17/17 watchdog recoveries from injected s3 kills, 0 errors, 0
> KG-write failures, 0 zombies** — 2026-06-15 on the consolidated GPU node with a
> local Neo4j KG) · **M7 ✅ COMPLETE 2026-06-16** (real GBNF-constrained Qwen-Coder
> serving on cuda:1; live propose→gate→apply→observe + a committed recovered episode,
> LLM-down fail-safe, multi-model comparison table, reproducibility kit — see
> [m7-implementation-plan.md](../regen/m7-implementation-plan.md)) ·
> **M-K (planner integration) ✅ BUILT + LIVE-VERIFIED 2026-06-17..19.** Kiran's node
> expired, so we now own the whole stack (planner + runtime + KG). The slow planner ↔
> runtime handoff is wired and proven end-to-end on hardware — see §8 below and
> [runtime-planner-contracts.md](runtime-planner-contracts.md), [planner-design.md](../planner/planner-design.md),
> [usage.md](../guides/usage.md). The full system (M0–M7 + slow-loop→fast-loop) is demonstrated
> end-to-end on the live testbed.
> See §7 "Node bring-up findings" + design §13 "Known issues & hardening backlog".

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
*Status: **✅ REALIZED + LIVE-VERIFIED (see §8).** Kiran's node expired → we own the whole stack, so the contract is now an internal spec and every open question was decided by us. The handoff adapter is built and the slow→fast loop runs end-to-end on hardware.*

### M0 · On-node spike — S (needs node access; everything in §10.7)
Bring up the existing multihop topology, pause before teardown, and verify by hand:
1. `table_add` handle output format vs `table_dump` (e.g. `echo "table_add forward_table forward 00:00:00:00:00:0b => 11" | simple_switch_CLI --thrift-port 9090` → "Entry has been added with handle N").
2. `table_modify forward_table forward <h> => 12` during a live ping — flip takes effect, no restart.
3. Host-namespace access out-of-process: `mnexec -a <pid> tc qdisc show` / `ip netns exec`.
4. Counter channel decision (§5.2): veth `/sys/class/net/s1-eth*/statistics/` update granularity vs declaring P4 counters. **Pick one.**
5. Port map confirm (s1 port 11→s2, 12→s3); BMv2 stability over ~1h idle + repeated CLI sessions.

**Exit:** `tools/spike_s0.md` records answers; deployer + monitor APIs are unblocked. *If any item fails (e.g., `table_modify` semantics differ), the fallback is `table_delete`+`table_add` — same API, noted in the spike doc.*
*Status: **RUN & PASSED on network-node 2026-06-14** (`spike_s0.md` results filled). Headline finding: the reroute is a **five-part** action, not three (relay-switch entry + edge re-ARP) — folded into design §10.1. Counter channel = veth sysfs (§5.2). `mnexec` confirmed as `run_host`. Versioned handles + `deploy`/`re_push` idempotency (§10.5). One open item: BMv2 crashes under churn → switch watchdog before M6.*

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
*Status: **DONE 2026-06-14.** `runtime/node_runner.py` (`NodeRunner`: `simple_switch_CLI` over thrift + `sudo mnexec -a <pid> sh -c`, anchored pgrep, `RunnerError` on thrift-down/timeout; injectable executor → 11 off-node tests). Deployer gained the multi-switch reroute (`config.RELAY_EDGE`, §10.1), the Model-B qos baseline (`config.SFC_QOS_BASELINE`, §10.2), and idempotent `deploy`/`re_push` (§10.5). The integration exit **passes live** (`tests/integration/test_m4_node_exit.py`): deploy → live flip → rollback → re_push on one uninterrupted network, ping-continuous.*

### M5 · Node track: real monitors + KG client — M
`network_monitor.py` (per-field metrics from the M0 counter channel + namespace pings; hysteresis; baseline capture; harm/headroom/exogenous-shift computation — on the testbed, exogenous shift can be read from `tc qdisc show` on link interfaces we don't manage), `system_monitor.py` (PID/thrift/table checks → status table §5.5), `kg_client.py` (all §8 reads/writes; **replaces `update_topology_state.py`'s hardcoded writes** — that script stops being called by our loop; the file itself is untouched).
**Exit (integration):** live `MonitorReport` matches induced conditions (kill s2 → `Failed`; congest primary → `Degraded`; harm list populates when a field is squeezed); baseline + status visible in Neo4j.
*Status: ◐ **core sampler built + live-smoke'd 2026-06-14.** `monitors/network_monitor.py` (`NetworkMonitor` window loop / hysteresis / status / report assembly over an injectable `sampler`; `NodeSampler` real I/O) + `monitors/system_monitor.py` (process/thrift liveness). `port_map` derived from `host_map`; env read switch-side (Model B); `system_sound` clarified (unsound iff s1 or both relays Failed — §5.5). 10 off-node tests; live smoke produced a real `MonitorReport`. Wired as `--monitor real` in `tools/run_episode.py`. **Induced-fault exit PASSES live** (`tests/integration/test_m5_monitor_node.py`): **kill s2** → `switch_status[s2]=Failed`, system stays sound (reroute-able); **congest primary** → target unmet + s2 `Degraded`; **squeeze a neighbour** → `displaced_harm=[F2]`. Plus `tools/switch_control.py` (the M6 watchdog primitive: kill/restart a single BMv2 + reinstall rules). Designed around the constraints (§6 bounds, §5.2 traffic): achievable bounds, rate-limited UDP, netem-child congestion. **KG wired into the loop** (`RuntimeManager(kg=…)`): switch_status + baseline on the entry observe, verdict always, escalation/last_good on the matching outcomes; field-id translation at the `kg_client` boundary (F1↔Field_1). Validated live against Neo4j (Verdict/Baseline/EscalationTicket/ProgrammableSwitch.status written, then cleaned up). **M5 COMPLETE** — 163 unit + 7 integration green.*

### M6 · End-to-end deterministic system (node) — M
Wire `runtime_manager.py` over real deployer + monitors + KG. Run the three acceptance scenarios from M3 **live**, plus a soak run (repeated episodes over hours — BMv2 stability per M0 item 5).
**Exit:** design-doc Phase-3/4 exit criteria pass on the live testbed **with Tier-2 still stubbed**. This is the paper's deterministic baseline system.
*Status: ✅ **COMPLETE — 3/3 acceptance scenarios pass LIVE 2026-06-15** (`tests/integration/test_m6_acceptance_node.py`, real deployer+monitor+KG over the loop): **reroute-and-commit** (relay fault → exogenous → Tier-1 reroute → commit, the first live commit, with KG Verdict+LastKnownGood) and **ddil-escalate** (both relays down → exhaust tiers → escalate + KG ticket). Bounds calibrated to measured path latency (§6). 165 unit + 9 integration green. **Watchdog + soak harness done** (`tools/soak.py`): episode loop with a switch watchdog (`switch_control.restart_switch` + `Deployer.recover_switch` — per-switch, key-based table restore of the CURRENT committed config). Validated by a soak (kill-every-3): commits + watchdog recoveries, 0 errors, 0 KG-write failures, 0 zombies — survives BMv2 crashes (C4) + KG hiccups. **All 3 acceptance scenarios pass LIVE** (`test_m6_acceptance_node.py`): reroute-and-commit, ddil-escalate, and **contention-harm → Tier-0 tune (harm relief) → commit** (calibrated on LATENCY, since the monitor measures access-link throughput upstream of the shared bottleneck — harm shows as F2 queuing delay). 165 unit + 10 integration green. **≥1h soak DONE 2026-06-15** (consolidated GPU node + local Neo4j): `soak --minutes 60 --kill-every 20 --kill s3` → 351 episodes, 314 healthy / 37 marginal, 0 escalations, **17/17 watchdog recoveries** from injected s3 kills, 0 errors, 0 KG-write failures, 0 zombies. **M6 COMPLETE** — only M7 (Tier-2 real serving) remains.*

### M7 · Tier-2 regen + reproducibility (node + GPU) — L
`regen/`: grammar (the two tables × existing actions — resolves open question §13.3), prompt template (violation, table dump, bounds, prior failures), `llm_client.py` against vLLM/llama.cpp serving Qwen-Coder (pinned revision, greedy, constrained). Gate L3 dry-install if M0 showed it's needed. Phase-5 hardening: structured logging of episodes/verdicts, multi-model comparison harness (Qwen vs DeepSeek-Coder vs Granite/StarCoder baseline) for the paper.
**Exit:** a regen candidate flows propose → gate → apply → observe end-to-end; LLM-down test degrades to escalate-sooner (fail-safe §7.4); comparison table generated.
*Status: plumbing done with a stub model — both exit behaviors above already pass locally (`runtime/regen/`: GBNF generated from gate constants and narrower than the gate, verbatim prompt TEMPLATE, stateless K-cap proposer, `StubLLMClient`). Remaining: the real serving client (vLLM/llama.cpp + pinned Qwen-Coder, `gbnf()` as the guided-decoding constraint), gate L3 if the spike shows it's needed, and the multi-model comparison harness.*

**Review-#8 prerequisites for the real-serving swap (2026-06-15):**
- ✅ **Exception fail-safe DONE** — the proposer now catches `generate()` exceptions (timeout/5xx/OOM) → escalate, not crash (§7.4).
- ◇ **Plumb `gbnf()` into the decoder** — the proposer calls `generate(prompt)` with NO grammar arg, so the real client must apply the GBNF constraint internally; without it, raw output burns the K-cap and escalates early. `gbnf()` is currently dead outside tests.
- ◇ **Decide gate L3 (dry-install)** — the gate stops at L2; a regen that passes L0–L2 can still fail at real BMv2 install (DUPLICATE_ENTRY/handle drift). Mitigated today by deployer idempotency + the dominates guard catching a bad install post-deploy, but decide explicitly.
- ◇ **Grammar↔gate over-accept** — GBNF doesn't bound the egress port to `SWITCH_PORTS` or condition key-type on table, so the gate L0-rejects some grammar-valid output (wasted K-cap, harmless). Tighten the grammar or accept the waste.
- ◇ **Reproducibility** — pin the model revision; greedy isn't bitwise-deterministic across vLLM batch/versions; normalize int-vs-float knob formatting in the prompt.

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
| Veth counters too coarse / slow for loss measurement | ~~Med~~ **Resolved** | M0 C6: veth sysfs chosen, parses live (`parse_ping`/`parse_qdisc`/counters verified on real output). P4-counter fallback unused. |
| `table_modify` semantics differ from expectation | ~~Low~~ **Resolved** | M0 C1b: both `=>` and bare forms accepted; deployer unchanged. |
| BMv2 instability over long runs | **Med — CONFIRMED** | Switches **crash under table churn** (M0 item 4); `launch_network.py` has no watchdog yet. Action: add the watchdog (restarting a dead switch = a rung-1 fault the loop self-heals) **before the M6 soak**; the ≥1h soak is still owed. |
| Namespace access (`mnexec`) unavailable out-of-process | ~~Low~~ **Resolved** | M0 C3: `sudo mnexec -a <pid>` reaches namespaces; it is `NodeRunner.run_host`. |
| GPU/serving availability on controller-node | Med | M7 is last and optional-for-baseline; M6 is a complete deterministic system either way |
| Contract drift with Kiran | Med | M-K starts now; `contracts.py` is the single source both sides import |
| Scenario fixtures diverge from real testbed behavior | **Med — CONFIRMED** | Live bounds gap: LowLatency's 20 ms is unachievable (~40 ms primary / ~52 ms backup) → real-monitor episodes escalate. Calibrate bounds to hardware or expect escalation (design §6). Throughput/harm need continuous traffic (§5.2). These are paper findings. |

---

## 6. Definition of done (runtime side of Milestone III)

1. The three live acceptance scenarios pass end-to-end on the persistent testbed (M6).
2. Every terminal outcome writes a `Verdict`/`EscalationTicket` + trace to the KG, and switch status in the KG is monitor-computed — `SCENARIO_STATE` is no longer in the loop.
3. Tier-2 regen demonstrably proposes, is gate-checked, and deploys at least one recovered episode — and the system demonstrably survives the LLM being unavailable (M7).
4. Reproducibility kit: pinned model revision + serving stack, greedy constrained decoding, fixture matrix, soak log — enough for a reviewer to re-run.

---

## 7. Node bring-up findings (M0 → M6, 2026-06-14 → -15)

Empirical results from migrating to the Chameleon network-node and running on real BMv2 — the things that weren't knowable off-box. Design-doc sections carry the detail; this is the index.

**Mechanics that changed the design:**
1. **Reroute is five-part, not three** (design §10.1). The documented 3-step flip gives 100% loss live: the destination relay needs the edge-identity entry (a primary SFC's `s3` has no `0c` entry → frame dropped there), and the edge must re-ARP the drones after its interface is flushed. Fix: `config.RELAY_EDGE` + the deployer's per-switch `_ensure_forward`; verified to drop exactly one in-flight packet.
2. **Qdisc ownership = "Model B"** (design §10.2). A TUNE's `tc qdisc replace … root` wipes the scenario `netem`, so the target drone-`eth0` is wholly deployer-owned (just the knob) and the **environment lives switch-side** (`s1-eth{N}`), where the monitor reads it and the deployer never writes. Reversibility via `config.SFC_QOS_BASELINE` (binding `qos` overrides).
3. **`system_sound` semantics** (design §5.5). Rung-1 bails the episode, so unsound must mean *no path exists*: **s1 Failed or both relays Failed**. A single relay death stays sound → the loop reroutes to the survivor. (An "any switch alive" rule was wrong and is fixed.)
4. **Idempotent `deploy`/`re_push` + versioned handles** (design §10.5). BMv2 re-issues large versioned handles after churn; a blind re-install hits `DUPLICATE_ENTRY`. Both now reset-from-live-dump before installing. Never roll back across a re_push (re-baseline instead).

**Constraints for live evaluation (not bugs — calibrate the tests to them):**
5. **SLA bounds must match hardware** (design §6). Measured ~40 ms primary / ~52 ms backup → LowLatency's 20 ms is unachievable; real-monitor episodes for it *correctly escalate*. Pick reachable bounds, expect escalation, or recalibrate.
6. **Throughput/harm need continuous traffic** (design §5.2). Idle fabric reads 0 Mbps → every field unmet at baseline → harm never fires. Keep representative `iperf` load running through baseline **and** observation.

**Operational:**
7. **BMv2 crashes under churn; no watchdog yet** (design §10.7 item 4) — the launcher stays "up" while its switches die. Add a switch watchdog before the M6 soak; `NodeRunner` already surfaces a dead switch as a `RunnerError` (thrift-down), and `system_monitor` maps it to `Failed` → rung-1.
8. **KG + persistent network confirmed** (design §13.5–6): `bolt://controller-node:7687` reachable, `neo4j` 6.2.0 installed; `launch_network.py` holds the topology resident. Relaunch hygiene: `pkill -9 -f launch_network; pkill -9 simple_switch; mn -c` (verify 0 leftover veths) before relaunching.

**The loop is wired** over the real stack in `tools/run_episode.py` (`build_and_run` + `--monitor model|real`), proven live by a path-quality-fault episode that drove a real reroute to a healthy commit.

**M5 monitor (real sampler) findings:**
9. **The monitor measures access-link throughput (drone→s1), UPSTREAM of the shared relay bottleneck** (design §5.2). So shared-link contention is **invisible to per-field throughput** (both fields read full demand regardless of shaping) — it manifests as **loss/latency** (drops + queuing at the bottleneck), read via ping. Consequence: the contention-harm scenario is calibrated on **latency**, not bandwidth (F2 RTT ~246 ms congested vs ~55 ms relieved).
10. **Exogenous-shift must read the RELAY links too** (design §5.7). The sampler first read only the drone links (`s1-eth4..10`); a path-quality fault degrades a *relay* link (`s1-eth11/12`), so it read `exogenous_shift=False` and rung-3 would wrongly roll back instead of reroute. Fixed to include the relay links.
11. **`system_sound` from switch_status, not "any alive"** (finding #3 above) — verified live: kill s2 → `Failed` + sound stays True → loop reroutes.
12. **Best-effort KG writes** — `RuntimeManager._kg_write` swallows + counts Neo4j failures (a soak must survive a KG hiccup). Field-id translation at the `kg_client` read boundary (`F1↔Field_1`, design §8).

**M6 watchdog + soak findings:**
13. **rung-1 recovery must restore the CURRENT config, per-switch** (design §10.5). `re_push`/`restart_switch` reinstall the *base* binding, dropping a committed reroute's entries. The watchdog recipe (validated): `switch_control.restart_switch` (process) + `Deployer.recover_switch` (table state from `last_good`, **diffed by KEY not handle** — a restart re-numbers handles). `restart_switch` retries (port TIME_WAIT/BMv2-crash flake) and reaps all duplicates.
14. **Soak resilience** — iperf can die mid-run → throughput floors → every field reads unmet; `soak.py` adds a per-episode traffic health-check + restart, and reaps zombie wrappers (`os.waitpid`). A 3-min soak: commits + watchdog recoveries, 0 errors, 0 KG-fail, 0 zombies. Diagnostic note: count switches with `pgrep -xc simple_switch`/`switch_pids`, NOT loose `pgrep -f` (matches the `sudo` wrapper + own shell).

**Review-#8 (whole-codebase, 2026-06-15) — see design §13 "Known issues & hardening backlog":**
15. Safety holds (dominates uses smoothed booleans; monitor+rollback catches blackholes; window-averaged metrics; escalation fail-safe), so the rest is near-bound inefficiency / M7-time / M-K — not unsafety.
16. **Fixed:** regen proposer now catches `generate()` exceptions (§7.4 fail-safe for a real endpoint). **M7 prereqs:** plumb `gbnf()` into the decoder; decide gate L3; grammar over-accepts vs gate (wasted K-cap). **Gap:** rung-1 re_push not wired into the live loop (watchdog covers switch-death only). **M-K:** outbound KG ids are runtime F-ids (planner maps); `jsonable` is one-way.

---

## 8. Planner integration (M-K) — realized & verified (2026-06-17 → -19)

Kiran's network node expired, so the planner↔runtime boundary became an **internal**
concern: we own the orchestrator, the KG, and the runtime. The handoff is now built and
proven end-to-end. Full detail: [runtime-planner-contracts.md](runtime-planner-contracts.md)
(the §6 integration log), [planner-design.md](../planner/planner-design.md),
[planner-lora-retrain.md](../planner/planner-lora-retrain.md), [planner-lora-eval.md](../planner/planner-lora-eval.md),
[usage.md](../guides/usage.md).

**Inbound handoff (planner → runtime).**
- `runtime/planner_adapter.py` — normalizes the orchestrator's
  `llm_generated_experiment_config.json` into a `DeploymentSpec`. Maps `selected_sfc`,
  reconstructs the **canonical per-switch binding** (the artifact labels rule files by the
  active relay; the deployer needs s1/s2/s3 files — so we rebuild them and keep `policy_type`
  verbatim for path selection), derives the envelope from the KG, and normalizes the field id
  (`Field_<n>` → runtime `F<n>`). Transport = file; the two missing fields
  (`correlation_id`, `target_field`) are runtime-supplied.
- `runtime/tools/run_from_planner.py` — `--no-kg` dry (normalize → gate) and `--deploy` live
  (deploy → observe → verdict → KG, reusing the M5/M6 `build_and_run`).
- `runtime/tools/seed_kg.py` — non-destructive (MERGE) strategic-KG seed that preserves
  runtime records; `controller/generate_kg.py` extended with the planner's switch topology
  (`ProgrammableSwitch.role`, `PRIMARY_PATH`/`BACKUP_PATH`, `P4PolicyMapping`).

**Slow-planner model work** (so the *planner* makes valid, mission-appropriate decisions):
- **Constrained decoding** (`decision_grammar.py` + `llm_runner._grammar_processors`): forces a
  complete, valid 6-key decision (the model otherwise emitted 4/6 keys and rambled).
- **LoRA retrain** (`train_decision_lora.py`): distilled the rule-based oracle into a fresh
  LoRA; fixed the always-LowLatency collapse on the known mission taxonomy. **Promoted** as the
  default adapter (`gpu-node.env`). Gap: novel/telemetry-only missions still default to
  BandwidthOptimized.

**Key insight (review, 2026-06-19): constrained decoding bypasses the fallback.** The
deterministic fallback only fires when the LLM output is *invalid*; the grammar makes it always
valid, so the LLM's choice is **used**. Hence the original (mode-collapsed) adapter would ship
the wrong SFC under the production default — the retrained adapter is the one correct under
constrained-on, which is why it was promoted.

**Verification (2026-06-19, live).** 206 unit tests; **M6 acceptance 3/3 live** (reroute /
ddil-escalate / contention-harm); and a full **slow→fast episode**: the promoted planner
generated an emergency artifact (`ReliableRelaySFC`, `parsed_json`) → `run_from_planner --deploy`
→ KG envelope → gate PASS → deploy → episode → verdict + records in the KG. Operational lesson:
**launch the testbed with the P4 program matching what you'll deploy** (the deployer installs
rules, not the program), and run live tests with **detached (`setsid`) traffic**.
