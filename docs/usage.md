# NetPrompt — Usage Guide

How to run the system end-to-end and each of its two LLMs individually. For *what* the
pieces are, see [runtime-manager-design.md](runtime-manager-design.md) (fast loop),
[planner-design.md](planner-design.md) (slow loop), and
[runtime-planner-contracts.md](runtime-planner-contracts.md) (the handoff). For a
**per-component reference** (one card each: runtime manager, gate, deployer, monitors,
evaluator, adapt engine, KG client, slow planner, and both LLMs) see
[components/](components/README.md).

**The system has two loops and two LLMs:**
- **Slow planner** (outer loop) — an LLM **decision model** (`Qwen2.5-1.5B-Instruct` + LoRA)
  that selects an SFC/policy/path and emits a deployment artifact. Runs on **`cuda:0`**.
- **Runtime Manager** (inner loop) — deploys + adapts on the live P4/BMv2 network. Its
  Tier-2 adaptation *can call* a separate **code model** (`Qwen2.5-Coder-1.5B-Instruct`) that
  regenerates P4 table rules. Runs on **`cuda:1`**.

> **The Tier-2 regen LLM is NOT the Runtime Manager.** The Runtime Manager is a
> *mostly-deterministic control system* (deploy → observe → evaluate → adapt). The code
> model is just a **component it can reach for** at the top of its adapt ladder
> (tier 0 tune → tier 1 reroute → **tier 2 regen = the LLM**), as a gated last resort. The
> RM runs fine with no LLM at all (Tier-2 stubbed → escalate). See §2.

## 0. Prerequisites (every session)

```bash
cd ~/Run-time-Manager
source deploy/gpu-node/gpu-node.env          # NETPROMPT_ROOT/TREE_ROOT, KG creds, devices
# venv: ~/netprompt-venv  (torch+cu121, transformers 4.46.3, peft, transformers-cfg, neo4j)
# Neo4j: local, bolt://localhost:7687 (neo4j / netprompt123) — must be running
```

> **P100 footgun:** the orchestrator CLI defaults override the env, so on the P100 you must
> pass **`--device-map cuda:0 --no-4bit`** to any `orchestrate.py` call (else it tries
> bitsandbytes 4-bit and fails).

---

## 1. Holistic system (slow loop → fast loop, end-to-end)

### 1.1 Seed the KG (once, or after a wipe)
The KG carries both the planner's topology (switch roles, P4 policies) and the runtime's
field/SFC bounds. Seed it **non-destructively**:

```bash
cd ~/Run-time-Manager/controller && ~/netprompt-venv/bin/python generate_kg.py   # -> drone_sfc_kg.json
cd ~/Run-time-Manager && ~/netprompt-venv/bin/python -m runtime.tools.seed_kg     # MERGE, keeps runtime records
```

### 1.2 Launch the resident testbed — **match the P4 program to what you'll deploy**
The deployer installs table *rules*, not the P4 *program* (fixed at switch launch). So
launch with the program for the SFC you intend to run, or deploy fails with
`N table_add lines but N-1 handles`.

```bash
# reliable_relay for an emergency/ReliableRelay deployment; low_latency for LowLatency, etc.
sudo -E python3 runtime/tools/launch_network.py \
  --p4-json "$NETPROMPT_ROOT/compiled_p4/reliable_relay.json" \
  --rules-dir "$NETPROMPT_ROOT/p4_multihop_rules" \
  --sfc reliable_relay --scenario baseline      # run in the background; holds the topology resident
```
Readiness: `echo 'table_dump forward_table' | simple_switch_CLI --thrift-port 9090` returns rows on 9090/9091/9092.

### 1.3 Generate a deployment from the slow planner
```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python -m llm_orchestrator.orchestrate \
  --mission emergency_alert_relay --bandwidth 20 --delay 25 --loss 2 --battery 80 \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --device-map cuda:0 --no-4bit \
  --output /tmp/plan.json
# -> a deployable artifact (selected_sfc/policy/path/relay + p4_json + rule paths)
```

