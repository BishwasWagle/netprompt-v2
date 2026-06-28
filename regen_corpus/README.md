# Bad-SFC corpus (E4 — Tier-2 regen correction)

The premade SFC rule files (`network/.../p4_multihop_rules/*.txt`) are **safe**. This
folder is the opposite: a corpus of deliberately **broken** s1 forward-table scripts the
Tier-2 regen subsystem must handle. It drives the **E4** experiment
([../docs/experiments/results/experiment-results.md](../docs/experiments/results/experiment-results.md))
and the `tests/unit/test_regen_corpus.py` guard.

Each item is one `bad/<id>.txt` rule file plus a metadata row in `manifest.json`.
Two kinds:

- **reject** — a bad *candidate* rule the guardians must refuse, checked against the
  healthy `base.txt` state. `fault_class`:
  - `syntactic` — caught by `grammar.validate()` (the proposer's pre-filter): unknown
    table/action, action/table mismatch, out-of-range or leading-zero port, wrong key
    type, no-arg action with an arg, prose instead of a command.
  - `runtime` — grammar-valid but caught by the **ValidationGate at L2**: blackhole
    (drop a required drone / the edge), duplicate add, dangling handle.
- **recover** — a faulty *installed* forward table (a drone mis-ported, or dropped).
  The regen must emit a corrective `table_modify` that is grammar-valid, gate-accepted,
  and restores the route (the `_recovers` oracle). The known-good fix is synthesized
  from `recovery_target` (re-point the MAC to its right port by handle).

Convention (a fixture, not the live topology): on `s1`, drone *i* (`00:…:0{i}`) routes
to port *i*; the edge (`00:…:0b`) to port 11. Handles are line order.

`#` lines are documentation; the harness and gate strip them before evaluating.

Regenerate the result CSVs (deterministic):

```bash
repro/e4.sh            # gate + stub arms; add the live model with: repro/e4.sh gate,stub,real
```
