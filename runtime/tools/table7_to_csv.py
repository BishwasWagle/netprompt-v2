"""Build #5 scorer — KRONOS Table VII: KG ablation (full vs NoKG), re-measured.

Reads the focused E3 ablation JSONL (from `e3_compare --arms rule,proposed,nokg`) and writes
docs/experiments/repeat-results/table_vii_ablation.csv + a figure.

Operational NoKG definition (single factor): the proposed pipeline (LLM SFC + adaptive runtime)
but with the KG-derived REROUTE capability removed — `build_envelope` grants ReliableRelay the
reroute tier *because the KG knows the alternative relay path*; NoKG strips it, so a path fault
cannot be escaped. We also report the `rule` arm (the draft's NoKG ~= static scenario mappings,
no adaptation). Loss is read at ping_count=2 (coarse, ~{0,50,100}%) — reported qualitatively per
the guardrail, with RTT as the honest dimension.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from statistics import mean

ARMS_ORDER = ["rule", "nokg", "proposed"]
SCEN_ORDER = ["healthy", "backup_fault", "ddil"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate the Table VII NoKG ablation E3 JSONL.")
    ap.add_argument("--in", dest="inp", default="/tmp/e3_nokg.jsonl")
    ap.add_argument("--outdir", default="docs/experiments/repeat-results")
    args = ap.parse_args()

    cells = defaultdict(list)  # (scenario, arm) -> [rec, ...]
    errors = []
    for line in open(args.inp):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if "error" in rec:
            errors.append(rec); continue
        cells[(rec["scenario"], rec["arm"])].append(rec)

    rows = []
    for scen in SCEN_ORDER:
        for arm in ARMS_ORDER:
            recs = cells.get((scen, arm))
            if not recs:
                continue
            f1 = [r["flows"][0] for r in recs]
            sla = sum(1 for r in recs if r.get("target_sla_met"))
            rows.append({
                "scenario": scen, "arm": arm, "sfc": recs[0]["sfc"],
                "outcome": recs[0]["outcome"], "tier_reached": recs[0].get("tier_reached"),
                "final_path": recs[0].get("final_path"),
                "f1_rtt_ms": round(mean(x["rtt_ms"] for x in f1), 2),
                "f1_loss_pct": round(mean(x["loss_pct"] for x in f1), 2),
                "sla_met": f"{sla}/{len(recs)}",
            })

    os.makedirs(args.outdir, exist_ok=True)
    cols = ["scenario", "arm", "sfc", "outcome", "tier_reached", "final_path",
            "f1_rtt_ms", "f1_loss_pct", "sla_met"]
    out_csv = os.path.join(args.outdir, "table_vii_ablation.csv")
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(rows)
    print(f"wrote {out_csv} ({len(rows)} cells; {len(errors)} errors)")
    for r in rows:
        print(f"  {r['scenario']:14s} {r['arm']:9s} {r['sfc']:18s} {r['outcome']:10s} "
              f"tier={r['tier_reached']} path={r['final_path']} rtt={r['f1_rtt_ms']}ms sla={r['sla_met']}")
    _plot(rows, args.outdir)


def _plot(rows, outdir: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available — skipping figure")
        return
    scen = [s for s in SCEN_ORDER if any(r["scenario"] == s for r in rows)]
    arms = [a for a in ARMS_ORDER if any(r["arm"] == a for r in rows)]
    if not scen or not arms:
        return
    colors = {"rule": "#E45756", "nokg": "#F58518", "proposed": "#4C78A8"}
    labels = {"rule": "rule (draft NoKG ~= static)", "nokg": "nokg (no KG reroute)", "proposed": "proposed (full KRONOS)"}
    w = 0.8 / len(arms)
    fig, ax = plt.subplots(figsize=(9, 5))
    for j, a in enumerate(arms):
        ys = []
        for s in scen:
            r = next((r for r in rows if r["scenario"] == s and r["arm"] == a), None)
            ys.append(r["f1_rtt_ms"] if r else 0)
        xs = [i + (j - (len(arms) - 1) / 2) * w for i in range(len(scen))]
        ax.bar(xs, ys, w, label=labels.get(a, a), color=colors.get(a, "#888"))
        for x, y in zip(xs, ys):
            ax.text(x, y + 1, f"{y:.0f}", ha="center", fontsize=8)
    ax.axhline(70, ls="--", color="black", lw=1, label="SLA bound (70 ms)")
    ax.set_xticks(range(len(scen))); ax.set_xticklabels(scen)
    ax.set_ylabel("F1 target RTT (ms)")
    ax.set_title("KRONOS Table VII — KG ablation: only full KRONOS reroutes around a path fault")
    ax.legend(fontsize=8)
    fig.tight_layout()
    plots_dir = os.path.join(outdir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out = os.path.join(plots_dir, "table_vii_ablation.png")
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