### 1.4 Run a Runtime Manager episode driven by that artifact
```bash
cd ~/Run-time-Manager
# dry (no testbed): normalize -> gate check, no deploy
~/netprompt-venv/bin/python -m runtime.tools.run_from_planner \
  --artifact /tmp/plan.json --target-field F2 --no-kg
# live: deploy + run one episode + write verdict/snapshots to the KG (needs the testbed + traffic)
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

### 1.5 Verify + tear down
```bash
# KG records under the run's correlation_id: Verdict, EscalationTicket, BaselineSnapshot, ProgrammableSwitch.status
# Teardown (bracketed patterns won't self-match):
sudo pkill -9 -f "[l]aunch_network"; sudo pkill -9 "[s]imple_switch"; sudo pkill -f "[i]perf"; sudo mn -c
```

### 1.6 Acceptance / regression
```bash
~/netprompt-venv/bin/python -m pytest tests/unit -q                       # offline, fast
sudo -E env NETPROMPT_TREE_ROOT="$NETPROMPT_ROOT" NETPROMPT_KG_URI=bolt://localhost:7687 \
  NETPROMPT_KG_PASS=netprompt123 ~/netprompt-venv/bin/python \
  -m pytest tests/integration/test_m6_acceptance_node.py -v               # live (needs low_latency testbed + traffic)
```

---

## 2. Runtime Manager — the inner-loop control system (mostly deterministic)

The RM runs one **episode**: deploy a binding, observe a window, run the 6-stage evaluator,
and adapt within the SFC envelope (tier 0 tune → tier 1 reroute → tier 2 regen), then commit /
rollback / escalate. It is deterministic except for the optional tier-2 LLM (§4).
See [runtime-manager-design.md](runtime-manager-design.md).

**Run an episode — scenario-driven** (no planner; a fixture drives the situation):
```bash
cd ~/Run-time-Manager
# 'model' = instant scenario-modelled monitor; 'real' = live NetworkMonitor over the testbed
sudo -E ~/netprompt-venv/bin/python -m runtime.tools.run_episode \
  --scenario relay_failure --monitor real          # needs the resident testbed + traffic
```

**Run an episode — planner-driven:** see §1.4 (`run_from_planner --deploy`).

**Soak** (repeated episodes + injected switch kills + watchdog recovery; `--with-regen`
wires in the real tier-2 model):
```bash
sudo -E ~/netprompt-venv/bin/python -m runtime.tools.soak \
  --minutes 60 --kill-every 20 --kill s3            # add --with-regen to exercise tier-2
```

**Acceptance / regression:** unit suite + M6 acceptance — see §1.6.

> **No LLM required.** With the default assembly, tier 2 is stubbed (`regen_proposer=None`),
> so the RM escalates instead of calling a model — the whole inner loop is exercised by the
> deterministic tiers. The tier-2 code model is wired only by `soak --with-regen` and the M7
> live tests (§4).

## 3. Planner LLM — the decision model (`Qwen2.5-1.5B-Instruct` + LoRA)

Selects `selected_sfc/policy/path/relay/priority/deployment_mode` from mission + telemetry +
KG context. Runs in `llm_orchestrator` on **`cuda:0`**. See [planner-design.md](planner-design.md).

**Run it (produces the artifact):** see §1.3. Key flags:
- `--mission/--bandwidth/--delay/--loss/--battery` — the situation.
- `--adapter-path <dir>` — which LoRA (overrides the env default).
- `--output <path>` — where to write `llm_generated_experiment_config.json`.

**Constrained decoding** (default ON) forces a complete, valid 6-key decision and stops the
rambling ([contracts §6c](runtime-planner-contracts.md)):
```bash
export NETPROMPT_LLM_CONSTRAINED=1     # on (default); =0 to disable (then invalid output -> rule-based fallback)
```

**Adapters — promote / roll back.** The default is set in `gpu-node.env`:
```bash
# PROMOTED default (mission-appropriate on the known taxonomy):
NETPROMPT_LLM_ADAPTER=".../final_adapter_retrained"
# ROLLBACK to the original (mode-collapsed to LowLatency under constrained-on):
NETPROMPT_LLM_ADAPTER=".../final_adapter"
# or per-run: orchestrate.py --adapter-path .../final_adapter
```
Both adapters are version-controlled; `final_adapter_original_backup/` is a redundant copy.

**Retrain the LoRA** (oracle distillation — see [planner-lora-retrain.md](planner-lora-retrain.md)):
```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python train_decision_lora.py \
  --neo4j-password netprompt123 \
  --out netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained \
  --per-class 160 --epochs 3 --device cuda:0      # ~55 min on a P100, fp32
