"""E4 — Tier-2 regen correction over a deliberately-broken SFC corpus.

The premade SFC rule files are safe; this experiment feeds the regen subsystem a
corpus of *broken* ones (regen_corpus/) and measures the three nested correctness
layers the M7 design separates (docs/components/regen-llm.md):

  reject items  — a bad candidate rule the guardians MUST refuse. Syntactic faults
                  are caught by grammar.validate() (the proposer's pre-filter);
                  runtime/semantic faults (blackhole / duplicate / dangling handle)
                  are grammar-valid but caught by the ValidationGate at L2.
  recover items — a faulty *installed* forward table (a drone mis-ported or dropped).
                  The regen must emit a corrective `table_modify` that is
                  grammar-valid, gate-accepted, AND restores the route (_recovers).

Arms:
  gate   — deterministic, every reject item: is the bad rule refused, at the layer
           we expect? (the safety result; model-independent).
  stub   — deterministic, every recover item: feed the StubLLMClient the synthesized
           correct fix; proves the validate->gate->recover machinery given a correct
           line (≈100%) — it does not exercise the model's generation.
  real   — every recover item through the live Qwen2.5-Coder LocalHFClient (cuda:1):
           the honest model-capability frontier (historically ~100% safe, ~0% exact
           recovery on small Coders).

Emits one JSON object per (item, arm) to a .jsonl (mirrors e3_compare) and, by
default, auto-writes the CSVs via results_to_csv.

  python3 -m runtime.tools.e4_regen --arms stub --out /tmp/e4_regen.jsonl       # deterministic
  python3 -m runtime.tools.e4_regen --arms stub,real --out /tmp/e4_regen.jsonl  # + GPU frontier
"""
from __future__ import annotations

import argparse
import json
import os
import time

from runtime import config
from runtime.contracts import Candidate, REGEN, TableEntry
from runtime.gate import ValidationGate
from runtime.regen import StubLLMClient
from runtime.regen.grammar import validate
from runtime.regen.prompt import build_prompt
# reuse the comparison harness's envelope, diagnosis shim, and recovery oracle so the
# correctness notions stay identical across E4 and regen_compare (single source).
from runtime.tools.regen_compare import _Diag, _ENV, _recovers

ROOT = "/home/cc/RuntimeManager"
DEFAULT_CORPUS = os.path.join(ROOT, "regen_corpus")
_DIAG = _Diag("target", "latency", 0.42)         # a representative target-latency violation


def rules_only(text: str) -> str:
    """Strip '#' doc-comment lines + blanks; keep the real rule content (the gate
    skips comments internally, but grammar.validate() does not)."""
    return "\n".join(l for l in text.splitlines()
                     if l.strip() and not l.strip().startswith("#"))


def parse_table(text: str) -> list[TableEntry]:
    """Parse a forward-table rule file into TableEntry rows; handle = line order
    (how BMv2 assigns handles), matching the gate's L2 and the recovery oracle."""
    ents, h = [], 0
    for ln in rules_only(text).splitlines():
        t = ln.split()
        sep = t.index("=>") if "=>" in t else len(t)
        ents.append(TableEntry(t[1], t[3].lower(), t[2], tuple(t[sep + 1:]), h))
        h += 1
    return ents


def load_corpus(corpus: str) -> tuple[dict, list[TableEntry]]:
    man = json.load(open(os.path.join(corpus, "manifest.json")))
    base = parse_table(open(os.path.join(corpus, man["base"])).read())
    return man, base


def _read(corpus, rel):
    return open(os.path.join(corpus, rel)).read()


def synth_fix(faulty: list[TableEntry], mac: str, right_port: int) -> str:
    """The known-good corrective row: re-point `mac` to `right_port` by its handle."""
    h = next(e.handle for e in faulty
             if e.table == "forward_table" and e.key == mac.lower())
    return f"table_modify forward_table forward {h} => {right_port}"


def eval_reject(item: dict, bad_text: str, base, switch: str, gate: ValidationGate) -> dict:
    """Run a bad candidate through grammar + gate; record whether it's refused and where.
    caught_by = the proximate guardian: grammar (the proposer's pre-filter) if it fails,
    else the gate's L<n> layer, else NONE (slipped through — a corpus/guard bug)."""
    rules = rules_only(bad_text)
    gv = validate(rules, switch)
    res = gate.check(Candidate(REGEN, (switch, rules)), _ENV, {switch: base})
    layer = "grammar" if not gv else (res.reason.split(":")[0] if not res.ok else "NONE")
    caught = (not gv) or (not res.ok)
    return {
        "id": item["id"], "kind": "reject", "fault_class": item["fault_class"],
        "subtype": item["subtype"], "switch": switch, "arm": "gate",
        "grammar_valid": gv, "gate_ok": res.ok, "reject_reason": res.reason,
        "reject_layer": layer, "expected_layer": item["expect_reject_layer"],
        "pass": caught and layer == item["expect_reject_layer"],
    }


