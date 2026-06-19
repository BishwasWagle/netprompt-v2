# Slow-Planner Analytics (`llm_orchestrator/analytics.py`)

**Subsystem:** Slow Planner (outer loop) — the "Results + analytics" hub of the two-loop design.
**One-liner:** Aggregates the Runtime Manager's verdict/escalation history into outcome/tier/headroom stats and a **per-SFC reliability signal** the planner can learn from.

## Responsibility
Read-only consumer of the runtime's KG records (the inner loop *writes* `Verdict`/`EscalationTicket`; this *reads + aggregates* them). It owns the aggregation + reporting, and optionally persists a `PlannerAnalytics` snapshot. It does **not** make planning decisions, write verdicts, or touch the network — it produces the signal a planner (or a human) reads.

## Files
- `analytics.py` — `collect()`, `format_report()`, `_sfc_for()`, `main()` (CLI).

## Interface
- `collect(run_cypher) -> dict` — `run_cypher(query)` returns row-dicts (e.g. `Neo4jContextClient.run_cypher`). Returns `{episodes, window, outcomes, tiers, headroom, escalations{total,by_sfc,by_reason}, per_sfc, attributed}`.
- `format_report(stats) -> str` — the human-readable report.
- `_sfc_for(cid, esc_sfc) -> str | None` — attribute a verdict to its SFC.
- `main()` — CLI: `--neo4j-*`, `--json`, `--write-kg`.

## How it works
- Reads all `Verdict` nodes (correlation_id, outcome, tier_reached, headroom, timestamp) + all `EscalationTicket` nodes (correlation_id, sfc, reason).
- Aggregates: outcome distribution, adapt-tier histogram, commit-headroom mean/min (healthy+marginal), escalations by SFC and by reason.
- **Per-SFC attribution** (since `Verdict` carries no SFC): prefer a joined `EscalationTicket.sfc` on the same correlation_id, else parse the planner's `plan-<sfc-slug>-<hex>` correlation_id; un-attributable ids (ad-hoc/test runs) are counted but not binned.
- `--write-kg` MERGEs a `PlannerAnalytics {id:'latest'}` node (`updated_by='slow-planner-analytics'`) for downstream consumption.

## Gotchas & lessons
- **The verdict has no SFC** — attribution is best-effort (join or `plan-` id). Ad-hoc correlation_ids (`soak-N`, manual) show up in totals but not the per-SFC table (`attributed N/total`).
- **Read-only except `--write-kg`.** It never alters runtime records.
- The signal is real: on the current fabric it shows `ReliableRelaySFC` escalating 100% (the documented ≤50 ms-bound-vs-~52 ms-backup gap) — exactly what a learning loop should flag.
- **Open next step:** closing the loop — the planner *reading* `PlannerAnalytics` at decision time to bias SFC/field selection — is not yet wired (the orchestrator's RAG history today comes from a results CSV).

## Usage
```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python -m llm_orchestrator.analytics \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123   # report
#   --json       machine-readable
#   --write-kg   persist a PlannerAnalytics node
```

## See also
[../planner-design.md](../planner-design.md) · [../runtime-planner-contracts.md](../runtime-planner-contracts.md) (§2 `Verdict`/`EscalationTicket` contracts) · [kg-client.md](kg-client.md) (the writer side) · [../usage.md](../usage.md) §3.
