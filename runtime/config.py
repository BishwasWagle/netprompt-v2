"""Runtime Manager configuration. Defaults are starting points — tune
empirically on the testbed (design §5.6, §7.6)."""
import os

# --- testbed topology (design §10.1) ---
THRIFT_PORTS = {"s1": 9090, "s2": 9091, "s3": 9092}
EDGE_MAC = "00:00:00:00:00:0b"
PORT_PRIMARY = 11      # s1 -> s2
PORT_BACKUP = 12       # s1 -> s3

# Per-switch valid egress ports (s1: 10 drones + 2 relays; s2/s3: s1-side + edge-side)
SWITCH_PORTS = {"s1": set(range(1, 13)), "s2": {1, 2}, "s3": {1, 2}}

# Every switch must keep all flows routable (design §11 L2): 10 drones + edge.
DRONE_MACS = tuple(f"00:00:00:00:00:{i:02x}" for i in range(1, 11))
REQUIRED_MACS = DRONE_MACS + (EDGE_MAC,)

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

# --- knob -> tc command templates (design §10.2; defaults from
#     apply_sfc_queue_policy in the milestone-II experiment) ---
TC_TEMPLATES = {
    "tbf_rate_mbit": "tc qdisc replace dev {dev} root tbf rate {value}mbit burst 32kbit latency 50ms",
    "pfifo_limit": "tc qdisc replace dev {dev} root pfifo limit {value}",
}

# Which binding key carries each switch's rule file (design §10.4).
SWITCH_RULES_KEYS = {"s1": "access_rules", "s2": "relay_rules", "s3": "backup_rules"}

# --- per-SFC action space (design §9: runtime-owned, NOT from the planner).
#     Bounds come from the KG; what we are allowed to TURN is ours. netem
#     impairments are never knobs (§7.3). Defaults follow the design's
#     reasoning: LowLatency cannot meet its bound on the backup path, so
#     reroute is not legal for it; ReliableRelay's whole point is the backup. ---
DEFAULT_MAX_LOSS_PERCENT = 2.0          # fields carry no loss bound in the KG
SFC_ACTION_SPACE = {
    "LowLatencyVideoSFC":   {"legal_tiers": frozenset(("tune", "regen")),
                             "legal_paths": frozenset(("primary",)),
                             "knob_ranges": {"pfifo_limit": (10, 50)}},
    "ReliableRelaySFC":     {"legal_tiers": frozenset(("tune", "reroute", "regen")),
                             "legal_paths": frozenset(("primary", "backup")),
                             "knob_ranges": {}},
    "BandwidthOptimizedSFC": {"legal_tiers": frozenset(("tune", "regen")),
                              "legal_paths": frozenset(("primary",)),
                              "knob_ranges": {"tbf_rate_mbit": (5, 80)}},
    "EnergyAwareSFC":       {"legal_tiers": frozenset(("tune",)),
                             "legal_paths": frozenset(("primary",)),
                             "knob_ranges": {"tbf_rate_mbit": (5, 40)}},
}
