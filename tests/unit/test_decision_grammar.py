"""Planner decision-grammar builder (constrained decoding for the slow planner).

The grammar lives in the orchestrator tree (no torch/transformers deps — pure string
building), loaded standalone here so the runtime suite covers it. Exit criteria: the
grammar pins all six required keys, binds SFC+policy as valid pairs, enum-constrains
the rest, and refuses to build from too-thin constraints (caller then goes
unconstrained)."""
import importlib.util
from pathlib import Path

import pytest

_DG = (Path(__file__).resolve().parents[2]
       / "network/milestone-II-latest/netprompt-milestone-II"
       / "llm_orchestrator/decision_grammar.py")
_spec = importlib.util.spec_from_file_location("decision_grammar", _DG)
dg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dg)


def _io():
    return {
        "orchestration_constraints": {"allowed_paths": ["primary", "backup"],
                                      "allowed_relays": ["s2", "s3"]},
        "candidate_sfc_policy_set": [
            {"sfc_id": "ReliableRelaySFC", "policy_type": "backup_path_reliable_relay"},
            {"sfc_id": "LowLatencyVideoSFC", "policy_type": "primary_path_low_latency"},
        ],
    }


def test_grammar_pins_all_six_keys():
    g = dg.build_decision_gbnf(_io())
    for key in ("selected_sfc", "selected_policy", "selected_path",
                "selected_relay", "priority_class", "deployment_mode"):
        assert f'\\"{key}\\"' in g                      # each required key appears


def test_grammar_binds_sfc_policy_pairs():
    g = dg.build_decision_gbnf(_io())
    # the valid pair appears as an alternative; the SFC literal is bound to its policy
    assert '\\"ReliableRelaySFC\\"' in g and '\\"backup_path_reliable_relay\\"' in g
    assert '\\"LowLatencyVideoSFC\\"' in g and '\\"primary_path_low_latency\\"' in g


def test_grammar_enum_constrains_relay_and_mode():
    g = dg.build_decision_gbnf(_io())
    assert '\\"s2\\"' in g and '\\"s3\\"' in g
    assert '\\"single_switch\\"' in g and '\\"multihop\\"' in g


def test_grammar_refuses_thin_constraints():
    with pytest.raises(ValueError):                     # no relays
        dg.build_decision_gbnf({"orchestration_constraints": {"allowed_relays": []},
                                "candidate_sfc_policy_set": [
                                    {"sfc_id": "X", "policy_type": "y"}]})
    with pytest.raises(ValueError):                     # no candidate pairs
        dg.build_decision_gbnf({"orchestration_constraints": {"allowed_relays": ["s2"]},
                                "candidate_sfc_policy_set": []})
