"""M1 — FakeDeployer state mechanics: apply, capture, deterministic rollback."""
from runtime.contracts import BACKUP, Candidate, PRIMARY, REGEN, REROUTE, TUNE
from runtime.fakes import FakeDeployer


def test_tune_and_reroute_mutate_state():
    d = FakeDeployer(initial_knobs={"tbf_rate_mbit": 80})
    d.apply(Candidate(TUNE, ("tbf_rate_mbit", 50)))
    assert d.state["knobs"]["tbf_rate_mbit"] == 50
    d.apply(Candidate(REROUTE, (BACKUP,)))
    assert d.state["path"] == BACKUP
    assert len(d.applied) == 2


def test_capture_then_rollback_restores_exactly():
    d = FakeDeployer(initial_knobs={"tbf_rate_mbit": 80})
    snap = d.capture()
    d.apply(Candidate(TUNE, ("tbf_rate_mbit", 50)))
    d.apply(Candidate(REROUTE, (BACKUP,)))
    d.rollback(snap)
    assert d.state == {"path": PRIMARY, "knobs": {"tbf_rate_mbit": 80}}
    assert d.rollbacks == 1


def test_capture_is_isolated_from_later_mutation():
    d = FakeDeployer()
    snap = d.capture()
    d.apply(Candidate(TUNE, ("pfifo_limit", 5)))
    assert "pfifo_limit" not in snap["knobs"]     # deep-copied, not aliased


def test_regen_applies_explicit_effects():
    d = FakeDeployer()
    d.apply(Candidate(REGEN, (("path", BACKUP), ("pfifo_limit", 30))))
    assert d.state["path"] == BACKUP
    assert d.state["knobs"]["pfifo_limit"] == 30
