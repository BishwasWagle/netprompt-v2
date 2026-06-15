#!/usr/bin/env bash
#
# setup_kg_node.sh — install a local Neo4j knowledge-graph on THIS node.
#
# Third companion to setup_gpu_node.sh + setup_testbed_node.sh, for the
# single-node consolidation: instead of pointing the runtime at a remote
# controller-node Neo4j, stand the KG up locally. An EMPTY graph is sufficient
# for the M6 acceptance tests and the soak — the runtime path only writes its
# own records (Verdict / LastKnownGood / EscalationTicket / switch status) and
# reads back its own last-known-good; it does NOT depend on pre-seeded data.
# (read_field_requirements() / :AgriculturalField is used by the LLM/RAG path,
# not the node-runtime tests.)
#
# Target: Ubuntu 24.04. Installs Neo4j 5.x community from the official apt repo,
# sets the initial password, starts it under systemd, and verifies bolt + auth.
#
# Usage:
#   ./setup_kg_node.sh [--smoke] [--password PASS] [--bind-lan]
#
#   --smoke        After install, round-trip a Cypher query over bolt to prove
#                  the auth + connection path.
#   --password P   Initial neo4j password (default: netprompt123 — the in-repo
#                  default from runtime/config.py; CHANGE IT for anything real).
#   --bind-lan     Bind Neo4j to 0.0.0.0 so the Browser/bolt are reachable from
#                  the LAN (default: localhost only). Exposes the DB — only on a
#                  trusted network.
#
# Run from deploy/gpu-node/. Needs sudo. Idempotent: re-running reuses an
# already-installed/initialised instance and just (re)starts + verifies it.
#
# After this, source the env so the runtime + orchestrator resolve the local KG:
#   source ./gpu-node.env
#   #   runtime reads NETPROMPT_KG_URI/USER/PASS ; orchestrator reads NEO4J_*

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS="${NEO4J_PASSWORD:-netprompt123}"
DO_SMOKE=0
BIND_LAN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke)     DO_SMOKE=1; shift ;;
    --password)  PASS="$2"; shift 2 ;;
    --bind-lan)  BIND_LAN=1; shift ;;
    -h|--help)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)           echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- step 0: OS
say "Step 0 — host check"
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  echo "OS: ${PRETTY_NAME:-unknown}"
  [[ "${VERSION_ID:-}" == "24.04" ]] || warn "expected Ubuntu 24.04; continuing anyway."
fi

# ---------------------------------------------------------------- step 1: apt repo
say "Step 1 — Neo4j apt repo"
if ! command -v neo4j >/dev/null 2>&1; then
  sudo install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key -o /tmp/neo4j.key \
    || die "could not fetch the Neo4j repo key (no network?)"
  sudo gpg --dearmor --yes -o /etc/apt/keyrings/neo4j.gpg /tmp/neo4j.key
  echo "deb [signed-by=/etc/apt/keyrings/neo4j.gpg] https://debian.neo4j.com stable 5" \
    | sudo tee /etc/apt/sources.list.d/neo4j.list >/dev/null
  sudo apt-get update
else
  echo "neo4j already installed: $(neo4j --version 2>/dev/null || echo present) — skipping repo add."
fi

# ---------------------------------------------------------------- step 2: install
say "Step 2 — install Neo4j (community)"
if command -v neo4j >/dev/null 2>&1; then
  echo "already installed: $(dpkg -l neo4j 2>/dev/null | awk '/^ii/{print $3}')"
else
  sudo apt-get install -y neo4j
fi
command -v cypher-shell >/dev/null 2>&1 || die "cypher-shell missing after install"

# ---------------------------------------------------------------- step 3: initial password
say "Step 3 — initial password"
# set-initial-password only takes effect before the DB is first started.
AUTH_STORE=/var/lib/neo4j/data/dbms/auth
if [[ -f "$AUTH_STORE" ]]; then
  warn "auth already initialised ($AUTH_STORE exists) — leaving the existing password in place."
  warn "to change it: stop neo4j, 'sudo -u neo4j neo4j-admin dbms set-initial-password ...' after removing the auth store, or use ALTER CURRENT USER."
else
  sudo systemctl stop neo4j 2>/dev/null || true
  sudo -u neo4j neo4j-admin dbms set-initial-password "$PASS"
  echo "initial password set for user 'neo4j'."
fi

# ---------------------------------------------------------------- step 4: bind + start
say "Step 4 — start Neo4j (systemd)"
if [[ "$BIND_LAN" == "1" ]]; then
  warn "binding to 0.0.0.0 — DB will be reachable on the LAN; ensure the network is trusted."
  CONF=/etc/neo4j/neo4j.conf
  sudo sed -i 's/^#\?server.default_listen_address=.*/server.default_listen_address=0.0.0.0/' "$CONF"
  grep -q '^server.default_listen_address=0.0.0.0' "$CONF" \
    || echo 'server.default_listen_address=0.0.0.0' | sudo tee -a "$CONF" >/dev/null
fi
sudo systemctl enable neo4j >/dev/null 2>&1 || true
sudo systemctl restart neo4j

# ---------------------------------------------------------------- step 5: verify
say "Step 5 — verify bolt + auth"
echo "waiting for bolt 7687 (up to ~40s)…"
up=0
for _ in $(seq 1 40); do
  if cypher-shell -a bolt://localhost:7687 -u neo4j -p "$PASS" "RETURN 1;" >/dev/null 2>&1; then up=1; break; fi
  sleep 1
done
[[ "$up" == "1" ]] || die "Neo4j did not accept a bolt connection — check 'systemctl status neo4j' and /var/log/neo4j/."
VER="$(cypher-shell -a bolt://localhost:7687 -u neo4j -p "$PASS" \
        "CALL dbms.components() YIELD name,versions,edition RETURN name+' '+versions[0]+' '+edition AS v;" \
        --format plain 2>/dev/null | tail -1)"
echo "  bolt 7687: UP — connected to $VER"

# ---------------------------------------------------------------- step 6: smoke
if [[ "$DO_SMOKE" == "1" ]]; then
  say "Step 6 — smoke: write + read + delete a node"
  cypher-shell -a bolt://localhost:7687 -u neo4j -p "$PASS" --format plain \
    "MERGE (m:__SetupCheck__ {id:'kg-setup-smoke'}) RETURN m.id;
     MATCH (m:__SetupCheck__ {id:'kg-setup-smoke'}) DETACH DELETE m;" >/dev/null
  echo "  round-trip OK — write/read/delete over bolt works."
else
  say "Step 6 — skipped (pass --smoke to round-trip a query)"
fi

say "Done."
echo "Wire the runtime + orchestrator to this local KG (already set in gpu-node.env):"
echo "  export NETPROMPT_KG_URI=bolt://localhost:7687 NETPROMPT_KG_USER=neo4j NETPROMPT_KG_PASS=$PASS  # runtime"
echo "  export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=$PASS                    # orchestrator"
echo "Browser UI: http://localhost:7474 (tunnel it: ssh -L 7474:localhost:7474 -L 7687:localhost:7687 ...)"
