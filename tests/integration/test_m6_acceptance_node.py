"""M6 — full deterministic system, acceptance scenarios LIVE end-to-end over the
real stack (deploy -> real monitor -> evaluate -> adapt -> deployer -> KG).

Headline scenario: a primary-relay fault makes the target miss its SLA; the loop
attributes it as exogenous (relay link qdisc shifted, not our config), reroutes
to the backup, re-observes the target met there, and COMMITS — writing the
verdict + last-known-good to Neo4j.

Bounds are CALIBRATED to the measured testbed (design §6): a ~70 ms latency bound
is met on primary (~40 ms) and backup (~52 ms) but blown by the +100 ms relay
fault — so the only fix is a reroute, and it can actually commit.

Node-only + slow. Run:
    python3 -m pytest tests/integration/test_m6_acceptance_node.py -v
"""
import os
import re
import shutil
import subprocess
import time

import pytest

from runtime.contracts import (
    BACKUP, DeploymentSpec, Envelope, PRIMARY, REROUTE, TUNE,
)
from runtime.deployer import Deployer
from runtime.gate import ValidationGate
from runtime.kg_client import KGClient
from runtime.monitors.network_monitor import NetworkMonitor, NodeSampler
from runtime.monitors.pipeline import read_interface_counters
from runtime.node_runner import NodeRunner
from runtime.runtime_manager import RuntimeManager
from runtime.tools.run_episode import DEFAULT_HOST_MAP, real_binding

TREE = os.environ.get(
    "NETPROMPT_TREE_ROOT",
    "/home/cc/Run-time-Manager-2/network/milestone-II-latest/netprompt-milestone-II")
SENSING = ["d4", "d5", "d6", "d7", "d8", "d9", "d10"]

# Calibrated so the target is met on either path but not under the relay fault.
REQ = {"F1": Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20),
       "F2": Envelope(max_latency_ms=120, min_bandwidth_mbps=2, max_loss_percent=20)}


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def _pid(host):
    return _sh(f"pgrep -f 'mininet:{host}$' | head -1").strip()


def _testbed_up():
    if not shutil.which("simple_switch_CLI"):
        return False
    out = _sh("echo 'table_dump forward_table' | simple_switch_CLI --thrift-port 9090")
    return "TABLE ENTRIES" in out and _pid("d4") != ""


pytestmark = pytest.mark.skipif(
    not _testbed_up(), reason="node testbed not up (need a resident launch_network.py)")


def _flowing(intf="s1-eth4", floor=1e6):
    a = read_interface_counters(intf, 0.0).rx_bytes
    time.sleep(2)
    return read_interface_counters(intf, 0.0).rx_bytes - a > floor


