"""Build #6 — KRONOS Fig. 6: decision-accuracy confusion matrix over the 4 SFC classes.

Runs a held-out probe set (set A known names + set B/C novel/generic variants, true class labelled;
see repro/fig6_probes.json) through the planner, scores predicted vs true, and builds the 4x4
row-normalized confusion matrix the draft's Fig. 6 presents.

Reframe (edits-doc §4 / §0 RESOLVED): the defensible reading is **known-taxonomy (set A) accuracy**,
NOT telemetry generalization. We report the set-A sub-matrix (the Fig-6 analog) separately from the
full matrix, which also shows the off-taxonomy behavior. We re-measure ourselves; the draft's exact
88.9-100% are not imported.

Run from NETPROMPT_ROOT via repro/fig6_confusion.sh. Writes
docs/experiments/repeat-results/fig6_{probes,confusion,summary}.csv + figure.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

_NP = os.environ.get("NETPROMPT_TREE_ROOT") or os.environ.get("NETPROMPT_ROOT")
if _NP and _NP not in sys.path:
    sys.path.insert(0, _NP)

from llm_orchestrator.config import RuntimeConfig  # noqa: E402
from llm_orchestrator.llm_runner import LLMOrchestrator, parse_model_decision  # noqa: E402
from llm_orchestrator.orchestrate import build_runtime_input_object, infer_priority  # noqa: E402

CLASSES = ["LowLatencyVideoSFC", "ReliableRelaySFC", "EnergyAwareSFC", "BandwidthOptimizedSFC"]
SHORT = {"LowLatencyVideoSFC": "LowLat", "ReliableRelaySFC": "Reliable",
         "EnergyAwareSFC": "Energy", "BandwidthOptimizedSFC": "Bandwidth"}


def _confusion(rows, subset=None):
    """counts[true][pred] over rows (optionally filtered to a set-tag subset)."""
    counts = {t: {p: 0 for p in CLASSES} for t in CLASSES}
    for r in rows:
        if subset and r["set"] not in subset:
            continue
        if r["true_sfc"] in counts and r["predicted_sfc"] in counts[r["true_sfc"]]:
            counts[r["true_sfc"]][r["predicted_sfc"]] += 1
    return counts


def _row_norm(counts):
    mat = {}
    for t in CLASSES:
        tot = sum(counts[t].values())
        mat[t] = {p: (100.0 * counts[t][p] / tot if tot else 0.0) for p in CLASSES}
    return mat


def main() -> None:
    ap = argparse.ArgumentParser(description="Fig. 6 confusion matrix over the 4 SFC classes.")
    ap.add_argument("--probes", default="repro/fig6_probes.json")
    ap.add_argument("--outdir", default="docs/experiments/repeat-results")
    args = ap.parse_args()

    blob = json.load(open(args.probes))
    probes = blob["probes"] if isinstance(blob, dict) else blob

    cfg = RuntimeConfig.from_env()
    orch = LLMOrchestrator(
        model_name=cfg.model_name, adapter_path=cfg.adapter_path,
        use_4bit=False, device_map=cfg.device_map or "cuda:0", max_new_tokens=cfg.max_new_tokens,
    ).load()

    rows = []
    for pr in probes:
        mission = pr["mission"]
        io = build_runtime_input_object(
            cfg, mission, infer_priority(mission, None),
            pr["bandwidth"], pr["delay"], pr["loss"], pr["battery"], None, None, None)
        d = parse_model_decision(orch.generate_raw(io))
        pred = d.get("selected_sfc")
        rows.append({"mission": mission, "set": pr["set"], "true_sfc": pr["true_sfc"],
                     "predicted_sfc": pred, "correct": "1" if pred == pr["true_sfc"] else "0"})
        print(f"[fig6] set{pr['set']} {mission:32s} true={SHORT.get(pr['true_sfc'],'?'):9s} pred={SHORT.get(pred,'?'):9s} {'OK' if pred==pr['true_sfc'] else 'X'}")

    os.makedirs(args.outdir, exist_ok=True)
    # per-probe
    with open(os.path.join(args.outdir, "fig6_probes.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["mission", "set", "true_sfc", "predicted_sfc", "correct"])
        w.writeheader(); w.writerows(rows)

    full = _row_norm(_confusion(rows))
    # confusion matrix (row-normalized %, full set)
    with open(os.path.join(args.outdir, "fig6_confusion.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["true\\predicted"] + CLASSES)
        for t in CLASSES:
            w.writerow([t] + [f"{full[t][p]:.1f}" for p in CLASSES])

    # summary: per-class accuracy (full + set-A only) and per-set accuracy
    setA = [r for r in rows if r["set"] == "A"]
    setBC = [r for r in rows if r["set"] in ("B", "C")]

    def acc(sub):
        return (sum(1 for r in sub if r["correct"] == "1"), len(sub))

    with open(os.path.join(args.outdir, "fig6_summary.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scope", "metric", "correct", "n", "accuracy_pct"])
        for label, sub in [("all", rows), ("set_A_known_taxonomy", setA), ("set_BC_off_taxonomy", setBC)]:
            c, n = acc(sub)
            w.writerow([label, "overall", c, n, f"{100.0*c/n:.1f}" if n else ""])
        for t in CLASSES:
            sub = [r for r in rows if r["true_sfc"] == t]
            subA = [r for r in setA if r["true_sfc"] == t]
            c, n = acc(sub); cA, nA = acc(subA)
            w.writerow([f"class:{t}", "all", c, n, f"{100.0*c/n:.1f}" if n else ""])
            w.writerow([f"class:{t}", "setA", cA, nA, f"{100.0*cA/nA:.1f}" if nA else ""])

    cA, nA = acc(setA); cAll, nAll = acc(rows)
    print(f"set-A (known taxonomy): {cA}/{nA} correct; full set: {cAll}/{nAll}")
    _plot(full, _row_norm(_confusion(rows, subset={"A"})), args.outdir)


def _plot(full, setA, outdir: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available — skipping figure")
        return
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    for ax, mat, title in [(axes[0], setA, "set A (known taxonomy) — the Fig-6 analog"),
                           (axes[1], full, "full held-out set (incl. off-taxonomy B/C)")]:
        grid = [[mat[t][p] for p in CLASSES] for t in CLASSES]
        im = ax.imshow(grid, cmap="Blues", vmin=0, vmax=100)
        ax.set_xticks(range(4)); ax.set_xticklabels([SHORT[c] for c in CLASSES], rotation=30, ha="right")
        ax.set_yticks(range(4)); ax.set_yticklabels([SHORT[c] for c in CLASSES])
        ax.set_xlabel("predicted SFC"); ax.set_ylabel("true SFC"); ax.set_title(title, fontsize=10)
        for i in range(4):
            for j in range(4):
                v = grid[i][j]
                ax.text(j, i, f"{v:.0f}", ha="center", va="center",
                        color="white" if v > 55 else "black", fontsize=9)
    fig.suptitle("KRONOS Fig. 6 — SFC decision confusion matrix, re-measured (row-normalized %)")
    fig.colorbar(im, ax=axes, fraction=0.025, pad=0.02, label="prediction rate (%)")
    plots_dir = os.path.join(outdir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out = os.path.join(plots_dir, "fig6_confusion.png")
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