```

**Evaluate** (see [planner-lora-eval.md](planner-lora-eval.md)): run §1.3 per mission with
`--adapter-path` and read `selected_sfc` + `decision.llm_parse_status` (`parsed_json` =
LLM decision used). Known limit: correct on the known mission taxonomy, defaults to
BandwidthOptimized on novel/telemetry-only missions.

---

## 4. Tier-2 regen LLM — the code model (`Qwen2.5-Coder-1.5B-Instruct`)

The runtime's **last-resort adaptation tier**: when tune (tier 0) and reroute (tier 1) can't
meet SLA, it **regenerates P4 table rules** under GBNF-constrained decoding, validated by the
gate before anything touches a switch. Runs on **`cuda:1`** (separate from the planner).
See [m7-implementation-plan.md](m7-implementation-plan.md) and design §4/§7.3.

**Config** (pins for reproducibility, in `gpu-node.env`):
```bash
NETPROMPT_REGEN_MODEL="Qwen/Qwen2.5-Coder-1.5B-Instruct"
NETPROMPT_REGEN_REVISION="2e1fd397ee46e1388853d2af2c993145b0f1098a"   # pinned HF commit
NETPROMPT_REGEN_DEVICE="cuda:1"
# config.py: REGEN_MAX_REJECTS=3 (K gate-rejects -> escalate), REGEN_MAX_NEW_TOKENS=64
```

**How it's invoked.** It is not a CLI; it's the `RegenProposer` (`runtime/regen/`) plugged
into the adapt engine's `regen_proposer` seam. **By default it is stubbed** (`None`) in
`run_episode`/`run_from_planner`, so Tier-2 escalates without loading the model. Real regen
serving is exercised by the M7 live demo and the soak:
```bash
# soak with the real Tier-2 model wired in:
sudo -E ~/netprompt-venv/bin/python -m runtime.tools.soak --minutes 60 --kill-every 20 --kill s3 --with-regen
# the M7 live propose->gate->apply->observe tests:
sudo -E ... ~/netprompt-venv/bin/python -m pytest tests/integration/test_m7_regen_live.py -v
```

**Reproducibility manifest** (model+rev, decoding, lib versions, grammar/prompt hashes):
```bash
~/netprompt-venv/bin/python -m runtime.tools.regen_manifest
```

**Multi-model comparison** (the paper's table — grammar-valid / gate-accept / recovery / latency):
```bash
NETPROMPT_REGEN_DEVICE=cuda:1 ~/netprompt-venv/bin/python -m runtime.tools.regen_compare \
  --models Qwen/Qwen2.5-Coder-0.5B-Instruct,Qwen/Qwen2.5-Coder-1.5B-Instruct,Qwen/Qwen2.5-Coder-3B-Instruct
```
See [m7-regen-comparison.md](m7-regen-comparison.md). Fail-safe: an unavailable/invalid model
escalates with the network untouched (capability degrades, safety doesn't).

---

## 5. Two-model summary

| | Planner (decision) | Tier-2 regen (code) |
|---|---|---|
| Model | Qwen2.5-1.5B-Instruct + LoRA | Qwen2.5-Coder-1.5B-Instruct (pinned rev) |
| Device | `cuda:0` | `cuda:1` |
| Loop | slow / outer (per mission) | fast / inner (last adapt tier) |
| Output | SFC decision → artifact | P4 table rules |
| Safety net | validator + rule-based fallback | ValidationGate (L0–L3) + K-cap + fail-safe |
| Constrained | GBNF decision grammar (default on) | GBNF table grammar (always) |
| Run it | `llm_orchestrator.orchestrate` | `RegenProposer` (in-loop); `regen_manifest`/`regen_compare` tools |
