"""E3 measurement step — one (scenario × arm) cell on the resident 3-switch fabric.

Topology-equivalent comparative: all arms run on the SAME live BMv2 fabric (the
driver launches `launch_network.py` with the cell's SFC P4 program + scenario
netem, and starts traffic), so the only difference is *how the SFC is chosen* and
*whether the runtime adapts*:

  static   : a FIXED SFC, deployed + measured over one window, NO adaptation.
  rule     : the if/elif-ladder SFC for the scenario, deployed + measured, NO adapt.
  proposed : the LLM-chosen SFC (passed in via --sfc), driven through the FULL
             RuntimeManager episode (deploy → observe → tiered adapt → verdict).

Run AFTER the fabric is up (thrift 9090/9091/9092 answering) and `iperf` is flowing.
Prints ONE JSON line of measured metrics (per-field RTT/loss/throughput + verdict).
Envelope/requirements come from the KG; the real monitor samples the live fabric.

Usage (the run_e3.sh driver supplies the right --sfc + a matching launched fabric):
  python -m runtime.tools.e3_measure --arm static   --scenario congestion
  python -m runtime.tools.e3_measure --arm rule      --scenario congestion
  python -m runtime.tools.e3_measure --arm proposed  --scenario congestion --sfc ReliableRelaySFC
"""
from __future__ import annotations

import argparse
import json
import time

from runtime.contracts import DeploymentSpec, Envelope
from runtime.deployer import Deployer
from runtime.gate import ValidationGate
from runtime.node_runner import NodeRunner
from runtime.runtime_manager import RuntimeManager
from runtime.monitors.network_monitor import NetworkMonitor, NodeSampler
from runtime.kg_client import KGClient
from runtime.tools.run_episode import real_binding, DEFAULT_HOST_MAP

# The runtime's static fixed-SFC baseline (the milestone-II "static" arm always
# ships LowLatencyVideoSFC regardless of scenario).
STATIC_SFC = "LowLatencyVideoSFC"

# scenario -> SFC, the published rule-based if/elif ladder
# (baselines/baseline_rule_based_experiment.py select_sfc_if_else).
RULE_LADDER = {
    "battery_depletion": "EnergyAwareSFC",
    "congestion": "ReliableRelaySFC",
    "ddil": "ReliableRelaySFC",
    "relay_failure": "ReliableRelaySFC",
    "baseline": "BandwidthOptimizedSFC",
    "low_latency": "LowLatencyVideoSFC",
}

TARGET_FIELD = "F1"                       # consistent target across arms/scenarios
MONITOR_FIELDS = ("F1", "F2")            # the fields the host_map (and port_map) cover


def _flow(fm) -> dict:
    return {"field": fm.field_id, "rtt_ms": round(fm.rtt_avg_ms, 3),
            "throughput_mbps": round(fm.throughput_mbps, 3),
            "loss_pct": round(fm.loss_percent, 3),
            "met": bool(fm.met), "margin": round(fm.margin, 4)}


def _requirements(kg) -> dict:
    """{F1,F2: Envelope(bounds-only)} for the monitor's per-flow checks."""
    allreq = kg.read_field_requirements()                 # {field: Envelope bounds}
    return {f: allreq[f] for f in MONITOR_FIELDS if f in allreq}


def _build_stack(spec, requirements):
    runner = NodeRunner()
    deployer = Deployer(runner, host_map=DEFAULT_HOST_MAP)
    deployer.deploy(spec)                                  # idempotent on the resident net
    sampler = NodeSampler(NodeRunner(), DEFAULT_HOST_MAP)
    monitor = NetworkMonitor(sampler, requirements, TARGET_FIELD, spec.correlation_id)
    return deployer, monitor


def run_cell(arm: str, scenario: str, sfc: str | None) -> dict:
    kg = KGClient.connect()
    try:
        requirements = _requirements(kg)
        if arm == "static":
            sfc = STATIC_SFC
        elif arm == "rule":
            sfc = RULE_LADDER[scenario]
        elif arm == "proposed":
            if not sfc:
                raise SystemExit("--sfc is required for the proposed arm (the LLM's pick)")
        else:
            raise SystemExit(f"unknown arm {arm!r}")
        envelope = kg.build_envelope(sfc, TARGET_FIELD)    # bounds + runtime action space
    finally:
        kg.close()

    cid = f"e3-{arm}-{scenario}"
    spec = DeploymentSpec(sfc=sfc, binding=real_binding(sfc), envelope=envelope,
                          correlation_id=cid, target_field=TARGET_FIELD)

    t0 = time.monotonic()
    deployer, monitor = _build_stack(spec, requirements)
    monitor.capture_baseline()                            # steady post-deploy baseline (§5.3)

    if arm == "proposed":
        rm = RuntimeManager(deployer, monitor, ValidationGate())
        result = rm.run_episode(spec)
        v = result.verdict
        outcome, tier = v.outcome, v.tier_reached
        reason = result.ticket.reason if result.ticket else ""
        report = monitor.observe_window()                # final measured state after the episode
    else:                                                # static / rule — deploy + measure, NO adapt
        report = monitor.observe_window()
        outcome = "met" if report.target_sla_met else "violated"
        tier, reason = None, ""
    elapsed = round(time.monotonic() - t0, 2)

    flows = [_flow(report.target)] + [_flow(f) for f in report.non_target]
    return {
        "arm": arm, "scenario": scenario, "sfc": sfc, "target_field": TARGET_FIELD,
        "outcome": outcome, "tier_reached": tier, "reason": reason,
        "target_sla_met": bool(report.target_sla_met),
        "headroom": round(report.headroom, 4),
        "switch_status": report.switch_status,
        "flows": flows, "wall_seconds": elapsed,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", required=True, choices=("static", "rule", "proposed"))
    ap.add_argument("--scenario", required=True, choices=sorted(RULE_LADDER))
    ap.add_argument("--sfc", default=None, help="proposed arm only: the LLM-chosen SFC")
    args = ap.parse_args()
    print(json.dumps(run_cell(args.arm, args.scenario, args.sfc)))


if __name__ == "__main__":
    main()
