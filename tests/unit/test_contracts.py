"""M1 — contract types: hashability, margin math, GOAL predicate."""
from runtime.contracts import (
    BACKUP, REROUTE, TUNE,
    Candidate, Envelope, MonitorReport, compute_flow_metrics, goal,
)

REQ = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2)


def test_candidate_is_hashable_and_usable_in_tried_set():
    tried = set()
    a = Candidate(TUNE, ("tbf_rate_mbit", 50))
    b = Candidate(TUNE, ("tbf_rate_mbit", 50))   # same step proposed twice
    c = Candidate(REROUTE, (BACKUP,))
    tried.add(a)
    assert b in tried                            # equal params == already tried
    assert c not in tried
    assert a.tier == 0 and c.tier == 1


def test_flow_metrics_met_inside_all_bounds():
    fm = compute_flow_metrics("F1", rtt_avg_ms=10, throughput_mbps=55,
                              loss_percent=0.5, requirement=REQ)
    assert fm.met
    # margin is the WORST dimension: bw (55-40)/40 = 0.375 < lat 0.5 < loss 0.75
    assert abs(fm.margin - 0.375) < 1e-9


def test_flow_metrics_violation_is_negative_margin():
    fm = compute_flow_metrics("F1", rtt_avg_ms=45, throughput_mbps=55,
                              loss_percent=0.5, requirement=REQ)
    assert not fm.met
    assert fm.margin < 0                          # latency dimension violates


def test_flow_metrics_boundary_counts_as_met():
    fm = compute_flow_metrics("F1", rtt_avg_ms=20, throughput_mbps=40,
                              loss_percent=2, requirement=REQ)
    assert fm.met and fm.margin == 0.0


def _report(target_met: bool, harm: list) -> MonitorReport:
    fm = compute_flow_metrics("F1", 10, 55, 0.5, REQ)
    return MonitorReport(
        correlation_id="t", switch_status={}, system_sound=True,
        target=fm, non_target=[], target_sla_met=target_met,
        vs_baseline={}, exogenous_shift=False, displaced_harm=harm,
        headroom=0.3, path_confidence={},
    )


def test_goal_requires_target_met_and_no_harm():
    assert goal(_report(True, []))
    assert not goal(_report(False, []))
    assert not goal(_report(True, ["F2"]))
    assert not goal(_report(False, ["F2"]))


def test_jsonable_makes_contracts_dumpable():
    """kg_client (M5) persists these as JSON — frozensets/tuples/nesting
    must all flatten cleanly."""
    import json
    from runtime.contracts import (
        AttemptRecord, BACKUP, EscalationTicket, PRIMARY, REROUTE, jsonable,
    )
    env = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                   legal_tiers=frozenset(("tune", "reroute")),
                   legal_paths=frozenset((PRIMARY, BACKUP)),
                   knob_ranges={"tbf_rate_mbit": (5, 80)})
    ticket = EscalationTicket(
        correlation_id="ep1", sfc="ReliableRelaySFC",
        observed={"F1": {"met": False, "margin": -0.5}},
        envelope=env,
        trace=[AttemptRecord(Candidate(REROUTE, (BACKUP,)), True, True,
                             False, kept=False, note="dominated")],
        reason="all tiers exhausted")

    data = json.loads(json.dumps(jsonable(ticket)))   # round-trip
    assert data["envelope"]["legal_tiers"] == ["reroute", "tune"]   # sorted
    assert data["trace"][0]["candidate"]["params"] == ["backup"]
    assert data["observed"]["F1"]["margin"] == -0.5
