"""M4-node exit (implementation plan §M4): deploy LowLatency binding -> live
flip to backup -> rollback -> re_push, on one uninterrupted network, verified by
ping continuity. Drives the REAL Deployer over the REAL NodeRunner against a
resident launch_network.py topology.

This is node-only: it SKIPS unless the testbed is up (simple_switch_CLI present,
thrift 9090 reachable, the d4 namespace exists). Off-node CI never runs it.

Bring the network up first:
    sudo python3 runtime/tools/launch_network.py \
        --p4-json  $TREE/compiled_p4/low_latency.json \
        --rules-dir $TREE/p4_multihop_rules --sfc low_latency --scenario baseline

Then (sudo is needed for mnexec pings):
    sudo $(which pytest) tests/integration/test_m4_node_exit.py -v
"""
import os
import shutil
import subprocess

import pytest

from runtime.contracts import (
    BACKUP, Candidate, DeploymentSpec, Envelope, PRIMARY, REROUTE, TUNE,
)
from runtime.deployer import Deployer, delete_command
from runtime.node_runner import NodeRunner

TREE = os.environ.get(
    "NETPROMPT_TREE_ROOT",
    "/home/cc/Run-time-Manager-2/network/milestone-II-latest/netprompt-milestone-II")


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def _testbed_up():
    if not shutil.which("simple_switch_CLI"):
        return False
    out = _sh("echo 'table_dump forward_table' | simple_switch_CLI --thrift-port 9090")
    return "TABLE ENTRIES" in out and _sh("pgrep -f 'mininet:d4$'").strip() != ""


pytestmark = pytest.mark.skipif(
    not _testbed_up(),
    reason="node testbed not up (need a resident launch_network.py on this host)")


def _pid(host):
    return _sh("pgrep -f 'mininet:%s$' | head -1" % host).strip()


def _reaches_edge(host="d4", n=3):
    out = _sh("sudo mnexec -a %s ping -c%d -W1 10.0.0.100" % (_pid(host), n))
    return "0% packet loss" in out


def _dead(host="d4"):
    return "100% packet loss" in _sh("sudo mnexec -a %s ping -c2 -W1 10.0.0.100" % _pid(host))


@pytest.fixture
def attached():
    """Attach a Deployer to the already-resident LowLatency network and capture
    the baseline snapshot (the restore point)."""
    binding = {
        "policy_type": "low_latency",
        "p4_json": "%s/compiled_p4/low_latency.json" % TREE,
        "access_rules": "%s/p4_multihop_rules/low_latency_s1_rules.txt" % TREE,
        "relay_rules": "%s/p4_multihop_rules/low_latency_s2_rules.txt" % TREE,
        "backup_rules": "%s/p4_multihop_rules/low_latency_s3_rules.txt" % TREE,
    }
    env = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                   legal_tiers=frozenset((TUNE, REROUTE)),
                   legal_paths=frozenset((PRIMARY, BACKUP)),
                   knob_ranges={"pfifo_limit": (10, 50)})
    spec = DeploymentSpec(sfc="LowLatencyVideoSFC", binding=binding, envelope=env,
                          correlation_id="m4exit", target_field="F1")
    d = Deployer(NodeRunner(), host_map={"F1": ["d4", "d5"]})
    d.spec = spec
    for sw in ("s1", "s2", "s3"):
        d._refresh_tables(sw)
    d._path = PRIMARY
    snap = d.capture()
    # No auto-rollback teardown: each test leaves the network on primary itself
    # (the flip test rolls back; re_push restores). Rolling back ACROSS a re_push
    # is intentionally unsupported — re_push re-numbers BMv2 handles, and
    # _restore_tables is handle-based, so a recovery re-baselines (design §7.6)
    # rather than rolling back to a pre-re_push snapshot.
    yield d, snap


def test_live_flip_to_backup_and_rollback(attached):
    d, snap = attached
    assert _reaches_edge()                 # baseline primary works

    d.apply(Candidate(REROUTE, (BACKUP,)))
    assert d.state["path"] == BACKUP
    assert _reaches_edge()                 # backup path carries traffic

    d.rollback(snap)
    assert d.state["path"] == PRIMARY
    assert _reaches_edge()                 # back on primary


def test_re_push_recovers_from_rung1_table_loss(attached):
    """Simulate a rung-1 fault: every switch loses its tables (as after a
    restart). re_push must reinstall and restore connectivity."""
    d, snap = attached
    runner = d.runner
    for sw in ("s1", "s2", "s3"):
        for e in list(d._tables[sw]):
            runner.run_cli(sw, delete_command(e.table, e.handle))
        d._tables[sw] = []
    assert _dead()                         # connectivity gone with empty tables

    d.re_push(snap)
    assert _reaches_edge()                 # reinstalled, traffic flows again
    assert all(len(d._tables[sw]) > 0 for sw in ("s1", "s2", "s3"))
