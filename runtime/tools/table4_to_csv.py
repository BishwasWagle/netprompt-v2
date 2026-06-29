"""Build #2 scorer — KRONOS Table IV (KG-driven decision provenance + KG query latency).

Reads the per-mission ``--output`` configs (decision provenance: selected SFC / path / policy /
relay) and the ``--save-timings`` sidecars (per-query KG latency: sfc_query_ms, path_query_ms)
emitted by ``llm_orchestrator.orchestrate`` via ``repro/table4_provenance.sh``, and writes
``docs/experiments/repeat-results/table_iv_decisions.csv`` + a KG-query-latency figure.

Re-measured on our system; we never import the draft's values. Scoped to the **set-A** known
taxonomy (the planner is correct there). Per the edits-doc guardrail, the SFC/path/policy columns
are honest decision provenance and the query-latency columns are honest Cypher latencies; the
draft's condition rows (congestion/relay/DDIL -> ReliableRelay) are NOT reproduced as LLM
"telemetry reasoning" — only the name-driven set-A decisions are run through the LLM.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from typing import Dict, Optional

# set-A oracle (the known taxonomy the promoted adapter gets right): mission -> SFC
ORACLE_A: Dict[str, str] = {
    "emergency_alert_relay": "ReliableRelaySFC",
    "bulk_data_transfer": "BandwidthOptimizedSFC",
    "real_time_pest_detection": "LowLatencyVideoSFC",
    "long_term_soil_monitoring": "EnergyAwareSFC",
}


def _fmt(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.4f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate Table IV provenance + KG query latency.")
    ap.add_argument("--indir", default="/tmp", help="dir holding t4_<mission>.json + _timings.json")
    ap.add_argument("--outdir", default="docs/experiments/repeat-results")
    args = ap.parse_args()

    outs = sorted(f for f in glob.glob(os.path.join(args.indir, "t4_*.json"))
                  if not f.endswith("_timings.json") and not f.endswith("_input.json"))
    if not outs:
        raise SystemExit(f"no t4_<mission>.json under {args.indir} — run repro/table4_provenance.sh first")

    rows = []
    for f in outs:
        mission = os.path.basename(f)[len("t4_"):-len(".json")]
        prov = json.load(open(f))
        tpath = f[:-len(".json")] + "_timings.json"
        timings = json.load(open(tpath)) if os.path.exists(tpath) else {}
        selected = prov.get("selected_sfc")
        expected = ORACLE_A.get(mission, "")
        parse = (prov.get("decision") or {}).get("llm_parse_status") or prov.get("llm_parse_status") or ""
        rows.append({
            "mission": mission,
            "expected_sfc": expected,
            "selected_sfc": selected or "",
            "correct": "1" if (expected and selected == expected) else "0",
            "selected_path": prov.get("selected_path") or "",
            "selected_policy": prov.get("selected_policy") or "",
            "selected_relay": prov.get("selected_relay") or "",
            "parse_status": parse,
            "sfc_query_ms": timings.get("sfc_query_ms"),
            "path_query_ms": timings.get("path_query_ms"),
        })

    os.makedirs(args.outdir, exist_ok=True)
    cols = ["mission", "expected_sfc", "selected_sfc", "correct", "selected_path",
            "selected_policy", "selected_relay", "parse_status", "sfc_query_ms", "path_query_ms"]
    out_csv = os.path.join(args.outdir, "table_iv_decisions.csv")
    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r["mission"], r["expected_sfc"], r["selected_sfc"], r["correct"],
                        r["selected_path"], r["selected_policy"], r["selected_relay"],
                        r["parse_status"], _fmt(r["sfc_query_ms"]), _fmt(r["path_query_ms"])])

    n_correct = sum(1 for r in rows if r["correct"] == "1")
    print(f"wrote {out_csv} ({len(rows)} missions; set-A correct {n_correct}/{len(rows)})")
    _plot(rows, args.outdir)


def _plot(rows, outdir: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available — skipping figure")
        return
    rows = [r for r in rows if r.get("sfc_query_ms") is not None or r.get("path_query_ms") is not None]
    if not rows:
        return
    missions = [r["mission"] for r in rows]
    sfc = [r.get("sfc_query_ms") or 0.0 for r in rows]
    path = [r.get("path_query_ms") or 0.0 for r in rows]
    x = range(len(missions))
    w = 0.38
    fig, ax = plt.subplots(figsize=(max(7, 1.6 * len(missions)), 4.5))
    ax.bar([i - w / 2 for i in x], sfc, w, label="SFC query (ms)", color="#4C78A8")
    ax.bar([i + w / 2 for i in x], path, w, label="Path query (ms)", color="#F58518")
    ax.set_xticks(list(x))
    ax.set_xticklabels(missions, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("KG Cypher query latency (ms, warm)")
    ax.set_title("KRONOS Table IV — KG query latency by set-A mission, re-measured")
    ax.legend()
    fig.tight_layout()
    plots_dir = os.path.join(outdir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out = os.path.join(plots_dir, "table_iv_kg_latency.png")
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
