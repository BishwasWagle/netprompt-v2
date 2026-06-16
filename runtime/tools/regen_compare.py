"""M7 #8 — multi-model comparison harness for Tier-2 regen (the paper's table).

For each model, run a fixed set of table-fault scenarios through propose -> gate
OFFLINE (fixture switch state, no testbed) and record, per model:
  * grammar_valid_rate — fraction of outputs that pass grammar.validate()
  * gate_pass_rate      — fraction the sound gate ACCEPTS (the "accept rate")
  * recovery_rate       — fraction whose candidate, simulated, restores the
                          mis-ported drone to its correct port (recovery-capable)
  * mean_latency_s      — wall-clock per generate (GBNF-constrained, greedy)

    source deploy/gpu-node/gpu-node.env
    python3 -m runtime.tools.regen_compare --models \
      Qwen/Qwen2.5-Coder-1.5B-Instruct,deepseek-ai/deepseek-coder-1.3b-instruct
"""
import argparse
import json
import time

from runtime.contracts import Candidate, Envelope, PRIMARY, REGEN, TableEntry
from runtime.config import EDGE_MAC, SWITCH_PORTS
from runtime.gate import ValidationGate
from runtime.regen import LocalHFClient
from runtime.regen.grammar import validate
from runtime.regen.prompt import build_prompt


class _Diag:
    def __init__(self, who, metric, severity):
        self.who, self.metric, self.severity = who, metric, severity


_ENV = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                legal_tiers=frozenset((REGEN,)), legal_paths=frozenset((PRIMARY,)))


def _entries(misport_drone: int, wrong_port: int):
    """s1 forward table with drone `misport_drone` pointed at `wrong_port`."""
    ent = [TableEntry("forward_table", f"00:00:00:00:00:{i:02x}", "forward",
                      ((str(wrong_port),) if i == misport_drone else (str(i),)), i - 1)
           for i in range(1, 11)]
    ent.append(TableEntry("forward_table", EDGE_MAC, "forward", ("11",), 10))
    # a priority_table entry (handle collides across tables — exercises the gate)
    ent.append(TableEntry("priority_table", "10.0.0.100", "set_low_latency_class", (), 0))
    return ent


def _scenarios():
    out = []
    for drone, wrong in [(4, 5), (7, 9), (2, 8)]:
        out.append({
            "name": f"d{drone}_misport_to_{wrong}",
            "diag": _Diag("F1", "latency", 0.42),
            "entries": _entries(drone, wrong),
            "mac": f"00:00:00:00:00:{drone:02x}",
            "right_port": drone,
        })
    return out


def _recovers(rules_text: str, entries, mac: str, right_port: int) -> bool:
    """True iff applying the candidate to `entries` lands `mac` on `right_port`."""
    by_tk = {(e.table, e.handle): e for e in entries}
    for line in rules_text.splitlines():
        t = line.split()
        if len(t) >= 4 and t[0] == "table_modify":
            try:
                h = int(t[3])
            except ValueError:
                continue
            e = by_tk.get(("forward_table", h))
            args = tuple(x for x in t[4:] if x != "=>")
            if e is not None:
                by_tk[("forward_table", h)] = TableEntry(e.table, e.key, t[2], args, h)
    for e in by_tk.values():
        if (e.table == "forward_table" and e.key.lower() == mac and e.action == "forward"
                and e.args and e.args[0] == str(right_port)):
            return True
    return False


def run_model(model: str, scenarios) -> dict:
    client = LocalHFClient(model=model, switch="s1")
    gate = ValidationGate()
    rows = []
    for s in scenarios:
        prompt = build_prompt(s["diag"], _ENV, {"path": PRIMARY, "knobs": {}},
                              s["entries"], [], "s1", ("forward_table",))
        t0 = time.monotonic()
        out = client.generate(prompt)
        dt = time.monotonic() - t0
        gvalid = validate(out, "s1")
        gpass = gate.check(Candidate(REGEN, ("s1", out)), _ENV,
                           {"s1": s["entries"]}).ok if gvalid else False
        rec = _recovers(out, s["entries"], s["mac"], s["right_port"]) if gpass else False
        rows.append({"scenario": s["name"], "grammar_valid": gvalid, "gate_pass": gpass,
                     "recovers": rec, "latency_s": round(dt, 2), "output": out.strip()})
    n = len(rows)
    return {
        "grammar_valid_rate": round(sum(r["grammar_valid"] for r in rows) / n, 2),
        "gate_pass_rate": round(sum(r["gate_pass"] for r in rows) / n, 2),
        "recovery_rate": round(sum(r["recovers"] for r in rows) / n, 2),
        "mean_latency_s": round(sum(r["latency_s"] for r in rows) / n, 2),
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", required=True, help="comma-separated HF model ids")
    args = ap.parse_args()
    scen = _scenarios()
    report = {}
    for m in [x.strip() for x in args.models.split(",") if x.strip()]:
        print(f"# running {m} over {len(scen)} scenarios…", flush=True)
        try:
            report[m] = run_model(m, scen)          # one bad model can't sink the rest
        except Exception as e:                       # noqa: BLE001
            report[m] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            print(f"#   {m}: {report[m]['error']}", flush=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
