"""Tier-2 regen prompt (design §7.3). TEMPLATE is a verbatim constant —
reported in the paper's methodology and never assembled dynamically beyond
the named slots, so the prompt is reproducible by construction."""
from __future__ import annotations

TEMPLATE = """You are the Tier-2 adaptation step of a network runtime manager.
A service function chain deployment is violating its envelope and the
deterministic fixes (queue tuning, path reroute) are exhausted. Propose
replacement BMv2 table entries for switch {switch}.

VIOLATION: flow={who} metric={metric} severity={severity:.3f}
ENVELOPE: max_latency_ms={max_latency_ms} min_bandwidth_mbps={min_bandwidth_mbps} max_loss_percent={max_loss_percent}
CURRENT STATE: path={path} knobs={knobs}

CURRENT TABLE ENTRIES ({switch}):
{tables}

PREVIOUSLY REJECTED OR FAILED (do not repeat):
{rejected}

RULES: output only table_modify or table_add lines over tables
{tables_allowed}; keep every existing destination reachable; one command
per line; no commentary.
OUTPUT:
"""


def render_tables(entries: list) -> str:
    """Deterministic serialization of TableEntry rows for the prompt."""
    if not entries:
        return "(none)"
    lines = []
    for e in sorted(entries, key=lambda e: (e.table, e.handle)):
        args = ",".join(str(a) for a in e.args)
        lines.append(f"handle={e.handle} {e.table} {e.key} -> {e.action}({args})")
    return "\n".join(lines)


def _fmt_knobs(knobs: dict) -> str:
    """Type-stable knob rendering for reproducibility: an integral value renders
    as an int, else a fixed-precision float — so 50 and 50.0 (which a dict repr
    would print differently) produce the SAME prompt, hence the same candidate."""
    def num(v):
        f = float(v)
        return str(int(f)) if f.is_integer() else f"{f:.3f}"
    return "{" + ", ".join(f"{k}: {num(v)}" for k, v in sorted(knobs.items())) + "}"


def build_prompt(diag, env, state, entries: list, rejected: list,
                 switch: str, tables_allowed) -> str:
    rejected_txt = "\n".join(f"- {r}" for r in rejected) if rejected else "(none)"
    return TEMPLATE.format(
        switch=switch,
        who=diag.who, metric=diag.metric, severity=diag.severity,
        max_latency_ms=env.max_latency_ms,
        min_bandwidth_mbps=env.min_bandwidth_mbps,
        max_loss_percent=env.max_loss_percent,
        path=state["path"],
        knobs=_fmt_knobs(state["knobs"]),
        tables=render_tables(entries),
        rejected=rejected_txt,
        tables_allowed=", ".join(sorted(tables_allowed)),
    )
