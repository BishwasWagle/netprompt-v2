#!/usr/bin/env bash
#
# setup_gpu_node.sh — provision a P100 (Pascal) GPU node for NetPrompt LLM serving.
#
# Target: Ubuntu 24.04 + Tesla P100 (compute capability 6.0, 16 GB).
# Serves Qwen2.5-1.5B-Instruct + the in-tree LoRA adapter in FP16 (no bitsandbytes,
# no vLLM — neither supports sm_60). See requirements-gpu.txt for the rationale.
#
# Usage:
#   ./setup_gpu_node.sh [--driver] [--smoke] [--venv DIR] [--root DIR] [--driver-pkg NAME]
#
#   --driver         Install the NVIDIA datacenter driver (needs sudo + a reboot).
#                    Skip this if `nvidia-smi` already works (e.g. a pre-imaged
#                    Chameleon GPU node).
#   --smoke          After install, download the base model and run a 16-token
#                    greedy generation on the GPU to prove the serving path.
#   --venv DIR       Virtualenv location           (default: $HOME/netprompt-venv)
#   --root DIR       netprompt-milestone-II tree    (default: the in-repo
#                    network/milestone-II-latest/netprompt-milestone-II tree)
#   --driver-pkg N   Driver apt package             (default: nvidia-driver-550-server)
#
# Idempotent: re-running reuses the venv and skips the driver if the GPU is already up.

set -euo pipefail

# ---------------------------------------------------------------- defaults / args
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
VENV="${VENV:-$HOME/netprompt-venv}"
# Consolidated node: the milestone-II tree is vendored in-repo under network/,
# not at ~/. Override with --root if your layout differs.
NETPROMPT_ROOT="${NETPROMPT_ROOT:-$REPO_ROOT/network/milestone-II-latest/netprompt-milestone-II}"
DRIVER_PKG="nvidia-driver-550-server"
DO_DRIVER=0
DO_SMOKE=0
KG_PASS="${NEO4J_PASSWORD:-netprompt123}"   # local KG dev default (runtime/config.py)
REQ="$HERE/requirements-gpu.txt"
MODEL="Qwen/Qwen2.5-1.5B-Instruct"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --driver)     DO_DRIVER=1; shift ;;
    --smoke)      DO_SMOKE=1; shift ;;
    --venv)       VENV="$2"; shift 2 ;;
    --root)       NETPROMPT_ROOT="$2"; shift 2 ;;
    --driver-pkg) DRIVER_PKG="$2"; shift 2 ;;
    -h|--help)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }

[[ -f "$REQ" ]] || { echo "missing $REQ (run this script from deploy/gpu-node/)" >&2; exit 1; }

# ---------------------------------------------------------------- step 0: OS check
say "Step 0 — host check"
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  echo "OS: ${PRETTY_NAME:-unknown}"
  [[ "${VERSION_ID:-}" == "24.04" ]] || warn "expected Ubuntu 24.04; continuing anyway."
fi

# ---------------------------------------------------------------- step 1: driver
say "Step 1 — NVIDIA driver"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  echo "Driver already present:"
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
elif [[ "$DO_DRIVER" == "1" ]]; then
  echo "Installing $DRIVER_PKG (sudo)…"
  sudo apt-get update
  sudo apt-get install -y "$DRIVER_PKG"
  warn "Driver installed. REBOOT, then re-run this script WITHOUT --driver to finish."
  exit 0
else
  warn "nvidia-smi not found. Re-run with --driver to install $DRIVER_PKG, or install a"
  warn "Pascal-capable driver manually. Continuing — the venv will build but CUDA will be off."
fi

# ---------------------------------------------------------------- step 2: system deps
say "Step 2 — system packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip build-essential git

# ---------------------------------------------------------------- step 3: venv
say "Step 3 — virtualenv at $VENV"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip wheel

# ---------------------------------------------------------------- step 4: python deps
say "Step 4 — python deps (this pulls a cu121 torch; takes a few minutes)"
pip install -r "$REQ"

# ---------------------------------------------------------------- step 5: GPU verification
say "Step 5 — verify the serving path"
python - <<'PY'
import sys, torch
print("torch:", torch.__version__)
ok = torch.cuda.is_available()
print("cuda available:", ok)
if not ok:
    print("NOTE: no CUDA visible — install/repair the driver, then re-run.")
    sys.exit(0)
i = 0
cap = torch.cuda.get_device_capability(i)
print("device:", torch.cuda.get_device_name(i))
print("compute capability:", "%d.%d" % cap)
print("bf16 supported:", torch.cuda.is_bf16_supported())
if cap < (7, 0):
    print(">> Pascal-class GPU: vLLM unsupported, bitsandbytes 4/8-bit unsupported.")
    print(">> Correct config: FP16 + NETPROMPT_LLM_USE_4BIT=0 (already the default below).")
PY

# ---------------------------------------------------------------- step 6: env template
say "Step 6 — write env template"
ENV_FILE="$HERE/gpu-node.env"
cat > "$ENV_FILE" <<EOF
# Source this before running the orchestrator on the GPU node:  source $ENV_FILE
export NETPROMPT_ROOT="$NETPROMPT_ROOT"
export NETPROMPT_LLM_MODEL="$MODEL"
export NETPROMPT_LLM_ADAPTER="\$NETPROMPT_ROOT/netprompt_qwen_kg_rag_orchestrator/final_adapter"
export NETPROMPT_LLM_DEVICE_MAP="cuda:0"
export NETPROMPT_LLM_USE_4BIT="0"      # CRITICAL on P100: FP16, no bitsandbytes
export NETPROMPT_LLM_MAX_NEW_TOKENS="128"
# --- Neo4j KG: local instance on this consolidated node (bolt on localhost) ---
# Runtime (runtime/config.py reads NETPROMPT_KG_*):
export NETPROMPT_KG_URI="bolt://localhost:7687"
export NETPROMPT_KG_USER="neo4j"
export NETPROMPT_KG_PASS="$KG_PASS"
# Orchestrator / milestone-II code reads NEO4J_*:
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="$KG_PASS"
EOF
echo "wrote $ENV_FILE"

# ---------------------------------------------------------------- step 7: smoke test
if [[ "$DO_SMOKE" == "1" ]]; then
  say "Step 7 — model smoke test (downloads ~3 GB base model, FP16 greedy)"
  MODEL="$MODEL" python - <<'PY'
import os, torch
from transformers import AutoTokenizer, AutoModelForCausalLM
m = os.environ["MODEL"]
tok = AutoTokenizer.from_pretrained(m, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    m, torch_dtype=torch.float16, device_map={"": 0}, trust_remote_code=True,
).eval()
ids = tok("Reply with OK.", return_tensors="pt").to(model.device)
out = model.generate(**ids, max_new_tokens=16, do_sample=False,
                     pad_token_id=tok.eos_token_id)
print("generated:", tok.decode(out[0][ids['input_ids'].shape[-1]:], skip_special_tokens=True))
print("OK — FP16 serving path works on this GPU.")
PY
else
  say "Step 7 — skipped (pass --smoke to load the model and generate)"
fi

say "Done."
echo "Next:"
echo "  source $VENV/bin/activate && source $HERE/gpu-node.env"
echo "  # no-model pipeline check:"
echo "  python -m llm_orchestrator.orchestrate --mission emergency_alert_relay \\"
echo "    --priority critical --bandwidth 20 --delay 25 --loss 2 --battery 80 \\"
echo "    --fallback-only --skip-artifact-check --output outputs/fallback.json"
