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
