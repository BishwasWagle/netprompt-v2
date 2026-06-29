"""Render E1–E4 result figures from the CSVs written by `results_to_csv`.

Mirrors the milestone-II `generate_final_plots.py` pipeline (CSV -> matplotlib PNGs)
for the experiments in `docs/experiments/results/experiment-results.md`. Each plotter is a
no-op if its CSV is absent, so this runs after any subset of experiments:
  E1  e1_confusion.csv      -> accuracy by probe set
  E2a e2a_gate.csv          -> tier/verdict per scenario (6/6 gate)
  E2b e2b_integration.csv   -> per-test wall time (M5/M6 pass)
  E3  e3_summary.csv         -> RTT / SLA-met / overhead by scenario x arm
  E4  e4_summary.csv         -> recover correctness by arm + reject safety

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


def plot_e2a(resultsdir: str, plots: str) -> list[str]:
    path = os.path.join(resultsdir, "e2a_gate.csv")
    if not os.path.exists(path):
        return []
    rows = list(reversed(_read(path)))               # first scenario on top
    tiers = [int(_f(r["tier_reached"])) for r in rows]
    passed = [str(r["pass"]).lower() == "true" for r in rows]
    fig, ax = plt.subplots(figsize=(9, 5))
    y = list(range(len(rows)))
    ax.barh(y, tiers, height=0.6,
            color=["#2e8b57" if p else "#c0392b" for p in passed])
    for yi, r, t in zip(y, rows, tiers):             # outcome label (works at tier 0)
        ax.text(t + 0.05, yi, r["outcome"], va="center", ha="left", fontsize=9)
    ax.set_yticks(y); ax.set_yticklabels([r["scenario"] for r in rows])
    ax.set_xlim(0, max(tiers) + 1.1); ax.set_xticks(range(max(tiers) + 1))
    ax.set_xlabel("tier reached  (0 = commit · 1 = reroute · 2 = escalate)")
    ax.set_title(f"E2a — off-node gate: {sum(passed)}/{len(rows)} scenarios "
                 f"reach their designed verdict")
    p = os.path.join(plots, "e2a_gate.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig)
    return [p]


def plot_e2b(resultsdir: str, plots: str) -> list[str]:
    path = os.path.join(resultsdir, "e2b_integration.csv")
    if not os.path.exists(path):
        return []
    rows = list(reversed(_read(path)))
    times = [_f(r["time_s"]) for r in rows]
    passed = [r["outcome"] == "passed" for r in rows]
    fig, ax = plt.subplots(figsize=(10, 5))
    y = list(range(len(rows)))
    ax.barh(y, times, height=0.6,
            color=["#2e8b57" if p else "#c0392b" for p in passed])
    for yi, t in zip(y, times):
        ax.text(t + max(times) * 0.01, yi, f"{t:.0f}s", va="center", ha="left", fontsize=9)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{r['suite'].replace('test_', '')}::{r['test'].replace('test_', '')}"
                        for r in rows], fontsize=8)
    ax.set_xlabel("wall time (s)")
    ax.set_title(f"E2b — M5/M6 node-gated suites: {sum(passed)}/{len(rows)} passed "
                 f"(Σ {sum(times):.0f}s)")
    p = os.path.join(plots, "e2b_integration.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig)
    return [p]


def plot_e4(resultsdir: str, plots: str) -> list[str]:
    path = os.path.join(resultsdir, "e4_summary.csv")
    if not os.path.exists(path):
        return []
    rows = _read(path)
    saved = []

    # Fig 1 — recover correctness by arm: the model-capability frontier. stub proves
    # the machinery (≈100% everywhere); real shows the 1.5B Coder is safe-but-not-recovery.
    rec = {r["arm"]: r for r in rows if r["group"] == "recover"}
    arms = [a for a in ("stub", "real") if a in rec]
    metrics = [("grammar_valid_rate", "grammar-valid"),
               ("gate_pass_rate", "gate-safe"), ("recovery_rate", "recovers")]
    arm_color = {"stub": "#2e8b57", "real": "#5b8fb9"}
    fig, ax = plt.subplots(figsize=(8, 5))
    n = len(arms); width = 0.8 / max(n, 1); x = list(range(len(metrics)))
    for i, arm in enumerate(arms):
        vals = [_f(rec[arm][k]) * 100 for k, _ in metrics]
        off = [xi + (i - (n - 1) / 2) * width for xi in x]
        for b, v in zip(ax.bar(off, vals, width, label=arm, color=arm_color.get(arm)), vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f}",
                    ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels([lbl for _, lbl in metrics])
    ax.set_ylim(0, 109); ax.set_ylabel("rate (%)"); ax.legend(title="arm")
    ax.set_title("E4 — recover correctness by arm "
                 "(stub = machinery · real = 1.5B Coder frontier)")
    p = os.path.join(plots, "e4_recover_by_arm.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); saved.append(p)

    # Fig 2 — reject safety: every deliberately-broken script is refused, by fault class.
    rej = [r for r in rows if r["group"].startswith("reject:")]
    guard = {"syntactic": "grammar", "runtime": "gate L2"}
    fig, ax = plt.subplots(figsize=(7, 5))
    labels = [r["group"].split(":")[1] for r in rej]
    rates = [_f(r["caught_rate"]) * 100 for r in rej]
    bars = ax.bar(range(len(labels)), rates, color="#2e8b57")
    for b, r in zip(bars, rej):
        n_ = int(_f(r["n"])); c = round(_f(r["caught_rate"]) * n_)
        ax.text(b.get_x() + b.get_width() / 2, _f(r["caught_rate"]) * 100 + 1.5,
                f"{c}/{n_}", ha="center", va="bottom", fontsize=10)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels([f"{l}\n(by {guard.get(l, '?')})" for l in labels])
    ax.set_ylim(0, 109); ax.set_ylabel("scripts refused (%)")
    ax.set_title("E4 — deliberately-broken scripts refused (safety)")
    p = os.path.join(plots, "e4_reject_safety.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); saved.append(p)
    return saved


FAMILY_COLOR = {"Qwen2": "#2e8b57", "Llama": "#5b8fb9", "Phi3": "#b9775b",
                "Granite": "#9b59b6", "StableLm": "#c0392b", "Starcoder2": "#7f8c8d"}


def plot_xfam(resultsdir: str, plots: str) -> list[str]:
    """M7 cross-family comparison (m7_xfam.csv) -> two figures: per-model
    grammar/gate/recovery rates, and per-model latency coloured by family."""
    path = os.path.join(resultsdir, "m7_xfam.csv")
    if not os.path.exists(path):
        return []
    rows = _read(path)
    ran = [r for r in rows if r.get("category") == "runs"]
    if not ran:
        return []
    saved = []
    label = lambda r: f"{r['model']}\n({r['family']})"            # noqa: E731

    # Fig 1 — grammar-valid / gate-accept, for models that produced any grammar-valid
    # output. recovery is intentionally NOT plotted: it is 0% for every model (no family
    # or size emits the corrective row), so a third all-zero series adds no insight — it
    # is stated in the text instead. The fully-unsuccessful models (grammar-valid 0% —
    # TinyLlama, SmolLM2, granite: truncation / wrong-action) are also omitted here (all
    # bars would be empty) and reported in the text.
    scored = [r for r in ran if _f(r["grammar_valid_rate"]) > 0]
    metrics = [("grammar_valid_rate", "grammar-valid", "#2e8b57"),
               ("gate_pass_rate", "gate-accept", "#5b8fb9")]
    fig, ax = plt.subplots(figsize=(9, 5))
    n = len(metrics); width = 0.8 / n; x = list(range(len(scored)))
    for i, (k, lbl, col) in enumerate(metrics):
        vals = [_f(r[k]) * 100 for r in scored]
        off = [xi + (i - (n - 1) / 2) * width for xi in x]
        ax.bar(off, vals, width, label=lbl, color=col)
    ax.set_xticks(x); ax.set_xticklabels([label(r) for r in scored], rotation=30, ha="right", fontsize=8)
    ax.set_ylim(0, 109); ax.set_ylabel("rate (%)"); ax.legend(title="metric", ncol=2)
    ax.set_title("M7 cross-family Tier-2 regen — grammar-valid / gate-accept\n"
                 "(models with grammar-valid output; recovery 0% for all, omitted)")
    p = os.path.join(plots, "m7_xfam_rates.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); saved.append(p)

    # Fig 2 — mean GBNF-constrained generation latency per model, coloured by family.
    import matplotlib.patches as mpatches
    fig, ax = plt.subplots(figsize=(10, 5))
    vals = [_f(r["mean_latency_s"]) for r in ran]
    colors = [FAMILY_COLOR.get(r["family"], "#333") for r in ran]
    for b, v in zip(ax.bar(range(len(ran)), vals, color=colors), vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:.1f}",
                ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(ran))); ax.set_xticklabels([label(r) for r in ran],
                                                       rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("mean latency (s)")
    ax.set_title("M7 cross-family Tier-2 regen — mean GBNF-constrained generation latency")
    fams = sorted({r["family"] for r in ran})
    ax.legend(handles=[mpatches.Patch(color=FAMILY_COLOR.get(f, "#333"), label=f) for f in fams],
              title="family")
    p = os.path.join(plots, "m7_xfam_latency.png")
    fig.tight_layout(); fig.savefig(p, dpi=300); plt.close(fig); saved.append(p)
    return saved


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
    saved += plot_e2a(args.resultsdir, plots)
    saved += plot_e2b(args.resultsdir, plots)
    saved += plot_e4(args.resultsdir, plots)
    saved += plot_xfam(args.resultsdir, plots)

    for p in saved:
        print(f"  wrote {p}")
    print(f"{len(saved)} figure(s) -> {plots}")


if __name__ == "__main__":
    main()
