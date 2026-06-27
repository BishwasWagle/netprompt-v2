"""Record experiment results as CSV for result tables and figures.

The live drivers emit machine-readable-but-awkward formats: `e3_compare` writes
one JSON object per (scenario × arm × repeat) cell to a `.jsonl`, and the E1
planner probes land as one `orchestrate --output` JSON each. This tool turns
those into tidy CSVs that pandas/matplotlib (or a spreadsheet) can consume
directly — mirroring the milestone-II `*_results_clean.csv` -> `generate_final_plots.py`
pipeline, but for the E1/E3 experiments in `docs/experiments/experiment-results.md`.

E3 (`e3` subcommand) reads the campaign JSONL and writes three views:
  e3_cells.csv    one row per cell (target field F1 + non-target F2 flattened) — raw.
  e3_flows.csv    one row per flow per cell (tidy/long) — the figure-feeding format.
  e3_summary.csv  one row per (scenario × arm): mean ± population-sd over repeats
                  for the target field, plus SLA-met count — matches the doc's E3 table.

E1 (`e1` subcommand) reads the 8 probe JSONs + the fixed expected-oracle map and
writes the confusion matrix (one row per probe, expected vs selected, parse status).

E2a (`e2a` subcommand) is instant + off-node, so it RUNS the gate itself: the 6
fixture scenarios through `RuntimeManager.run_episode` (FakeDeployer + FakeMonitor,
the canonical `tests/unit/test_runtime_manager.py` rig) and writes one row per
scenario — verdict/tier/reason vs the designed verdict (`e2a_gate.csv`).

E2b (`e2b` subcommand) parses a pytest JUnit XML (the node-gated M5/M6 suites,
`pytest --junitxml=...`) into one row per test — outcome + time (`e2b_integration.csv`).

  python3 -m runtime.tools.results_to_csv e3 --in /tmp/e3_full.jsonl --outdir docs/experiments/results
  python3 -m runtime.tools.results_to_csv e1 --indir /tmp --outdir docs/experiments/results
  python3 -m runtime.tools.results_to_csv e2a --outdir docs/experiments/results
  python3 -m runtime.tools.results_to_csv e2b --junit /tmp/e2b_junit.xml --outdir docs/experiments/results
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from statistics import mean, pstdev

# Canonical ordering so tables/figures read like the design doc, not hash order.
SCENARIO_ORDER = ["healthy", "primary_fault", "backup_fault", "ddil"]
ARM_ORDER = ["static", "rule", "proposed"]

# E1 probe set (sets A/B/C from planner-lora-eval.md) with the deterministic oracle
# expectation. Kept in lockstep with the reproduce loop in experiment-results.md §E1;
# the probe JSONs themselves don't carry "expected", so the oracle lives here.
E1_PROBES = [
    ("A1", "emergency_alert_relay", "A", "ReliableRelaySFC"),
    ("A2", "bulk_data_transfer", "A", "BandwidthOptimizedSFC"),
    ("A3", "real_time_pest_detection", "A", "LowLatencyVideoSFC"),
    ("A4", "long_term_soil_monitoring", "A", "EnergyAwareSFC"),
    ("B1", "real_time_video", "B", "LowLatencyVideoSFC"),
    ("B2", "soil_moisture_survey", "B", "EnergyAwareSFC"),
    ("C1", "routine_field_patrol", "C", "LowLatencyVideoSFC"),
    ("C2", "routine_field_patrol", "C", "EnergyAwareSFC"),
]


def _scen_key(s: str) -> int:
    return SCENARIO_ORDER.index(s) if s in SCENARIO_ORDER else len(SCENARIO_ORDER)


def _arm_key(a: str) -> int:
    return ARM_ORDER.index(a) if a in ARM_ORDER else len(ARM_ORDER)


def _load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("{"):
                rows.append(json.loads(line))
    return rows


def _write_csv(path: str, fieldnames: list[str], rows: list[dict]):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    print(f"  wrote {path}  ({len(rows)} rows)")


def _agg(vals: list[float], nd: int = 4) -> tuple[float, float]:
    """mean, population sd (matches the doc's 'mean ± population sd over reps')."""
    if not vals:
        return ("", "")
    m = round(mean(vals), nd)
    sd = round(pstdev(vals), nd) if len(vals) > 1 else 0.0
    return (m, sd)


def e3(in_path: str, outdir: str):
    recs = [r for r in _load_jsonl(in_path) if "error" not in r]
    errs = [r for r in _load_jsonl(in_path) if "error" in r]
    if errs:
        print(f"  note: skipped {len(errs)} error cell(s) in {in_path}")
    recs.sort(key=lambda r: (_scen_key(r["scenario"]), _arm_key(r["arm"]), r.get("repeat", 0)))

    # --- per-flow (tidy / long) — one row per flow per cell ---
    flow_rows = []
    for r in recs:
        target = r.get("target_field")
        for fl in r.get("flows", []):
            flow_rows.append({
                "scenario": r["scenario"], "arm": r["arm"], "repeat": r.get("repeat", 0),
                "sfc": r["sfc"], "fault": r["fault"], "field": fl["field"],
                "is_target": fl["field"] == target,
                "rtt_ms": fl["rtt_ms"], "throughput_mbps": fl["throughput_mbps"],
                "loss_pct": fl["loss_pct"], "met": fl["met"], "margin": fl["margin"],
                "outcome": r["outcome"], "tier_reached": r.get("tier_reached"),
                "final_path": r.get("final_path"), "target_sla_met": r["target_sla_met"],
                "wall_seconds": r.get("wall_seconds"),
            })
    _write_csv(os.path.join(outdir, "e3_flows.csv"),
               ["scenario", "arm", "repeat", "sfc", "fault", "field", "is_target",
                "rtt_ms", "throughput_mbps", "loss_pct", "met", "margin", "outcome",
                "tier_reached", "final_path", "target_sla_met", "wall_seconds"], flow_rows)

    # --- per-cell — one row per (scenario × arm × repeat), F1/F2 flattened ---
    cell_rows = []
    for r in recs:
        by_field = {fl["field"]: fl for fl in r.get("flows", [])}
        sw = r.get("switch_status", {})
        row = {
            "scenario": r["scenario"], "arm": r["arm"], "repeat": r.get("repeat", 0),
            "sfc": r["sfc"], "fault": r["fault"], "outcome": r["outcome"],
            "tier_reached": r.get("tier_reached"), "reason": r.get("reason", ""),
            "final_path": r.get("final_path"), "target_sla_met": r["target_sla_met"],
            "headroom": r.get("headroom"), "wall_seconds": r.get("wall_seconds"),
            "switch_s1": sw.get("s1"), "switch_s2": sw.get("s2"), "switch_s3": sw.get("s3"),
        }
        for fld in ("F1", "F2"):
            fl = by_field.get(fld, {})
            p = fld.lower()
            row[f"{p}_rtt_ms"] = fl.get("rtt_ms")
            row[f"{p}_throughput_mbps"] = fl.get("throughput_mbps")
            row[f"{p}_loss_pct"] = fl.get("loss_pct")
            row[f"{p}_met"] = fl.get("met")
            row[f"{p}_margin"] = fl.get("margin")
        cell_rows.append(row)
    _write_csv(os.path.join(outdir, "e3_cells.csv"),
               ["scenario", "arm", "repeat", "sfc", "fault", "outcome", "tier_reached",
                "reason", "final_path", "target_sla_met", "headroom", "wall_seconds",
                "switch_s1", "switch_s2", "switch_s3",
                "f1_rtt_ms", "f1_throughput_mbps", "f1_loss_pct", "f1_met", "f1_margin",
                "f2_rtt_ms", "f2_throughput_mbps", "f2_loss_pct", "f2_met", "f2_margin"], cell_rows)

    # --- summary — aggregate repeats per (scenario × arm); the doc's E3 table ---
    groups: dict[tuple, list[dict]] = {}
    for r in recs:
        groups.setdefault((r["scenario"], r["arm"]), []).append(r)
    summary_rows = []
    for (scenario, arm), g in sorted(groups.items(), key=lambda kv: (_scen_key(kv[0][0]), _arm_key(kv[0][1]))):
        target = g[0].get("target_field")
        tflows = [next((fl for fl in r.get("flows", []) if fl["field"] == target), {}) for r in g]
        rtt_m, rtt_sd = _agg([f["rtt_ms"] for f in tflows if "rtt_ms" in f], 3)
        loss_m, loss_sd = _agg([f["loss_pct"] for f in tflows if "loss_pct" in f])
        tp_m, tp_sd = _agg([f["throughput_mbps"] for f in tflows if "throughput_mbps" in f])

        def _distinct(key):                       # arms are deterministic; surface drift if not
            vals = sorted({str(r.get(key)) for r in g})
            return vals[0] if len(vals) == 1 else "|".join(vals)
        summary_rows.append({
            "scenario": scenario, "arm": arm, "sfc": _distinct("sfc"),
            "outcome": _distinct("outcome"), "tier_reached": _distinct("tier_reached"),
            "final_path": _distinct("final_path"), "n_repeats": len(g),
            "f1_rtt_ms_mean": rtt_m, "f1_rtt_ms_sd": rtt_sd,
            "f1_loss_pct_mean": loss_m, "f1_loss_pct_sd": loss_sd,
            "f1_throughput_mbps_mean": tp_m, "f1_throughput_mbps_sd": tp_sd,
            "sla_met_count": sum(1 for r in g if r["target_sla_met"]), "sla_met_n": len(g),
            "wall_seconds_mean": round(mean([r["wall_seconds"] for r in g]), 2),
        })
    _write_csv(os.path.join(outdir, "e3_summary.csv"),
               ["scenario", "arm", "sfc", "outcome", "tier_reached", "final_path", "n_repeats",
                "f1_rtt_ms_mean", "f1_rtt_ms_sd", "f1_loss_pct_mean", "f1_loss_pct_sd",
                "f1_throughput_mbps_mean", "f1_throughput_mbps_sd",
                "sla_met_count", "sla_met_n", "wall_seconds_mean"], summary_rows)


def e1(indir: str, outdir: str):
    rows = []
    for pid, mission, mset, expected in E1_PROBES:
        matches = sorted(glob.glob(os.path.join(indir, f"e1_{pid}*.json")))
        if not matches:
            print(f"  note: no probe JSON for {pid} ({mission}) in {indir}; skipping")
            continue
        d = json.load(open(matches[-1]))
        dec = d.get("decision", d)
        selected = dec.get("selected_sfc", "")
        parse = dec.get("llm_parse_status", "")
        rows.append({
            "id": pid, "set": mset, "mission": mission, "expected_sfc": expected,
            "selected_sfc": selected, "parse_status": parse,
            "correct": selected == expected,
        })
    _write_csv(os.path.join(outdir, "e1_confusion.csv"),
               ["id", "set", "mission", "expected_sfc", "selected_sfc", "parse_status", "correct"], rows)
    if rows:
        n_ok = sum(1 for r in rows if r["correct"])
        n_valid = sum(1 for r in rows if r["parse_status"].startswith("parsed"))
        print(f"  E1: {n_ok}/{len(rows)} correct · {n_valid}/{len(rows)} constrained-valid")


# E2a behavioral gate: scenario -> (expected outcome, expected tier). The canonical
# off-node setup mirrors tests/unit/test_runtime_manager.py rig(); only causal_regression
# needs the prior bad deploy applied (the rest reach their verdict from a clean deployer).
E2A_EXPECTED = {
    "healthy": ("healthy", 0), "causal_regression": ("rollback", 0),
    "path_quality_fault": ("healthy", 1), "contention_harm_with_knob": ("marginal", 0),
    "contention_harm_no_knob": ("escalated", 2), "ddil": ("escalated", 2),
}


def e2a(outdir: str) -> int:
    """Run the 6-scenario off-node behavioral gate and write e2a_gate.csv.
    Returns the number of scenarios that missed their designed verdict (0 = all pass)."""
    from runtime.contracts import Candidate, TUNE
    from runtime.fakes import FakeDeployer, FakeMonitor
    from runtime.fixtures import ALL_SCENARIOS
    from runtime.gate import ValidationGate
    from runtime.runtime_manager import RuntimeManager

    pre = {"causal_regression": Candidate(TUNE, ("pfifo_limit", 5))}  # the prior bad deploy
    rows, fails = [], 0
    for name, cls in ALL_SCENARIOS.items():
        model = cls()
        deployer = FakeDeployer()
        if name in pre:
            deployer.apply(pre[name])
        rm = RuntimeManager(deployer, FakeMonitor(model, deployer), ValidationGate(),
                            active_capacity_ok=lambda c: True)
        res = rm.run_episode(model.spec())
        v = res.verdict
        exp_out, exp_tier = E2A_EXPECTED.get(name, ("", ""))
        ok = (v.outcome == exp_out and v.tier_reached == exp_tier)
        fails += not ok
        rows.append({
            "scenario": name, "sfc": model.sfc, "outcome": v.outcome,
            "tier_reached": v.tier_reached, "reason": res.ticket.reason if res.ticket else "",
            "headroom": round(v.headroom, 4), "expected_outcome": exp_out,
            "expected_tier": exp_tier, "pass": ok,
        })
    _write_csv(os.path.join(outdir, "e2a_gate.csv"),
               ["scenario", "sfc", "outcome", "tier_reached", "reason", "headroom",
                "expected_outcome", "expected_tier", "pass"], rows)
    print(f"  E2a: {len(rows) - fails}/{len(rows)} reach their designed verdict")
    return fails


def e4(in_path: str, outdir: str):
    """E4 regen-correction JSONL -> e4_corpus.csv (per item×arm) + e4_summary.csv."""
    recs = _load_jsonl(in_path)
    rejects = [r for r in recs if r.get("kind") == "reject"]
    recovers = [r for r in recs if r.get("kind") == "recover"]

    _write_csv(os.path.join(outdir, "e4_corpus.csv"),
               ["id", "kind", "fault_class", "subtype", "switch", "arm", "grammar_valid",
                "gate_ok", "reject_layer", "expected_layer", "reject_reason", "gate_pass",
                "recovers", "latency_s", "error", "pass"], recs)

    def _rate(rows, key):
        return round(sum(1 for r in rows if r.get(key)) / len(rows), 3) if rows else ""

    def _caught_rate(rows):                       # refused by grammar OR the gate
        return round(sum(1 for r in rows
                         if not (r.get("grammar_valid") and r.get("gate_ok"))) / len(rows), 3)

    summary = []
    # reject items grouped by fault class (the gate arm — the safety result).
    for fc in ("syntactic", "runtime"):
        g = [r for r in rejects if r["fault_class"] == fc]
        if not g:
            continue
        summary.append({"group": f"reject:{fc}", "arm": "gate", "n": len(g),
                        "caught_rate": _caught_rate(g), "pass_rate": _rate(g, "pass")})
    # recover items per arm (stub = machinery, real = model frontier).
    for arm in sorted({r["arm"] for r in recovers}):
        g = [r for r in recovers if r["arm"] == arm]
        lat = [r["latency_s"] for r in g if isinstance(r.get("latency_s"), (int, float))]
        summary.append({"group": "recover", "arm": arm, "n": len(g),
                        "grammar_valid_rate": _rate(g, "grammar_valid"),
                        "gate_pass_rate": _rate(g, "gate_pass"),
                        "recovery_rate": _rate(g, "recovers"),
                        "mean_latency_s": round(mean(lat), 2) if lat else ""})
    _write_csv(os.path.join(outdir, "e4_summary.csv"),
               ["group", "arm", "n", "caught_rate", "pass_rate", "grammar_valid_rate",
                "gate_pass_rate", "recovery_rate", "mean_latency_s"], summary)

    nrej = sum(1 for r in rejects if r.get("pass"))
    print(f"  E4: reject {nrej}/{len(rejects)} refused at the expected layer; "
          f"recover arms {sorted({r['arm'] for r in recovers})}")


def e2b(junit_path: str, outdir: str):
    """Parse a pytest JUnit XML (the node-gated M5/M6 suites) into e2b_integration.csv."""
    import xml.etree.ElementTree as ET
    root = ET.parse(junit_path).getroot()
    rows = []
    for tc in root.iter("testcase"):
        child = next((c.tag for c in tc if c.tag in ("failure", "error", "skipped")), None)
        outcome = {"failure": "failed", "error": "error", "skipped": "skipped",
                   None: "passed"}[child]
        cls = tc.get("classname", "")
        suite = cls.split(".")[-1] if cls else ""           # e.g. test_m6_acceptance_node
        rows.append({
            "suite": suite, "test": tc.get("name", ""), "outcome": outcome,
            "time_s": round(float(tc.get("time", 0) or 0), 3),
        })
    _write_csv(os.path.join(outdir, "e2b_integration.csv"),
               ["suite", "test", "outcome", "time_s"], rows)
    n_pass = sum(1 for r in rows if r["outcome"] == "passed")
    print(f"  E2b: {n_pass}/{len(rows)} passed")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe3 = sub.add_parser("e3", help="E3 campaign JSONL -> cells/flows/summary CSVs")
    pe3.add_argument("--in", dest="in_path", default="/tmp/e3_results.jsonl")
    pe3.add_argument("--outdir", default="docs/experiments/results")

    pe1 = sub.add_parser("e1", help="E1 probe JSONs -> confusion-matrix CSV")
    pe1.add_argument("--indir", default="/tmp")
    pe1.add_argument("--outdir", default="docs/experiments/results")

    pe2a = sub.add_parser("e2a", help="run the off-node behavioral gate -> e2a_gate.csv")
    pe2a.add_argument("--outdir", default="docs/experiments/results")

    pe2b = sub.add_parser("e2b", help="pytest JUnit XML -> e2b_integration.csv")
    pe2b.add_argument("--junit", required=True, help="pytest --junitxml output path")
    pe2b.add_argument("--outdir", default="docs/experiments/results")

    pe4 = sub.add_parser("e4", help="E4 regen-correction JSONL -> corpus/summary CSVs")
    pe4.add_argument("--in", dest="in_path", default="/tmp/e4_regen.jsonl")
    pe4.add_argument("--outdir", default="docs/experiments/results")

    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    if args.cmd == "e3":
        e3(args.in_path, args.outdir)
    elif args.cmd == "e1":
        e1(args.indir, args.outdir)
    elif args.cmd == "e2a":
        raise SystemExit(1 if e2a(args.outdir) else 0)
    elif args.cmd == "e2b":
        e2b(args.junit, args.outdir)
    elif args.cmd == "e4":
        e4(args.in_path, args.outdir)


if __name__ == "__main__":
    main()
