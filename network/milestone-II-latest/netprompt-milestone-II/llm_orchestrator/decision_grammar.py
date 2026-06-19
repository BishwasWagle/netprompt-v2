"""GBNF grammar for the orchestrator's decision JSON (constrained decoding).

The fine-tuned model emits an *incomplete* decision (4/6 keys — missing
priority_class/deployment_mode), then rambles past the JSON, so the validator
rejects it and the deterministic fallback takes over. Constraining decoding to this
grammar makes that impossible: the model must emit exactly the six required keys, in
order, each value drawn from its allowed set, and stop at the closing brace.

The grammar is built per-request from the same `orchestration_constraints` the
validator checks, so a grammar-valid decision is validator-valid by construction:
  * selected_sfc + selected_policy are bound as VALID PAIRS (from
    candidate_sfc_policy_set), so the validator's SFC-policy pair check also passes;
  * selected_path / selected_relay are the KG-derived allowed sets;
  * priority_class / deployment_mode are the fixed enums the validator accepts.

Same transformers-cfg GBNF dialect as the M7 Tier-2 regen grammar
(runtime/regen/grammar.py), proven under this venv's transformers-cfg.
"""
from __future__ import annotations

PRIORITY_VALUES = ("critical", "high", "medium", "low")
DEPLOYMENT_MODES = ("single_switch", "multihop")


def _q(value) -> str:
    """A GBNF literal that emits the JSON string "value" (with its quotes)."""
    return r'"\"' + str(value) + r'\""'


def _enum(values) -> str:
    return " | ".join(_q(v) for v in values)


def build_decision_gbnf(input_object: dict) -> str:
    """GBNF for a complete, valid decision object, derived from this request's
    constraints. Raises ValueError if the constraints are too thin to constrain
    (caller falls back to unconstrained decoding)."""
    constraints = input_object.get("orchestration_constraints", {}) or {}
    paths = constraints.get("allowed_paths") or ["primary", "backup"]
    relays = constraints.get("allowed_relays") or []

    pairs, seen = [], set()
    for item in input_object.get("candidate_sfc_policy_set", []) or []:
        key = (item.get("sfc_id"), item.get("policy_type"))
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            pairs.append(key)

    if not pairs or not relays:
        raise ValueError("insufficient constraints (no candidate pairs or no relays)")

    pair_alts = " | ".join(
        f'{_q("selected_sfc")} ws ":" ws {_q(sfc)} ws "," ws '
        f'{_q("selected_policy")} ws ":" ws {_q(pol)}'
        for sfc, pol in pairs
    )

    # NOTE: f-string "{{"/"}}" emit literal "{"/"}" GBNF brace matchers.
    return f'''root ::= ws "{{" ws sfcpolicy ws "," ws kv_path ws "," ws kv_relay ws "," ws kv_prio ws "," ws kv_mode ws "}}"
sfcpolicy ::= {pair_alts}
kv_path  ::= {_q("selected_path")} ws ":" ws ( {_enum(paths)} )
kv_relay ::= {_q("selected_relay")} ws ":" ws ( {_enum(relays)} )
kv_prio  ::= {_q("priority_class")} ws ":" ws ( {_enum(PRIORITY_VALUES)} )
kv_mode  ::= {_q("deployment_mode")} ws ":" ws ( {_enum(DEPLOYMENT_MODES)} )
ws ::= [ \\t\\n]*
'''
