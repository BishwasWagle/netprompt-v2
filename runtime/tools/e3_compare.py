"""E3 campaign driver — topology-equivalent 3-arm comparative across scenarios.

For each (scenario × arm × repeat): pick the SFC, launch the 3-switch BMv2 fabric
with that SFC's P4 program + the scenario's netem, start traffic at rates that make
each field's bandwidth bound *achievable* (so failures are RTT/loss-driven, not
offered-load artifacts — validity §2.1), run `runtime.tools.e3_measure`, record the
JSON, and tear the fabric down. All arms run on the SAME 3-switch fabric
(topology-equivalent); only SFC-choice + whether the runtime adapts differ.

  static   = fixed LowLatencyVideoSFC
  rule     = the if/elif ladder (RULE_LADDER)
  proposed = the LLM planner's pick (runs llm_orchestrator.orchestrate for the
             scenario's representative mission + telemetry), then the full adaptive
             RuntimeManager episode.

Runs as the user and calls `sudo` for launch/measure/teardown. `launch_network`
uses SYSTEM python3 (mininet); `e3_measure` + `orchestrate` use the venv python.

  python3 -m runtime.tools.e3_compare --scenarios congestion --arms static,rule,proposed
  python3 -m runtime.tools.e3_compare --repeats 3            # full 6×3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

ROOT = "/home/cc/RuntimeManager"
NETPROMPT_ROOT = os.environ.get(
    "NETPROMPT_ROOT", f"{ROOT}/network/milestone-II-latest/netprompt-milestone-II")
VENV = "/home/cc/netprompt-venv/bin/python"
SYS_PY = "/usr/bin/python3"
KG_URI = os.environ.get("NETPROMPT_KG_URI", "bolt://localhost:7687")
KG_PASS = os.environ.get("NETPROMPT_KG_PASS", "netprompt123")

# E3 fault scenarios (the fault is injected on the relay links by e3_measure after
# a healthy baseline; the fabric is always launched on a CLEAN access profile so the
# only variable is the relay fault + the arm).
# E3 fault scenarios (the fault is injected on the relay links by e3_measure after
# a healthy baseline; the fabric is always launched on a CLEAN access profile so the
# only variables are the relay fault + the arm).
#   primary_fault -> isolates SFC SELECTION; backup_fault -> isolates RUNTIME ADAPTATION;
#   ddil -> honest escalate; healthy -> control.
SCENARIOS = ["healthy", "primary_fault", "backup_fault", "ddil"]
ARMS = ["static", "rule", "proposed"]
ACCESS_PROFILE = "low_latency"            # clean drone->s1 access (not the bottleneck)

SFC_PREFIX = {"LowLatencyVideoSFC": "low_latency", "ReliableRelaySFC": "reliable_relay",
              "BandwidthOptimizedSFC": "bandwidth_optimized", "EnergyAwareSFC": "energy_aware"}
RULE_LADDER = {"primary_fault": "ReliableRelaySFC", "backup_fault": "ReliableRelaySFC",
               "ddil": "ReliableRelaySFC", "healthy": "LowLatencyVideoSFC"}
# proposed arm: scenario -> representative KNOWN mission + (bw,delay,loss,batt).
# (the LLM keys on the mission name; the scenario is the relay condition.)
PROPOSED_MISSION = {
    "healthy": ("real_time_pest_detection", 80, 5, 0, 80),
    "primary_fault": ("emergency_alert_relay", 30, 10, 1, 80),
    "backup_fault": ("emergency_alert_relay", 30, 10, 1, 80),
    "ddil": ("emergency_alert_relay", 30, 10, 1, 80),
}


def _run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def thrift_ready() -> bool:
    try:
        out = subprocess.run(
            "echo 'table_dump forward_table' | simple_switch_CLI --thrift-port 9090",
            shell=True, capture_output=True, text=True, timeout=8).stdout
        return "Dumping entry" in out
    except Exception:
        return False


def teardown():
    _run(["sudo", "mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _run(["sudo", "pkill", "-9", "-x", "simple_switch"], stderr=subprocess.DEVNULL)
    _run(["sudo", "pkill", "-9", "-x", "iperf"], stderr=subprocess.DEVNULL)
    pids = subprocess.run(["pgrep", "-f", "[r]untime.tools.launch_network"],
                          capture_output=True, text=True).stdout.split()
    for p in pids:
        _run(["sudo", "kill", "-9", p], stderr=subprocess.DEVNULL)
    time.sleep(2)


def reseed_kg():
    _run([VENV, "-m", "runtime.tools.seed_kg"], cwd=ROOT,
         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch(prefix, scenario):
    # always a CLEAN access profile; the scenario's fault is injected on the relay
    # links by e3_measure (after a healthy baseline).
    proc = subprocess.Popen(
        ["sudo", "-E", SYS_PY, "-m", "runtime.tools.launch_network",
         "--p4-json", f"{NETPROMPT_ROOT}/compiled_p4/{prefix}.json",
         "--rules-dir", f"{NETPROMPT_ROOT}/p4_multihop_rules",
         "--sfc", prefix, "--scenario", ACCESS_PROFILE],
        cwd=ROOT, env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(45):                       # up to ~90 s for readiness
        if thrift_ready():
            return proc
        time.sleep(2)
    raise RuntimeError(f"fabric not ready: {prefix}/{scenario}")


def start_traffic():
    edge = subprocess.run(["pgrep", "-f", "mininet:edge"], capture_output=True, text=True).stdout.split()
    if not edge:
        raise RuntimeError("no edge namespace")
    _run(f"sudo setsid mnexec -a {edge[0]} iperf -s -u </dev/null >/dev/null 2>&1 &", shell=True)
    # sane 5 Mbit/drone for d4-d10 (M6 rate): F1 (d4-6)=15M, F2 (d7-10)=20M, total
    # 35M < backup relay cap 40M -> no flood even after a reroute. bw bound (5) met.
    for d in ("d4", "d5", "d6", "d7", "d8", "d9", "d10"):
        r = subprocess.run(["pgrep", "-f", f"mininet:{d}$"], capture_output=True, text=True).stdout.split()
        if r:
            _run(f"sudo setsid mnexec -a {r[0]} iperf -u -c 10.0.0.100 -b 5M -t 180 "
                 f"</dev/null >/dev/null 2>&1 &", shell=True)
    time.sleep(5)                             # let iperf ramp before measuring


def proposed_sfc(scenario) -> str:
    mission, bw, dl, ls, bt = PROPOSED_MISSION[scenario]
    plan = "/tmp/e3_plan.json"
    if os.path.exists(plan):
        os.remove(plan)                       # never silently reuse a prior cell's plan
    r = _run([VENV, "-m", "llm_orchestrator.orchestrate", "--mission", mission,
              "--bandwidth", str(bw), "--delay", str(dl), "--loss", str(ls), "--battery", str(bt),
              "--neo4j-uri", KG_URI, "--neo4j-password", KG_PASS, "--device-map", "cuda:0",
              "--no-4bit", "--output", plan], cwd=NETPROMPT_ROOT,
             capture_output=True, text=True, timeout=240)
    if r.returncode != 0 or not os.path.exists(plan):
        raise RuntimeError(f"orchestrate failed (rc={r.returncode}): {r.stderr[-300:]}")
    d = json.load(open(plan))
    dec = d.get("decision", d)
    return dec["selected_sfc"]


def measure(arm, scenario, sfc) -> dict:
    cmd = ["sudo", "-E", "env", f"NETPROMPT_TREE_ROOT={NETPROMPT_ROOT}",
           f"NETPROMPT_KG_URI={KG_URI}", f"NETPROMPT_KG_PASS={KG_PASS}",
           VENV, "-m", "runtime.tools.e3_measure", "--arm", arm, "--scenario", scenario]
    if arm == "proposed":
        cmd += ["--sfc", sfc]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    lines = [l for l in out.stdout.strip().splitlines() if l.startswith("{")]
    if not lines:
        raise RuntimeError(f"e3_measure produced no JSON; stderr: {out.stderr[-300:]}")
    return json.loads(lines[-1])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out", default="/tmp/e3_results.jsonl")
    ap.add_argument("--csv-dir", default=None,
                    help="dir for the cells/flows/summary CSVs (default: alongside --out)")
    args = ap.parse_args()
    scen = [s for s in args.scenarios.split(",") if s]
    arms = [a for a in args.arms.split(",") if a]

    with open(args.out, "w") as fout:
        for s in scen:
            for arm in arms:
                for rep in range(args.repeats):
                    teardown()
                    reseed_kg()
                    try:
                        sfc = ("LowLatencyVideoSFC" if arm == "static"
                               else RULE_LADDER[s] if arm == "rule"
                               else proposed_sfc(s))
                        launch(SFC_PREFIX[sfc], s)
                        start_traffic()
                        rec = measure(arm, s, sfc)
                        rec["repeat"] = rep
                        fout.write(json.dumps(rec) + "\n"); fout.flush()
                        f1 = rec["flows"][0]
                        print(f"[{s}/{arm}#{rep}] sfc={sfc} outcome={rec['outcome']} "
                              f"tier={rec['tier_reached']} F1_rtt={f1['rtt_ms']}ms "
                              f"F1_loss={f1['loss_pct']}% met={rec['target_sla_met']}", flush=True)
                    except Exception as e:
                        err = {"scenario": s, "arm": arm, "repeat": rep, "error": str(e)[:300]}
                        fout.write(json.dumps(err) + "\n"); fout.flush()
                        print(f"[{s}/{arm}#{rep}] ERROR: {str(e)[:200]}", flush=True)
                    finally:
                        teardown()
    print("E3 done ->", args.out)

    # Self-record as CSV (cells/flows/summary) next to the JSONL, for result tables
    # and figures. Best-effort: a CSV error must never lose the just-collected JSONL.
    csv_dir = args.csv_dir or os.path.dirname(os.path.abspath(args.out)) or "."
    try:
        from runtime.tools.results_to_csv import e3 as e3_to_csv
        os.makedirs(csv_dir, exist_ok=True)
        e3_to_csv(args.out, csv_dir)
        print("E3 CSVs ->", csv_dir)
    except Exception as e:                       # noqa: BLE001 — never fail the campaign on export
        print(f"WARN: CSV export failed ({e}); JSONL is intact at {args.out}", flush=True)


if __name__ == "__main__":
    main()
