#!/usr/bin/env bash
#
# setup_testbed_node.sh — install the BMv2 + Mininet testbed on THIS node.
#
# Companion to setup_gpu_node.sh. Lets one box host everything: the P4/BMv2
# testbed, the Runtime Manager, and the LLM serving endpoint. The GPU plays no
# role here — BMv2/Mininet are CPU + network-namespace tools.
#
# Target: Ubuntu 24.04. Installs Mininet + BMv2 (simple_switch /
# simple_switch_CLI) + the net tooling launch_network.py needs, then does an
# optional smoke launch of the resident topology against the in-repo compiled
# P4 JSON and rule files (this is effectively an automated M0 bring-up).
#
# Usage:
#   ./setup_testbed_node.sh [--smoke] [--build-bmv2] [--sfc NAME] [--tree DIR]
#
#   --smoke        After install, launch the topology, confirm thrift ports
#                  9090/9091/9092 come up, then tear it down cleanly.
#   --build-bmv2   Build BMv2 from source instead of the p4lang apt repo
#                  (use if the apt package isn't published for your release;
#                  takes 10-20 min).
#   --sfc NAME     SFC used for the smoke launch (default: low_latency).
#   --tree DIR     milestone-II tree root holding compiled_p4/ + p4_multihop_rules/
#                  (default: <repo>/network/milestone-II-latest/netprompt-milestone-II)
#
# Run from deploy/gpu-node/. Needs sudo. The smoke launch itself runs as root
# (Mininet requirement) using SYSTEM python3 + the apt-installed mininet module
# — NOT the venv.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
TREE="$REPO_ROOT/network/milestone-II-latest/netprompt-milestone-II"
LAUNCHER="$REPO_ROOT/runtime/tools/launch_network.py"
SFC="low_latency"
DO_SMOKE=0
BUILD_BMV2=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke)      DO_SMOKE=1; shift ;;
    --build-bmv2) BUILD_BMV2=1; shift ;;
    --sfc)        SFC="$2"; shift 2 ;;
    --tree)       TREE="$2"; shift 2 ;;
    -h|--help)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$LAUNCHER" ]] || die "launcher not found at $LAUNCHER (run from deploy/gpu-node/ inside the repo)"

# ---------------------------------------------------------------- step 0: OS
say "Step 0 — host check"
VERSION_ID=""
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  echo "OS: ${PRETTY_NAME:-unknown}"
  [[ "${VERSION_ID:-}" == "24.04" ]] || warn "expected Ubuntu 24.04; continuing anyway."
fi

# ---------------------------------------------------------------- step 1: net tooling
say "Step 1 — system + network tooling"
sudo apt-get update
# nc (thrift probe), ethtool (offload disable), net-tools (arp), iproute2 (ip),
# iperf/iperf3 (traffic), plus repo-add prerequisites.
sudo apt-get install -y \
  netcat-openbsd ethtool net-tools iproute2 iperf iperf3 \
  git curl gnupg ca-certificates python3

# ---------------------------------------------------------------- step 2: Mininet
say "Step 2 — Mininet"
if python3 -c "import mininet" 2>/dev/null; then
  echo "mininet python module already present."
else
  sudo apt-get install -y mininet
fi
python3 -c "import mininet; print('mininet module OK')" \
  || die "mininet still not importable by system python3"

# ---------------------------------------------------------------- step 3: BMv2
say "Step 3 — BMv2 (simple_switch / simple_switch_CLI)"
have_bmv2() { command -v simple_switch >/dev/null 2>&1 && command -v simple_switch_CLI >/dev/null 2>&1; }

build_bmv2_from_source() {
  say "Step 3b — building BMv2 from source (10-20 min)"
  local src=/tmp/behavioral-model
  rm -rf "$src"
  git clone --depth 1 https://github.com/p4lang/behavioral-model.git "$src"
  # Ubuntu 24.04 (noble) marks the system Python as externally-managed (PEP 668).
  # BMv2's bundled PI/P4Runtime component runs a system-wide `pip install` of its
  # Python bindings during `make install`, which PEP 668 blocks — aborting the
  # install before simple_switch is staged. Allow it for this build; the only
  # system-wide pip package involved is the PI binding.
  ( cd "$src"
    export PIP_BREAK_SYSTEM_PACKAGES=1
    ./install_deps.sh
    ./autogen.sh
    ./configure
    make -j"$(nproc)"
    sudo PIP_BREAK_SYSTEM_PACKAGES=1 make install
    sudo ldconfig )
}

if have_bmv2; then
  echo "BMv2 already installed: $(command -v simple_switch)"
elif [[ "$BUILD_BMV2" == "1" ]]; then
  build_bmv2_from_source
