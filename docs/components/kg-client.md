# KG Client (`runtime/kg_client.py`)

**Subsystem:** Runtime Manager (inner loop)
**One-liner:** The runtime's Neo4j read/write boundary — reads strategic bounds, writes runtime records, and translates field ids across the KG naming convention.

## Responsibility
Owns the runtime's entire Neo4j surface. Reads *strategic* state (SFCTemplate bounds, per-field requirements) and writes *runtime* state only (switch status, verdicts, tickets, snapshots). It NEVER touches the SFC library or planner-owned topology. It does not compute envelopes' action space from scratch (that is the runtime-owned registry in `config`), and it does not seed the graph — seeding is a separate tool.

## Files
- `runtime/kg_client.py` — the `KGClient` class, field-id translation, all Cypher.
- `runtime/contracts.py` — `Envelope`, `Verdict`, `EscalationTicket`, `BaselineSnapshot`, `ConfigSnapshot`, `jsonable`.
- `runtime/config.py` — `KG_URI/USER/PASS`, `SFC_ACTION_SPACE`, `DEFAULT_MAX_LOSS_PERCENT`.
- `runtime/tools/seed_kg.py` — separate, non-destructive seeding tool (MERGE; keeps runtime records).

## Interface
```python
class KGClient:
    def __init__(self, driver)                                  # injectable driver
    @classmethod
    def connect(cls, uri=config.KG_URI, user=config.KG_USER, password=config.KG_PASS) -> "KGClient"
    def build_envelope(self, sfc: str, target_field: str) -> Envelope
    def read_field_requirements(self) -> dict                   # {field_id: Envelope (bounds only)}
    def read_last_good(self, sfc: str) -> dict | None
    def write_switch_status(self, status: dict, timestamp: str) -> None
    def write_verdict(self, v: Verdict) -> None
    def write_escalation(self, t: EscalationTicket, timestamp: str) -> None
    def write_baseline(self, b: BaselineSnapshot) -> None
    def write_last_good(self, snap: ConfigSnapshot, timestamp: str) -> None
    def close(self) -> None
```

## How it works
- **`connect()` factory + injectable driver:** tests pass a fake; production lazily imports `neo4j.GraphDatabase` and constructs against the same endpoint/credentials as the milestone-II scripts.
- **`build_envelope(sfc, target_field)`** = strictest bounds (min latency / max bandwidth across the SFCTemplate's and the AgriculturalField's own requirements, both KG-owned) PLUS the runtime-owned action space (`config.SFC_ACTION_SPACE` → legal_tiers/paths/knob_ranges). The planner never authors tiers/paths/knobs.
- **`read_field_requirements()`** returns one bounds-only `Envelope` per field for the monitor's per-flow checks; a field missing a bound is SKIPPED rather than built with `None` bounds (which would crash the monitor deep in `compute_flow_metrics`).
- **Writes** persist `Verdict`, `EscalationTicket`, `BaselineSnapshot`, `LastKnownGood`, and monitor switch status. Complex payloads (traces, envelopes, snapshots) are stored as JSON-string properties via `contracts.jsonable` — Neo4j properties cannot hold nested maps. Every write tags `updated_by='runtime-manager'`.
- **Idempotent writes:** Verdict/EscalationTicket MERGE on `(correlation_id, timestamp)`; BaselineSnapshot/LastKnownGood MERGE on their key — so a best-effort retry after a transient error doesn't duplicate.

## Gotchas & lessons
- **Field-id translation at the boundary:** the runtime uses `F<n>`; the KG's `AgriculturalField` nodes use `Field_<n>`. `_to_kg_field`/`_from_kg_field` translate; an id already in the matching form passes through unchanged.
- `read_last_good` is keyed by SFC NAME only (two deployments of the same SFC share one slot) and returns a RAW DICT because `jsonable` is one-way (tuples→lists, frozensets→lists). It is not consumed by the live loop today — the deployer holds its own `ConfigSnapshot` last-good.
- Switch-status writes replace the hardcoded `SCENARIO_STATE` writes of `update_topology_state.py`.

## Usage
Mostly in-loop (driver constructed via `KGClient.connect()`, exercised through `run_from_planner --deploy`). The one direct CLI is the separate seeding tool:
```bash
cd ~/Run-time-Manager && ~/netprompt-venv/bin/python -m runtime.tools.seed_kg   # MERGE, non-destructive
```

## See also
- `../runtime-manager-design.md` §8 (Data Model & KG Integration), §9 (the planner authors no tiers/paths/knobs), §5.1 (field requirements).
- `../usage.md` §1.1 (seed the KG), §1.5 (KG records under the run's correlation_id).
- Sibling: `monitors.md` (`read_field_requirements`, `write_switch_status`, `write_baseline`), `validation-gate.md` (`build_envelope` feeds the gate's `Envelope`), `deployer.md` (`write_last_good` persists its `ConfigSnapshot`).
