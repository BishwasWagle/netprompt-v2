"""M7 — Tier-2 regen LIVE end-to-end over the real stack, with the real
GBNF-constrained Qwen-Coder client (in-process transformers-cfg, cuda:1).

Three scenarios:
  A  propose -> gate : a REGEN-legal spec under a fault drives the ladder to
     Tier-2; the real model emits grammar-valid candidates the gate evaluates
     (M7 exit #1, the deterministic half — apply/recover is test C).[must-pass]
  B  LLM-down fail-safe : same fault, an empty stub client -> proposer returns
     None -> the engine escalates, no regen applied (§7.4).         [must-pass]
  C  committed recovery : a table-level fault on the F1-MEASURED path (delete
     d4's forward entry; d4 is F1's ping target) that a corrective regen entry
     can fix; assert the episode recovers and the route is restored ON THE LIVE
     SWITCH (DoD #3). Depends on the 1.5B Coder emitting the exact corrective
     row, so it is xfail-tolerant and skipped when M7_REGEN_RECOVERY=0.
                                                            [stretch / togglable]

The ladder reaches Tier-2 because the spec makes tune + reroute exhaust:
knob_ranges={} (nothing to tune) and legal_paths={PRIMARY} (no backup to flip).

Node-only + slow + needs the GPU. Run (resident testbed up, gpu-node.env sourced):
    sudo env NETPROMPT_TREE_ROOT=$TREE NETPROMPT_KG_URI=bolt://localhost:7687 \
      NETPROMPT_REGEN_DEVICE=cuda:1 \
      ~/netprompt-venv/bin/python -m pytest tests/integration/test_m7_regen_live.py -v
"""
import os
import re
import shutil
import subprocess
import time

import pytest

from runtime.contracts import (
    DeploymentSpec, Envelope, PRIMARY, REGEN, REROUTE, TUNE,
)
from runtime.deployer import Deployer
from runtime.gate import ValidationGate
from runtime.kg_client import KGClient
from runtime.monitors.network_monitor import NetworkMonitor, NodeSampler
from runtime.monitors.pipeline import read_interface_counters
from runtime.node_runner import NodeRunner
from runtime.regen import LocalHFClient, RegenProposer, StubLLMClient
from runtime.regen.grammar import validate
from runtime.runtime_manager import RuntimeManager
from runtime.tools.run_episode import DEFAULT_HOST_MAP, real_binding

TREE = os.environ.get(
    "NETPROMPT_TREE_ROOT",
    "/home/cc/Run-time-Manager-2/network/milestone-II-latest/netprompt-milestone-II")
SENSING = ["d4", "d5", "d6", "d7", "d8", "d9", "d10"]
REQ = {"F1": Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20),
       "F2": Envelope(max_latency_ms=120, min_bandwidth_mbps=2, max_loss_percent=20)}
D4_MAC = "00:00:00:00:00:04"         # drone d4 -> port 4; d4 is F1's ping target
ATTEMPT_RECOVERY = os.environ.get("M7_REGEN_RECOVERY", "1") != "0"


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def _pid(host):
    p = _sh(f"pgrep -f 'mininet:{host}$' | head -1").strip()
    if not p:                                   # empty pid -> mnexec -a '' would mis-target
        raise RuntimeError(f"no namespace pid for {host!r} (is the testbed up?)")
    return p


def _reap():
    """Drain zombie Popen wrappers (sh/sudo/iperf) the tests never wait() on."""
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        pass


def _cli(cmd, port=9090):
    return _sh(f"echo '{cmd}' | simple_switch_CLI --thrift-port {port}")


def _testbed_up():
    if not shutil.which("simple_switch_CLI"):
        return False
    try:
        return "TABLE ENTRIES" in _cli("table_dump forward_table") and bool(_pid("d4"))
    except RuntimeError:
        return False


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
    # bracket trick: the pattern must not match the `sh -c "...pkill..."` wrapper
    _sh("sudo pkill -f '[i]perf -u -c 10.0.0.100'")
    try:
        _sh(f"sudo mnexec -a {_pid('edge')} pkill iperf")
    except RuntimeError:
        pass                                       # edge namespace already gone
    _reap()
    time.sleep(1)


def _change_netem(intf, spec):
    out = _sh(f"sudo tc qdisc show dev {intf}")
    m = re.search(r"qdisc netem (\w+): parent (\S+)", out)
    assert m, f"no netem child on {intf}: {out!r}"
    _sh(f"sudo tc qdisc change dev {intf} parent {m.group(2)} handle {m.group(1)}: netem {spec}")


