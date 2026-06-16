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

**Path remap.** The artifact's file paths are absolute under the planner's
``netprompt_root`` (e.g. ``/home/cc/netprompt-milestone-II/...``). We remap that
prefix to the runtime's resolved tree root (``config.NODE_TREE_ROOT``) so the same
artifact installs on whatever node runs it.

**`policy_type` is preserved verbatim.** The deployer reads it to choose the active
path (a ``"...backup..."`` policy deploys onto the backup relay — deployer.py). The
runtime never re-selects the SFC, path, or relay; those are the planner's decision,
encoded in the artifact (design §1.2).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from runtime import config
from runtime.contracts import DeploymentSpec, Envelope

# Path components that anchor a rule/JSON file under the netprompt tree, used to
# remap when the artifact carries no explicit `netprompt_root` to strip.
_ANCHORS = ("compiled_p4", "p4_multihop_rules", "p4_single_switch_rules")

# The binding keys the gate (BINDING_KEYS) and deployer (SWITCH_RULES_KEYS +
# policy_type) require. Kept here so a contract drift surfaces as a test failure.
_BINDING_KEYS = ("policy_type", "p4_json", "access_rules", "relay_rules", "backup_rules")

# SFCs the runtime knows how to bind + adapt (the action-space registry is the
# source of truth — an SFC absent here has no envelope and can't be deployed).
KNOWN_SFCS = frozenset(config.SFC_ACTION_SPACE)


def load_artifact(path: str | Path) -> dict:
    """Read + parse the orchestrator's `llm_generated_experiment_config.json`."""
    return json.loads(Path(path).read_text())


def _remap(path: str | None, new_root: str, old_root: str | None) -> str | None:
    """Rebase an artifact path onto `new_root`.

    Prefer stripping the planner's declared `old_root` (`deployment.netprompt_root`);
    fall back to anchoring on a known subdir (`compiled_p4/…`) so a differing or
    absent root still remaps. A path under neither is returned unchanged (it may
    already be relative or intentionally external)."""
    if not path:
        return path
    norm = path.replace("\\", "/")
    if old_root and norm.startswith(old_root.replace("\\", "/").rstrip("/")):
        tail = norm[len(old_root.rstrip("/")):].lstrip("/")
        return str(Path(new_root) / tail)
    parts = norm.split("/")
    for anchor in _ANCHORS:
        if anchor in parts:
            return str(Path(new_root, *parts[parts.index(anchor):]))
    return path


def binding_from_artifact(artifact: dict, tree: str = config.NODE_TREE_ROOT) -> dict:
    """The 5-key `binding` the gate + deployer consume, mapped from the artifact.

    `policy_type` is taken verbatim (drives the deployer's path choice); the four
    file paths are remapped onto `tree`. Raises ValueError if the artifact yields an
    incomplete binding (e.g. a single-switch handoff with null relay/backup rules —
    not yet supported by the multihop deployer)."""
    dep = artifact.get("deployment") or {}
    old_root = dep.get("netprompt_root")

    def pick(key: str):
        # prefer the top-level key, fall back to the `deployment` subdict
        v = artifact.get(key)
        return v if v is not None else dep.get(key)

    policy = artifact.get("policy_type") or artifact.get("selected_policy")
    if not policy:
        raise ValueError("artifact missing policy_type / selected_policy")

    binding = {
        "policy_type": policy,                                    # verbatim
        "p4_json":      _remap(pick("p4_json"), tree, old_root),
        "access_rules": _remap(pick("access_rules"), tree, old_root),
        "relay_rules":  _remap(pick("relay_rules"), tree, old_root),
        "backup_rules": _remap(pick("backup_rules"), tree, old_root),
    }
    missing = [k for k in _BINDING_KEYS if not binding.get(k)]
    if missing:
        raise ValueError(
            f"artifact yields incomplete binding (missing/null {missing}); "
            "single-switch / non-multihop handoffs are not yet supported")
    return binding


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

    if envelope is None:
        if kg is None:
            raise ValueError("provide either envelope= or kg= to derive the envelope")
        envelope = kg.build_envelope(sfc, target_field)

    return DeploymentSpec(
        sfc=sfc,
        binding=binding_from_artifact(artifact, tree),
        envelope=envelope,
        correlation_id=correlation_id or new_correlation_id(artifact),
        target_field=target_field,
    )
