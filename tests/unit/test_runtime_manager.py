"""M3 — episode boundary mechanics (design §7.6): any commit promotes
last-known-good, re-baselines, and the budget is per-episode."""
from runtime.contracts import Candidate, PRIMARY, TUNE
from runtime.fakes import FakeDeployer, FakeMonitor
from runtime.fixtures import CausalRegression, ContentionHarmWithKnob, Ddil
from runtime.gate import ValidationGate
from runtime.runtime_manager import RuntimeManager


def rig(model, deployer, **kw):
    monitor = FakeMonitor(model, deployer)
    return RuntimeManager(deployer, monitor, ValidationGate(),
                          active_capacity_ok=lambda c: True, **kw), monitor


def test_commit_promotes_lkg_and_rebaselines():
    model = ContentionHarmWithKnob()
    deployer = FakeDeployer(initial_knobs={"tbf_rate_mbit": 80})
    rm, monitor = rig(model, deployer)

    first = rm.run_episode(model.spec())
    assert first.verdict.outcome == "marginal"               # adapted to rate 45
    assert rm.last_good == {"path": PRIMARY, "knobs": {"tbf_rate_mbit": 45}}
    assert model.baseline["F2"].met                          # re-baselined at commit

    # New episode against the new baseline: already at goal, no adaptation.
    second = rm.run_episode(model.spec())
    assert second.verdict.outcome == "marginal"              # headroom still thin
    assert second.verdict.trace == []                        # nothing to adapt


def test_rollback_episode_does_not_promote_lkg():
    model = CausalRegression()
    deployer = FakeDeployer()                                # pristine
    rm, monitor = rig(model, deployer)
    pristine = rm.last_good
    deployer.apply(Candidate(TUNE, ("pfifo_limit", 5)))      # the bad deploy

    res = rm.run_episode(model.spec())
    assert res.verdict.outcome == "rollback"
    assert deployer.state == pristine                        # restored
    assert rm.last_good == pristine                          # NOT promoted


def test_budget_is_reset_per_episode():
    model = Ddil()
    deployer = FakeDeployer()
    rm, monitor = rig(model, deployer, budget_n=1)

    first = rm.run_episode(model.spec())
    second = rm.run_episode(model.spec())
    assert first.verdict.outcome == second.verdict.outcome == "escalated"
    # identical traces prove the second episode got a FULL budget again —
    # a carried-over spent budget would produce an empty second trace.
    assert len(first.verdict.trace) == len(second.verdict.trace) == 1


def test_gate_rejected_binding_never_touches_the_network():
    model = ContentionHarmWithKnob()
    deployer = FakeDeployer(initial_knobs={"tbf_rate_mbit": 80})
    rm, monitor = rig(model, deployer)
    spec = model.spec()
    spec.binding = {"p4_json": "only.json"}                  # missing keys

    res = rm.run_episode(spec)
    assert res.verdict.outcome == "rejected"
    assert deployer.applied == [] and deployer.rollbacks == 0


# ---------------- KG writes (design §8) ----------------

class FakeKG:
    """Records each runtime write so the RM's wiring points are asserted
    without a Neo4j. FakeMonitor exposes no baseline_snapshot, so the baseline
    write is exercised by the node integration test, not here."""
    def __init__(self):
        self.kinds = []
    def write_switch_status(self, status, ts):
        self.kinds.append("status")
    def write_verdict(self, v):
        self.kinds.append(("verdict", v.outcome))
    def write_escalation(self, t, ts):
        self.kinds.append("escalation")
    def write_last_good(self, snap, ts):
        self.kinds.append("last_good")
    def write_baseline(self, b):
        self.kinds.append("baseline")


def test_kg_writes_status_verdict_and_lkg_on_commit():
    model = ContentionHarmWithKnob()
    deployer = FakeDeployer(initial_knobs={"tbf_rate_mbit": 80})
    kg = FakeKG()
    rm, _ = rig(model, deployer, kg=kg)
    res = rm.run_episode(model.spec())
    assert res.verdict.outcome == "marginal"
    assert "status" in kg.kinds                         # monitor-computed status
    assert ("verdict", "marginal") in kg.kinds
    assert "last_good" in kg.kinds                       # committed config persisted
    assert "escalation" not in kg.kinds


def test_kg_writes_escalation_and_no_lkg_on_escalate():
    model = Ddil()
    kg = FakeKG()
    rm, _ = rig(model, FakeDeployer(), kg=kg)
    res = rm.run_episode(model.spec())
    assert res.verdict.outcome == "escalated"
    assert ("verdict", "escalated") in kg.kinds and "escalation" in kg.kinds
    assert "last_good" not in kg.kinds                   # no commit -> no promotion


def test_kg_writes_verdict_for_a_gate_rejection():
    kg = FakeKG()
    rm, _ = rig(ContentionHarmWithKnob(), FakeDeployer(), kg=kg)
    spec = ContentionHarmWithKnob().spec()
    spec.binding = {"p4_json": "only.json"}              # gate refuses
    rm.run_episode(spec)
    assert kg.kinds == [("verdict", "rejected")]         # nothing else; never deployed


def test_kg_write_failure_does_not_abort_episode():
    """Best-effort writes (M6 soak): a Neo4j hiccup must not crash the loop."""
    class BrokenKG:
        def __getattr__(self, _):
            def boom(*a):
                raise RuntimeError("neo4j down")
            return boom
    model = Ddil()
    rm, _ = rig(model, FakeDeployer(), kg=BrokenKG())
    res = rm.run_episode(model.spec())                   # must NOT raise
    assert res.verdict.outcome == "escalated"
    assert rm.kg_write_failures > 0                       # failures were swallowed
