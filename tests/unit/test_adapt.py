"""M3 — the adaptation engine against the fixture physics (design §7)."""
from runtime.adapt import Budget, adapt, diagnose, propose
from runtime.contracts import (
    BACKUP, PRIMARY, REROUTE, TUNE, Candidate, GateResult, goal,
)
from runtime.fakes import FakeDeployer, FakeMonitor
from runtime.fixtures import (
    ContentionHarmNoKnob, ContentionHarmWithKnob, Ddil, Healthy, PathQualityFault,
)
from runtime.gate import ValidationGate


def rig(model_cls, initial_knobs=None):
    model = model_cls()
    deployer = FakeDeployer(initial_knobs=initial_knobs)
    monitor = FakeMonitor(model, deployer)
    return model, deployer, monitor


def run_adapt(model, deployer, monitor, budget_n=6, capacity=None, gate=None):
    budget = Budget(budget_n)
    res = adapt(model.spec(), monitor.observe_window(), budget,
                deployer, monitor, gate or ValidationGate(),
                active_capacity_ok=capacity)
    return res, budget


# ---------------- diagnose / propose units ----------------

def test_diagnose_picks_target_first_and_worst_metric():
    model, deployer, monitor = rig(PathQualityFault)
    d = diagnose(monitor.observe_window())
    assert d.who == "target" and d.metric == "latency"   # rtt 45 vs bound 20

    model, deployer, monitor = rig(ContentionHarmWithKnob,
                                   initial_knobs={"tbf_rate_mbit": 80})
    d = diagnose(monitor.observe_window())
    assert d.who == "F2" and d.metric == "throughput"    # starved neighbor


def test_diagnose_none_at_goal():
    model, deployer, monitor = rig(Healthy)
    assert diagnose(monitor.observe_window()) is None


def test_propose_tune_steps_down_from_current_and_respects_exclude():
    model = ContentionHarmWithKnob()
    state = {"path": PRIMARY, "knobs": {"tbf_rate_mbit": 80}}
    d = diagnose(FakeMonitor(model, FakeDeployer(initial_knobs=state["knobs"])).observe_window())
    first = propose(d, 0, model.envelope, state, exclude=set())
    assert first == Candidate(TUNE, ("tbf_rate_mbit", 75))   # lo-anchored grid
    second = propose(d, 0, model.envelope, state, exclude={first})
    assert second == Candidate(TUNE, ("tbf_rate_mbit", 65))


def test_propose_reroute_offers_other_legal_path_once():
    model = PathQualityFault()
    state = {"path": PRIMARY, "knobs": {}}
    d = diagnose(FakeMonitor(model, FakeDeployer()).observe_window())
    cand = propose(d, 1, model.envelope, state, exclude=set())
    assert cand == Candidate(REROUTE, (BACKUP,))
    assert propose(d, 1, model.envelope, state, exclude={cand}) is None


def test_propose_tier2_is_stubbed():
    model = Ddil()
    d = diagnose(FakeMonitor(model, FakeDeployer()).observe_window())
    assert propose(d, 2, model.envelope, {"path": PRIMARY, "knobs": {}}, set()) is None


def test_propose_tier2_consults_injected_regen_proposer():
    """The M7 seam: a regen proposer plugs in without touching the engine."""
    model = Ddil()
    d = diagnose(FakeMonitor(model, FakeDeployer()).observe_window())
    sentinel = Candidate("regen", ("s1", "table_modify forward_table forward 2 => 12"))
    seen = []
    def proposer(diag, env, state, exclude):
        seen.append((diag.who, state["path"]))
        return sentinel
    got = propose(d, 2, model.envelope, {"path": PRIMARY, "knobs": {}}, set(),
                  regen_proposer=proposer)
    assert got is sentinel and seen == [("target", PRIMARY)]