def _regen_spec(cid, sfc="LowLatencyVideoSFC"):
    """REGEN-legal, but tune + reroute exhaust (empty knobs, PRIMARY-only paths),
    so the ladder falls through to Tier-2."""
    env = Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20,
                   legal_tiers=frozenset((TUNE, REROUTE, REGEN)),
                   legal_paths=frozenset((PRIMARY,)), knob_ranges={})
    return DeploymentSpec(sfc=sfc, binding=real_binding(sfc, TREE), envelope=env,
                          correlation_id=cid, target_field="F1")


def _real_proposer(deployer):
    return RegenProposer(LocalHFClient(), table_state_fn=deployer.table_state, switch="s1")


def _routable_macs(deployer):
    return {e.key.lower() for e in deployer.table_state()["s1"]
            if e.action == "forward" and e.args}


def _live_routable(deployer):
    """Resync the deployer cache to the ACTUAL switch first — raw _cli edits
    (the fault injection) bypass the deployer, so table_state()'s cache is
    otherwise stale and would report a deleted route as still present."""
    deployer._refresh_tables("s1")
    return _routable_macs(deployer)


def _kg_cleanup(kg, cid):
    with kg.driver.session() as s:
        s.run("MATCH (n) WHERE n.correlation_id=$c AND (n:Verdict OR n:BaselineSnapshot "
              "OR n:EscalationTicket OR n:LastKnownGood) DELETE n", c=cid)


# ---------------- A: propose -> gate -> apply -> observe (real model) ----------

def test_regen_proposes_and_gates_live():
    """Propose -> gate (M7 exit #1, the deterministic half): under a Tier-2
    violation the real Coder model produces grammar-valid table candidates that
    the engine gate-evaluates, and the ladder reaches Tier-2. Whether a candidate
    APPLIES and recovers the network is model-dependent (the live monitor is
    noisy, so the prompt — and thus the candidate — varies per episode) and is
    proven by the recovery test below."""
    cid = "M7_A_TMP"
    kg = KGClient.connect()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(_regen_spec(cid))
    _start_traffic()
    try:
        monitor = NetworkMonitor(NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2),
                                 REQ, "F1", cid, k=2, m=2)
        monitor.capture_baseline()
        assert monitor.observe_window().target_sla_met is True
        _change_netem("s1-eth11", "delay 100ms")          # F1 violation; tune+reroute exhaust

        rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg,
                            regen_proposer=_real_proposer(deployer))
        result = rm.run_episode(_regen_spec(cid), timestamp="tM7A")

        regen = [a for a in result.verdict.trace if a.candidate.kind == REGEN]
        assert regen, "the ladder never reached Tier-2 (model never consulted)"
        assert result.verdict.tier_reached == 2
        # constrained decoding => every Tier-2 candidate is grammar-valid (and
        # each AttemptRecord already carries the gate's verdict).
        assert all(validate(a.candidate.params[1], "s1") for a in regen)
    finally:
        _change_netem("s1-eth11", "delay 10ms")
        _stop_traffic()
        deployer.deploy(_regen_spec(cid))                 # clean primary baseline
        _kg_cleanup(kg, cid)
        kg.close()


# ---------------- B: LLM-down fail-safe ----------------------------------------

def test_regen_llm_down_escalates_live():
    cid = "M7_B_TMP"
    kg = KGClient.connect()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(_regen_spec(cid))
    _start_traffic()
    try:
        monitor = NetworkMonitor(NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2),
                                 REQ, "F1", cid, k=2, m=2)
        monitor.capture_baseline()
        _change_netem("s1-eth11", "delay 100ms")
        # "LLM down" == a client that says nothing -> proposer returns None.
        down = RegenProposer(StubLLMClient([]), table_state_fn=deployer.table_state, switch="s1")

        rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg, regen_proposer=down)
        result = rm.run_episode(_regen_spec(cid), timestamp="tM7B")

        assert result.verdict.outcome == "escalated", result.verdict.outcome
        applied_regen = [a for a in result.verdict.trace
                         if a.candidate.kind == REGEN and a.applied]
        assert not applied_regen, "LLM-down must not apply any regen candidate"
    finally:
        _change_netem("s1-eth11", "delay 10ms")
        _stop_traffic()
        deployer.deploy(_regen_spec(cid))
        _kg_cleanup(kg, cid)
        kg.close()


# ---------------- C: committed recovery (stretch, togglable) -------------------

@pytest.mark.skipif(not ATTEMPT_RECOVERY,
                    reason="recovery attempt disabled (set M7_REGEN_RECOVERY=0)")
