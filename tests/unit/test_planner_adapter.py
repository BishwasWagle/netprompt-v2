"""Planner→Runtime handoff adapter (docs/runtime-planner-contracts.md §1).

Exit criteria: the orchestrator's `llm_generated_experiment_config.json` normalizes
into a `DeploymentSpec` that passes the gate's binding check, with file paths
rebased onto the runtime tree, the planner's `policy_type` preserved verbatim (so
the deployer keeps the chosen path), and the two runtime-supplied fields
(`target_field`, `correlation_id`) threaded through — without touching planner code.
"""
import json
from pathlib import Path

import pytest

from runtime import config
from runtime.contracts import Envelope
from runtime.gate import ValidationGate
from runtime.planner_adapter import (
    binding_from_artifact, load_artifact, new_correlation_id, spec_from_artifact,
    to_runtime_field,
)

TREE = "/opt/node/netprompt"          # a stand-in runtime tree root for assertions
PLANNER_ROOT = "/home/cc/netprompt-milestone-II"

# The real committed artifact the orchestrator emits.
REAL_ARTIFACT = (Path(__file__).resolve().parents[2]
                 / "network/milestone-II-latest/netprompt-milestone-II"
                 / "outputs/llm_generated_experiment_config.json")


def _artifact(**over):
    """A minimal multihop artifact shaped like the orchestrator's output."""
    a = {
        "selected_sfc": "ReliableRelaySFC",
        "policy_type": "backup_path_reliable_relay",
        "selected_policy": "backup_path_reliable_relay",
        "selected_path": "backup",
        "p4_json": f"{PLANNER_ROOT}/compiled_p4/reliable_relay.json",
        "access_rules": f"{PLANNER_ROOT}/p4_multihop_rules/reliable_relay_s1_rules.txt",
        # the orchestrator labels rule files by the ACTIVE relay — a backup
        # deployment points relay_rules at the s3 file (the quirk D4' normalizes).
        "relay_rules": f"{PLANNER_ROOT}/p4_multihop_rules/reliable_relay_s3_rules.txt",
        "backup_rules": f"{PLANNER_ROOT}/p4_multihop_rules/reliable_relay_s3_rules.txt",
        "deployment": {"netprompt_root": PLANNER_ROOT},
    }
    a.update(over)
    return a


def _envelope(sfc="ReliableRelaySFC"):
    """An explicit Envelope (the offline path; live derives it from the KG)."""
    space = config.SFC_ACTION_SPACE[sfc]
    return Envelope(max_latency_ms=50, min_bandwidth_mbps=20, max_loss_percent=2,
                    legal_tiers=space["legal_tiers"], legal_paths=space["legal_paths"],
                    knob_ranges=dict(space["knob_ranges"]))


# ---------------- binding ----------------

def test_binding_is_canonical_per_switch():
    # s1←access, s2←relay, s3←backup — each slot holds THAT switch's file, under tree
    b = binding_from_artifact(_artifact(), tree=TREE)
    assert b["p4_json"] == f"{TREE}/compiled_p4/reliable_relay.json"
    assert b["access_rules"] == f"{TREE}/p4_multihop_rules/reliable_relay_s1_rules.txt"
    assert b["relay_rules"] == f"{TREE}/p4_multihop_rules/reliable_relay_s2_rules.txt"
    assert b["backup_rules"] == f"{TREE}/p4_multihop_rules/reliable_relay_s3_rules.txt"


def test_binding_ignores_artifact_literal_relay_path():
    # the artifact's relay_rules points at the s3 file (active-relay labelling);
    # the binding must still put the s2 file in the s2 slot (the D4' fix).
    art = _artifact()
    assert art["relay_rules"].endswith("reliable_relay_s3_rules.txt")     # quirk in
    b = binding_from_artifact(art, tree=TREE)
    assert b["relay_rules"].endswith("reliable_relay_s2_rules.txt")       # fixed out


def test_binding_preserves_policy_type_verbatim():
    # the deployer reads 'backup' out of policy_type to pick the active path
    b = binding_from_artifact(_artifact(), tree=TREE)
    assert b["policy_type"] == "backup_path_reliable_relay"
    assert "backup" in b["policy_type"].lower()