def _start_traffic():
    _stop_traffic()
    subprocess.Popen(f"sudo mnexec -a {_pid('edge')} iperf -s -u",
                     shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    for _ in range(4):
        for d in SENSING:
            subprocess.Popen(
                f"sudo mnexec -a {_pid(d)} iperf -u -c 10.0.0.100 -b 5M -t 240",
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        if _flowing():
            return
    raise RuntimeError("iperf traffic never started flowing")


def _stop_traffic():
    _sh("sudo pkill -f 'iperf -u -c 10.0.0.100'")
    _sh(f"sudo mnexec -a {_pid('edge')} pkill iperf")
    time.sleep(1)


def _change_netem(intf, spec):
    out = _sh(f"sudo tc qdisc show dev {intf}")
    m = re.search(r"qdisc netem (\w+): parent (\S+)", out)
    assert m, f"no netem child on {intf}: {out!r}"
    _sh(f"sudo tc qdisc change dev {intf} parent {m.group(2)} handle {m.group(1)}: netem {spec}")


def _spec(correlation_id):
    """Real low_latency binding (deploys primary) but a reroute-legal, calibrated
    envelope — empty knob_ranges so Tier-0 tune exhausts instantly and the engine
    goes straight to the Tier-1 reroute."""
    env = Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20,
                   legal_tiers=frozenset((TUNE, REROUTE)),
                   legal_paths=frozenset((PRIMARY, BACKUP)), knob_ranges={})
    return DeploymentSpec(sfc="LowLatencyVideoSFC",
                          binding=real_binding("LowLatencyVideoSFC", TREE),
                          envelope=env, correlation_id=correlation_id,
                          target_field="F1")


def test_relay_fault_reroutes_and_commits_live():
    cid = "M6_REROUTE_TMP"
    kg = KGClient.connect()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(_spec(cid))
    _start_traffic()
    try:
        monitor = NetworkMonitor(NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2),
                                 REQ, "F1", cid, k=2, m=2)
        monitor.capture_baseline()                       # healthy primary
        assert monitor.observe_window().target_sla_met is True

        _change_netem("s1-eth11", "delay 100ms")         # PRIMARY RELAY degrades

        rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg)
        result = rm.run_episode(_spec(cid), timestamp="tM6")

        # exogenous relay fault -> no rollback -> Tier-1 reroute -> met on backup -> commit
        assert result.verdict.outcome in ("healthy", "marginal"), result.verdict.outcome
        assert result.verdict.tier_reached == 1
        assert deployer.state["path"] == BACKUP
        # and it is recorded in the KG
        with kg.driver.session() as s:
            v = s.run("MATCH (n:Verdict {correlation_id:$c}) RETURN n.outcome AS o",
                      c=cid).single()
            g = s.run("MATCH (g:LastKnownGood {sfc:'LowLatencyVideoSFC'}) "
                      "RETURN g.correlation_id AS c").single()
        assert v is not None and v["o"] in ("healthy", "marginal")
        assert g is not None and g["c"] == cid          # backup config promoted
    finally:
        _change_netem("s1-eth11", "delay 10ms")
        _stop_traffic()
        # restore the network to a clean primary baseline for the next test/run
        deployer.deploy(_spec(cid))
        with kg.driver.session() as s:
            s.run("MATCH (n) WHERE n.correlation_id=$c AND "
                  "(n:Verdict OR n:BaselineSnapshot OR n:EscalationTicket) DELETE n", c=cid)
            s.run("MATCH (g:LastKnownGood {sfc:'LowLatencyVideoSFC'}) "
                  "WHERE g.correlation_id=$c DELETE g", c=cid)
        kg.close()


def test_ddil_both_paths_degraded_escalates_live():
    """ddil analog: BOTH relays degrade, so neither a reroute nor (latency-driven)
    a tune can recover the target — the engine exhausts its tiers and escalates
    with a ticket, and the ticket is recorded in the KG."""
    cid = "M6_DDIL_TMP"
    kg = KGClient.connect()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(_spec(cid))
    _start_traffic()
    try:
        monitor = NetworkMonitor(NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2),
                                 REQ, "F1", cid, k=2, m=2)
        monitor.capture_baseline()
        _change_netem("s1-eth11", "delay 100ms")         # primary relay ...
        _change_netem("s1-eth12", "delay 100ms")         # ... AND backup relay

        rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg)
        result = rm.run_episode(_spec(cid), timestamp="tDDIL")

        assert result.verdict.outcome == "escalated", result.verdict.outcome
        assert result.ticket is not None
        with kg.driver.session() as s:
            t = s.run("MATCH (n:EscalationTicket {correlation_id:$c}) RETURN n.reason AS r",
                      c=cid).single()
        assert t is not None                             # escalation recorded
    finally:
        _change_netem("s1-eth11", "delay 10ms")
        _change_netem("s1-eth12", "delay 15ms")          # s1-eth12 nominal is 15ms
        _stop_traffic()
        deployer.deploy(_spec(cid))
        with kg.driver.session() as s:
            s.run("MATCH (n) WHERE n.correlation_id=$c AND "
                  "(n:Verdict OR n:BaselineSnapshot OR n:EscalationTicket) DELETE n", c=cid)
        kg.close()


