"""M4-node -> M6 — the Runtime Manager episode loop wired over the REAL node
stack (NodeRunner + Deployer + gate + adapt/evaluate), driving real config on a
resident launch_network.py topology. The monitor is the M5 seam: a scenario
model maps the REAL deployer state to metrics, so config actions are real and
verifiable while observations are modelled.

Node-only: SKIPS unless the testbed is up. Run:
    python3 -m pytest tests/integration/test_m4_loop_wired.py -v
"""
import shutil
import subprocess

import pytest

from runtime import config
from runtime.contracts import BACKUP, PRIMARY
from runtime.fakes import FakeMonitor
from runtime.fixtures import Healthy, PathQualityFault
from runtime.tools.run_episode import build_and_run, spec_for


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def _testbed_up():
    if not shutil.which("simple_switch_CLI"):
        return False
    out = _sh("echo 'table_dump forward_table' | simple_switch_CLI --thrift-port 9090")
    return "TABLE ENTRIES" in out and _sh("pgrep -f 'mininet:d4$'").strip() != ""


pytestmark = pytest.mark.skipif(
    not _testbed_up(), reason="node testbed not up (need a resident launch_network.py)")


def _restore_primary(deployer):
    """Leave the network on the clean primary binding for the next test/run."""
    deployer.deploy(spec_for(Healthy()))


def test_healthy_episode_commits_without_adaptation():
    """The simplest wire: deploy -> observe healthy -> commit, no candidates
    applied. Proves the whole stack assembles and an episode runs end to end."""
    model = Healthy()
    result, deployer = build_and_run(spec_for(model), lambda d: FakeMonitor(model, d))
    try:
        assert result.verdict.outcome in ("healthy", "marginal")
        assert deployer.state["path"] == PRIMARY        # no reroute needed
    finally:
        _restore_primary(deployer)


def test_path_quality_fault_drives_a_real_reroute_and_commits():
    """The headline wire: a primary-relay fault (exogenous) makes the loop apply
    a REAL Tier-1 reroute on the live switches and commit. Verifies both the
    verdict AND that the live fabric actually moved to the backup identity."""
    model = PathQualityFault()
    result, deployer = build_and_run(spec_for(model), lambda d: FakeMonitor(model, d))
    try:
        assert result.verdict.outcome in ("healthy", "marginal")
        assert result.verdict.tier_reached == 1          # Tier-1 reroute
        assert deployer.state["path"] == BACKUP
        # the live switches carry the backup edge identity on BOTH s1 and the
        # destination relay s3 (Issue-1 multi-switch reroute)
        assert deployer._find_forward("s1", config.EDGE_MAC_BACKUP) is not None
        assert deployer._find_forward("s3", config.EDGE_MAC_BACKUP) is not None
        d4 = _sh("pgrep -f 'mininet:d4$' | head -1").strip()
        assert "0% packet loss" in _sh(
            "sudo mnexec -a %s ping -c3 -W1 10.0.0.100" % d4)
    finally:
        _restore_primary(deployer)
