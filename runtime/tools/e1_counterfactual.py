"""Build #4 — KRONOS §V.D(3): counterfactual sensitivity (planner vs oracle).

Vary ONE signal at a time, hold the rest fixed, and record whether the planner's SFC changes —
contrasted against the deterministic oracle (`validator.fallback_decision`) on the same input.

Per the edits-doc reframe (§6.2 — LET GO as generalization): we do NOT claim the planner
generalizes. We report two defensible facets:
  * mission-name counterfactual (set A): the planner SHOULD track the name (it does);
  * telemetry counterfactuals (delay / loss / battery), with the name fixed to
    `bulk_data_transfer` (which has no name-trigger in the oracle, so telemetry alone drives the
    oracle): the planner is expected to stay FLAT while the oracle responds — quantifying the
    0/4-telemetry limitation as a counterfactual result, scored as per-episode oracle-agreement
    (never a blended generalization accuracy).

Run from NETPROMPT_ROOT via repro/vd3_counterfactual.sh. Writes
docs/experiments/repeat-results/vd3_counterfactual.csv (+ summary + figure).
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
from llm_orchestrator.validator import fallback_decision, validate_generated_decision  # noqa: E402

# Each sweep varies one field; the rest come from `fixed`. Telemetry sweeps fix the mission to
# bulk_data_transfer (no oracle name-trigger) so telemetry alone moves the oracle.
NEUTRAL = dict(bw=40, delay=20, loss=1, batt=80)
SWEEPS = [
    dict(name="mission_name", field="mission",
         points=["emergency_alert_relay", "bulk_data_transfer",
                 "real_time_pest_detection", "long_term_soil_monitoring"],
         fixed=dict(**NEUTRAL)),
    dict(name="delay_ms", field="delay", points=[3, 6, 20, 50, 100],
         fixed=dict(mission="bulk_data_transfer", bw=40, loss=1, batt=80)),
    dict(name="loss_percent", field="loss", points=[0, 1, 3, 5, 10],
         fixed=dict(mission="bulk_data_transfer", bw=40, delay=20, batt=80)),
    dict(name="battery_percent", field="batt", points=[90, 50, 35, 20, 10],
         fixed=dict(mission="bulk_data_transfer", bw=40, delay=20, loss=1)),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="§V.D(3) counterfactual sensitivity (planner vs oracle).")
    ap.add_argument("--outdir", default="docs/experiments/repeat-results")
    args = ap.parse_args()

    cfg = RuntimeConfig.from_env()
    orch = LLMOrchestrator(
        model_name=cfg.model_name, adapter_path=cfg.adapter_path,
        use_4bit=False, device_map=cfg.device_map or "cuda:0", max_new_tokens=cfg.max_new_tokens,
    ).load()

    rows = []
    for sweep in SWEEPS:
        for pt in sweep["points"]:
            p = dict(sweep["fixed"])
            p[sweep["field"]] = pt
            mission = p["mission"]
            io = build_runtime_input_object(
                cfg, mission, infer_priority(mission, None),
                p["bw"], p["delay"], p["loss"], p["batt"], None, None, None)
            d = parse_model_decision(orch.generate_raw(io))
            v = validate_generated_decision(io, d)
            oracle = fallback_decision(io)
            psfc, osfc = d.get("selected_sfc"), oracle.get("selected_sfc")
            rows.append({
                "sweep": sweep["name"], "varied_field": sweep["field"], "value": pt,
                "mission": mission, "planner_sfc": psfc, "oracle_sfc": osfc,
                "agree": "1" if psfc == osfc else "0",
                "valid": "1" if v.get("valid") else "0",
                "parse_status": d.get("llm_parse_status") or "",
            })
            print(f"[vd3] {sweep['name']:16s} {sweep['field']}={pt!s:24s} planner={psfc:22s} oracle={osfc:22s} agree={psfc==osfc}")

    os.makedirs(args.outdir, exist_ok=True)
    cols = ["sweep", "varied_field", "value", "mission", "planner_sfc", "oracle_sfc",
            "agree", "valid", "parse_status"]
    out_csv = os.path.join(args.outdir, "vd3_counterfactual.csv")
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    json.dump(rows, open(os.path.join(args.outdir, "vd3_counterfactual.json"), "w"), indent=2)

    # per-sweep: how many distinct SFCs did the planner vs the oracle produce, and agreement.
    summary = []
    for sweep in SWEEPS:
        sub = [r for r in rows if r["sweep"] == sweep["name"]]
        p_distinct = len({r["planner_sfc"] for r in sub})
        o_distinct = len({r["oracle_sfc"] for r in sub})
        agree = sum(1 for r in sub if r["agree"] == "1")
        summary.append((sweep["name"], sweep["field"], len(sub), p_distinct, o_distinct,
                        "1" if p_distinct > 1 else "0", agree))
    sum_csv = os.path.join(args.outdir, "vd3_summary.csv")
    with open(sum_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sweep", "varied_field", "n", "planner_distinct_sfcs",
                    "oracle_distinct_sfcs", "planner_sensitive", "agree"])
        for r in summary:
            w.writerow(list(r))

    n_agree = sum(1 for r in rows if r["agree"] == "1")
    print(f"wrote {out_csv} ({len(rows)} points; planner-oracle agreement {n_agree}/{len(rows)})")
    _plot(summary, args.outdir)


def _plot(summary, outdir: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available — skipping figure")
        return
    if not summary:
        return
    names = [s[0] for s in summary]
    p_dist = [s[3] for s in summary]
    o_dist = [s[4] for s in summary]
    x = range(len(names))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.bar([i - w / 2 for i in x], p_dist, w, label="planner: distinct SFCs", color="#4C78A8")
    ax.bar([i + w / 2 for i in x], o_dist, w, label="oracle: distinct SFCs", color="#F58518")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=12, ha="right", fontsize=9)
    ax.set_ylabel("# distinct SFCs across the sweep")
    ax.set_title("KRONOS §V.D(3) — counterfactual sensitivity: planner tracks name, flat on telemetry")
    ax.legend()
    for i, (pv, ov) in enumerate(zip(p_dist, o_dist)):
        ax.text(i - w / 2, pv + 0.05, str(pv), ha="center", fontsize=9)
        ax.text(i + w / 2, ov + 0.05, str(ov), ha="center", fontsize=9)
    fig.tight_layout()
    plots_dir = os.path.join(outdir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out = os.path.join(plots_dir, "vd3_counterfactual.png")
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
