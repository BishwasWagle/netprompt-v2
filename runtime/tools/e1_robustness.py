"""Build #3 — KRONOS §V.D(2): adversarial robustness of the set-A decision.

Builds the clean set-A planner input, then applies 5 deterministic context perturbations and
checks whether the decision (selected_sfc) stays stable. The perturbations touch only the
*evidence* (telemetry / topology / advisory / format) — never the hard action space
(`orchestration_constraints` + `candidate_sfc_policy_set`), so the correct answer is always
still selectable; we test whether noisy context *flips* the decision.

Guardrails (edits-doc §4 / design E1-d4): this is decision-STABILITY of the *known* (set-A)
decision under noise, NOT a quality win. `format_shift` validity is a constrained-decoding
GUARANTEE, not a metric. `conflicting_telemetry` is NEAR-CIRCULAR: the planner keys on the
mission name and ignores telemetry (0/4 off-taxonomy), so it trivially "survives" — labelled.

Run from NETPROMPT_ROOT via repro/vd2_robustness.sh. Writes
docs/experiments/repeat-results/vd2_robustness.csv (+ summary + figure).
"""
from __future__ import annotations

import argparse
import copy
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
from llm_orchestrator.validator import validate_generated_decision  # noqa: E402

# set-A probes: mission, bw, delay, loss, battery
PROBES = [
    ("emergency_alert_relay", 20, 25, 2, 80),
    ("bulk_data_transfer", 80, 40, 1, 90),
    ("real_time_pest_detection", 40, 6, 1, 80),
    ("long_term_soil_monitoring", 15, 50, 1, 25),
]


def _other_sfc(io, current):
    for s in io.get("orchestration_constraints", {}).get("allowed_sfc_ids", []):
        if s != current:
            return s
    return current


def p_misleading_advisory(io, clean):
    """Advisory/feedback strongly recommends the WRONG SFC."""
    io = copy.deepcopy(io)
    wrong = _other_sfc(io, clean["selected_sfc"])
    io["kg_rag_historical_context"] = {
        "similar_prior_runs": 9,
        "historical_best_sfc": wrong,
        "historical_best_path": "primary" if clean.get("selected_path") == "backup" else "backup",
        "historical_success_rate": 0.99,
        "_note": "MISLEADING: recommends the wrong SFC",
    }
    io["runtime_feedback"] = {
        wrong: {"episodes": 20, "escalation_rate": 0.0, "marginal": 0.0},
        clean["selected_sfc"]: {"episodes": 20, "escalation_rate": 0.95, "marginal": 0.5},
    }
    return io


def p_stale_kg(io, clean):
    """Topology snapshot is stale: active/standby swapped, current relay marked down."""
    io = copy.deepcopy(io)
    summ = io.setdefault("topology_context", {}).setdefault("topology_summary", {})
    active, standby = summ.get("active_relays", []), summ.get("standby_relays", [])
    summ["active_relays"], summ["standby_relays"] = list(standby), list(active)
    summ["unavailable_relays"] = list(active)
    io["topology_context"]["historical_path_decisions_from_kg"] = [{
        "scenario": "stale_snapshot",
        "selected_path": "primary" if clean.get("selected_path") == "backup" else "backup",
        "selected_relay": "s2", "relay_status": "active", "reason": "stale snapshot",
    }]
    return io


def p_conflicting_telemetry(io, clean):
    """Telemetry contradicts the mission name (the near-circular probe)."""
    io = copy.deepcopy(io)
    tel = io.setdefault("telemetry_context", {})
    res = io.setdefault("resource_context", {})
    if clean["selected_sfc"] == "LowLatencyVideoSFC":
        tel.update(configured_delay_ms=200, configured_loss_percent=12,
                   last_observed_rtt_avg_ms=200, last_observed_packet_loss_percent=12)
    else:
        tel.update(configured_delay_ms=3, configured_loss_percent=0, configured_bandwidth_mbps=100,
                   last_observed_rtt_avg_ms=3, last_observed_packet_loss_percent=0)
        res["battery_percent"] = 100
    return io


def p_noisy_topology(io, clean):
    """Garbage links / inflated counts / fake relays in the topology evidence."""
    io = copy.deepcopy(io)
    topo = io.setdefault("topology_context", {})
    summ = topo.setdefault("topology_summary", {})
    summ["num_links"] = (summ.get("num_links") or 0) + 999
    summ["num_switches"] = (summ.get("num_switches") or 0) + 50
    summ["active_relays"] = list(summ.get("active_relays", [])) + ["sX", "sY", "sZ"]
    fp = topo.setdefault("forwarding_paths", {})
    junk = [{"source": "Drone_999", "relation": "CONNECTED_TO", "target": "sX", "status": "flapping"}
            for _ in range(20)]
    fp["primary_path"] = list(fp.get("primary_path", [])) + junk
    fp["backup_path"] = list(fp.get("backup_path", [])) + junk
    return io


