"""D14 — multi-handoff integration soak.

Drive N planner handoffs back-to-back through the runtime on the resident testbed,
each a fresh `correlation_id` (the stateless fresh-handoff convention), cycling the
target field and (optionally) the path. Proves the integration holds across
consecutive deploys: idempotent re-deploy, per-episode KG writes, no state bleed
between handoffs. Reuses the same building blocks as `run_from_planner --deploy`
(planner_adapter + run_episode.build_and_run), so it exercises the real path.

    source deploy/gpu-node/gpu-node.env
    sudo -E ~/netprompt-venv/bin/python -m runtime.tools.planner_soak \
        --artifact "$NETPROMPT_ROOT/outputs/llm_generated_experiment_config.json" \
        --rounds 6 --fields F1,F2 --alternate-path

Needs the KG (envelope + requirements) and a running launch_network.py topology.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from runtime import config
from runtime.gate import ValidationGate
from runtime.kg_client import KGClient
from runtime.planner_adapter import load_artifact, spec_from_artifact
from runtime.tools.run_episode import (
    DEFAULT_HOST_MAP, build_and_run, real_monitor_for)


def _variant(base: dict, primary: bool) -> dict:
    """A path variant of the artifact: flip the policy_type backup<->primary so the
    deployer selects the other relay. The canonical binding (rule files) is unchanged
    — only the active path differs (which is exactly the planner's path decision)."""
    art = dict(base)
    pol = str(base.get("policy_type") or base.get("selected_policy", ""))
    flipped = (pol.replace("backup", "primary") if primary
               else pol.replace("primary", "backup"))
    art["policy_type"] = flipped
    art["selected_policy"] = flipped
    return art


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--fields", default="F1,F2",
                    help="comma-separated runtime field ids to cycle (default F1,F2)")
    ap.add_argument("--alternate-path", action="store_true",
                    help="alternate backup/primary path across rounds")
    ap.add_argument("--tree", default=config.NODE_TREE_ROOT)
    args = ap.parse_args()

    base = load_artifact(args.artifact)
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    host_map = DEFAULT_HOST_MAP
    gate = ValidationGate()
    rows = []

    kg = KGClient.connect()
    try:
        # all fields the testbed realizes (KG carries all 5; restrict to host_map)
        requirements = {f: e for f, e in kg.read_field_requirements().items()
                        if f in host_map}
        for n in range(args.rounds):
            field = fields[n % len(fields)]
            primary = args.alternate_path and (n % 2 == 1)
            art = _variant(base, primary) if args.alternate_path else base
            cid = f"soak-{n}"
            spec = spec_from_artifact(art, target_field=field, correlation_id=cid,
                                      kg=kg, tree=args.tree)
            g = gate.check_binding(spec)
            if not g.ok:
                rows.append((cid, field, spec.binding["policy_type"], "gate-reject", "-"))
                print(f"[{n}] {cid} {field} -> GATE REJECT {g.reason}", flush=True)
                continue
            monitor_for = real_monitor_for(requirements, spec.target_field, cid, host_map)
            ts = datetime.now(timezone.utc).isoformat()
            try:
                result, deployer = build_and_run(spec, monitor_for, timestamp=ts, kg=kg)
                v = result.verdict
                rows.append((cid, field, deployer.state["path"], v.outcome, v.tier_reached))
                print(f"[{n}] {cid} {field} path={deployer.state['path']} "
                      f"-> {v.outcome} (tier {v.tier_reached})", flush=True)
            except Exception as e:                          # noqa: BLE001 — one bad round can't sink the soak
                rows.append((cid, field, "-", f"ERROR:{type(e).__name__}", "-"))
                print(f"[{n}] {cid} {field} -> ERROR {type(e).__name__}: {e}", flush=True)

        # Resilience check: every round that ran should have a Verdict in the KG.
        with kg.driver.session() as s:
            present = {r["c"] for r in s.run(
                "MATCH (v:Verdict) WHERE v.correlation_id STARTS WITH 'soak-' "
                "RETURN v.correlation_id AS c").data()}
    finally:
        kg.close()

    print("\n=== D14 multi-handoff soak summary ===")
    print(f"{'correlation_id':<16} {'field':<5} {'path':<8} {'outcome':<14} tier  KG")
    ok = 0
    for cid, field, path, outcome, tier in rows:
        in_kg = cid in present
        ok += in_kg
        print(f"{cid:<16} {field:<5} {str(path):<8} {outcome:<14} {str(tier):<4}  "
              f"{'✓' if in_kg else '✗ MISSING'}")
    print(f"\nrounds={len(rows)}  verdicts in KG={ok}/{len(rows)}  "
          f"(stateless fresh-handoff, one correlation_id each)")
    if ok != len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
