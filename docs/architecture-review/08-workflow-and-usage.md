# 8 · Workflow & Usage

How the system runs end-to-end — the operational lifecycle, what happens at each
stage, and the concrete commands to drive it. This frames the workflow; the
exhaustive flag-level reference is [`docs/usage.md`](../usage.md), which this
distills and cross-links (commands here mirror it).

For *what* the pieces are, see [01-architecture.md](01-architecture.md) (the loop)
and [06-knowledge-graph.md](06-knowledge-graph.md) (the KG hub).

---

## 8.1 The operational lifecycle

```
                                    ┌──────────── prerequisites ────────────┐
                                    │ source gpu-node.env · venv · Neo4j up   │
                                    └────────────────────────────────────────┘
                                                     │
 (1) SEED KG ──▶ (2) LAUNCH TESTBED ──▶ (3) PLAN (slow loop) ──▶ (4) EPISODE (fast loop) ──▶ (5) VERIFY
  generate_kg       launch_network         orchestrate              run_from_planner            KG records
  + seed_kg         (resident BMv2)        → plan.json artifact     deploy→observe→adapt→commit  + teardown
   strategic         3-switch fabric        decision LLM (cuda:0)    /rollback/escalate           │
   graph                                                             (+ optional regen LLM cuda:1) │
      ▲                                                                                            │
      └───────────────── (6) analytics: per-SFC reliability ◀── Verdict/EscalationTicket ─────────┘
                              (folded into the next plan's context — the closed loop)
```

Six stages. (1)–(2) are one-time setup per environment; (3)–(5) are the per-mission
run; (6) closes the loop back to the planner.

| # | Stage | Component | What happens |
|---|-------|-----------|--------------|
| 0 | Prereqs | `gpu-node.env`, venv, Neo4j | env (KG creds, devices, adapter), FP16/no-4bit on P100 |
| 1 | Seed KG | `generate_kg.py` → `seed_kg` | build the strategic graph JSON, MERGE it in non-destructively |
| 2 | Launch testbed | `launch_network.py` | bring up the **resident** 3-switch BMv2/P4 fabric (s1/s2/s3) |
| 3 | Plan | `llm_orchestrator.orchestrate` | decision LLM picks SFC/policy/path → writes `plan.json` |
| 4 | Episode | `run_from_planner` (or `run_episode`) | normalize → gate → deploy → observe → adapt → commit/rollback/escalate |
| 5 | Verify | KG / pytest | inspect `Verdict`/snapshots; tear the testbed down |
| 6 | Feedback | `llm_orchestrator.analytics` | aggregate verdicts → per-SFC reliability → next plan's context |

---

## 8.2 Prerequisites (every session)

```bash
cd ~/Run-time-Manager
source deploy/gpu-node/gpu-node.env     # NETPROMPT_ROOT/TREE_ROOT, KG creds, devices, promoted adapter
# venv: ~/netprompt-venv  ·  Neo4j: bolt://localhost:7687 (neo4j / netprompt123) must be running
```

> **P100 note:** FP16, no bitsandbytes. `gpu-node.env` sets
> `NETPROMPT_LLM_USE_4BIT=0` + `NETPROMPT_LLM_DEVICE_MAP=cuda:0`; sourcing the env
> is enough (the orchestrator defers to it when `--4bit`/`--device-map` are omitted).

---

## 8.3 End-to-end run

**(1) Seed the KG** — strategic graph (planner topology + runtime bounds), idempotent:

```bash
cd ~/Run-time-Manager/controller && ~/netprompt-venv/bin/python generate_kg.py   # -> drone_sfc_kg.json
cd ~/Run-time-Manager && ~/netprompt-venv/bin/python -m runtime.tools.seed_kg     # MERGE, keeps runtime records
```

**(2) Launch the resident testbed** — *match the P4 program to the SFC you'll deploy*
(the deployer installs table *rules*, not the P4 *program*, which is fixed at launch):

```bash
sudo -E python3 runtime/tools/launch_network.py \
  --p4-json "$NETPROMPT_ROOT/compiled_p4/reliable_relay.json" \
  --rules-dir "$NETPROMPT_ROOT/p4_multihop_rules" \
  --sfc reliable_relay --scenario baseline      # resident; hold it in the background
# readiness: echo 'table_dump forward_table' | simple_switch_CLI --thrift-port 9090
```

