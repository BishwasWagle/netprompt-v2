"""Seed the KG with the STRATEGIC nodes the runtime reads — non-destructively.

`build_envelope` needs `SFCTemplate` + `AgriculturalField` nodes (bounds), and the
orchestrator's `kg_context` reads the wider strategic graph (drones, infra). The
legacy `controller/import_kg.py` populates these but opens with
`MATCH (n) DETACH DELETE n` — which would also wipe the runtime's own records
(`Verdict`, `EscalationTicket`, `BaselineSnapshot`, `ConfigSnapshot`/`LastKnownGood`,
`ProgrammableSwitch.status`). This tool seeds the SAME strategic graph idempotently
via MERGE, leaving runtime records intact (D12b), so it's safe to re-run on a live KG.

Source graph: `controller/drone_sfc_kg.json` (produced by `controller/generate_kg.py`).
KG endpoint + creds come from `runtime.config` (NETPROMPT_KG_*), not hardcoded.

    python3 -m runtime.tools.seed_kg                 # idempotent MERGE (default)
    python3 -m runtime.tools.seed_kg --reset-strategic   # drop ONLY strategic labels first

`--reset-strategic` deletes nodes carrying a strategic label (below) before re-merging
— a clean reseed that still never touches runtime records (they carry none of these
labels). It does NOT touch `ProgrammableSwitch` (the runtime writes switch status there).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime import config
from runtime.kg_client import KGClient

# Labels authored by generate_kg.py — the strategic graph. The runtime writes none of
# these, so deleting them (for --reset-strategic) can't clobber a Verdict/snapshot.
STRATEGIC_LABELS = ("SFCTemplate", "AgriculturalField", "Drone",
                    "CentralController", "ProgrammableNetworkNode", "EdgeComputeNode")

# repo-root/controller/drone_sfc_kg.json
DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "controller" / "drone_sfc_kg.json"


def seed(kg: KGClient, graph: dict, reset_strategic: bool = False) -> dict:
    """MERGE the strategic nodes + relationships. Returns a small summary dict."""
    nodes = graph["knowledge_graph"]["nodes"]
    rels = graph["knowledge_graph"]["relationships"]
    with kg.driver.session() as s:
        if reset_strategic:
            for label in STRATEGIC_LABELS:
                s.run(f"MATCH (n:`{label}`) DETACH DELETE n")
        for node in nodes:
            props = {k: v for k, v in node.items() if k != "type"}
            s.run(f"MERGE (n:`{node['type']}` {{id:$id}}) SET n += $props",
                  id=node["id"], props=props)
        for rel in rels:
            s.run(f"MATCH (a {{id:$src}}) MATCH (b {{id:$dst}}) "
                  f"MERGE (a)-[:`{rel['relation']}`]->(b)",
                  src=rel["source"], dst=rel["target"])
        counts = {lbl: s.run(f"MATCH (n:`{lbl}`) RETURN count(n) AS c").single()["c"]
                  for lbl in STRATEGIC_LABELS}
    return {"nodes": len(nodes), "relationships": len(rels), "counts": counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=str(DEFAULT_SOURCE),
                    help="strategic graph JSON (default: controller/drone_sfc_kg.json)")
    ap.add_argument("--reset-strategic", action="store_true",
                    help="delete strategic-label nodes first (never touches runtime records)")
    args = ap.parse_args()

    graph = json.loads(Path(args.source).read_text())
    kg = KGClient.connect()
    try:
        summary = seed(kg, graph, reset_strategic=args.reset_strategic)
    finally:
        kg.close()

    print(f"KG endpoint   : {config.KG_URI}")
    print(f"source        : {args.source}")
    print(f"mode          : {'reset-strategic + merge' if args.reset_strategic else 'merge (idempotent)'}")
    print(f"merged        : {summary['nodes']} nodes, {summary['relationships']} relationships")
    print(f"strategic now : {summary['counts']}")


if __name__ == "__main__":
    main()
