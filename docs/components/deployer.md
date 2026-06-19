# Deployer (`runtime/deployer.py`)

**Subsystem:** Runtime Manager (inner loop)
**One-liner:** Live re-installs table rules and host QoS over a persistent, never-torn-down BMv2 network, and tracks the state needed to roll back.

## Responsibility
Owns all command construction, output parsing, table/QoS state tracking, and rollback-diff logic for the data plane. It installs table **rules** (not the P4 *program*, which is fixed at switch launch) and host `tc` qdiscs, applies tune/reroute/regen candidates, and restores prior config from a `ConfigSnapshot`. It does NOT decide *what* to deploy (the engine/planner) nor *whether* a config is safe (the gate) nor *whether* it met SLA (the monitor/evaluator). It is agnostic to *how* commands reach the switches — that is the injected `Runner`.

## Files
- `runtime/deployer.py` — parsers, command builders, and the `Deployer` class.
- `runtime/node_runner.py` — the live `NodeRunner` (`simple_switch_CLI` over thrift + `mnexec`); off-node a `ScriptedRunner` fake is injected.
- `runtime/config.py` — `SWITCH_RULES_KEYS`, `EDGE_MACS`, `RELAY_EDGE`, `EDGE_PORTS`, `TC_TEMPLATES`, `SFC_QOS_BASELINE`.

## Interface
```python
class Deployer:
    def __init__(self, runner, host_map: dict)
    def deploy(self, spec: DeploymentSpec) -> ConfigSnapshot
    def apply(self, cand: Candidate) -> None            # TUNE | REROUTE | REGEN
    def rollback(self, snapshot: ConfigSnapshot) -> None
    def re_push(self, snapshot: ConfigSnapshot) -> None  # same revision, fresh full install
    def recover_switch(self, switch: str, snapshot: ConfigSnapshot) -> None
    def dry_install(self, switch: str, rules_text: str) -> GateResult   # gate L3 hook
    def capture(self) -> ConfigSnapshot
    def table_state(self) -> dict
    @property
    def state(self) -> dict
```

## How it works
- **Two mutation surfaces (§10.2):** switch tables via `run_cli` (`table_add/modify/delete`) and host namespaces via `run_host` (`tc`, `ip`, `arp`). The P4 is thin; most behavior is rules + host config.
- **Reroute is a 5-part action (§10.1)** under the dual edge identity: install the destination relay's edge-MAC forward FIRST, then repoint s1's edge-MAC entry, then `_set_path` rebinds 10.0.0.100 to the path's edge interface and re-arps the drones. Ordered so a mid-failure leaves the working old path (a superset), never a blackhole.
- **Active path** is picked from `policy_type`: a binding whose `policy_type` contains `"backup"` (or whose s1 carries the backup edge MAC but not the primary) installs the backup identity.
- **Handle tracking (§10.5):** `_install_rules` checks the count of parsed `Entry has been added with handle N` lines equals the number of `table_add` lines, raising `DeployError` otherwise.
- **`recover_switch` and `rollback`/`_restore_tables` diff by `(table, KEY)` — NOT handle** — because a switch restart re-numbers every handle; a handle-based diff would churn (delete-all + add-all, racy). Rollback restores *semantics* (key/action/args), not handle numbers; re-added entries get fresh handles, which the deployer re-parses.
- **`dry_install`** is the gate's L3 hook: applies `rules_text` live, scans for errors, then rolls back — catches real DUPLICATE_ENTRY / handle-drift the simulation can't.

## Gotchas & lessons
- **Operational lesson:** the testbed must be launched with the P4 program matching what you deploy. Because the deployer installs rules over a fixed program, a mismatch surfaces as `N table_add lines but N-1 handles` (BMv2 rejects a re-add of an existing key with DUPLICATE_ENTRY and emits no handle).
- `deploy` is idempotent: it `_reset_switch`-es residual entries first, because `launch_network.py` may already have populated the tables on the resident network.
- `simple_switch_CLI` exits 0 even when one line in a batch fails (printing the error to stdout); `apply(REGEN)` scans stdout and raises so the engine rolls back rather than committing a partially-applied config.
- `_set_path` uses `ip addr replace` (not `add`) — `add` raises "File exists" and aborts mid-sequence, leaving stale routes/ARP.

## Usage
In-loop only — not a CLI. Driven via `runtime.tools.run_from_planner --deploy` (see usage). Network ownership (§10.6): the deployer drives a *resident* topology out-of-band; it never launches or tears it down.

## See also
- `../runtime-manager-design.md` §10 (Deployer mechanics): §10.1 reroute, §10.2 mutation surfaces, §10.5 handle tracking, §10.6 network ownership.
- `../usage.md` §1.2 (match the P4 program), §1.4 (run an episode), §1.5 (verify + teardown).
- Sibling: `validation-gate.md` (`dry_install` is its L3), `monitors.md` (observes what the deployer installed), `kg-client.md` (`write_last_good` persists the `ConfigSnapshot`).
