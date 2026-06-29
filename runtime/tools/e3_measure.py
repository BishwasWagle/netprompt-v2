"""E3 measurement step — one (scenario × arm) cell on the resident 3-switch fabric.

Topology-equivalent comparative: all arms run on the SAME live BMv2 fabric (the
driver launches `launch_network.py` with the cell's SFC P4 program + a CLEAN access
profile, and starts sane 5 Mbit/drone traffic). This step deploys, captures a
healthy baseline, then INJECTS the scenario's fault on the relay links (exactly the
M6 mechanism) so the only differences across arms are *which SFC is chosen* and
*whether the runtime adapts*:

  static   : a FIXED SFC (LowLatencyVideoSFC), deploy → baseline → fault → measure, NO adapt.
  rule     : the if/elif-ladder SFC, deploy → baseline → fault → measure, NO adapt.
  proposed : the LLM-chosen SFC (--sfc), deploy → baseline → fault → FULL RuntimeManager
             episode (observe → tiered adapt → verdict).

Bounds are CALIBRATED to the testbed (M6 §6): a ~70 ms latency bound is met on
primary (~40 ms) and backup (~52 ms) but blown by the +100 ms relay fault — so the
only fix is a reroute, and it can actually commit. bw is 5 Mbit (met at 15 Mbit
offered), so LATENCY is the gate, not offered load (validity §2.1).

Faults (--fault):
  none          : no injection (healthy control — every arm should commit)
  primary_relay : +100 ms on s1-eth11 (primary) — reroutable (backup ~52 ms stays good)
  both_relays   : +100 ms on s1-eth11 AND s1-eth12 — infeasible → honest escalate

Run AFTER the fabric is up + iperf is flowing. Prints ONE JSON line of metrics.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time

from runtime.contracts import BACKUP, DeploymentSpec, Envelope, PRIMARY, REROUTE, TUNE
from runtime.deployer import Deployer
from runtime.gate import ValidationGate
from runtime.node_runner import NodeRunner
from runtime.runtime_manager import RuntimeManager
from runtime.monitors.network_monitor import NetworkMonitor, NodeSampler
from runtime.kg_client import KGClient
from runtime.tools.run_episode import real_binding, DEFAULT_HOST_MAP

STATIC_SFC = "LowLatencyVideoSFC"

# scenario -> SFC, the published rule-based if/elif ladder (collapsed to the E3
# fault scenarios: any relay issue is the "reliable relay" regime).
RULE_LADDER = {
    "primary_fault": "ReliableRelaySFC",
    "backup_fault": "ReliableRelaySFC",
    "ddil": "ReliableRelaySFC",
    "healthy": "LowLatencyVideoSFC",
}

TARGET_FIELD = "F1"

# Calibrated, SATISFIABLE bounds (M6 REQ) — latency is the gate; bw met at sane load.
CAL_REQ = {"F1": Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20),
           "F2": Envelope(max_latency_ms=120, min_bandwidth_mbps=2, max_loss_percent=20)}
CAL_LAT, CAL_BW, CAL_LOSS = 70, 5, 20

# scenario -> the relay-link fault to inject after the healthy baseline.
#   primary_fault: degrades s1-eth11 (primary relay) -> isolates SFC SELECTION
#                  (LowLatency/primary fails; Reliable/backup dodges it, no adapt).
#   backup_fault : degrades s1-eth12 (backup relay) -> isolates RUNTIME ADAPTATION
#                  (Reliable/backup fails; only the adaptive arm reroutes to primary).
#   ddil         : degrades BOTH relays -> infeasible -> honest escalate.
SCENARIO_FAULT = {"healthy": "none", "primary_fault": "primary_relay",
                  "backup_fault": "backup_relay", "ddil": "both_relays"}


def _sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def _change_netem(intf: str, spec: str):
    """Shift the netem child qdisc on a relay link (matches M6 _change_netem)."""
    out = _sh(f"sudo tc qdisc show dev {intf}")
    m = re.search(r"qdisc netem (\w+): parent (\S+)", out)
    if not m:
        raise RuntimeError(f"no netem child on {intf}: {out!r}")
    _sh(f"sudo tc qdisc change dev {intf} parent {m.group(2)} handle {m.group(1)}: netem {spec}")


def inject_fault(fault: str):
    if fault == "none":
        return
    if fault == "primary_relay":
        _change_netem("s1-eth11", "delay 100ms")
    elif fault == "backup_relay":
        _change_netem("s1-eth12", "delay 100ms")
    elif fault == "both_relays":
        _change_netem("s1-eth11", "delay 100ms")
        _change_netem("s1-eth12", "delay 100ms")
    else:
        raise SystemExit(f"unknown fault {fault!r}")


def _flow(fm) -> dict:
    return {"field": fm.field_id, "rtt_ms": round(fm.rtt_avg_ms, 3),
            "throughput_mbps": round(fm.throughput_mbps, 3),
            "loss_pct": round(fm.loss_percent, 3),
            "met": bool(fm.met), "margin": round(fm.margin, 4)}


def run_cell(arm: str, scenario: str, sfc: str | None, fault: str | None) -> dict:
    if fault is None:
        fault = SCENARIO_FAULT.get(scenario, "none")

    kg = KGClient.connect()
    try:
        if arm == "static":
            sfc = STATIC_SFC
        elif arm == "rule":
            sfc = RULE_LADDER[scenario]
        elif arm in ("proposed", "nokg"):
            if not sfc:
                raise SystemExit(f"--sfc is required for the {arm} arm (the LLM's pick)")
        else:
            raise SystemExit(f"unknown arm {arm!r}")
        base = kg.build_envelope(sfc, TARGET_FIELD)        # per-SFC action space
    finally:
        kg.close()

    # calibrated bounds + the SFC's own legal action space (LowLatency=primary-only;
    # ReliableRelay=primary+backup -> reroute possible).
    legal_tiers, legal_paths = base.legal_tiers, base.legal_paths
    if arm == "nokg":
        # NoKG ablation (Table VII): keep the SFC + the adaptive loop, but remove the
        # KG's topology reasoning. build_envelope derives the REROUTE tier from the KG
        # knowing the alternative relay path; without the KG that path is unknown, so
        # reroute is unavailable and a path fault cannot be escaped (the resilience the
        # KG enables; cf. the draft's NoKG losing under relay-failure/DDIL). Single
        # factor: only the KG-enabled reroute capability is removed.
        legal_tiers = base.legal_tiers - frozenset({REROUTE})
    env = Envelope(max_latency_ms=CAL_LAT, min_bandwidth_mbps=CAL_BW, max_loss_percent=CAL_LOSS,
                   legal_tiers=legal_tiers, legal_paths=legal_paths,
                   knob_ranges=base.knob_ranges)
    cid = f"e3-{arm}-{scenario}"
    spec = DeploymentSpec(sfc=sfc, binding=real_binding(sfc), envelope=env,
                          correlation_id=cid, target_field=TARGET_FIELD)

    t0 = time.monotonic()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(spec)
    sampler = NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2)
    monitor = NetworkMonitor(sampler, CAL_REQ, TARGET_FIELD, cid, k=2, m=2)
    monitor.capture_baseline()                             # healthy, pre-fault
    inject_fault(fault)                                    # <-- the scenario fault

    if arm in ("proposed", "nokg"):
        rm = RuntimeManager(deployer, monitor, ValidationGate())
        result = rm.run_episode(spec)
        v = result.verdict
        outcome, tier = v.outcome, v.tier_reached
        reason = result.ticket.reason if result.ticket else ""
        report = monitor.observe_window()                  # final state after the episode
    else:                                                  # static / rule — measure under fault, NO adapt
        report = monitor.observe_window()
        outcome = "met" if report.target_sla_met else "violated"
        tier, reason = None, ""
    elapsed = round(time.monotonic() - t0, 2)

    flows = [_flow(report.target)] + [_flow(f) for f in report.non_target]
    return {
        "arm": arm, "scenario": scenario, "fault": fault, "sfc": sfc,
        "target_field": TARGET_FIELD, "outcome": outcome, "tier_reached": tier,
        "reason": reason, "final_path": deployer.state.get("path"),
        "target_sla_met": bool(report.target_sla_met), "headroom": round(report.headroom, 4),
        "switch_status": report.switch_status, "flows": flows, "wall_seconds": elapsed,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", required=True, choices=("static", "rule", "proposed", "nokg"))
    ap.add_argument("--scenario", required=True, choices=sorted(SCENARIO_FAULT))
    ap.add_argument("--sfc", default=None, help="proposed arm only: the LLM-chosen SFC")
    ap.add_argument("--fault", default=None,
                    choices=("none", "primary_relay", "backup_relay", "both_relays"),
                    help="override the scenario's default fault")
    args = ap.parse_args()
    print(json.dumps(run_cell(args.arm, args.scenario, args.sfc, args.fault)))


if __name__ == "__main__":
    main()