def eval_recover(item, faulty, switch, gate, arm, client) -> dict:
    """Generate a corrective rule via `client`, then score grammar/gate/recover."""
    tgt = item["recovery_target"]
    mac, port = tgt["mac"].lower(), tgt["right_port"]
    prompt = build_prompt(_DIAG, _ENV, {"path": "primary", "knobs": {}},
                          faulty, [], switch, ("forward_table",))
    t0 = time.monotonic()
    out = client.generate(prompt).strip()
    dt = round(time.monotonic() - t0, 3)
    gv = validate(out, switch)
    gp = gate.check(Candidate(REGEN, (switch, out)), _ENV, {switch: faulty}).ok if gv else False
    rec = _recovers(out, faulty, mac, port) if gp else False
    return {
        "id": item["id"], "kind": "recover", "fault_class": item["fault_class"],
        "subtype": item["subtype"], "switch": switch, "arm": arm,
        "grammar_valid": gv, "gate_pass": gp, "recovers": rec,
        "latency_s": dt, "output": out, "pass": rec,
    }


def run(corpus: str, arms: list[str], model: str) -> list[dict]:
    man, base = load_corpus(corpus)
    gate = ValidationGate()
    rejects = [it for it in man["items"] if it["kind"] == "reject"]
    recovers = [it for it in man["items"] if it["kind"] == "recover"]
    records = []

    # gate arm — the safety result over every reject item (deterministic).
    for it in rejects:
        r = eval_reject(it, _read(corpus, it["bad"]), base, it["switch"], gate)
        records.append(r)
        print(f"[gate] {it['id']:24} caught_by={r['reject_layer']:7} "
              f"expect={r['expected_layer']:7} {'PASS' if r['pass'] else 'FAIL'}", flush=True)

    # recover arms — stub (deterministic machinery) and/or real (model frontier).
    real_client = None
    for arm in arms:
        if arm == "gate":
            continue
        if arm not in ("stub", "real"):
            raise SystemExit(f"unknown arm {arm!r} (want gate|stub|real)")
        if arm == "real":
            try:                                  # loading the GPU model must not sink stub/gate
                from runtime.regen import LocalHFClient
                real_client = LocalHFClient(model=model, switch="s1")
            except Exception as e:                # noqa: BLE001
                print(f"WARN: real arm unavailable ({type(e).__name__}: {str(e)[:160]}); "
                      f"skipping", flush=True)
                continue
        for it in recovers:
            try:
                faulty = parse_table(_read(corpus, it["bad"]))
                client = (StubLLMClient([synth_fix(faulty, it["recovery_target"]["mac"],
                                                   it["recovery_target"]["right_port"])])
                          if arm == "stub" else real_client)
                r = eval_recover(it, faulty, it["switch"], gate, arm, client)
                print(f"[{arm}] {it['id']:24} gv={r['grammar_valid']} gp={r['gate_pass']} "
                      f"rec={r['recovers']} {r['latency_s']}s", flush=True)
            except Exception as e:                # noqa: BLE001 — corpus/IO/generation fault:
                # record as a non-recovery (keeps the denominator honest), never sink the run.
                r = {"id": it["id"], "kind": "recover", "fault_class": it["fault_class"],
                     "subtype": it["subtype"], "switch": it["switch"], "arm": arm,
                     "grammar_valid": False, "gate_pass": False, "recovers": False,
                     "latency_s": "", "output": "", "pass": False, "error": str(e)[:160]}
                print(f"[{arm}] {it['id']:24} ERROR {str(e)[:100]}", flush=True)
            records.append(r)
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--arms", default="gate,stub",
                    help="comma list of gate,stub,real (real needs the GPU)")
    ap.add_argument("--model", default=config.REGEN_MODEL, help="HF id for the real arm")
    ap.add_argument("--out", default="/tmp/e4_regen.jsonl")
    ap.add_argument("--csv-dir", default=None,
                    help="dir for e4_corpus/e4_summary CSVs (default: alongside --out)")
    args = ap.parse_args()
    arms = [a for a in args.arms.split(",") if a]
    if "gate" not in arms:                       # the reject safety pass always runs
        arms = ["gate"] + arms

    records = run(args.corpus, arms, args.model)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print("E4 done ->", args.out)

    csv_dir = args.csv_dir or os.path.dirname(os.path.abspath(args.out)) or "."
    try:
        from runtime.tools.results_to_csv import e4 as e4_to_csv
        os.makedirs(csv_dir, exist_ok=True)
        e4_to_csv(args.out, csv_dir)
        print("E4 CSVs ->", csv_dir)
    except Exception as e:                        # noqa: BLE001 — never lose the JSONL
        print(f"WARN: CSV export failed ({e}); JSONL intact at {args.out}", flush=True)


if __name__ == "__main__":
    main()
