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


def _free_gpu(client):
    """Release a model's GPU tensors so a multi-model sweep doesn't accumulate
    weights across models — a cross-family --models list can otherwise exceed one
    P100's 16 GB. No-op for CPU/stub; never raises (cleanup must not fail a run)."""
    try:
        import gc
        import torch
        client._model = client._tok = client._proc = client._gen_cfg = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:                                # noqa: BLE001
        pass


def run_model(model: str, scenarios, device=None, revision="main") -> dict:
    # device=None -> LocalHFClient falls back to config.REGEN_DEVICE (default unchanged).
    # revision defaults to "main" (each model's OWN latest) — NOT config.REGEN_REVISION,
    # which pins ONE model's commit and would make every other repo fail to load. The
    # production Tier-2 path still inherits the pin via LocalHFClient()'s own default.
    client = LocalHFClient(model=model, switch="s1", device=device, revision=revision)
    gate = ValidationGate()
    rows = []
    try:
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
    finally:
        _free_gpu(client)                            # release VRAM before the next model
    n = len(rows)
    return {
        "grammar_valid_rate": round(sum(r["grammar_valid"] for r in rows) / n, 2),
        "gate_pass_rate": round(sum(r["gate_pass"] for r in rows) / n, 2),
        "recovery_rate": round(sum(r["recovers"] for r in rows) / n, 2),
        "mean_latency_s": round(sum(r["latency_s"] for r in rows) / n, 2),
        "rows": rows,
    }


# ----------------------------------------------------------------------------
# Cross-family frontier categorization (M7 #8 extension).
#
# A cross-family run hits three hard gates that a single-family (Qwen) run never
# does: (1) the model's architecture must load natively under the pinned
# transformers (no trust_remote_code is passed); (2) it must carry a chat
# template (base models don't); (3) its EXACT tokenizer class must be in
# transformers-cfg's supported set (no superclass walk). We map every model to
# one category so the comparison table reports *why* a family is in or out,
# instead of a bare exception string.
# ----------------------------------------------------------------------------

CATEGORIES = ("runs", "fail_load", "fail_no_chat_template",
              "fail_unsupported_tokenizer", "fail_oom", "fail_gated")

_SUPPORTED_TOK = None


def _supported_tokenizer_classes():
    """transformers-cfg's exact-match allowlist (cached). Empty set if the lib
    layout changes, in which case the precheck simply defers to the live load."""
    global _SUPPORTED_TOK
    if _SUPPORTED_TOK is None:
        try:
            from transformers_cfg.tokenization.SUPPORTED_TOKENIZERS import (
                SUPPORTED_TOKENIZERS)
            _SUPPORTED_TOK = set(SUPPORTED_TOKENIZERS)
        except Exception:                            # noqa: BLE001
            _SUPPORTED_TOK = set()
    return _SUPPORTED_TOK


def _classify_exc(exc) -> str:
    """Map a load/generate exception to a frontier category (best-effort, by
    type + message, since HF/transformers-cfg raise plain Assertion/RuntimeError)."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if "out of memory" in msg or name == "OutOfMemoryError":
        return "fail_oom"
    if "tokenizer not supported" in msg:
        return "fail_unsupported_tokenizer"
    if "chat template" in msg or "chat_template" in msg:
        return "fail_no_chat_template"
    if name in ("GatedRepoError", "RepositoryNotFoundError") or any(
            k in msg for k in ("gated", "401 client", "403 client",
                               "awaiting a review", "access to model",
                               "is not authorized", "must be authenticated")):
        return "fail_gated"
    return "fail_load"


def _precheck(model_id: str, revision="main") -> dict:
    """Weights-free triage: load only the tokenizer (a few KB) and decide whether
    the expensive FP16 model load is even worth attempting. Catches the two most
    common cross-family failures — unsupported tokenizer and missing chat
    template — without downloading multi-GB weights or touching the GPU.
    `category=None` means 'proceed to the full load'. Uses the SAME revision the
    full run will, so the triage can't diverge from the loaded model."""
    info = {"tokenizer_class": None, "tokenizer_supported": None,
            "has_chat_template": None, "category": None, "detail": None}
    try:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(model_id, revision=revision)
    except Exception as e:                           # noqa: BLE001
        info["category"] = _classify_exc(e)
        info["detail"] = f"tokenizer load: {type(e).__name__}: {str(e)[:160]}"
        return info
    cls = type(tok)
    info["tokenizer_class"] = cls.__name__
    info["tokenizer_supported"] = cls in _supported_tokenizer_classes()
    info["has_chat_template"] = bool(getattr(tok, "chat_template", None))
    if _supported_tokenizer_classes() and not info["tokenizer_supported"]:
        info["category"] = "fail_unsupported_tokenizer"
        info["detail"] = (f"{cls.__name__} not in transformers-cfg supported set "
                          f"{sorted(c.__name__ for c in _supported_tokenizer_classes())}")
    elif info["has_chat_template"] is False:
        info["category"] = "fail_no_chat_template"
        info["detail"] = ("tokenizer has no chat_template (base model?) — "
                          "apply_chat_template would raise")
    return info


