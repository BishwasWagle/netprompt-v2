"""Runtime Manager configuration. Defaults are starting points — tune
empirically on the testbed (design §5.6, §7.6)."""
import os

# --- testbed topology (design §10.1; milestone-II-latest mechanics) ---
THRIFT_PORTS = {"s1": 9090, "s2": 9091, "s3": 9092}
PORT_PRIMARY = 11      # s1 -> s2
PORT_BACKUP = 12       # s1 -> s3

# The edge host has TWO identities (milestone-II-latest fix): the active path
# is selected by which interface owns 10.0.0.100 + the drones' static ARP,
# not by switch tables alone.
EDGE_MAC = "00:00:00:00:00:0b"           # primary identity (edge-eth0 -> s2)
EDGE_MAC_BACKUP = "00:00:00:00:00:0c"    # backup identity (edge-eth1 -> s3)
EDGE_MACS = {"primary": EDGE_MAC, "backup": EDGE_MAC_BACKUP}
EDGE_IFACES = {"primary": "edge-eth0", "backup": "edge-eth1"}
EDGE_PORTS = {"primary": PORT_PRIMARY, "backup": PORT_BACKUP}
# A reroute is MULTI-SWITCH (M0/C2 finding): s1 selects the relay egress port,
# and the destination RELAY switch must itself forward the active edge identity
# to the edge — under a primary SFC, s3 has no 0c entry, so without this the
# frame is dropped at s3 and the path is dead despite s1 being correct. Both
# relays reach the edge on port 2 (s2-eth2 / s3-eth2 per the topology).
RELAY_EDGE = {"primary": ("s2", 2), "backup": ("s3", 2)}
EDGE_IP = "10.0.0.100"
SUBNET = "10.0.0.0/24"
EDGE_HOST = "edge"
DRONE_HOSTS = tuple(f"d{i}" for i in range(1, 11))

# Per-switch valid egress ports (s1: 10 drones + 2 relays; s2/s3: s1-side + edge-side)
SWITCH_PORTS = {"s1": set(range(1, 13)), "s2": {1, 2}, "s3": {1, 2}}

# Gate L2 invariants (design §11): every drone MAC must stay routable, and the
# edge must stay reachable on AT LEAST ONE of its identities.
DRONE_MACS = tuple(f"00:00:00:00:00:{i:02x}" for i in range(1, 11))

# --- canonical milestone-II tree on network-node (per Bishwas: the
#     milestone-II-latest copy is authoritative — the sole archived snapshot;
#     earlier duplicate trees were removed). Binding rule/P4 paths resolve
#     against this root. ---
NODE_TREE_ROOT = os.environ.get(
    "NETPROMPT_TREE_ROOT",
    "/home/cc/Run-time-Manager-2/network/milestone-II-latest/netprompt-milestone-II")

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
REGRESSION_EPS = 0.02          # rung-3 margin-drop deadband: a real (noisy) monitor
                               #   jitters vs_baseline by ~1e-7 on idle flows, so a
                               #   regression must exceed this to be attributed to us
                               #   (fixtures regress by ~1.0+, far above the floor)
REGEN_MAX_REJECTS = 3          # K gate-rejections before Tier-2 returns None

# --- Tier-2 regen serving (M7; design §7.3 — in-process HF transformers +
#     transformers-cfg GBNF constrained decoding, FP16/greedy). The regen Coder
#     model sits on the 2nd GPU so it doesn't contend with the orchestrator's
#     Instruct model on cuda:0. Pin REVISION for reproducibility (DoD #4). ---
REGEN_MODEL = os.environ.get("NETPROMPT_REGEN_MODEL", "Qwen/Qwen2.5-Coder-1.5B-Instruct")
REGEN_REVISION = os.environ.get("NETPROMPT_REGEN_REVISION", "") or None  # "" -> latest
REGEN_DEVICE = os.environ.get("NETPROMPT_REGEN_DEVICE", "cuda:1")
REGEN_MAX_NEW_TOKENS = int(os.environ.get("NETPROMPT_REGEN_MAX_NEW_TOKENS", "64"))

# --- tune knob quantization (design §7.3): knob -> step size ---
KNOB_STEPS = {
    "tbf_rate_mbit": 10,
    "pfifo_limit": 10,
}

# --- knob -> tc command templates (design §10.2; defaults from
#     apply_sfc_queue_policy in the milestone-II experiment).
#     QDISC MODEL (M0 Issue-2 decision, "Model B" = milestone-II): the target
#     field's drone-eth0 root qdisc is WHOLLY deployer-owned (just the knob), so
#     `replace root` is correct — there is no impairment to preserve there. The
#     scenario environment (delay/loss) lives on the SWITCH-side veths
#     (s1-eth{N}), which the deployer never writes and the monitor reads (M5).
#     A TUNE therefore composes with the environment without disturbing it, and
#     rollback only has to restore the baseline knob (SFC_QOS_BASELINE). ---
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

# --- per-SFC qos BASELINE (Issue-2 / Model B): the knob value deploy() installs
#     as the field's deployer-owned drone-eth0 root qdisc, and the value rollback
#     reverts a TUNE to. Mirrors apply_sfc_queue_policy in the milestone-II
#     experiment. A real binding may carry its own `qos`; this is the fallback
#     when it does not (until the planner supplies it — M-K). ReliableRelay's
#     policy is a netem (not a knob) and it adapts via reroute, so no baseline. ---
SFC_QOS_BASELINE = {
    "LowLatencyVideoSFC":    {"pfifo_limit": 20},
    "BandwidthOptimizedSFC": {"tbf_rate_mbit": 80},
    "EnergyAwareSFC":        {"tbf_rate_mbit": 5},
    "ReliableRelaySFC":      {},
}
