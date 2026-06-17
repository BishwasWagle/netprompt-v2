"""Planner → Runtime handoff adapter — the Kiran⇄Kevin boundary (design §1a, the
outer-loop→inner-loop edge; contract: docs/runtime-planner-contracts.md §1).

The LLM orchestrator (`network/.../llm_orchestrator/orchestrate.py`) writes
`outputs/llm_generated_experiment_config.json` and then **stops** — it triggers no
deploy. This module is the runtime-side adapter that turns that artifact into a
normalized `DeploymentSpec` the RuntimeManager can run, **without modifying any
planner code** (the decision kernel — validator / prompt_builder / kg_context /
policy_compiler — stays untouched). Transport is "file"; handoff shape is the
Option-2 "full spec" the planner already emits (§3b).

Two fields the artifact does not carry (contracts §5, still planner-side TBD) are
supplied by the runtime at invocation:

  * ``target_field``  — which field the mission serves (required; drives envelope
                        derivation + the monitor's host_map). The artifact carries
                        ``mission_type`` / ``connected_drone_ids`` but no single
                        field id, so the caller passes it explicitly.
  * ``correlation_id`` — the run id tying deploy → monitor → verdict → escalation;
                         auto-generated here when the caller doesn't supply one.

**Canonical per-switch binding (not the artifact's literal paths).** The deployer
installs all three switches every deploy — s1←``access_rules``, s2←``relay_rules``,
s3←``backup_rules`` (config.SWITCH_RULES_KEYS) — and then selects the active path
from ``policy_type``. So each slot must hold *that switch's* rule file. We therefore
**reconstruct** the four paths from the SFC's canonical names
(``<prefix>_s{1,2,3}_rules.txt`` + ``<prefix>.json``) under ``config.NODE_TREE_ROOT``,
rather than trusting the artifact's literal paths — the orchestrator labels rule
files by the *active relay* (e.g. a backup deployment points ``relay_rules`` at the
s3 file), which would load s2 with s3's rules. The planner's real decisions —
*which SFC* and *which path* — are preserved (SFC name + ``policy_type`` verbatim);
the runtime never re-selects them (design §1.2).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from runtime import config
from runtime.contracts import DeploymentSpec, Envelope

# Planner/KG field ids are ``Field_<n>``; the runtime-internal form (host_map,
# monitor requirement keys) is ``F<n>``. The spec we hand the runtime must use the
# runtime form so the deployer's host_map + the monitor's requirement lookup align.
_KG_FIELD = re.compile(r"^Field_(\d+)$", re.IGNORECASE)


def to_runtime_field(field_id: str) -> str:
    """Normalize a field id to the runtime-internal ``F<n>`` form. Accepts either the
    planner/KG ``Field_<n>`` form or the runtime ``F<n>`` form (idempotent)."""
    m = _KG_FIELD.match(field_id.strip())
    return f"F{m.group(1)}" if m else field_id

# SFC -> canonical rule/JSON file prefix (matches launch_network.py / the on-node
# tree). The per-switch files are `<prefix>_s{1,2,3}_rules.txt`; the P4 program is
# `<prefix>.json`. Same map run_episode.real_binding uses.
SFC_FILE_PREFIX = {
    "LowLatencyVideoSFC": "low_latency",
    "ReliableRelaySFC": "reliable_relay",
    "BandwidthOptimizedSFC": "bandwidth_optimized",
    "EnergyAwareSFC": "energy_aware",
}

# The binding keys the gate (BINDING_KEYS) and deployer (SWITCH_RULES_KEYS +
# policy_type) require. Kept here so a contract drift surfaces as a test failure.
_BINDING_KEYS = ("policy_type", "p4_json", "access_rules", "relay_rules", "backup_rules")

# SFCs the runtime knows how to bind + adapt (the action-space registry is the
# source of truth — an SFC absent here has no envelope and can't be deployed).
KNOWN_SFCS = frozenset(config.SFC_ACTION_SPACE)


def load_artifact(path: str | Path) -> dict:
    """Read + parse the orchestrator's `llm_generated_experiment_config.json`."""
    return json.loads(Path(path).read_text())


