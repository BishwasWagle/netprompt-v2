# Shared setup for the E1/E2a/E2b/E3 reproduce scripts. SOURCE this — do not run it.
#
# Resolves the repo root from this file's own location (works regardless of the
# checkout dir name, like gpu-node.env), cd's there, and sources the node env so
# NETPROMPT_ROOT / NETPROMPT_KG_* are exported. Resolves the two interpreters the
# drivers use: the venv python for app + pytest, system python3 for Mininet/BMv2.
# Override either with NETPROMPT_PY / NETPROMPT_SYS_PY.

REPRO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$REPRO_DIR/.." && pwd)"
cd "$REPO"

set +u                                   # gpu-node.env is sourced defensively
# shellcheck disable=SC1091
source deploy/gpu-node/gpu-node.env
set -u

PY="${NETPROMPT_PY:-$HOME/netprompt-venv/bin/python}"      # venv: orchestrator + pytest
SYS_PY="${NETPROMPT_SYS_PY:-/usr/bin/python3}"             # system: Mininet/BMv2

[ -x "$PY" ] || { echo "venv python not found at $PY (set NETPROMPT_PY)"; exit 1; }