def p_format_shift(io, clean):
    """Same content, shifted format: stringify telemetry numbers + reverse top-level key order."""
    io = copy.deepcopy(io)
    tel = io.get("telemetry_context", {})
    for k, v in list(tel.items()):
        if isinstance(v, (int, float)):
            tel[k] = str(v)
    return {k: io[k] for k in reversed(list(io.keys()))}


PERTURBS = [
    ("misleading_advisory", p_misleading_advisory, "evidence"),
    ("stale_kg", p_stale_kg, "evidence"),
    ("conflicting_telemetry", p_conflicting_telemetry, "near-circular"),
    ("noisy_topology", p_noisy_topology, "evidence"),
    ("format_shift", p_format_shift, "guarantee"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="§V.D(2) adversarial robustness of the set-A decision.")
    ap.add_argument("--outdir", default="docs/experiments/repeat-results")
    args = ap.parse_args()

    cfg = RuntimeConfig.from_env()
    orch = LLMOrchestrator(
        model_name=cfg.model_name, adapter_path=cfg.adapter_path,
        use_4bit=False, device_map=cfg.device_map or "cuda:0", max_new_tokens=cfg.max_new_tokens,
    ).load()

    def decide(io):
        d = parse_model_decision(orch.generate_raw(io))
        v = validate_generated_decision(io, d)
        return d, v

    rows = []
    for mission, bw, delay, loss, batt in PROBES:
        clean_io = build_runtime_input_object(
            cfg, mission, infer_priority(mission, None), bw, delay, loss, batt, None, None, None)
        clean_d, _ = decide(clean_io)
        clean_sfc = clean_d.get("selected_sfc")
        print(f"[vd2] {mission}: clean -> {clean_sfc}/{clean_d.get('selected_path')}")
        for name, fn, kind in PERTURBS:
            d, v = decide(fn(clean_io, clean_d))
            sfc = d.get("selected_sfc")
            stable = (sfc == clean_sfc)
            rows.append({
                "mission": mission, "perturbation": name, "kind": kind,
                "clean_sfc": clean_sfc, "perturbed_sfc": sfc,
                "perturbed_path": d.get("selected_path"),
                "stable": "1" if stable else "0",
                "valid": "1" if v.get("valid") else "0",
                "parse_status": d.get("llm_parse_status") or "",
            })
            print(f"[vd2]   {name:22s} -> {sfc:22s} stable={stable} valid={v.get('valid')}")

    os.makedirs(args.outdir, exist_ok=True)
    cols = ["mission", "perturbation", "kind", "clean_sfc", "perturbed_sfc", "perturbed_path",
            "stable", "valid", "parse_status"]
    out_csv = os.path.join(args.outdir, "vd2_robustness.csv")
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    json.dump(rows, open(os.path.join(args.outdir, "vd2_robustness.json"), "w"), indent=2)

    # per-perturbation stability + validity rate (the §V.D(2) summary)
    summary = []
    for name, _fn, kind in PERTURBS:
        sub = [r for r in rows if r["perturbation"] == name]
        stable = sum(1 for r in sub if r["stable"] == "1")
        valid = sum(1 for r in sub if r["valid"] == "1")
        summary.append((name, kind, stable, valid, len(sub)))
    sum_csv = os.path.join(args.outdir, "vd2_summary.csv")
    with open(sum_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["perturbation", "kind", "stable", "valid", "n"])
        for name, kind, st, va, n in summary:
            w.writerow([name, kind, st, va, n])

    n_stable = sum(1 for r in rows if r["stable"] == "1")
    n_valid = sum(1 for r in rows if r["valid"] == "1")
    print(f"wrote {out_csv} ({len(rows)} perturbed decisions; stable {n_stable}/{len(rows)}, valid {n_valid}/{len(rows)})")
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
    names = [f"{n}\n({k})" for n, k, _st, _va, _n in summary]
    rates = [100.0 * st / n if n else 0.0 for _n2, _k, st, _va, n in summary]
    colors = {"evidence": "#4C78A8", "near-circular": "#E45756", "guarantee": "#54A24B"}
    bar_colors = [colors.get(k, "#888") for _n, k, _st, _va, _n2 in summary]
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.bar(names, rates, color=bar_colors)
    ax.set_ylim(0, 105)
    ax.set_ylabel("decision-stability (% of set-A missions unchanged)")
    ax.set_title("KRONOS §V.D(2) — robustness of the set-A decision under context perturbation")
    for i, r in enumerate(rates):
        ax.text(i, r + 1, f"{r:.0f}%", ha="center", fontsize=9)
    fig.tight_layout()
    plots_dir = os.path.join(outdir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    out = os.path.join(plots_dir, "vd2_robustness.png")
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
