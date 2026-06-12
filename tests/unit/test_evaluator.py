"""M3 — the 6-stage ladder routes every fixture to its designed verdict
(design §6). One test per rung/stage outcome."""
from runtime.adapt import Budget
from runtime.contracts import Candidate, PRIMARY, TUNE
from runtime.evaluator import EvalContext, commit_outcome, evaluate
from runtime.fakes import FakeDeployer, FakeMonitor
from runtime.fixtures import (
    CausalRegression, ContentionHarmNoKnob, ContentionHarmWithKnob,
    Ddil, Healthy, PathQualityFault,
)
from runtime.gate import ValidationGate


def run_eval(model, deployer, capacity=lambda c: True, budget_n=6, last_good=None):
    monitor = FakeMonitor(model, deployer)
    ctx = EvalContext(deployer, monitor, ValidationGate(), Budget(budget_n),
                      last_good=last_good, active_capacity_ok=capacity)
    return evaluate(model.spec(), monitor.observe_window(), ctx)


def test_rung1_system_fault_short_circuits():
    model = Healthy()
    model.system_sound = False
    res = run_eval(model, FakeDeployer())
    assert res.verdict.outcome == "system_fault" and res.ticket is None


def test_rung2_yes_clean_commit_is_healthy():
    res = run_eval(Healthy(), FakeDeployer())
    assert res.verdict.outcome == "healthy"
    assert res.verdict.tier_reached == 0 and res.verdict.trace == []


def test_rung3_causal_regression_rolls_back_to_last_good():
    deployer = FakeDeployer()                      # pristine = last-known-good
    lkg = deployer.capture()
    deployer.apply(Candidate(TUNE, ("pfifo_limit", 5)))     # the bad deploy
    res = run_eval(CausalRegression(), deployer, last_good=lkg)
    assert res.verdict.outcome == "rollback"
    assert deployer.state == {"path": PRIMARY, "knobs": {}}  # restored


def test_rung3_exogenous_regression_does_not_roll_back():
    deployer = FakeDeployer()
    res = run_eval(PathQualityFault(), deployer, last_good=deployer.capture())
    # regressed vs baseline BUT exogenous -> rung 4 -> reroute -> commit
    assert res.verdict.outcome == "healthy"
    assert res.verdict.tier_reached == 1
    assert deployer.rollbacks == 0                 # never treated as our fault


def test_rung4_failure_escalates_with_ticket():
    res = run_eval(Ddil(), FakeDeployer())
    assert res.verdict.outcome == "escalated"
    assert res.ticket is not None
    assert res.ticket.reason == "all tiers exhausted"
    assert res.ticket.trace                        # planner sees what was tried


def test_stage5_harm_adapts_then_commits_marginal():
    deployer = FakeDeployer(initial_knobs={"tbf_rate_mbit": 80})
    res = run_eval(ContentionHarmWithKnob(), deployer)
    assert res.verdict.outcome == "marginal"       # headroom 0.125 < tau 0.15
    assert res.verdict.tier_reached == 0
    assert deployer.state["knobs"]["tbf_rate_mbit"] == 45
    assert res.ticket is None


def test_stage5_no_harm_free_config_escalates():
    res = run_eval(ContentionHarmNoKnob(), FakeDeployer())
    assert res.verdict.outcome == "escalated"
    assert res.ticket.reason == "no harm-free config"
    assert res.ticket.observed["F2"]["met"] is False


def test_stage6_tier2_recovery_is_marginal_regardless_of_headroom():
    assert commit_outcome(headroom=0.5, tier_reached=2) == "marginal"
    assert commit_outcome(headroom=0.5, tier_reached=1) == "healthy"
    assert commit_outcome(headroom=0.05, tier_reached=0) == "marginal"
