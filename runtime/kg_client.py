"""KG client — the runtime's Neo4j surface (design §8).

Reads strategic state (SFCTemplate bounds, field requirements); writes
RUNTIME state only (switch status, verdicts, tickets, snapshots). Never
touches the SFC library — see docs/runtime-planner-contracts.md §3.

The driver is injectable: tests pass a fake; production constructs via
KGClient.connect() (lazy neo4j import, same endpoint/credentials as the
existing milestone-II scripts). Complex payloads (traces, envelopes,
snapshots) are persisted as JSON-string properties via contracts.jsonable —
Neo4j properties cannot hold nested maps.
"""
from __future__ import annotations

import json

from runtime import config
from runtime.contracts import (
    BaselineSnapshot, ConfigSnapshot, Envelope, EscalationTicket, Verdict,
    jsonable,
)


class KGClient:

    def __init__(self, driver):
        self.driver = driver

    @classmethod
    def connect(cls, uri: str = config.KG_URI, user: str = config.KG_USER,
                password: str = config.KG_PASS) -> "KGClient":
        from neo4j import GraphDatabase            # lazy: tests never need it
        return cls(GraphDatabase.driver(uri, auth=(user, password)))

    def close(self) -> None:
        self.driver.close()

    # ---------------- reads (strategic state) ----------------

    def build_envelope(self, sfc: str, target_field: str) -> Envelope:
        """Bounds = strictest of the SFC template's and the field's own
        requirements (both KG-owned); action space = the runtime-owned
        registry (config.SFC_ACTION_SPACE). The planner never authors
        tiers/paths/knobs — see design §9."""
        with self.driver.session() as s:
            t = s.run(
                "MATCH (t:SFCTemplate {id:$sfc}) "
                "RETURN t.max_latency_ms AS lat, t.min_bandwidth_mbps AS bw",
                sfc=sfc).single()
            f = s.run(
                "MATCH (f:AgriculturalField {id:$field}) "
                "RETURN f.latency_requirement_ms AS lat, "
                "f.bandwidth_requirement_mbps AS bw",
                field=target_field).single()
        if f is None:
            raise LookupError(f"unknown field {target_field!r}")
        lats = [v for v in ((t or {}).get("lat"), f["lat"]) if v is not None]
        bws = [v for v in ((t or {}).get("bw"), f["bw"]) if v is not None]
        if not lats or not bws:
            raise LookupError(f"no bounds resolvable for {sfc}/{target_field}")
        space = config.SFC_ACTION_SPACE.get(sfc, {})
        return Envelope(
            max_latency_ms=min(lats),               # strictest
            min_bandwidth_mbps=max(bws),            # strictest
            max_loss_percent=config.DEFAULT_MAX_LOSS_PERCENT,
            legal_tiers=frozenset(space.get("legal_tiers", ())),
            legal_paths=frozenset(space.get("legal_paths", ("primary",))),
            knob_ranges=dict(space.get("knob_ranges", {})),
        )

    def read_field_requirements(self) -> dict:
        """{field_id: Envelope(bounds only)} for ALL fields — the monitor's
        per-flow requirements (design §5.1)."""
        with self.driver.session() as s:
            rows = s.run(
                "MATCH (f:AgriculturalField) RETURN f.id AS id, "
                "f.latency_requirement_ms AS lat, "
                "f.bandwidth_requirement_mbps AS bw")
            return {r["id"]: Envelope(
                        max_latency_ms=r["lat"], min_bandwidth_mbps=r["bw"],
                        max_loss_percent=config.DEFAULT_MAX_LOSS_PERCENT)
                    for r in rows}

    def read_last_good(self, sfc: str) -> dict | None:
        with self.driver.session() as s:
            row = s.run(
                "MATCH (g:LastKnownGood {sfc:$sfc}) RETURN g.payload AS payload",
                sfc=sfc).single()
        return json.loads(row["payload"]) if row else None

    # ---------------- writes (runtime state only) ----------------

    def write_switch_status(self, status: dict, timestamp: str) -> None:
        """Monitor-computed status (§5.5) — replaces the hardcoded
        SCENARIO_STATE writes of update_topology_state.py."""
        with self.driver.session() as s:
            for switch, value in status.items():
                s.run(
                    "MERGE (p:ProgrammableSwitch {id:$id}) "
                    "SET p.status=$status, p.last_updated=$ts, "
                    "p.updated_by='runtime-manager'",
                    id=switch, status=value, ts=timestamp)

    def write_verdict(self, v: Verdict) -> None:
        with self.driver.session() as s:
            s.run(
                "CREATE (n:Verdict {correlation_id:$cid, outcome:$outcome, "
                "tier_reached:$tier, headroom:$headroom, trace:$trace, "
                "timestamp:$ts, updated_by:'runtime-manager'})",
                cid=v.correlation_id, outcome=v.outcome, tier=v.tier_reached,
                headroom=v.headroom, trace=json.dumps(jsonable(v.trace)),
                ts=v.timestamp)

    def write_escalation(self, t: EscalationTicket, timestamp: str) -> None:
        with self.driver.session() as s:
            s.run(
                "CREATE (n:EscalationTicket {correlation_id:$cid, sfc:$sfc, "
                "reason:$reason, observed:$observed, envelope:$envelope, "
                "trace:$trace, timestamp:$ts, status:'open', "
                "updated_by:'runtime-manager'})",
                cid=t.correlation_id, sfc=t.sfc, reason=t.reason,
                observed=json.dumps(jsonable(t.observed)),
                envelope=json.dumps(jsonable(t.envelope)),
                trace=json.dumps(jsonable(t.trace)), ts=timestamp)

    def write_baseline(self, b: BaselineSnapshot) -> None:
        with self.driver.session() as s:
            s.run(
                "MERGE (n:BaselineSnapshot {correlation_id:$cid}) "
                "SET n.payload=$payload, n.timestamp=$ts, "
                "n.updated_by='runtime-manager'",
                cid=b.correlation_id, payload=json.dumps(jsonable(b)),
                ts=b.timestamp)

    def write_last_good(self, snap: ConfigSnapshot, timestamp: str) -> None:
        with self.driver.session() as s:
            s.run(
                "MERGE (g:LastKnownGood {sfc:$sfc}) "
                "SET g.payload=$payload, g.correlation_id=$cid, "
                "g.timestamp=$ts, g.updated_by='runtime-manager'",
                sfc=snap.sfc, payload=json.dumps(jsonable(snap)),
                cid=snap.correlation_id, ts=timestamp)