@pytest.mark.xfail(reason="the engine recovers a deleted entry via the cheaper rung-3 "
                          "ROLLBACK (restore last-known-good) before Tier-2 regen fires "
                          "-> outcome='rollback', empty trace. A regen-DRIVEN recovery "
                          "needs a fault in regen's narrow niche (exogenous + table-fixable "
                          "+ not reroute-fixable), which a single injected delete isn't.",
                   strict=False)
def test_regen_recovers_table_fault_live():
    cid = "M7_C_TMP"
    kg = KGClient.connect()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(_regen_spec(cid))
    _start_traffic()
    try:
        monitor = NetworkMonitor(NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2),
                                 REQ, "F1", cid, k=2, m=2)
        monitor.capture_baseline()
        assert monitor.observe_window().target_sla_met is True
        # Table-level fault on the F1-MEASURED path: delete d4's forward entry
        # (d4 is F1's ping target). edge->d4 replies then drop -> F1 loss
        # violation -> ladder to Tier-2 -> regen must re-add the entry to recover.
        handle = next((e.handle for e in deployer.table_state()["s1"]
                       if e.key.lower() == D4_MAC), None)
        assert handle is not None, "d4 forward entry absent at setup"
        _cli(f"table_delete forward_table {handle}")
        assert D4_MAC not in _live_routable(deployer), "fault did not take on the live switch"

        rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg,
                            regen_proposer=_real_proposer(deployer))
        result = rm.run_episode(_regen_spec(cid), timestamp="tM7C")

        # recovery is judged against the LIVE switch, not the deployer cache.
        assert result.verdict.outcome in ("healthy", "marginal"), result.verdict.outcome
        assert D4_MAC in _live_routable(deployer), "regen did not restore d4's route"
    finally:
        if D4_MAC not in _live_routable(deployer):
            _cli(f"table_add forward_table forward {D4_MAC} => 4")
        _stop_traffic()
        deployer.deploy(_regen_spec(cid))
        _kg_cleanup(kg, cid)
        kg.close()


# ---------------- D: recovery PATH proven deterministically (DoD #3) -----------

def _d4_handle(deployer):
    return next(e.handle for e in deployer.table_state()["s1"]
                if e.key.lower() == D4_MAC)


def test_regen_recovers_via_path_live():
    """Deploys >=1 RECOVERED episode via Tier-2 regen end-to-end (DoD #3),
    deterministically. The fault (d4 mis-ported to a wrong-but-valid port) is
    baked into the BASELINE, so it isn't a regression and the cheaper rung-3
    rollback doesn't pre-empt Tier-2 (evaluator stage 3). A StubLLMClient feeds
    the known-correct corrective row, so this proves the
    propose->gate->apply->observe->COMMIT recovery PATH — independent of whether
    the 1.5B model can emit that row (the xfail test above)."""
    cid = "M7_PATH_TMP"
    kg = KGClient.connect()
    deployer = Deployer(NodeRunner(), host_map=DEFAULT_HOST_MAP)
    deployer.deploy(_regen_spec(cid))
    _start_traffic()
    handle = _d4_handle(deployer)
    try:
        # Mis-port d4 (F1's ping target) BEFORE the baseline -> the violation is
        # the steady state, not a regression -> the engine adapts to Tier-2.
        _cli(f"table_modify forward_table forward {handle} => 5")    # d4 -> wrong port 5
        deployer._refresh_tables("s1")                               # gate sees the live faulty state
        monitor = NetworkMonitor(NodeSampler(NodeRunner(), DEFAULT_HOST_MAP, ping_count=2),
                                 REQ, "F1", cid, k=2, m=2)
        monitor.capture_baseline()                                   # baseline = F1 violating
        assert monitor.observe_window().target_sla_met is False, "fault not present at baseline"

        fix = f"table_modify forward_table forward {handle} => 4"    # the corrective row
        proposer = RegenProposer(StubLLMClient([fix]),
                                 table_state_fn=deployer.table_state, switch="s1")
        rm = RuntimeManager(deployer, monitor, ValidationGate(), kg=kg, regen_proposer=proposer)
        result = rm.run_episode(_regen_spec(cid), timestamp="tM7PATH")

        applied = [a for a in result.verdict.trace if a.candidate.kind == REGEN and a.applied]
        assert applied, "the Tier-2 regen candidate was not applied"
        assert result.verdict.outcome in ("healthy", "marginal"), result.verdict.outcome
        assert result.verdict.tier_reached == 2                      # recovered at Tier-2
        assert D4_MAC in _live_routable(deployer)                    # d4 routed again, live
    finally:
        _cli(f"table_modify forward_table forward {handle} => 4")    # ensure d4 restored
        _stop_traffic()
        deployer.deploy(_regen_spec(cid))
        _kg_cleanup(kg, cid)
        kg.close()
