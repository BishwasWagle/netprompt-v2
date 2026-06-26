"""Render E1/E3 result figures from the CSVs written by `results_to_csv`.

Mirrors the milestone-II `generate_final_plots.py` pipeline (CSV -> matplotlib
PNGs) for the E1/E3 experiments in `docs/experiments/experiment-results.md`. Reads the
aggregated `e3_summary.csv` (mean +/- population-sd per scenario x arm) and
`e1_confusion.csv`, and writes PNGs under `<resultsdir>/plots/`.

Stdlib csv + matplotlib only (no pandas). Headless (Agg backend).

  python3 -m runtime.tools.plot_results --resultsdir docs/experiments/results
"""
from __future__ import annotations

import argparse
import csv
import os

import matplotlib
matplotlib.use("Agg")                            # headless — no display needed
import matplotlib.pyplot as plt                  # noqa: E402

SCENARIO_ORDER = ["healthy", "primary_fault", "backup_fault", "ddil"]
ARM_ORDER = ["static", "rule", "proposed"]
# proposed is the arm under test -> green; static/rule are the baselines -> muted.
ARM_COLOR = {"static": "#9e9e9e", "rule": "#5b8fb9", "proposed": "#2e8b57"}
SLA_LATENCY_MS = 70                              # CAL_LAT (e3_measure) — the gate


def _read(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _grouped_bars(ax, rows_by_arm, value_key, sd_key=None):
    """Grouped bars: x = scenarios, one bar per arm. Returns nothing (draws on ax)."""
    scenarios = [s for s in SCENARIO_ORDER
                 if any(s in {r["scenario"] for r in rows_by_arm.get(a, [])} for a in ARM_ORDER)]
    arms = [a for a in ARM_ORDER if rows_by_arm.get(a)]
    n = len(arms)
    width = 0.8 / max(n, 1)
    x = list(range(len(scenarios)))
    for i, arm in enumerate(arms):
        by_scen = {r["scenario"]: r for r in rows_by_arm[arm]}
        offsets = [xi + (i - (n - 1) / 2) * width for xi in x]
        vals = [_f(by_scen.get(s, {}).get(value_key)) for s in scenarios]
        errs = [_f(by_scen.get(s, {}).get(sd_key)) for s in scenarios] if sd_key else None
        ax.bar(offsets, vals, width, label=arm, color=ARM_COLOR.get(arm),
               yerr=errs, capsize=3, error_kw={"elinewidth": 1})
    ax.set_xticks(x)
    ax.set_xticklabels(scenarios, rotation=20, ha="right")
    ax.legend(title="arm")
    return scenarios


def plot_e3(resultsdir: str, plots: str) -> list[str]:
    rows = _read(os.path.join(resultsdir, "e3_summary.csv"))
    by_arm: dict[str, list[dict]] = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)
    saved = []

    # Fig 1 — F1 RTT by scenario x arm, with the SLA bound. THE thesis figure:
    # only `proposed` stays under the 70 ms gate across both fault locations.
    fig, ax = plt.subplots(figsize=(9, 5))
    _grouped_bars(ax, by_arm, "f1_rtt_ms_mean", "f1_rtt_ms_sd")
    ax.axhline(SLA_LATENCY_MS, ls="--", color="#c0392b", lw=1.3,
               label=f"SLA bound ({SLA_LATENCY_MS} ms)")
    ax.set_ylabel("Target (F1) RTT (ms)")
    ax.set_title("E3 — F1 RTT by scenario and arm (mean ± sd, 3 repeats)")
    ax.legend(title="arm")
    p = os.path.join(plots, "e3_rtt_by_scenario_arm.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); saved.append(p)

    # Fig 2 — SLA-met repeats by scenario x arm (0..N).
    fig, ax = plt.subplots(figsize=(9, 5))
    n_rep = max((int(_f(r["n_repeats"], 0)) for r in rows), default=3)
    by_arm_met = {a: [{"scenario": r["scenario"],
                       "v": str(int(_f(r["sla_met_count"], 0)))} for r in rs]
                  for a, rs in by_arm.items()}
    _grouped_bars(ax, by_arm_met, "v")
    ax.set_ylim(0, n_rep + 0.3)
    ax.set_yticks(range(n_rep + 1))
    ax.set_ylabel(f"SLA-met repeats (of {n_rep})")
    ax.set_title("E3 — SLA satisfaction by scenario and arm")
    p = os.path.join(plots, "e3_sla_met_by_scenario_arm.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); saved.append(p)

    # Fig 3 — orchestration overhead (wall seconds) by scenario x arm.
    fig, ax = plt.subplots(figsize=(9, 5))
    _grouped_bars(ax, by_arm, "wall_seconds_mean")
    ax.set_ylabel("Wall time (s)")
    ax.set_title("E3 — orchestration overhead by scenario and arm (mean, 3 repeats)")
    p = os.path.join(plots, "e3_overhead_by_scenario_arm.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); saved.append(p)
    return saved


def plot_e1(resultsdir: str, plots: str) -> list[str]:
    path = os.path.join(resultsdir, "e1_confusion.csv")
    if not os.path.exists(path):
        return []
    rows = _read(path)
    # accuracy per probe set (A/B/C) — matches the doc's "set A 4/4, B+C 0/4".
    sets: dict[str, list[bool]] = {}
    for r in rows:
        sets.setdefault(r["set"], []).append(r["correct"].lower() == "true")
    labels = sorted(sets)
    correct = [sum(sets[s]) for s in labels]
    totals = [len(sets[s]) for s in labels]
    acc = [100 * c / t if t else 0 for c, t in zip(correct, totals)]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(labels, acc, color=["#2e8b57", "#c0392b", "#c0392b"][:len(labels)])
    for b, c, t in zip(bars, correct, totals):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5, f"{c}/{t}",
                ha="center", va="bottom", fontsize=10)
    ax.set_ylim(0, 109)
    ax.set_ylabel("Planner accuracy (%)")
    ax.set_xlabel("Probe set")
    ax.set_title("E1 — planner decision accuracy by probe set (known taxonomy vs held-out)")
    p = os.path.join(plots, "e1_accuracy_by_set.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); plt.close("all")
    return [p]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resultsdir", default="docs/experiments/results")
    args = ap.parse_args()
    plots = os.path.join(args.resultsdir, "plots")
    os.makedirs(plots, exist_ok=True)

    saved = []
    if os.path.exists(os.path.join(args.resultsdir, "e3_summary.csv")):
        saved += plot_e3(args.resultsdir, plots)
    else:
        print("  note: no e3_summary.csv — run results_to_csv e3 first")
    saved += plot_e1(args.resultsdir, plots)

    for p in saved:
        print(f"  wrote {p}")
    print(f"{len(saved)} figure(s) -> {plots}")


if __name__ == "__main__":
    main()
