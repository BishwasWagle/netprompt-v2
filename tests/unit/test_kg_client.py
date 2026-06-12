"""Item 5 — kg_client against a mocked driver. Cypher + parameter shapes
verified locally; M5-node points the same client at the real Neo4j."""
import json

import pytest

from runtime.contracts import (
    AttemptRecord, BACKUP, BaselineSnapshot, Candidate, ConfigSnapshot,
    Envelope, EscalationTicket, PRIMARY, REROUTE, Verdict,
)
from runtime.kg_client import KGClient


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def single(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class FakeDriver:
    """Records every (query, params); answers from a scripted queue."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        self.calls.append((" ".join(query.split()), params))
        return FakeResult(self.responses.pop(0) if self.responses else [])

    def close(self):
        pass


# ---------------- reads ----------------

def test_build_envelope_takes_strictest_bounds_and_runtime_action_space():
    driver = FakeDriver(responses=[
        [{"lat": 50, "bw": 30}],          # SFCTemplate (ReliableRelaySFC)
        [{"lat": 40, "bw": 20}],          # AgriculturalField
    ])
    env = KGClient(driver).build_envelope("ReliableRelaySFC", "Field_2")
    assert env.max_latency_ms == 40       # strictest latency wins
    assert env.min_bandwidth_mbps == 30   # strictest bandwidth wins
    # action space comes from the runtime registry, never the KG
    assert env.legal_paths == frozenset((PRIMARY, BACKUP))
    assert "reroute" in env.legal_tiers and env.knob_ranges == {}


def test_build_envelope_survives_missing_template_bounds():
    driver = FakeDriver(responses=[
        [{"lat": None, "bw": None}],      # template exists, carries no bounds
        [{"lat": 20, "bw": 40}],
    ])
    env = KGClient(driver).build_envelope("LowLatencyVideoSFC", "Field_1")
    assert (env.max_latency_ms, env.min_bandwidth_mbps) == (20, 40)
    assert env.knob_ranges == {"pfifo_limit": (10, 50)}


def test_build_envelope_unknown_field_raises():
    driver = FakeDriver(responses=[[{"lat": 50, "bw": 30}], []])
    with pytest.raises(LookupError):
        KGClient(driver).build_envelope("ReliableRelaySFC", "Field_99")


def test_read_field_requirements_maps_all_fields():
    driver = FakeDriver(responses=[[
        {"id": "Field_1", "lat": 20, "bw": 40},
        {"id": "Field_2", "lat": 50, "bw": 20},
    ]])
    reqs = KGClient(driver).read_field_requirements()
    assert set(reqs) == {"Field_1", "Field_2"}
    assert reqs["Field_1"].max_latency_ms == 20


def test_read_last_good_roundtrips_payload():
    payload = {"active_path": "primary", "qos_state": {"d4": {"tbf_rate_mbit": 45}}}
    driver = FakeDriver(responses=[[{"payload": json.dumps(payload)}]])
    assert KGClient(driver).read_last_good("BandwidthOptimizedSFC") == payload
    driver2 = FakeDriver(responses=[[]])
    assert KGClient(driver2).read_last_good("BandwidthOptimizedSFC") is None


# ---------------- writes ----------------

def test_write_switch_status_one_merge_per_switch():
    driver = FakeDriver()
    KGClient(driver).write_switch_status(
        {"s1": "Active", "s2": "Degraded", "s3": "Active"}, timestamp="t0")
    assert len(driver.calls) == 3
    q, p = driver.calls[1]
    assert "MERGE (p:ProgrammableSwitch" in q
    assert p == {"id": "s2", "status": "Degraded", "ts": "t0"}
    assert "updated_by='runtime-manager'" in q


def test_write_verdict_serializes_trace_as_json():
    trace = [AttemptRecord(Candidate(REROUTE, (BACKUP,)), True, True, False,
                           kept=False, note="dominated")]
    driver = FakeDriver()
    KGClient(driver).write_verdict(Verdict("ep1", "escalated", 1, 0.0, trace, "t1"))
    q, p = driver.calls[0]
    assert "CREATE (n:Verdict" in q and p["outcome"] == "escalated"
    parsed = json.loads(p["trace"])
    assert parsed[0]["candidate"]["params"] == ["backup"]


def test_write_escalation_serializes_envelope_with_frozensets():
    env = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                   legal_tiers=frozenset(("tune",)),
                   legal_paths=frozenset((PRIMARY,)))
    ticket = EscalationTicket("ep1", "LowLatencyVideoSFC",
                              {"F1": {"met": False}}, env, [], "budget spent")
    driver = FakeDriver()
    KGClient(driver).write_escalation(ticket, timestamp="t2")
    q, p = driver.calls[0]
    assert "CREATE (n:EscalationTicket" in q and "status:'open'" in q
    assert json.loads(p["envelope"])["legal_tiers"] == ["tune"]


def test_write_snapshots():
    driver = FakeDriver()
    client = KGClient(driver)
    client.write_baseline(BaselineSnapshot("ep1", {"F1": {"rtt_avg_ms": 12.0}},
                                           {"s1": "Active"}, 5, "t3"))
    client.write_last_good(ConfigSnapshot("ep1", "ReliableRelaySFC", {},
                                          {}, {"d4": {}}, PRIMARY), "t4")
    (q1, p1), (q2, p2) = driver.calls
    assert "MERGE (n:BaselineSnapshot" in q1
    assert json.loads(p1["payload"])["per_flow"]["F1"]["rtt_avg_ms"] == 12.0
    assert "MERGE (g:LastKnownGood" in q2 and p2["sfc"] == "ReliableRelaySFC"
    assert json.loads(p2["payload"])["active_path"] == PRIMARY
