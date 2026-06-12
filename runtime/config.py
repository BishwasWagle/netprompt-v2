"""Runtime Manager configuration. Defaults are starting points — tune
empirically on the testbed (design §5.6, §7.6)."""
import os

# --- testbed topology (design §10.1) ---
THRIFT_PORTS = {"s1": 9090, "s2": 9091, "s3": 9092}
EDGE_MAC = "00:00:00:00:00:0b"
PORT_PRIMARY = 11      # s1 -> s2
PORT_BACKUP = 12       # s1 -> s3

# --- KG (controller-node hosts Neo4j; existing scripts use the same endpoint) ---
KG_URI = os.environ.get("NETPROMPT_KG_URI", "bolt://controller-node:7687")
KG_USER = os.environ.get("NETPROMPT_KG_USER", "neo4j")
KG_PASS = os.environ.get("NETPROMPT_KG_PASS", "netprompt123")

# --- hysteresis (design §5.6): K-of-M consecutive probes to flip state ---
HYSTERESIS_K = 3
HYSTERESIS_M = 5
PROBE_INTERVAL_S = 2.0
BASELINE_WINDOW_PROBES = 5     # pre-cutover steady-state window (design §5.3)

# --- adaptation engine (design §7) ---
BUDGET_N = 6                   # applied attempts per episode
HEADROOM_TAU = 0.15            # min margin for Commit-healthy; below = marginal
EPS_IMPROVE = 0.02             # strict-progress threshold on headroom
REGEN_MAX_REJECTS = 3          # K gate-rejections before Tier-2 returns None

# --- tune knob quantization (design §7.3): knob -> step size ---
KNOB_STEPS = {
    "tbf_rate_mbit": 10,
    "pfifo_limit": 10,
}
