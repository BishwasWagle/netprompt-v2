"""Slow-planner analytics — the outer loop's "Results + analytics" (design two-loop hub).

The Runtime Manager writes a `Verdict` per episode and an `EscalationTicket` when it
gives up (kg_client, `updated_by='runtime-manager'`). This module is the planner-side
consumer: it aggregates that history into outcome/tier/headroom stats and a **per-SFC
reliability signal** the planner can learn from (which SFCs commit cleanly vs escalate on
this fabric). Read-only over the KG; optional `--write-kg` stores a `PlannerAnalytics`
snapshot node.

    source deploy/gpu-node/gpu-node.env
    ~/netprompt-venv/bin/python -m llm_orchestrator.analytics \
        --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123
    # --json for machine output; --write-kg to persist a PlannerAnalytics node
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict

# SFCs the planner knows (slug -> canonical), for attributing a verdict to an SFC via
# the adapter's `plan-<sfc-slug>-<hex>` correlation_id.
_KNOWN_SFCS = ["LowLatencyVideoSFC", "ReliableRelaySFC",
               "BandwidthOptimizedSFC", "EnergyAwareSFC"]
_SLUG_TO_SFC = {s.lower(): s for s in _KNOWN_SFCS}
_PLAN_CID = re.compile(r"^plan-([a-z0-9]+)-[0-9a-f]+$")
_COMMITTED = ("healthy", "marginal")


def _sfc_for(cid: str, esc_sfc: dict) -> str | None:
    """Attribute a verdict's SFC: prefer a matching EscalationTicket.sfc, else parse the
    planner correlation_id (`plan-<slug>-<hex>`). None when not attributable."""
    if cid in esc_sfc:
        return esc_sfc[cid]
    m = _PLAN_CID.match(cid or "")
    return _SLUG_TO_SFC.get(m.group(1)) if m else None


def collect(run_cypher) -> dict:
    """Aggregate the runtime's verdict/escalation history. `run_cypher(query)` returns a
    list of dict rows (e.g. Neo4jContextClient.run_cypher)."""
    verdicts = run_cypher(
        "MATCH (v:Verdict) RETURN v.correlation_id AS cid, v.outcome AS outcome, "
        "v.tier_reached AS tier, v.headroom AS headroom, v.timestamp AS ts")
    escalations = run_cypher(
        "MATCH (e:EscalationTicket) RETURN e.correlation_id AS cid, e.sfc AS sfc, "
        "e.reason AS reason")

    esc_sfc = {e["cid"]: e["sfc"] for e in escalations if e.get("sfc")}
    outcomes = Counter(v["outcome"] for v in verdicts)
    tiers = Counter(v["tier"] for v in verdicts if v["tier"] is not None)
    headrooms = [v["headroom"] for v in verdicts
                 if v["outcome"] in _COMMITTED and v.get("headroom") is not None]
    timestamps = sorted(v["ts"] for v in verdicts if v.get("ts"))

    # per-SFC reliability (attributed verdicts only)
    per_sfc = defaultdict(Counter)
    attributed = 0
    for v in verdicts:
        sfc = _sfc_for(v["cid"], esc_sfc)
        if sfc:
            attributed += 1
            per_sfc[sfc][v["outcome"]] += 1

    return {
        "episodes": len(verdicts),
        "window": [timestamps[0], timestamps[-1]] if timestamps else [None, None],
        "outcomes": dict(outcomes),
        "tiers": {str(k): c for k, c in sorted(tiers.items())},
        "headroom": {
            "n": len(headrooms),
            "mean": round(sum(headrooms) / len(headrooms), 4) if headrooms else None,
            "min": round(min(headrooms), 4) if headrooms else None,
        },
        "escalations": {
            "total": len(escalations),
            "by_sfc": dict(Counter(e["sfc"] for e in escalations if e.get("sfc"))),
            "by_reason": dict(Counter(e["reason"] for e in escalations if e.get("reason"))),
        },
        "per_sfc": {sfc: dict(c) for sfc, c in sorted(per_sfc.items())},
        "attributed": attributed,
    }


def reliability_summary(stats: dict) -> dict:
    """A compact, prompt-friendly slice of collect(): per-SFC episodes + escalation_rate +
    marginal count. This is what the planner folds into its decision context."""
    per = {}
    for sfc, c in stats.get("per_sfc", {}).items():
        eps = sum(c.values())
        esc = c.get("escalated", 0)
        per[sfc] = {"episodes": eps,
                    "escalation_rate": round(esc / eps, 2) if eps else None,
                    "marginal": c.get("marginal", 0)}
    return {"total_episodes": stats.get("episodes", 0), "per_sfc_reliability": per}


def feedback_for_planner(run_cypher) -> dict:
    """The runtime-reliability block the orchestrator folds into its LLM input
    (build_runtime_input_object). Graceful: returns {} if disabled
    (NETPROMPT_PLANNER_FEEDBACK=0) or on any KG/aggregation error, so the planner runs
    unchanged when there's no history."""
    import os
    if os.getenv("NETPROMPT_PLANNER_FEEDBACK", "1") != "1":
        return {}
    try:
        return reliability_summary(collect(run_cypher))
    except Exception:  # noqa: BLE001 — feedback is best-effort, never break planning
        return {}


