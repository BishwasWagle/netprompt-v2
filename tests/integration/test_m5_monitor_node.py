"""M5 exit — the REAL network monitor reports induced conditions correctly on
the live testbed (design §5.4–§5.7):

  * kill s2          -> switch_status[s2] == Failed, system stays sound (reroute-able)
  * congest primary  -> switch_status[s2] == Degraded, target SLA unmet
  * squeeze a field  -> displaced_harm lists the harmed neighbour

Designed around the two node constraints (design §6, §5.2):
  * BOUNDS are calibrated to the hardware (~40 ms primary) so a field is MET at
    baseline and the induced fault flips it — LowLatency's 20 ms is unachievable
    and would never be met, defeating the met->unmet test.
  * TRAFFIC (iperf) runs through baseline AND observation, or throughput reads 0
    and every field is "unmet" at baseline, so harm could never fire.

Node-only: SKIPS unless the testbed is up. Slow (real K-of-M windows). Run:
    python3 -m pytest tests/integration/test_m5_monitor_node.py -v
"""
import os
import re
import shutil
import subprocess
import time

import pytest

from runtime.contracts import Envelope
from runtime.monitors.network_monitor import NetworkMonitor, NodeSampler
from runtime.monitors.pipeline import read_interface_counters
from runtime.node_runner import NodeRunner
from runtime.tools import switch_control
from runtime.tools.run_episode import DEFAULT_HOST_MAP

TREE = os.environ.get(
    "NETPROMPT_TREE_ROOT",
    "/home/cc/Run-time-Manager-2/network/milestone-II-latest/netprompt-milestone-II")
JSON = f"{TREE}/compiled_p4/low_latency.json"
S2_RULES = f"{TREE}/p4_multihop_rules/low_latency_s2_rules.txt"

# Bounds the primary path can actually meet (≈40 ms RTT, shared ≈60 Mbit) — so a
# field is MET at baseline and an induced fault flips it.
ACHIEVABLE = {
    "F1": Envelope(max_latency_ms=100, min_bandwidth_mbps=5, max_loss_percent=20),
    "F2": Envelope(max_latency_ms=100, min_bandwidth_mbps=2, max_loss_percent=20),
}
SENSING = ["d4", "d5", "d6", "d7", "d8", "d9", "d10"]


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


@pytest.fixture(scope="module", autouse=True)
def _isolate_drone_qdiscs():
    """Module isolation: earlier tests (e.g. the M4 loop) leave the sensing
    drones knob-owned (pfifo-20), whose tiny buffer chokes iperf and degrades a
    field before any fault is induced. Reset each sensing drone to the default
    qdisc so fields are met at baseline under load — the scenario environment
    still lives switch-side (s1-eth{N}), which is what the monitor reads."""
    for d in SENSING:
        _sh(f"sudo mnexec -a {_pid(d)} tc qdisc del dev {d}-eth0 root 2>/dev/null || true")
    yield


def _flowing(intf="s1-eth4", floor=1e6):
    a = read_interface_counters(intf, 0.0).rx_bytes
    time.sleep(2)
    return read_interface_counters(intf, 0.0).rx_bytes - a > floor


def _spawn_clients():
    # UDP at a MODERATE bitrate (5 Mbit/drone, ~35 Mbit total < the 60 Mbit
    # primary): rate-controlled so the drones never overload the path regardless
    # of their egress qdisc — otherwise an unshaped drone blasts TCP at line rate
    # and the downstream htb queues to 200 ms / 25% loss, failing a field before
    # any fault is induced. UDP also lets the monitor read steady throughput.
    for d in SENSING:
        subprocess.Popen(f"sudo mnexec -a {_pid(d)} iperf -u -c 10.0.0.100 -b 5M -t 200",
                         shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _start_traffic():
    """iperf UDP server on edge + an upstream client on every sensing drone,
    VERIFIED flowing before returning (retry the clients if the server wasn't
    ready)."""
    _stop_traffic()
    subprocess.Popen(f"sudo mnexec -a {_pid('edge')} iperf -s -u",
                     shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    for _ in range(4):
        _spawn_clients()
        time.sleep(2)
        if _flowing():
            return
    raise RuntimeError("iperf traffic never started flowing")


def _stop_traffic():
    _sh("sudo pkill -f 'iperf -u -c 10.0.0.100'")
    _sh(f"sudo mnexec -a {_pid('edge')} pkill iperf")
    time.sleep(1)


def _monitor(target="F1", k=2, m=2):
    sampler = NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2)
    return NetworkMonitor(sampler, ACHIEVABLE, target, "m5exit", k=k, m=m)


def _change_netem(intf, spec):
    """Change ONLY the netem child (`parent A:B handle C:`), preserving the htb
    rate cap above it — `tc qdisc replace … root` would drop the cap and let
    iperf self-congest, corrupting later tests."""
    out = _sh(f"sudo tc qdisc show dev {intf}")
    m = re.search(r"qdisc netem (\w+): parent (\S+)", out)
    assert m, f"no netem child on {intf}: {out!r}"
    handle, parent = m.group(1), m.group(2)
    _sh(f"sudo tc qdisc change dev {intf} parent {parent} handle {handle}: netem {spec}")


# ---------------- congest primary -> Degraded ----------------

def test_congest_primary_reports_degraded():
    _start_traffic()
    try:
        mon = _monitor()
        mon.capture_baseline()
        assert mon.observe_window().target_sla_met is True       # met before congestion
        _change_netem("s1-eth11", "delay 100ms")                # +100 ms on s1->s2
        r = mon.observe_window()
        assert r.target_sla_met is False                        # latency now blown
        assert r.switch_status["s2"] == "Degraded"              # alive+carrying, SLA bad
    finally:
        _change_netem("s1-eth11", "delay 10ms")                 # restore the link delay
        _stop_traffic()


# ---------------- squeeze a neighbour -> harm ----------------

def test_squeeze_neighbour_populates_harm_list():
    _start_traffic()
    try:
        mon = _monitor(target="F1")
        mon.capture_baseline()                                   # F1 + F2 met (traffic)
        base = mon.observe_window()
        assert base.target_sla_met is True and base.displaced_harm == []
        # Degrade F2's representative drone path (d7 -> port 7). Use DELAY, not
        # loss: with a small ping count, loss is too coarse to measure (0/50/100%
        # over 2 packets), while added delay flips the latency bound reliably.
        _change_netem("s1-eth7", "delay 80ms loss 1%")          # F2 RTT past its bound
        r = mon.observe_window()
        assert "F2" in r.displaced_harm                         # neighbour flipped met->unmet
        assert r.target_sla_met is True                         # target itself unharmed
    finally:
        _change_netem("s1-eth7", "delay 15ms loss 1%")          # restore F2 drone link
        _stop_traffic()


# ---------------- kill s2 -> Failed, still sound (runs LAST: it kills a switch
# and disrupts traffic, so it must not precede the reversible tests) ----------

def test_kill_s2_reports_failed_and_stays_sound():
    _start_traffic()
    try:
        mon = _monitor()
        mon.capture_baseline()
        assert switch_control.switch_pid("s2") is not None      # alive at baseline
        switch_control.kill_switch("s2")                        # induce the fault
        r = mon.observe_window()
        assert r.switch_status["s2"] == "Failed"
        # one relay down is recoverable: the loop should reroute, not bail
        assert r.system_sound is True
    finally:
        switch_control.restart_switch("s2", JSON, S2_RULES)     # recover the fabric
        _stop_traffic()