**(3) Plan** — the slow planner emits a deployable artifact:

```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python -m llm_orchestrator.orchestrate \
  --mission emergency_alert_relay --bandwidth 20 --delay 25 --loss 2 --battery 80 \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --device-map cuda:0 --no-4bit --output /tmp/plan.json
```

**(4) Run an episode** driven by that artifact:

```bash
cd ~/Run-time-Manager
# dry (no testbed): normalize -> gate check only, no deploy
~/netprompt-venv/bin/python -m runtime.tools.run_from_planner \
  --artifact /tmp/plan.json --target-field F2 --no-kg
# live: deploy + one episode + write verdict/snapshots to the KG (needs testbed + traffic)
sudo -E ~/netprompt-venv/bin/python -m runtime.tools.run_from_planner \
  --artifact /tmp/plan.json --target-field F2 --deploy
```

Representative traffic for a live episode (detached so it survives the run):

```bash
EDGE=$(pgrep -f 'mininet:edge')
sudo setsid mnexec -a "$EDGE" iperf -s -u </dev/null >/dev/null 2>&1 &
for d in d4 d5 d6 d7 d8 d9 d10; do P=$(pgrep -f "mininet:$d\b")
  sudo setsid mnexec -a "$P" iperf -u -c 10.0.0.100 -b 5M -t 600 </dev/null >/dev/null 2>&1 & done
```

**(5) Verify + tear down:**

```bash
# KG records under the run's correlation_id: Verdict, EscalationTicket, BaselineSnapshot, ProgrammableSwitch.status
sudo pkill -9 -f "[l]aunch_network"; sudo pkill -9 "[s]imple_switch"; sudo pkill -f "[i]perf"; sudo mn -c
```

---

## 8.4 Three ways to run an episode — pick by intent

| Mode | Command | When to use |
|------|---------|-------------|
| **Scenario-driven** | `runtime.tools.run_episode --scenario relay_failure --monitor {model\|real}` | exercise the loop against a fixture situation; `model` = instant scenario monitor (no testbed), `real` = live monitor |
| **Planner-driven** | `runtime.tools.run_from_planner --artifact plan.json --target-field F2 --deploy` | end-to-end from a real planner artifact (§8.3) |
| **Soak** | `runtime.tools.soak --minutes 60 --kill-every 20 --kill s3 [--with-regen]` | repeated episodes + injected switch kills + watchdog recovery; `--with-regen` wires the real Tier-2 model |

> **No LLM required for the inner loop.** By default Tier-2 is stubbed
> (`regen_proposer=None`), so the RM escalates instead of loading a model — the
> deterministic tiers (tune/reroute) exercise the whole loop. The regen model is
> wired only by `soak --with-regen` and the M7 live tests.

---

## 8.5 The two models

**Decision model** (planner, `Qwen2.5-1.5B-Instruct` + LoRA, `cuda:0`):

```bash
# constrained decoding (default ON) forces a complete, valid 6-key decision:
export NETPROMPT_LLM_CONSTRAINED=1          # =0 disables -> invalid output falls to the rule oracle
# promote / roll back the adapter (default set in gpu-node.env):
NETPROMPT_LLM_ADAPTER=".../final_adapter_retrained"   # promoted (mission-appropriate on known taxonomy)
NETPROMPT_LLM_ADAPTER=".../final_adapter"             # rollback (mode-collapsed under constrained-on)
# retrain (oracle distillation, ~55 min fp32 on a P100):
~/netprompt-venv/bin/python train_decision_lora.py --neo4j-password netprompt123 \
  --out netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained --per-class 160 --epochs 3 --device cuda:0
```

**Tier-2 regen model** (`Qwen2.5-Coder-1.5B-Instruct`, `cuda:1`) — not a CLI; it's the
`RegenProposer` plugged into the adapt engine. Pinned for reproducibility in
`gpu-node.env` (`NETPROMPT_REGEN_REVISION=2e1fd397…`). Tools around it:

```bash
~/netprompt-venv/bin/python -m runtime.tools.regen_manifest    # reproducibility manifest
~/netprompt-venv/bin/python -m runtime.tools.regen_compare --models <0.5B,1.5B,3B>   # the paper's comparison table
```

See [05-evolution-from-original.md §7](05-evolution-from-original.md) for the retrain
story and [06-knowledge-graph.md §6.5](06-knowledge-graph.md) for which model reads
the KG vs live switch state.

