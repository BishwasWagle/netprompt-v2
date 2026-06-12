"""M1 — scenario fixtures behave per their docstrings. These assertions are
the precondition for the M3 evaluator/engine tests: each scenario must present
exactly the conditions its design-doc rung expects."""
from runtime.config import HEADROOM_TAU
from runtime.contracts import BACKUP, PRIMARY, goal
from runtime.fixtures import (
    CausalRegression, ContentionHarmNoKnob, ContentionHarmWithKnob,
    Ddil, Healthy, PathQualityFault,
)

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


def test_ddil_unrecoverable_on_both_paths():
    m = Ddil()
    assert not m.report(ON_PRIMARY).target_sla_met
    assert not m.report(ON_BACKUP).target_sla_met
    r = m.report(ON_PRIMARY)
    assert r.exogenous_shift                      # not our fault -> adapt, not rollback
    assert r.vs_baseline["F1"] < 0