def evaluate_model(model: str, scenarios, device=None, skip_precheck=False,
                   revision="main") -> dict:
    """Precheck -> (maybe) full run, always tagged with a `category`. One bad
    model can never sink the rest; successful runs keep the exact same rate keys
    as before (grammar_valid_rate, gate_pass_rate, recovery_rate, mean_latency_s,
    rows) with `category="runs"` added — additive, backward compatible."""
    pre = {} if skip_precheck else _precheck(model, revision=revision)
    if pre.get("category"):                          # cheap triage already decided
        return {"category": pre["category"], "error": pre["detail"],
                "tokenizer_class": pre.get("tokenizer_class"),
                "tokenizer_supported": pre.get("tokenizer_supported"),
                "has_chat_template": pre.get("has_chat_template")}
    try:
        res = run_model(model, scenarios, device=device, revision=revision)
        res["category"] = "runs"
        if pre.get("tokenizer_class"):
            res["tokenizer_class"] = pre["tokenizer_class"]
        return res
    except Exception as e:                           # noqa: BLE001
        out = {"category": _classify_exc(e),
               "error": f"{type(e).__name__}: {str(e)[:200]}"}
        if pre.get("tokenizer_class"):
            out["tokenizer_class"] = pre["tokenizer_class"]
            out["has_chat_template"] = pre.get("has_chat_template")
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", required=True, help="comma-separated HF model ids")
    ap.add_argument("--device", default=None,
                    help="override NETPROMPT_REGEN_DEVICE for every model, e.g. cuda:0")
    ap.add_argument("--probe", action="store_true",
                    help="frontier triage: 1 scenario/model (cheap categorization sweep)")
    ap.add_argument("--no-precheck", action="store_true",
                    help="skip the weights-free tokenizer triage; always attempt full load")
    ap.add_argument("--revision", default="main",
                    help="default HF revision, applied to any model given WITHOUT an explicit "
                         "'@<rev>' pin (default 'main' = each model's own latest). Per-model pins "
                         "via 'org/model@<sha>' override this. Cross-family runs must NOT inherit "
                         "a single model's commit across all repos.")
    ap.add_argument("--out", default=None,
                    help="also write {manifest, results} JSON to this path")
    args = ap.parse_args()
    scen = _scenarios()
    if args.probe:
        scen = scen[:1]
    report = {}
    for spec in [x.strip() for x in args.models.split(",") if x.strip()]:
        # Per-model pin: "org/model@<rev>" -> (org/model, <rev>); HF ids never contain '@',
        # so rsplit is unambiguous. No '@' -> fall back to the global --revision default.
        m, rev = spec.rsplit("@", 1) if "@" in spec else (spec, args.revision)
        m, rev = m.strip(), rev.strip()
        print(f"# running {m}@{rev} over {len(scen)} scenario(s)…", flush=True)
        report[m] = evaluate_model(m, scen, device=args.device,
                                   skip_precheck=args.no_precheck, revision=rev)
        report[m]["revision"] = rev                  # record the exact pin used, per model
        cat = report[m].get("category")
        if cat and cat != "runs":
            print(f"#   {m}: {cat} — {report[m].get('error', '')}", flush=True)
    print(json.dumps(report, indent=2))
    if args.out:
        from runtime.regen.llm_client import manifest
        # Per-model pins live in results[m]["revision"]; default_revision is the fallback
        # for any model given without an explicit '@<rev>'.
        bundle = {"manifest": manifest(), "device": args.device,
                  "default_revision": args.revision, "probe": args.probe, "results": report}
        with open(args.out, "w") as f:
            json.dump(bundle, f, indent=2)
        print(f"# wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
