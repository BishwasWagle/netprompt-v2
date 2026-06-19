"""Slow-planner analytics (outer-loop Results+analytics over runtime verdicts).

Loaded standalone (the module's only heavy import, kg_context, is lazy in main()).
Exit criteria: verdict/escalation rows aggregate into outcome/tier/headroom stats and a
per-SFC reliability table, with each verdict attributed to its SFC via the planner
correlation_id (`plan-<sfc>-<hex>`) or a joined EscalationTicket.sfc."""
import importlib.util
from pathlib import Path

_AN = (Path(__file__).resolve().parents[2]
       / "network/milestone-II-latest/netprompt-milestone-II"
       / "llm_orchestrator/analytics.py")
_spec = importlib.util.spec_from_file_location("analytics", _AN)
an = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(an)

VERDICTS = [
    {"cid": "plan-reliablerelaysfc-aaa111", "outcome": "escalated", "tier": 2, "headroom": 0.0, "ts": "t1"},
    {"cid": "plan-lowlatencyvideosfc-bbb222", "outcome": "healthy", "tier": 0, "headroom": 0.30, "ts": "t2"},
    {"cid": "ep-x1", "outcome": "escalated", "tier": 2, "headroom": 0.0, "ts": "t3"},
    {"cid": "soak-0", "outcome": "marginal", "tier": 1, "headroom": 0.05, "ts": "t4"},
    {"cid": "manual-7", "outcome": "rollback", "tier": 0, "headroom": 0.0, "ts": "t5"},
]
ESCALATIONS = [
    {"cid": "plan-reliablerelaysfc-aaa111", "sfc": "ReliableRelaySFC", "reason": "all tiers exhausted"},
    {"cid": "ep-x1", "sfc": "EnergyAwareSFC", "reason": "budget spent"},
]


def _fake_cypher(query, params=None):
    if "Verdict" in query:
        return list(VERDICTS)
    if "EscalationTicket" in query:
        return list(ESCALATIONS)
    return []


def test_attribution_plan_id_and_escalation_join():
    esc = {"ep-x1": "EnergyAwareSFC"}
    assert an._sfc_for("plan-reliablerelaysfc-aaa111", esc) == "ReliableRelaySFC"  # parsed
    assert an._sfc_for("ep-x1", esc) == "EnergyAwareSFC"                           # joined
    assert an._sfc_for("soak-0", esc) is None                                      # neither


def test_outcome_tier_headroom_aggregates():
    s = an.collect(_fake_cypher)
    assert s["episodes"] == 5
    assert s["outcomes"] == {"escalated": 2, "healthy": 1, "marginal": 1, "rollback": 1}
    assert s["tiers"] == {"0": 2, "1": 1, "2": 2}
    assert s["headroom"]["n"] == 2                       # committed = healthy + marginal
    assert s["headroom"]["min"] == 0.05
    assert s["headroom"]["mean"] == 0.175


def test_escalations_and_per_sfc():
    s = an.collect(_fake_cypher)
    assert s["escalations"]["total"] == 2
    assert s["escalations"]["by_sfc"] == {"ReliableRelaySFC": 1, "EnergyAwareSFC": 1}
    # 3 verdicts attributable (2 via plan-id, 1 via escalation join); soak-0/manual-7 not
    assert s["attributed"] == 3
    assert s["per_sfc"]["ReliableRelaySFC"] == {"escalated": 1}
    assert s["per_sfc"]["LowLatencyVideoSFC"] == {"healthy": 1}
    assert s["per_sfc"]["EnergyAwareSFC"] == {"escalated": 1}


def test_format_report_is_text():
    r = an.format_report(an.collect(_fake_cypher))
    assert "Slow-Planner Analytics" in r
    assert "per-SFC reliability" in r and "escalations:" in r


def test_reliability_summary_compact():
    rs = an.reliability_summary(an.collect(_fake_cypher))
    assert rs["total_episodes"] == 5
    assert rs["per_sfc_reliability"]["ReliableRelaySFC"]["escalation_rate"] == 1.0
    assert rs["per_sfc_reliability"]["LowLatencyVideoSFC"]["escalation_rate"] == 0.0


def test_feedback_for_planner_is_graceful():
    # a raising KG read must never break planning -> {}
    def boom(query, params=None):
        raise RuntimeError("kg down")
    assert an.feedback_for_planner(boom) == {}
    # disabled via env -> {}
    import os
    os.environ["NETPROMPT_PLANNER_FEEDBACK"] = "0"
    try:
        assert an.feedback_for_planner(_fake_cypher) == {}
    finally:
        del os.environ["NETPROMPT_PLANNER_FEEDBACK"]
    # enabled (default) -> the compact signal
    assert "per_sfc_reliability" in an.feedback_for_planner(_fake_cypher)