def _rate(part, whole):
    return f"{(100.0 * part / whole):.0f}%" if whole else "—"


def format_report(s: dict) -> str:
    n = s["episodes"]
    L = ["NetPrompt — Slow-Planner Analytics (runtime verdict history)",
         "=" * 60,
         f"episodes (verdicts): {n}   window: {s['window'][0]} .. {s['window'][1]}",
         ""]
    L.append("outcomes:")
    for o, c in sorted(s["outcomes"].items(), key=lambda kv: -kv[1]):
        L.append(f"  {o:<14} {c:>5}  ({_rate(c, n)})")
    L.append("")
    L.append("adapt tier reached: " +
             ", ".join(f"{k}:{c}" for k, c in s["tiers"].items()) + "  (0 none/tune · 1 reroute · 2 regen)")
    h = s["headroom"]
    L.append(f"commit headroom (healthy+marginal, n={h['n']}): mean={h['mean']} min={h['min']}")
    L.append("")
    e = s["escalations"]
    L.append(f"escalations: {e['total']}")
    for sfc, c in sorted(e["by_sfc"].items(), key=lambda kv: -kv[1]):
        L.append(f"  by sfc    {sfc:<24} {c}")
    for r, c in sorted(e["by_reason"].items(), key=lambda kv: -kv[1]):
        L.append(f"  by reason {r:<24} {c}")
    L.append("")
    L.append(f"per-SFC reliability (attributed {s['attributed']}/{n} verdicts):")
    L.append(f"  {'SFC':<22} {'eps':>4} {'healthy':>8} {'marginal':>9} {'rollback':>9} {'escal':>6} {'escal%':>7}")
    for sfc, c in s["per_sfc"].items():
        eps = sum(c.values())
        esc = c.get("escalated", 0)
        L.append(f"  {sfc:<22} {eps:>4} {c.get('healthy',0):>8} {c.get('marginal',0):>9} "
                 f"{c.get('rollback',0):>9} {esc:>6} {_rate(esc, eps):>7}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", required=True)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--write-kg", action="store_true",
                    help="persist a PlannerAnalytics snapshot node to the KG")
    args = ap.parse_args()

    from .kg_context import Neo4jContextClient
    client = Neo4jContextClient(args.neo4j_uri, args.neo4j_user, args.neo4j_password)
    try:
        stats = collect(client.run_cypher)
        if args.write_kg:
            client.run_cypher(
                "MERGE (a:PlannerAnalytics {id:'latest'}) "
                "SET a.payload=$p, a.episodes=$n, a.updated_by='slow-planner-analytics'",
                {"p": json.dumps(stats), "n": stats["episodes"]})
    finally:
        client.close()

    print(json.dumps(stats, indent=2) if args.json else format_report(stats))


if __name__ == "__main__":
    main()
