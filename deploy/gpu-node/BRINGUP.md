# Consolidated node bring-up

Stand up a **single node** that hosts everything: the P4/BMv2 + Mininet testbed,
the Runtime Manager, the LLM serving endpoint, and the Neo4j knowledge graph.
Target: **Ubuntu 24.04 (noble)** + a Pascal GPU (Tesla P100).

Three setup scripts in this directory, each idempotent and `--smoke`-able:

| Script | Provisions | Smoke check |
|---|---|---|
| `setup_gpu_node.sh` | NVIDIA driver, venv (`~/netprompt-venv`, cu121 torch/transformers/peft), env template | FP16 generate on GPU |
| `setup_testbed_node.sh` | Mininet + BMv2 (`simple_switch`/`_CLI`) | launch topology, thrift 9090/9091/9092 UP |
| `setup_kg_node.sh` | local Neo4j 5.x (apt), password, systemd | bolt round-trip |

## Quick path

```bash
cd ~/Run-time-Manager/deploy/gpu-node

# 1. GPU / LLM serving stack (skip --driver if nvidia-smi already works)
./setup_gpu_node.sh --smoke

# 2. Testbed — M0 bring-up. On noble the p4lang apt repo has no package, so the
#    script builds BMv2 from source. A green smoke = thrift 909{0,1,2} UP.
./setup_testbed_node.sh --smoke --build-bmv2

# 3. Local KG (single-node consolidation: don't depend on a remote controller)
./setup_kg_node.sh --smoke

# 4. Wire env (runtime + orchestrator) and validate the milestones (below)
source ./gpu-node.env

# 5. Seed the KG (only needed for the planner/orchestrator path; M6 + soak run on an empty KG)
( cd ../../controller && ~/netprompt-venv/bin/python generate_kg.py )   # -> drone_sfc_kg.json
~/netprompt-venv/bin/python -m runtime.tools.seed_kg                     # MERGE (non-destructive)
```

## Fresh-instance / re-setup notes (things that bite after an instance is reimaged)

- **Checkout directory name doesn't matter.** `gpu-node.env` is now *self-locating*
  (`NETPROMPT_ROOT` is derived from the file's own path), so it works whether the repo is
  `RuntimeManager`, `Run-time-Manager`, or anything else — **no symlink needed**. The
  `~/Run-time-Manager` paths in the commands below are illustrative; substitute your checkout.
- **`setup_gpu_node.sh` preserves an existing `gpu-node.env`.** It only writes the template
  when the file is *missing*, so re-running it no longer reverts the promoted
  `final_adapter_retrained` back to `final_adapter`. Delete the file first to regenerate.
- **Run the three setup scripts sequentially.** They each `apt-get`; running them concurrently
  used to abort the loser with an apt-lock error (exit 100). They now pass
  `-o DPkg::Lock::Timeout=300` to wait the lock out, but sequential is still cleanest.
- **`pytest` is in `requirements-gpu.txt`** (the unit suite needs it). On an older venv built
  before this was added: `~/netprompt-venv/bin/pip install pytest`. Green baseline =
  **212 passed** (`~/netprompt-venv/bin/python -m pytest tests/unit -q`).
- **A fresh instance has no git identity.** Before committing:
  `git config user.name "<you>" && git config user.email "<you@…>"`.

## Validation sequence (M0 → soak)

`TREE` = the in-repo milestone tree (NOT `~/netprompt-milestone-II`, which doesn't
exist on the consolidated node):

```bash
TREE=~/Run-time-Manager/network/milestone-II-latest/netprompt-milestone-II
```

- **M0** — covered by `setup_testbed_node.sh --smoke` (thrift ports UP).
- **Resident testbed** (M5/M6/soak mutate a *live* topology — bring it up first, leave it running):
  ```bash
  sudo env NETPROMPT_TREE_ROOT="$TREE" python3 ../../runtime/tools/launch_network.py \
    --p4-json "$TREE/compiled_p4/low_latency.json" \
    --rules-dir "$TREE/p4_multihop_rules" --sfc low_latency
  ```
- **M5** (monitor, KG-free; needs sudo; 3rd test kills s2 and doesn't recover):
  ```bash
  sudo env NETPROMPT_TREE_ROOT="$TREE" ~/netprompt-venv/bin/python -m pytest \
    tests/integration/test_m5_monitor_node.py -v
  ```
- **M6** (acceptance, KG-backed — needs the local Neo4j; pass the KG env through sudo):
  ```bash
  sudo env NETPROMPT_TREE_ROOT="$TREE" \
    NETPROMPT_KG_URI=bolt://localhost:7687 NETPROMPT_KG_USER=neo4j NETPROMPT_KG_PASS=netprompt123 \
    ~/netprompt-venv/bin/python -m pytest tests/integration/test_m6_acceptance_node.py -v
  ```
- **Soak** (≥1h; run as your user — it self-escalates with sudo internally):
  ```bash
  NETPROMPT_TREE_ROOT="$TREE" NETPROMPT_KG_URI=bolt://localhost:7687 \
    ~/netprompt-venv/bin/python -m runtime.tools.soak --minutes 60 --kill-every 20 --kill s3
  ```
  Green = `errors=0`, `kg_write_failures=0`, `recoveries == injected_kills`, 0 zombies.

## noble / consolidation gotchas

- **BMv2 from source needs `PIP_BREAK_SYSTEM_PACKAGES=1`.** noble marks system Python
  externally-managed (PEP 668); BMv2's bundled PI binding `pip install` during
  `make install` fails and aborts before `simple_switch` is staged.
  `setup_testbed_node.sh --build-bmv2` sets it.
- **Two env conventions for the KG.** The runtime reads `NETPROMPT_KG_URI/USER/PASS`
  (`runtime/config.py`); the milestone-II orchestrator reads `NEO4J_URI/USER/PASSWORD`.
  `gpu-node.env` sets **both** → `bolt://localhost:7687`. Setting only `NEO4J_*`
  leaves the runtime pointing at the dead default `bolt://controller-node:7687`.
- **`NETPROMPT_ROOT` is in-repo here.** The milestone tree is vendored under
  `network/milestone-II-latest/...`, not `~/netprompt-milestone-II`.
- **An empty KG is enough** for M6 + soak — the runtime only writes/reads its own
  records. `read_field_requirements()` (`:AgriculturalField`) is the LLM/RAG path,
  not these tests. (A populated milestone KG also exists remotely; use it read-only.)
- **The soak runs as your user, not under sudo** — it prefixes `sudo` on its own
  privileged sub-commands.
- **Verdict `timestamp` is a string** (`soak1`, `soak2`, …). `ORDER BY v.timestamp`
  sorts *lexically* (`soak99` > `soak332`). Order numerically:
  `ORDER BY toInteger(replace(v.timestamp,'soak',''))`.
- **`teardown` of the testbed**: `pkill -f` from a shell self-matches its own
  command line; use the bracket trick — `pkill -f '[l]aunch_network.py'`,
  `pkill -f 'simple_swit[c]h'` — and don't echo the literal process name in the
  same command.

## Neo4j Browser

Localhost-only by default. Tunnel both ports and open `http://localhost:7474`:
```bash
ssh -L 7474:localhost:7474 -L 7687:localhost:7687 <user>@<node>
```
Connect URL `bolt://localhost:7687`, user `neo4j`, pass `netprompt123`. If the
Browser looks empty, you're on the `system` database — run `:use neo4j`.