# ---------------- contention-harm -> Tier-0 tune (harm relief) -> commit -------
# F1 (BandwidthOptimized, tbf knob) over-sends and congests the shared s1->s2
# link, harming neighbour F2. The engine relieves it by tuning F1's tbf DOWN.
# Harm shows as F2 LATENCY (bottleneck queuing) — the monitor measures throughput
# at the ACCESS links (upstream of the bottleneck), so it's invisible there, but
# the queuing delay is precise via ping. Bounds calibrated to the measured testbed
# (F2 RTT ~246ms under contention vs ~55ms relieved; F1 stays <400ms throughout).

CH_F1 = ["d4", "d5", "d6"]
CH_F2 = ["d7", "d8", "d9", "d10"]
CH_REQ = {"F1": Envelope(max_latency_ms=400, min_bandwidth_mbps=1, max_loss_percent=95),
          "F2": Envelope(max_latency_ms=100, min_bandwidth_mbps=1, max_loss_percent=95)}


def _iperf_udp(drones, rate_mbit, seconds):
    for d in drones:
        subprocess.Popen(
            f"sudo mnexec -a {_pid(d)} iperf -u -c 10.0.0.100 -b {rate_mbit}M -t {seconds}",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ch_spec(cid):
    binding = real_binding("LowLatencyVideoSFC", TREE)   # forwarding is SFC-agnostic
    binding["qos"] = {"tbf_rate_mbit": 25}               # F1 baseline grab
    env = Envelope(max_latency_ms=400, min_bandwidth_mbps=1, max_loss_percent=95,
                   legal_tiers=frozenset((TUNE,)),
                   legal_paths=frozenset((PRIMARY,)),
                   knob_ranges={"tbf_rate_mbit": (5, 80)})
    return DeploymentSpec(sfc="BandwidthOptimizedSFC", binding=binding, envelope=env,
                          correlation_id=cid, target_field="F1")


def test_contention_harm_tunes_target_down_and_commits_live():
    cid = "M6_HARM_TMP"
    kg = KGClient.connect()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(_ch_spec(cid))
    _stop_traffic()
    subprocess.Popen(f"sudo mnexec -a {_pid('edge')} iperf -s -u",
                     shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    try:
        _iperf_udp(CH_F2, 6, 200)                        # neighbour traffic only ...
        time.sleep(3)
        monitor = NetworkMonitor(NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=4),
                                 CH_REQ, "F1", cid, k=2, m=2)
        monitor.capture_baseline()                       # ... so F2 is MET at baseline
        _iperf_udp(CH_F1, 30, 200)                       # now F1 over-sends -> F2 harmed
        time.sleep(4)

        rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg, budget_n=10)
        result = rm.run_episode(_ch_spec(cid), timestamp="tHARM")

        # F1 stays met, F2 was harmed -> stage-5 harm relief tunes F1's tbf DOWN
        # (Tier-0) until F2 recovers -> commit.
        assert result.verdict.outcome in ("healthy", "marginal"), result.verdict.outcome
        assert result.verdict.tier_reached == 0           # Tier-0 tune, not reroute
        assert deployer.state["knobs"]["tbf_rate_mbit"] < 25   # F1 grab reduced
        tuned = [a for a in result.verdict.trace if a.candidate.kind == TUNE]
        assert tuned and all(c.candidate.params[0] == "tbf_rate_mbit" for c in tuned)
    finally:
        _sh("sudo pkill -x iperf")
        deployer.deploy(_spec(cid))                      # restore clean low_latency primary
        with kg.driver.session() as s:
            s.run("MATCH (n) WHERE n.correlation_id=$c AND "
                  "(n:Verdict OR n:BaselineSnapshot OR n:EscalationTicket OR n:LastKnownGood) "
                  "DELETE n", c=cid)
        kg.close()