def test_engine_reaches_injected_regen_proposer_at_tier2():
    model, deployer, monitor = rig(Ddil)
    calls = []
    def proposer(diag, env, state, exclude):
        calls.append(diag.who)
        return None                                   # nothing to offer
    budget = Budget(6)
    res = adapt(model.spec(), monitor.observe_window(), budget,
                deployer, monitor, ValidationGate(),
                active_capacity_ok=lambda c: True, regen_proposer=proposer)
    assert calls == ["target"]                        # tier 2 was consulted
    assert not res.success and res.reason == "all tiers exhausted"


# ---------------- acceptance traces ----------------

def test_path_quality_fault_recovers_via_tier1_reroute():
    model, deployer, monitor = rig(PathQualityFault)
    res, budget = run_adapt(model, deployer, monitor, capacity=lambda c: True)
    assert res.success and res.tier_reached == 1
    assert deployer.state["path"] == BACKUP
    assert budget.spent == 1 and goal(res.final_report)


def test_contention_harm_relieved_by_tier0_rate_down():
    model, deployer, monitor = rig(ContentionHarmWithKnob,
                                   initial_knobs={"tbf_rate_mbit": 80})
    res, budget = run_adapt(model, deployer, monitor)
    assert res.success and res.tier_reached == 0
    assert deployer.state["knobs"]["tbf_rate_mbit"] == 45    # the feasible point
    assert deployer.rollbacks == 3                           # 75, 65, 55 undone
    assert budget.spent == 4
    assert goal(res.final_report)
    cands = [a.candidate for a in res.trace]
    assert len(cands) == len(set(cands))                     # never retried


def test_no_harm_free_config_exhausts_without_sacrificing_target():
    model, deployer, monitor = rig(ContentionHarmNoKnob)
    res, budget = run_adapt(model, deployer, monitor, capacity=lambda c: True)
    assert not res.success and res.reason == "all tiers exhausted"
    # the doomed reroute was tried, dominated (target dropped), rolled back
    assert deployer.state == {"path": PRIMARY, "knobs": {}}
    assert res.final_report.target_sla_met                   # target never sacrificed


def test_ddil_exhausts_all_tiers():
    model, deployer, monitor = rig(Ddil)
    res, budget = run_adapt(model, deployer, monitor, capacity=lambda c: True)
    assert not res.success and res.reason == "all tiers exhausted"
    assert deployer.state["path"] == PRIMARY                 # reroute rolled back
    assert deployer.rollbacks == 1


def test_failed_capacity_check_skips_probe_and_spends_no_budget():
    model, deployer, monitor = rig(Ddil)
    res, budget = run_adapt(model, deployer, monitor, capacity=lambda c: False)
    assert not res.success and budget.spent == 0
    assert any("capacity" in a.note for a in res.trace)
    assert deployer.applied == []                            # never touched the net


def test_gate_rejection_consumes_no_budget_but_is_bounded():
    class RejectFirst:
        def __init__(self):
            self.inner, self.done = ValidationGate(), False
        def check(self, cand, env, tables=None):
            if not self.done:
                self.done = True
                return GateResult(False, "L1: stub rejection")
            return self.inner.check(cand, env, tables)

    model, deployer, monitor = rig(ContentionHarmWithKnob,
                                   initial_knobs={"tbf_rate_mbit": 80})
    res, budget = run_adapt(model, deployer, monitor, gate=RejectFirst())
    assert res.success
    assert budget.spent == 3                                 # 65, 55, 45 applied
    assert not res.trace[0].gate_ok and not res.trace[0].applied


def test_budget_exhaustion_leaves_best_achieved_dominating_state():
    model, deployer, monitor = rig(ContentionHarmWithKnob,
                                   initial_knobs={"tbf_rate_mbit": 80})
    res, budget = run_adapt(model, deployer, monitor, budget_n=2)
    assert not res.success and res.reason == "budget spent"
    assert budget.spent == 2
    # both attempts (75, 65) were non-improving -> rolled back to entry state
    assert deployer.state["knobs"]["tbf_rate_mbit"] == 80
    assert res.final_report.target_sla_met                   # never worse than entry
