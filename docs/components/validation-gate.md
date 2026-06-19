# Validation Gate (`runtime/gate.py`)

**Subsystem:** Runtime Manager (inner loop)
**One-liner:** The sound, pre-deploy authority that proves a binding or adapt candidate is safe to install before it ever touches the data plane.

## Responsibility
Owns the *sound* half of the sound/noisy split: deterministic, conservative, fast structural checks that guarantee a config installs cleanly, stays in-envelope, and cannot blackhole a flow. It is what makes an LLM acceptable in a live control loop — the model proposes, the gate is the authority. It does NOT decide whether a config will *meet SLA* (that is the noisy, post-deploy evaluator's job), and it does not deploy, roll back, or talk to the KG.

## Files
- `runtime/gate.py` — the `ValidationGate` class, L0–L2 logic, and the parser/simulator.
- `runtime/contracts.py` — `Candidate`, `Envelope`, `GateResult`, `TableEntry` (consumed, not defined here).
- `runtime/config.py` — `DRONE_MACS`, `EDGE_MACS`, `SWITCH_PORTS` (the ground-truth bounds).

## Interface
```python
class ValidationGate:
    def __init__(self, required_macs=DRONE_MACS,
                 edge_macs=tuple(EDGE_MACS.values()), dry_install_fn=None)
    def check_binding(self, spec: DeploymentSpec) -> GateResult        # entry point 1
    def check(self, cand: Candidate, env: Envelope,
              current_tables: dict | None = None) -> GateResult        # entry point 2
```
Rejections are stable `"L<n>: ..."` strings, so a rejected candidate is reproducibly rejected and feeds the adapt engine's `tried` set and the escalation trace.

## How it works
- **Two entry points, one gate.** `check_binding(spec)` validates the full `DeploymentSpec` binding pre-deploy (required keys present, envelope bounds well-formed). `check(cand, env, current_tables)` validates one tune/reroute/regen candidate before `apply`.
- **L0 syntax/grammar** — parses as `simple_switch_CLI`; references only known tables (`forward_table`, `priority_table`, `relay_policy_table`) and their actions; well-formed MAC/IPv4 keys; egress is a real port for the switch.
- **L1 envelope bounds** — tier ∈ `legal_tiers`, knob value ∈ `knob_ranges`, path ∈ `legal_paths`.
- **L2 safety invariants** — simulates the parsed commands against a copy of `current_tables[switch]`, then asserts every required drone MAC stays forwarded to a valid port and the edge stays reachable on at least one edge MAC. `forward_table`'s `default_action` is `drop()`, so an unrouted MAC is a silent blackhole.
- **L3 dry-install (optional, node-only)** — if `dry_install_fn` is wired, runs after a passing L2 to catch real-install errors (DUPLICATE_ENTRY / handle drift) the simulation can't. OFF by default.
- **Sound = conservative:** for regen, if no current table state is available the gate rejects (`L2: no current table state`) rather than hope.

## Gotchas & lessons
- **L2 keys the working set by `(table, handle)`, not handle alone.** BMv2 handles are NOT unique across tables — a `priority_table` entry can share a handle value with a `forward_table` entry. Keying by handle alone collapsed them and dropped a forward entry, producing a false blackhole rejection of a valid regen candidate. This was a real bug; the `(table, handle)` key is the fix.
- Port args are matched as exact strings, which rejects leading zeros (e.g. `011`, which the CLI may read as octal).
- The `table_modify` parser accepts both `... <handle> => <args>` and `... <handle> <args>` forms.
- Gate-rejected candidates never spend retry budget (only *applied* attempts do), but they are bounded — rejects join the finite `tried` set, so termination holds.

## Usage
In-loop only — not a CLI. Constructed by the loop/orchestrator and invoked per binding and per candidate.

## See also
- `../runtime-manager-design.md` §11 (Validation Gate), §7.2 (per-candidate check), §10.1 (dual edge identity / tables).
- `../usage.md` §1.4 (a dry `run_from_planner --no-kg` run performs the gate check without deploying).
- Sibling: `deployer.md` (L3 `dry_install` hook), `kg-client.md` (`build_envelope` supplies the `Envelope`).
