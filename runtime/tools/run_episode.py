"""Wire the Runtime Manager episode loop over the REAL node stack
(implementation plan M4-node -> M6).

Assembles NodeRunner -> Deployer -> monitor -> ValidationGate -> RuntimeManager,
deploys the binding (idempotent on the resident launch_network.py topology), and
runs one episode. Config actions (deploy / apply / rollback / re_push) are real
and verifiable on the live switches.

THE MONITOR IS THE REMAINING SEAM. Until M5's real sampler lands, observations
come from a scenario model (fakes.FakeMonitor) that reads the REAL deployer state
and maps it to a MonitorReport — metrics are modelled, but every config action is
real. Swap in the real monitor at M5 by changing the `monitor_for` callable.
"""
from __future__ import annotations

import argparse

from runtime import config
from runtime.contracts import DeploymentSpec
from runtime.deployer import Deployer
from runtime.fakes import FakeMonitor
from runtime.fixtures import ALL_SCENARIOS
from runtime.gate import ValidationGate
from runtime.monitors.network_monitor import NetworkMonitor, NodeSampler
from runtime.node_runner import NodeRunner
from runtime.runtime_manager import RuntimeManager

# field -> drones it shapes (host_map resolves a TUNE to the field's hosts). The
# milestone-II SFC experiments shaped d4/d5/d6; F1 is the target field, F2 the
# neighbour.
DEFAULT_HOST_MAP = {"F1": ["d4", "d5", "d6"],
                    "F2": ["d7", "d8", "d9", "d10"]}

# tree -> SFC rule/JSON files (matches launch_network.py / config.NODE_TREE_ROOT).
SFC_FILE_PREFIX = {"LowLatencyVideoSFC": "low_latency",
                   "ReliableRelaySFC": "reliable_relay",
                   "BandwidthOptimizedSFC": "bandwidth_optimized",
                   "EnergyAwareSFC": "energy_aware"}


def real_binding(sfc: str, tree: str = config.NODE_TREE_ROOT) -> dict:
    p = SFC_FILE_PREFIX[sfc]
    return {
        "policy_type": p,
        "p4_json": f"{tree}/compiled_p4/{p}.json",
        "access_rules": f"{tree}/p4_multihop_rules/{p}_s1_rules.txt",
        "relay_rules": f"{tree}/p4_multihop_rules/{p}_s2_rules.txt",
        "backup_rules": f"{tree}/p4_multihop_rules/{p}_s3_rules.txt",
    }


def spec_for(model, tree: str = config.NODE_TREE_ROOT) -> DeploymentSpec:
    """A real, installable spec aligned to a scenario model (same sfc / target
    field / envelope) but with on-node rule paths instead of the fixture stubs."""
    return DeploymentSpec(sfc=model.sfc, binding=real_binding(model.sfc, tree),
                          envelope=model.envelope,
                          correlation_id=model.correlation_id,
                          target_field=model.target_field)


def real_monitor_for(requirements, target_field, correlation_id, host_map=None):
    """M5 seam: a `monitor_for` that builds the REAL NetworkMonitor over a live
    NodeSampler (ping + sysfs counters + tc + liveness). It reads the network
    directly, so it ignores the deployer arg. Baseline is captured by build_and_run
    after deploy (the steady, pre-fault state, §5.3)."""
    hm = host_map or DEFAULT_HOST_MAP

    def factory(_deployer):
        sampler = NodeSampler(NodeRunner(), hm)
        return NetworkMonitor(sampler, requirements, target_field, correlation_id)
    return factory


def build_and_run(spec, monitor_for, host_map=None, *, timestamp="", kg=None):
    """Assemble the real stack, deploy the binding, run one episode.
    monitor_for: callable(deployer) -> monitor (the M5 seam).
    kg: optional KGClient — when present the RM writes verdict/status/LKG/baseline
    to Neo4j (§8). Returns (EvalResult, deployer)."""
    runner = NodeRunner()
    deployer = Deployer(runner, host_map=host_map or DEFAULT_HOST_MAP)
    deployer.deploy(spec)
    monitor = monitor_for(deployer)
    # Real monitor: capture the post-deploy healthy baseline before the episode
    # adapts (FakeMonitor carries its baseline in the scenario model, so skip).
    if getattr(monitor, "baseline", "n/a") is None:
        monitor.capture_baseline()
    rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg)
    return rm.run_episode(spec, timestamp), deployer


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", default="path_quality_fault",
                    choices=sorted(ALL_SCENARIOS),
                    help="scenario model driving the (placeholder) monitor")
    ap.add_argument("--tree", default=config.NODE_TREE_ROOT)
    ap.add_argument("--monitor", default="model", choices=("model", "real"),
                    help="'model' = scenario-driven (instant); 'real' = live "
                         "NetworkMonitor sampling the testbed (M5)")
    args = ap.parse_args()

    model = ALL_SCENARIOS[args.scenario]()
    spec = spec_for(model, args.tree)
    if args.monitor == "real":
        monitor_for = real_monitor_for(model.fields, model.target_field,
                                       model.correlation_id)
    else:
        monitor_for = lambda dep: FakeMonitor(model, dep)
    result, deployer = build_and_run(spec, monitor_for)

    v = result.verdict
    print(f"scenario      : {args.scenario}")
    print(f"verdict       : {v.outcome}  (tier {v.tier_reached}, headroom {v.headroom:.3f})")
    print(f"live path     : {deployer.state['path']}")
    print(f"live knobs    : {deployer.state['knobs']}")
    if result.ticket:
        print(f"escalation    : {result.ticket.reason}")


if __name__ == "__main__":
    main()
