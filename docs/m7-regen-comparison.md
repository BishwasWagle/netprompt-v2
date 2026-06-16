# M7 #8 — Tier-2 regen multi-model comparison

Harness: `runtime/tools/regen_compare.py`. For each model it runs a fixed set of table-fault
scenarios (a drone mis-ported to a wrong-but-valid port) through **propose → gate**, OFFLINE
(fixture switch state — no testbed), and records:

- **grammar-valid** — fraction of outputs that pass `grammar.validate()`
- **gate-accept** — fraction the sound `ValidationGate` accepts (the "accept rate")
- **recovery** — fraction whose candidate, simulated, restores the mis-ported drone to its
  correct port (recovery-capable)
- **latency** — mean wall-clock per GBNF-constrained greedy generation

Run (each model uses its own latest revision; don't pin one model's revision across all):
```
NETPROMPT_REGEN_DEVICE=cuda:1 python3 -m runtime.tools.regen_compare \
  --models Qwen/Qwen2.5-Coder-0.5B-Instruct,Qwen/Qwen2.5-Coder-1.5B-Instruct,Qwen/Qwen2.5-Coder-3B-Instruct
```

## Result — Qwen2.5-Coder size sweep (2026-06-16, P100 cuda:1, FP16, greedy, GBNF-constrained)

| Model | grammar-valid | gate-accept | recovery | mean latency |
|---|---|---|---|---|
| Qwen2.5-Coder-0.5B-Instruct | 100% | 100% | 0% | 7.3 s |
| Qwen2.5-Coder-1.5B-Instruct | 100% | 100% | 0% | 6.1 s |
| Qwen2.5-Coder-3B-Instruct | 100% | 100% | 0% | 8.0 s |

**Reading.** Constrained decoding + the sound gate make Tier-2 **safe at every size** — every output
is grammar-valid and gate-accepted, so a Tier-2 attempt can never deploy a blackhole regardless of
the model. But **none of the small Coders emit the *corrective* row** (0% recovery), and **size
doesn't help** here (0% at 0.5B → 3B). So Tier-2's demonstrated value is "safe, gate-vetted attempts";
a *committed recovery* depends on the model emitting the exact fix — the model-capability frontier,
matching the live `xfail` (test C in m7-regen-live-demo.md). The recovery *path/machinery* is proven
separately (test D, DoD #3).

## Notes / limitations

- **transformers pin.** The cu121/Pascal stack pins `transformers==4.46.3`; DeepSeek-Coder-1.3B and
  Granite-3B-code don't load cleanly under it (the harness records a per-model error and continues).
  The Qwen-Coder family loads natively. A cross-family table needs either a newer transformers (risks
  the torch/cu121 + transformers-cfg compatibility) or models that load under 4.46.3
  (qwen2 / code_llama / starcoder2 / phi3 / stablelm families).
- The harness is per-model fault-isolating: one model failing to load reports an `error` entry and the
  rest still run.