else
  echo "Trying the p4lang apt repo for Ubuntu ${VERSION_ID:-unknown}…"
  REPO_URL="https://download.opensuse.org/repositories/home:/p4lang/xUbuntu_${VERSION_ID}"
  if curl -fsSL "${REPO_URL}/Release.key" -o /tmp/p4lang.key 2>/dev/null; then
    sudo install -d -m 0755 /etc/apt/keyrings
    # scope the key to THIS repo via [signed-by=...], NOT trusted.gpg.d (which
    # would trust it for every repo on the box).
    gpg --dearmor < /tmp/p4lang.key | sudo tee /etc/apt/keyrings/p4lang.gpg >/dev/null
    echo "deb [signed-by=/etc/apt/keyrings/p4lang.gpg] ${REPO_URL}/ /" \
      | sudo tee /etc/apt/sources.list.d/home-p4lang.list >/dev/null
    if ! { sudo apt-get update && sudo apt-get install -y p4lang-bmv2; }; then
      warn "apt p4lang-bmv2 failed — removing the repo so a broken source can't poison later apt runs."
      sudo rm -f /etc/apt/sources.list.d/home-p4lang.list /etc/apt/keyrings/p4lang.gpg
    fi
  else
    warn "no p4lang apt repo for xUbuntu_${VERSION_ID}."
  fi
  have_bmv2 || { warn "apt path didn't yield BMv2 — falling back to source build."; build_bmv2_from_source; }
fi
have_bmv2 || die "BMv2 not installed. Re-run with --build-bmv2."

# ---------------------------------------------------------------- step 4: verify
say "Step 4 — verify toolchain"
echo "simple_switch     : $(command -v simple_switch)"
echo "simple_switch_CLI : $(command -v simple_switch_CLI)"
echo "mn                : $(command -v mn || echo MISSING)"
[[ -d "$TREE/compiled_p4" ]]       || die "missing $TREE/compiled_p4 (set --tree)"
[[ -d "$TREE/p4_multihop_rules" ]] || die "missing $TREE/p4_multihop_rules (set --tree)"
echo "tree              : $TREE  (compiled_p4/ + p4_multihop_rules/ present)"

# ---------------------------------------------------------------- step 5: smoke launch
if [[ "$DO_SMOKE" == "1" ]]; then
  say "Step 5 — smoke launch ($SFC), confirm thrift, tear down"
  P4_JSON="$TREE/compiled_p4/${SFC}.json"
  [[ -f "$P4_JSON" ]] || die "no compiled JSON at $P4_JSON"

  sudo mn -c >/dev/null 2>&1 || true            # clear any stale mininet state

  # Resident launcher runs as root with system python3 (mininet lives there).
  sudo env NETPROMPT_TREE_ROOT="$TREE" python3 "$LAUNCHER" \
      --p4-json "$P4_JSON" --rules-dir "$TREE/p4_multihop_rules" --sfc "$SFC" \
      >/tmp/testbed_smoke.log 2>&1 &

  cleanup() { sudo pkill -TERM -f "launch_network.py" 2>/dev/null || true; sleep 3; sudo mn -c >/dev/null 2>&1 || true; }
  trap cleanup EXIT

  echo "waiting for switches to come up (up to ~40s)…"
  ok=1
  for port in 9090 9091 9092; do
    up=0
    for _ in $(seq 1 40); do
      if nc -z 127.0.0.1 "$port" 2>/dev/null; then up=1; break; fi
      sleep 1
    done
    if [[ "$up" == "1" ]]; then echo "  thrift $port: UP"; else echo "  thrift $port: DOWN"; ok=0; fi
  done

  if [[ "$ok" == "1" ]]; then
    echo "  all three switches reachable — testbed launches correctly."
  else
    warn "not all thrift ports came up — inspect /tmp/testbed_smoke.log and /tmp/s*.log"
  fi
  echo "tearing down…"
  cleanup
  trap - EXIT
  [[ "$ok" == "1" ]] || die "smoke failed: not all thrift ports came up (see /tmp/testbed_smoke.log, /tmp/s*.log)"
else
  say "Step 5 — skipped (pass --smoke to launch + verify the topology)"
fi

say "Done."
echo "To run the resident testbed yourself (leave it up; mutate out-of-band):"
echo "  sudo env NETPROMPT_TREE_ROOT=\"$TREE\" python3 $LAUNCHER \\"
echo "    --p4-json \"$TREE/compiled_p4/${SFC}.json\" \\"
echo "    --rules-dir \"$TREE/p4_multihop_rules\" --sfc ${SFC}"
echo
echo "Then, for the runtime/soak, export the tree root so config.py resolves paths here:"
echo "  export NETPROMPT_TREE_ROOT=\"$TREE\""
echo "  # KG: controller-node must resolve, or override:"
echo "  #   export NETPROMPT_KG_URI=bolt://<controller-ip>:7687"