def canonical_binding(sfc: str, policy_type: str,
                      tree: str = config.NODE_TREE_ROOT) -> dict:
    """The deployer-correct 5-key binding for `sfc`: per-switch rule files +
    P4 program from the SFC's canonical names under `tree`. `policy_type` is carried
    verbatim — the deployer reads `"...backup..."` out of it to pick the active path
    (deployer.py). Raises ValueError for an SFC with no known rule-file prefix."""
    prefix = SFC_FILE_PREFIX.get(sfc)
    if prefix is None:
        raise ValueError(f"no rule-file prefix for SFC {sfc!r}; "
                         f"known: {sorted(SFC_FILE_PREFIX)}")
    rules = Path(tree, "p4_multihop_rules")
    return {
        "policy_type": policy_type,
        "p4_json":      str(Path(tree, "compiled_p4", f"{prefix}.json")),
        "access_rules": str(rules / f"{prefix}_s1_rules.txt"),     # s1
        "relay_rules":  str(rules / f"{prefix}_s2_rules.txt"),     # s2
        "backup_rules": str(rules / f"{prefix}_s3_rules.txt"),     # s3
    }


def binding_from_artifact(artifact: dict, tree: str = config.NODE_TREE_ROOT) -> dict:
    """The 5-key `binding` the gate + deployer consume, derived from the artifact.

    Takes the planner's *decisions* — the SFC (`selected_sfc`) and the path
    (`policy_type`/`selected_policy`, verbatim) — and reconstructs the canonical
    per-switch binding (see module docstring on why we don't trust the artifact's
    literal rule paths)."""
    sfc = artifact.get("selected_sfc")
    if not sfc:
        raise ValueError("artifact missing selected_sfc")
    policy = artifact.get("policy_type") or artifact.get("selected_policy")
    if not policy:
        raise ValueError("artifact missing policy_type / selected_policy")
    return canonical_binding(sfc, policy, tree)


def new_correlation_id(artifact: dict) -> str:
    """A unique, human-greppable run id when the planner didn't supply one.
    Form: ``plan-<sfc>-<8hex>`` (sfc lower-cased, non-alnum collapsed)."""
    sfc = str(artifact.get("selected_sfc", "sfc"))
    slug = "".join(c.lower() if c.isalnum() else "-" for c in sfc).strip("-") or "sfc"
    return f"plan-{slug}-{uuid.uuid4().hex[:8]}"


def spec_from_artifact(artifact: dict, *, target_field: str,
                       correlation_id: str | None = None,
                       envelope: Envelope | None = None,
                       kg=None,
                       tree: str = config.NODE_TREE_ROOT) -> DeploymentSpec:
    """Normalize a planner artifact into a `DeploymentSpec`.

    Envelope source (exactly one required):
      * ``envelope=`` — an explicit Envelope (offline / dry-run / tests), or
      * ``kg=``       — a KGClient; we call ``kg.build_envelope(sfc, target_field)``
                        (the live path: bounds from the KG, action space runtime-owned).

    The planner already chose the SFC; we never re-select. `target_field` and
    `correlation_id` are runtime-supplied (contracts §5)."""
    sfc = artifact.get("selected_sfc")
    if not sfc:
        raise ValueError("artifact missing selected_sfc")
    if sfc not in KNOWN_SFCS:
        raise ValueError(f"unknown SFC {sfc!r}; known: {sorted(KNOWN_SFCS)}")
    if not target_field:
        raise ValueError("target_field is required (artifact carries no field id)")
    target_field = to_runtime_field(target_field)        # spec is runtime-canonical

    if envelope is None:
        if kg is None:
            raise ValueError("provide either envelope= or kg= to derive the envelope")
        envelope = kg.build_envelope(sfc, target_field)  # build_envelope maps F<n>→Field_<n>

    return DeploymentSpec(
        sfc=sfc,
        binding=binding_from_artifact(artifact, tree),
        envelope=envelope,
        correlation_id=correlation_id or new_correlation_id(artifact),
        target_field=target_field,
    )