---

## 8.6 Testing & verification workflow

```bash
# offline, fast — the green baseline every change must preserve:
~/netprompt-venv/bin/python -m pytest tests/unit -q                       # 212 passed (~0.3s)
# node-gated integration (needs the resident testbed + traffic):
sudo -E env NETPROMPT_TREE_ROOT="$NETPROMPT_ROOT" NETPROMPT_KG_URI=bolt://localhost:7687 \
  NETPROMPT_KG_PASS=netprompt123 ~/netprompt-venv/bin/python \
  -m pytest tests/integration/test_m6_acceptance_node.py -v
```

- **Unit tier (212 / 454 asserts):** runs off-testbed against `FakeDeployer`/
  `FakeMonitor`/`ScriptedRunner` + 6 scenario fixtures — no `sudo`, no Mininet.
- **Integration tier (16 / 62 asserts):** all `skipif(not _testbed_up())`; drives the
  real BMv2 fabric and asserts on live switch state / traffic.

---

## 8.7 The closed feedback loop

```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python -m llm_orchestrator.analytics \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123   # report; --json / --write-kg
```

The runtime's `Verdict`/`EscalationTicket` history is aggregated into a per-SFC
reliability signal and folded into every planner decision as
`input_object.runtime_feedback` (disable with `NETPROMPT_PLANNER_FEEDBACK=0`). The
path is wired and robust; the current 1.5B model doesn't yet *exploit* it (it's
advisory) — see [05 §7.6](05-evolution-from-original.md).

---

## 8.8 Operational gotchas (the ones that bite)

1. **Match the P4 program to the SFC** at launch, or deploy fails with
   `N table_add lines but N-1 handles` (the program is fixed at switch launch; only
   rules are installed live).
2. **FP16 / no-4bit on the P100** — `gpu-node.env` handles it; don't force bitsandbytes.
3. **Live episodes need traffic** — start the detached `iperf` flows (§8.3) or the
   monitor reads an idle/unreachable network.
4. **Seed with `seed_kg`, not the legacy `import_kg.py`** — the latter does
   `DETACH DELETE` and wipes runtime records; the former is idempotent `MERGE`.
5. **Teardown uses bracketed patterns** so the `pkill` doesn't match itself; finish
   with `sudo mn -c`.

---

## 8.9 Command cheat-sheet

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

- **Six-stage lifecycle, two of them one-time.** Seed KG → launch testbed (one-time per environment) → plan → run episode → verify → feedback (per mission). Stages 1–2 stand up the resident BMv2 fabric and the strategic graph; 3–5 are the actual run; 6 closes the loop back to the planner via analytics.

- **The resident testbed is the big operational shift.** Unlike the original torn-down-per-run scripts, `launch_network.py` holds a 3-switch BMv2 fabric (thrift 9090/9091/9092) resident, and the Runtime Manager mutates it out-of-band. The one rule that bites: launch with the **P4 program matching the SFC** you'll deploy, or the install handle-count check fails.

- **Three ways to run an episode, picked by intent.** Scenario-driven (`run_episode`, fixture situation, `--monitor model` needs no testbed) for fast loop exercise; planner-driven (`run_from_planner --deploy`) for true end-to-end; soak (`soak --with-regen`) for repeated episodes with injected kills and the real Tier-2 model.

- **The inner loop needs no LLM by default.** Tier-2 is stubbed (`regen_proposer=None`), so the deterministic tune/reroute tiers exercise the whole loop and escalate instead of loading a model. The decision LLM (`cuda:0`) and the regen LLM (`cuda:1`) are opt-in and live on separate GPUs.

- **`pytest tests/unit -q` (212 green, ~0.3 s) is the everyday gate.** It runs off-testbed against fakes + 6 scenario fixtures — no `sudo`, no Mininet — so it's the fast feedback every change must keep green; the 16 node-gated integration tests verify real switch state when the testbed is up.

- **Use `seed_kg`, not `import_kg.py`.** The idempotent `MERGE` seed preserves runtime records (verdicts, snapshots, live switch status); the legacy importer's `DETACH DELETE` wipes them. This is the read-strategic / write-runtime contract made operational.

- **The canonical command reference is [`docs/usage.md`](../usage.md).** This document frames the lifecycle and the why; `usage.md` carries the exhaustive flags, the per-LLM sections, and the two-model summary table.
