"""M6 soak — the deterministic system run over time with a switch WATCHDOG.

Proves the two robustness claims: BMv2 instability (C4) is RECOVERED rather than
fatal, and a Neo4j hiccup doesn't abort the loop. Each iteration:
  1. watchdog: restart any Failed switch + restore its CURRENT committed config
     (process restart via switch_control; table recovery via deployer.recover_switch
     from the RM's last_good — NOT the base binding);
  2. run one episode (deploy stays up; the RM monitors + adapts + writes the KG).
Optional `--kill-every N` injects a switch kill to exercise the watchdog.

    python3 -m runtime.tools.soak --minutes 60 --kill-every 20 --kill s3
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time

from runtime import config
from runtime.monitors.pipeline import read_interface_counters
from runtime.contracts import (
    BACKUP, DeploymentSpec, Envelope, PRIMARY, REROUTE, TUNE,
)
from runtime.deployer import Deployer
from runtime.gate import ValidationGate
from runtime.kg_client import KGClient
from runtime.monitors.network_monitor import NetworkMonitor, NodeSampler
from runtime.monitors.system_monitor import SystemMonitor
from runtime.node_runner import NodeRunner
from runtime.runtime_manager import RuntimeManager
from runtime.tools import switch_control
from runtime.tools.run_episode import DEFAULT_HOST_MAP, real_binding

REQ = {"F1": Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20),
       "F2": Envelope(max_latency_ms=120, min_bandwidth_mbps=2, max_loss_percent=20)}
SENSING = ["d4", "d5", "d6", "d7", "d8", "d9", "d10"]


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def _pid(host):
    return _sh(f"pgrep -f 'mininet:{host}$' | head -1").strip()


def _spec(cid, tree):
    env = Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20,
                   legal_tiers=frozenset((TUNE, REROUTE)),
                   legal_paths=frozenset((PRIMARY, BACKUP)), knob_ranges={})
    return DeploymentSpec(sfc="LowLatencyVideoSFC",
                          binding=real_binding("LowLatencyVideoSFC", tree),
                          envelope=env, correlation_id=cid, target_field="F1")


def start_traffic(seconds):
    _sh(f"sudo mnexec -a {_pid('edge')} pkill iperf")
    subprocess.Popen(f"sudo mnexec -a {_pid('edge')} iperf -s -u",
                     shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    for d in SENSING:
        subprocess.Popen(
            f"sudo mnexec -a {_pid(d)} iperf -u -c 10.0.0.100 -b 5M -t {seconds}",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)


def stop_traffic():
    _sh("sudo pkill -f 'iperf -u -c 10.0.0.100'")
    _sh(f"sudo mnexec -a {_pid('edge')} pkill iperf")


def _reap():
    """Drain zombie children (the bash/sudo/iperf wrappers from Popen). Over a
    1h soak with periodic traffic restarts these would otherwise leak PIDs."""
    try:
        while os.waitpid(-1, os.WNOHANG)[0] != 0:
            pass
    except ChildProcessError:
        pass


def _rx(intf="s1-eth4"):
    """Cumulative rx bytes on a representative field veth, 0 if it vanished."""
    try:
        return read_interface_counters(intf, 0.0).rx_bytes
    except OSError:
        return 0


def recover_dead_switches(sysmon, deployer, last_good, p4_json, rules_dir):
    """Watchdog. Returns the switches it brought back."""
    recovered = []
    for sw, (alive, thrift) in sysmon.liveness().items():
        if alive and thrift:
            continue
        rules = f"{rules_dir}/low_latency_{sw}_rules.txt"
        if switch_control.restart_switch(sw, p4_json, rules):
            deployer.recover_switch(sw, last_good)        # current config, not base
            recovered.append(sw)
    return recovered


def run_soak(minutes, kill_every, kill, tree, with_regen=False):
    p4_json = f"{tree}/compiled_p4/low_latency.json"
    rules_dir = f"{tree}/p4_multihop_rules"
    cid = "SOAK"

    runner = NodeRunner()
    deployer = Deployer(runner, host_map=DEFAULT_HOST_MAP)
    kg = KGClient.connect()
    sysmon = SystemMonitor()
    deployer.deploy(_spec(cid, tree))
    start_traffic(int(minutes * 60) + 60)
    monitor = NetworkMonitor(NodeSampler(runner, DEFAULT_HOST_MAP, ping_count=2),
                             REQ, "F1", cid, k=2, m=2)
    monitor.capture_baseline()
    regen_proposer = None
    if with_regen:
        # Tier-2 real serving (M7): constrained-decoding Qwen-Coder on the GPU.
        # table_state_fn reads the deployer's LIVE tables for the prompt; the
        # gate's L2 sees the same state (run_episode refreshes current_tables).
        from runtime.regen import RegenProposer, LocalHFClient
        regen_proposer = RegenProposer(LocalHFClient(),
                                       table_state_fn=deployer.table_state, switch="s1")
        print("soak: Tier-2 regen ENABLED (LocalHFClient on the GPU)", flush=True)
    rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg,
                        regen_proposer=regen_proposer)

    stats = {"episodes": 0, "healthy": 0, "marginal": 0, "escalated": 0,
             "rollback": 0, "rejected": 0, "system_fault": 0,
             "recoveries": 0, "injected_kills": 0, "traffic_restarts": 0,
             "errors": 0}
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < minutes * 60:
            try:
                stats["recoveries"] += len(recover_dead_switches(
                    sysmon, deployer, rm.last_good, p4_json, rules_dir))
                rx_before = _rx()
                result = rm.run_episode(_spec(cid, tree),
                                        timestamp=f"soak{stats['episodes']}")
                stats["episodes"] += 1
                stats[result.verdict.outcome] = stats.get(result.verdict.outcome, 0) + 1
                # Traffic health-check: if no bytes moved during the episode the
                # iperf clients died (host churn / a path drop) — restart them, or
                # every field reads ~0 Mbps and the rest of the soak is invalid.
                if _rx() - rx_before < 1_000_000:
                    remaining = int(minutes * 60 - (time.monotonic() - t0)) + 60
                    start_traffic(max(remaining, 60))
                    stats["traffic_restarts"] += 1
                if kill_every and stats["episodes"] % kill_every == 0:
                    switch_control.kill_switch(kill)      # simulate a BMv2 crash
                    stats["injected_kills"] += 1
                _reap()                                   # drain zombie wrappers
                if stats["episodes"] % 10 == 0:
                    print(f"  [{int((time.monotonic()-t0)/60)}m ep{stats['episodes']}] "
                          f"recoveries={stats['recoveries']} "
                          f"traffic_restarts={stats['traffic_restarts']} "
                          f"kg_fail={rm.kg_write_failures}", flush=True)
            except Exception as e:                        # one bad episode never ends the soak
                stats["errors"] += 1
                print(f"  [ep {stats['episodes']}] error: {type(e).__name__}: {e}", flush=True)
    finally:
        stats["kg_write_failures"] = rm.kg_write_failures
        stop_traffic()
        _reap()
        kg.close()
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=2.0)
    ap.add_argument("--kill-every", type=int, default=0,
                    help="inject a kill of --kill every N episodes (0 = off)")
    ap.add_argument("--kill", default="s3", help="which switch the injector kills")
    ap.add_argument("--tree", default=config.NODE_TREE_ROOT)
    ap.add_argument("--with-regen", action="store_true",
                    help="enable Tier-2 real-serving regen (loads the Qwen-Coder "
                         "model on the GPU via LocalHFClient)")
    args = ap.parse_args()
    print(f"soak: {args.minutes} min, kill-every={args.kill_every} ({args.kill}), "
          f"regen={'on' if args.with_regen else 'off'}")
    stats = run_soak(args.minutes, args.kill_every, args.kill, args.tree, args.with_regen)
    print("SOAK DONE:", stats)


if __name__ == "__main__":
    main()
