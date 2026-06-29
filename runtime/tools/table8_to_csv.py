"""Build #1 scorer — KRONOS Table VIII (control-plane timing), measured on our system.

Reads the per-probe ``t8_*_timings.json`` sidecars emitted by
``llm_orchestrator.orchestrate --save-timings`` (run via ``repro/table8_timing.sh``)
and aggregates them into two CSVs under ``docs/experiments/repeat-results/``:

* ``table_viii_timing.csv``  — one row per probe (wide): every stage, cold vs warm SFC.
* ``table_viii_summary.csv`` — the paper Table VIII analog (component, avg_s, std_s, note).

We re-measure ourselves and never import the draft's numbers. The summary separates the
warm steady-state SFC-selection cost (the comparable figure) from the cold first-call
(CUDA warmup) and from the rule-based fallback (the ~us baseline). KG Update is a
runtime-side write stage (not in the planner/orchestrate path) and is reported as a
documented gap rather than a fabricated number.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from statistics import mean, pstdev
from typing import Dict, List, Optional


def _probe_name(path: str) -> str:
    stem = os.path.basename(path)
    if stem.endswith("_timings.json"):
        stem = stem[: -len("_timings.json")]
    if stem.startswith("t8_"):
        stem = stem[len("t8_"):]
    return stem


def _is_rule(name: str, blob: Dict) -> bool:
    # The deterministic fallback probe has no model load and ~us SFC selection.
    return "model_load_s" not in blob and ("rule" in name or "fallback" in name)


def _warm_cold(blob: Dict) -> (Optional[float], Optional[float], Optional[float]):
    """Return (cold_s, warm_mean_s, warm_sd_s) from a timings blob."""
    runs: List[float] = blob.get("sfc_selection_runs_s") or []
    if len(runs) >= 2:
        return runs[0], mean(runs[1:]), pstdev(runs[1:]) if len(runs[1:]) > 1 else 0.0
    cold = blob.get("sfc_selection_s")
    return cold, None, None


def _fmt(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.6f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate Table VIII timing sidecars into CSVs.")
    ap.add_argument("--indir", default="/tmp", help="dir holding t8_*_timings.json")
    ap.add_argument("--outdir", default="docs/experiments/repeat-results")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.indir, "t8_*_timings.json")))
    if not files:
        raise SystemExit(f"no t8_*_timings.json under {args.indir} — run repro/table8_timing.sh first")

    os.makedirs(args.outdir, exist_ok=True)
    per_probe_path = os.path.join(args.outdir, "table_viii_timing.csv")
    summary_path = os.path.join(args.outdir, "table_viii_summary.csv")

    rows: List[Dict] = []
    rule_sfc: Optional[float] = None
    for f in files:
        name = _probe_name(f)
        blob = json.load(open(f))
        if _is_rule(name, blob):
            rule_sfc = blob.get("sfc_selection_s")
            continue
        cold, warm_mean, warm_sd = _warm_cold(blob)
        rows.append({
            "probe": name,
            "model_load_s": blob.get("model_load_s"),
            "kg_reasoning_s": blob.get("kg_reasoning_s"),
            "history_build_s": blob.get("history_build_s"),
            "sfc_cold_s": cold,
            "sfc_warm_mean_s": warm_mean,
            "sfc_warm_sd_s": warm_sd,
            "compile_s": blob.get("compile_s"),
            "result_writeback_s": blob.get("result_writeback_s"),
        })

    cols = ["probe", "model_load_s", "kg_reasoning_s", "history_build_s",
            "sfc_cold_s", "sfc_warm_mean_s", "sfc_warm_sd_s", "compile_s", "result_writeback_s"]
    with open(per_probe_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r["probe"]] + [_fmt(r[c]) for c in cols[1:]])

    # Summary: aggregate each stage across the LLM probes (component, avg_s, std_s, note).
    def agg(key: str) -> (Optional[float], Optional[float]):
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return None, None
        return mean(vals), (pstdev(vals) if len(vals) > 1 else 0.0)

    warm_vals = [r["sfc_warm_mean_s"] for r in rows if r.get("sfc_warm_mean_s") is not None]
    warm_avg = mean(warm_vals) if warm_vals else None
    warm_std = (pstdev(warm_vals) if len(warm_vals) > 1 else 0.0) if warm_vals else None
    summary = [
        ("SFC Selection (LLM, warm steady-state)", warm_avg, warm_std,
            "comparable to draft 'SFC Selection 1.00 s'; warm = excludes cold first-call"),
        ("SFC Selection (LLM, cold first call)", *agg("sfc_cold_s"),
            "first decode after load — includes CUDA warmup + constrained decode"),
        ("SFC Selection (rule-based fallback)", rule_sfc, None,
            "deterministic baseline — the draft's 'rule-based ~us' contrast"),
        ("KG Reasoning", *agg("kg_reasoning_s"),
            "Cypher context reads; cold here, near-warm within a process (draft warm 7.7 ms)"),
        ("Compile + artifact check", *agg("compile_s"), "decision -> experiment config"),
        ("Result Writeback (planner config file)", *agg("result_writeback_s"),
            "planner-side write; draft 'Result Writeback 0.54 s' was a KG write"),
        ("History Build", *agg("history_build_s"), "CSV history + input assembly"),
        ("Model Load (one-time)", *agg("model_load_s"),
            "amortized — excluded from steady-state SFC selection"),
        ("KG Update (runtime-side)", None, None,
            "NOT in planner path; the closed-loop Verdict/snapshot KG write lives in runtime/ (E2/E3) — gap vs draft Table VIII"),
    ]
    with open(summary_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["component", "avg_s", "std_s", "note"])
        for comp, avg_s, std_s, note in summary:
            w.writerow([comp, _fmt(avg_s), _fmt(std_s), note])

    print(f"wrote {per_probe_path} ({len(rows)} probes)")
    print(f"wrote {summary_path}")
    if rule_sfc is not None:
        print(f"rule-based SFC selection: {rule_sfc*1e6:.1f} us")

    _plot(summary, args.outdir)


def _plot(summary, outdir: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available — skipping figure")
        return
    pairs = [(c, a) for (c, a, _s, _n) in summary if a is not None and a > 0]
    if not pairs:
        return
    labels = [c for c, _ in pairs][::-1]
    vals = [a for _, a in pairs][::-1]
    fig, ax = plt.subplots(figsize=(9, 0.6 * len(labels) + 1))
    ax.barh(labels, vals, color="#4C78A8")
    ax.set_xscale("log")
    ax.set_xlabel("seconds (log scale)")
    ax.set_title("KRONOS Table VIII — control-plane timing, re-measured (P100, constrained decode)")
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v*1000:.2f} ms" if v < 1 else f" {v:.2f} s", va="center", fontsize=8)
    fig.tight_layout()
    plots_dir = os.path.join(outdir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out = os.path.join(plots_dir, "table_viii_timing.png")
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