def test_binding_has_exactly_the_gate_keys():
    b = binding_from_artifact(_artifact(), tree=TREE)
    from runtime.gate import BINDING_KEYS
    assert BINDING_KEYS <= set(b)


def test_unknown_sfc_has_no_prefix():
    with pytest.raises(ValueError, match="no rule-file prefix"):
        binding_from_artifact(_artifact(selected_sfc="MagicSFC"), tree=TREE)


def test_missing_policy_raises():
    art = _artifact()
    del art["policy_type"], art["selected_policy"]
    with pytest.raises(ValueError, match="policy_type"):
        binding_from_artifact(art, tree=TREE)


# ---------------- spec ----------------

def test_to_runtime_field_normalizes_both_forms():
    assert to_runtime_field("Field_2") == "F2"     # planner/KG form -> runtime form
    assert to_runtime_field("F2") == "F2"           # already runtime form (idempotent)
    assert to_runtime_field("Field_10") == "F10"


def test_spec_passes_gate_binding_check():
    spec = spec_from_artifact(_artifact(), target_field="Field_2",
                              envelope=_envelope(), tree=TREE)
    assert spec.sfc == "ReliableRelaySFC"
    assert spec.target_field == "F2"               # normalized to runtime form
    assert ValidationGate().check_binding(spec).ok


def test_correlation_id_autogenerated_and_unique():
    s1 = spec_from_artifact(_artifact(), target_field="Field_2",
                            envelope=_envelope(), tree=TREE)
    s2 = spec_from_artifact(_artifact(), target_field="Field_2",
                            envelope=_envelope(), tree=TREE)
    assert s1.correlation_id.startswith("plan-reliablerelaysfc-")
    assert s1.correlation_id != s2.correlation_id          # uuid suffix


def test_correlation_id_respected_when_supplied():
    spec = spec_from_artifact(_artifact(), target_field="Field_2",
                              correlation_id="run-42", envelope=_envelope(), tree=TREE)
    assert spec.correlation_id == "run-42"


def test_unknown_sfc_rejected():
    with pytest.raises(ValueError, match="unknown SFC"):
        spec_from_artifact(_artifact(selected_sfc="MagicSFC"),
                           target_field="Field_2", envelope=_envelope(), tree=TREE)


def test_missing_sfc_rejected():
    art = _artifact()
    del art["selected_sfc"]
    with pytest.raises(ValueError, match="selected_sfc"):
        spec_from_artifact(art, target_field="Field_2", envelope=_envelope(), tree=TREE)


def test_target_field_required():
    with pytest.raises(ValueError, match="target_field"):
        spec_from_artifact(_artifact(), target_field="", envelope=_envelope(), tree=TREE)


def test_envelope_or_kg_required():
    with pytest.raises(ValueError, match="envelope= or kg="):
        spec_from_artifact(_artifact(), target_field="Field_2", tree=TREE)


def test_kg_path_calls_build_envelope():
    class FakeKG:
        def __init__(self): self.seen = None
        def build_envelope(self, sfc, field):
            self.seen = (sfc, field)
            return _envelope(sfc)
    kg = FakeKG()
    spec = spec_from_artifact(_artifact(), target_field="Field_2", kg=kg, tree=TREE)
    assert kg.seen == ("ReliableRelaySFC", "F2")           # normalized, then delegated
    assert ValidationGate().check_binding(spec).ok


# ---------------- the real artifact on disk ----------------

def test_real_orchestrator_artifact_normalizes():
    assert REAL_ARTIFACT.exists(), f"missing planner artifact: {REAL_ARTIFACT}"
    art = load_artifact(REAL_ARTIFACT)
    spec = spec_from_artifact(art, target_field="Field_2",
                              envelope=_envelope(), tree=TREE)
    assert spec.sfc == "ReliableRelaySFC"
    # the planner's broken root is gone; everything is canonical under the tree
    assert spec.binding["access_rules"] == f"{TREE}/p4_multihop_rules/reliable_relay_s1_rules.txt"
    # the real artifact's relay_rules is the s3 file; we install the s2 file on s2
    assert spec.binding["relay_rules"].endswith("reliable_relay_s2_rules.txt")
    assert spec.binding["backup_rules"].endswith("reliable_relay_s3_rules.txt")
    assert spec.binding["policy_type"] == "backup_path_reliable_relay"
    assert ValidationGate().check_binding(spec).ok
