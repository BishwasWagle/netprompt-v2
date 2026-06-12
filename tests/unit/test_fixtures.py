"""M1 — scenario fixtures behave per their docstrings. These assertions are
the precondition for the M3 evaluator/engine tests: each scenario must present
exactly the conditions its design-doc rung expects."""
import pytest

from runtime.config import HEADROOM_TAU
from runtime.contracts import BACKUP, PRIMARY, REROUTE, TUNE, Candidate, goal
from runtime.fixtures import (
    ALL_SCENARIOS,
    CausalRegression, ContentionHarmNoKnob, ContentionHarmWithKnob,
    Ddil, Healthy, PathQualityFault,
)
from runtime.gate import ValidationGate

ON_PRIMARY = {"path": PRIMARY, "knobs": {}}
ON_BACKUP = {"path": BACKUP, "knobs": {}}


def test_reports_are_deterministic():
    m = PathQualityFault()
    assert m.report(ON_PRIMARY) == m.report(ON_PRIMARY)


def test_healthy_commits_clean():
    r = Healthy().report(ON_PRIMARY)
    assert r.system_sound and r.target_sla_met
    assert r.displaced_harm == [] and not r.exogenous_shift
    assert r.headroom > HEADROOM_TAU              # stage 6: margin ok


def test_causal_regression_points_at_us():
    r = CausalRegression().report({"path": PRIMARY, "knobs": {"pfifo_limit": 5}})
    assert not r.target_sla_met
    assert not r.exogenous_shift                  # rung 3: AND-guard passes -> rollback
    assert r.vs_baseline["F1"] < 0                # regressed vs our own baseline
    assert r.displaced_harm == []


def test_path_fault_is_exogenous_and_reroute_fixes_it():
    m = PathQualityFault()
    on_primary = m.report(ON_PRIMARY)
    assert not on_primary.target_sla_met
    assert on_primary.exogenous_shift             # rung 3: skip rollback -> rung 4
    assert on_primary.vs_baseline["F1"] < 0       # regressed, but not our fault
    assert on_primary.switch_status["s2"] == "Degraded"

    on_backup = m.report(ON_BACKUP)               # Tier-1 candidate outcome
    assert goal(on_backup)                        # target met, nobody harmed
    assert on_backup.headroom > HEADROOM_TAU      # commits healthy


def test_contention_harm_relieved_by_rate_down():
    m = ContentionHarmWithKnob()
    grabbing = m.report({"path": PRIMARY, "knobs": {"tbf_rate_mbit": 80}})
    assert grabbing.target_sla_met                # rung 2 yes -> commit path
    assert grabbing.displaced_harm == ["F2"]      # stage 5: harm found

    relieved = m.report({"path": PRIMARY, "knobs": {"tbf_rate_mbit": 45}})
    assert goal(relieved)                         # feasible harm-free point exists

    too_far = m.report({"path": PRIMARY, "knobs": {"tbf_rate_mbit": 30}})
    assert not too_far.target_sla_met             # domination guard must reject this


def test_contention_without_knob_has_no_feasible_config():
    m = ContentionHarmNoKnob()
    base = m.report(ON_PRIMARY)
    assert base.target_sla_met and base.displaced_harm == ["F2"]
    rerouted = m.report(ON_BACKUP)                # global reroute: contention follows
    assert not rerouted.target_sla_met            # dominated -> rolled back -> escalate


# ---------------- M3 readiness: fixtures x gate cross-checks ----------------

@pytest.mark.parametrize("name", sorted(ALL_SCENARIOS))
def test_every_scenario_spec_is_gate_valid(name):
    """The adapt engine gate-checks candidates against the fixture's envelope;
    a gate-invalid fixture would make M3 failures unattributable."""
    spec = ALL_SCENARIOS[name]().spec()
    assert ValidationGate().check_binding(spec).ok
    # bounds of the deployment envelope must match the target's requirement
    target_req = ALL_SCENARIOS[name]().fields[spec.target_field]
    assert (target_req.max_latency_ms, target_req.min_bandwidth_mbps,
            target_req.max_loss_percent) == (spec.envelope.max_latency_ms,
                                             spec.envelope.min_bandwidth_mbps,
                                             spec.envelope.max_loss_percent)


def test_path_fault_envelope_permits_the_fixing_reroute():
    m = PathQualityFault()
    assert ValidationGate().check(Candidate(REROUTE, (BACKUP,)), m.envelope).ok
    assert m.envelope.knob_ranges == {}        # Tier 0 exhausts instantly


def test_harm_envelope_permits_the_relieving_tune():
    m = ContentionHarmWithKnob()
    # rate 45 is on the lo-anchored grid (5 + 4*10) and inside the range
    assert ValidationGate().check(Candidate(TUNE, ("tbf_rate_mbit", 45)), m.envelope).ok
    lo, step = m.envelope.knob_ranges["tbf_rate_mbit"][0], 10
    assert (45 - lo) % step == 0


def test_no_knob_envelope_offers_only_the_doomed_reroute():
    m = ContentionHarmNoKnob()
    assert m.envelope.legal_tiers == frozenset((REROUTE,))
    assert ValidationGate().check(Candidate(REROUTE, (BACKUP,)), m.envelope).ok
    r = ValidationGate().check(Candidate(TUNE, ("tbf_rate_mbit", 45)), m.envelope)
    assert not r.ok                            # no shaping lever exists


def test_ddil_unrecoverable_on_both_paths():
    m = Ddil()
    assert not m.report(ON_PRIMARY).target_sla_met
    assert not m.report(ON_BACKUP).target_sla_met
    r = m.report(ON_PRIMARY)
    assert r.exogenous_shift                      # not our fault -> adapt, not rollback
    assert r.vs_baseline["F1"] < 0
