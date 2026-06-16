"""Drive a Runtime Manager episode from a planner artifact (the file-transport
handoff). Thin CLI over `runtime.planner_adapter`.

Default is a **dry** run: load the orchestrator's
`llm_generated_experiment_config.json`, normalize it into a `DeploymentSpec`,
derive the envelope (from the KG, or `--no-kg` for a structural offline check),
and run the gate's pre-deploy binding check — **no testbed, no deploy**. This is
the offline verification of the seam.

    # offline, no KG, no testbed — just prove the artifact normalizes + gates
    python3 -m runtime.tools.run_from_planner \
        --artifact network/.../outputs/llm_generated_experiment_config.json \
        --target-field Field_2 --no-kg

    # live envelope from the KG (still no deploy)
    python3 -m runtime.tools.run_from_planner --artifact <path> --target-field Field_2

`--deploy` (live, needs the resident testbed) is intentionally a separate flag and
the follow-up slice — this tool's default never touches the network.
"""
from __future__ import annotations

import argparse
import json

from runtime import config
from runtime.contracts import Envelope
from runtime.gate import ValidationGate
from runtime.planner_adapter import load_artifact, spec_from_artifact


def _offline_envelope(sfc: str) -> Envelope:
    """A structural envelope for `--no-kg` dry checks: the runtime-owned action
    space (config) with placeholder positive bounds. Bounds are NOT authoritative
    here — the live path derives them from the KG (kg_client.build_envelope)."""
    space = config.SFC_ACTION_SPACE.get(sfc, {})
    return Envelope(max_latency_ms=50, min_bandwidth_mbps=20, max_loss_percent=2,
                    legal_tiers=frozenset(space.get("legal_tiers", ())),
                    legal_paths=frozenset(space.get("legal_paths", ("primary",))),
                    knob_ranges=dict(space.get("knob_ranges", {})))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifact", required=True,
                    help="path to llm_generated_experiment_config.json")
    ap.add_argument("--target-field", required=True,
                    help="field the mission serves, e.g. Field_2 (runtime-supplied)")
    ap.add_argument("--correlation-id", default=None,
                    help="run id; auto-generated if omitted")
    ap.add_argument("--tree", default=config.NODE_TREE_ROOT,
                    help="runtime tree root the artifact paths are rebased onto")
    ap.add_argument("--no-kg", action="store_true",
                    help="skip the KG; use a structural offline envelope (bounds "
                         "are placeholders, action space is real)")
    args = ap.parse_args()

    artifact = load_artifact(args.artifact)

    if args.no_kg:
        env = _offline_envelope(artifact.get("selected_sfc", ""))
        spec = spec_from_artifact(artifact, target_field=args.target_field,
                                  correlation_id=args.correlation_id,
                                  envelope=env, tree=args.tree)
        env_src = "offline (structural; bounds are placeholders)"
    else:
        from runtime.kg_client import KGClient
        kg = KGClient.connect()
        try:
            spec = spec_from_artifact(artifact, target_field=args.target_field,
                                      correlation_id=args.correlation_id,
                                      kg=kg, tree=args.tree)
        finally:
            kg.close()
        env_src = "KG (kg_client.build_envelope)"

    gate = ValidationGate().check_binding(spec)

    print("planner artifact :", args.artifact)
    print("selected_sfc     :", spec.sfc)
    print("target_field     :", spec.target_field, "(runtime-supplied)")
    print("correlation_id   :", spec.correlation_id)
    print("policy_type      :", spec.binding["policy_type"])
    print("envelope source  :", env_src)
    print("envelope         : lat<=%.1fms bw>=%.1fmbps loss<=%.1f%% tiers=%s paths=%s"
          % (spec.envelope.max_latency_ms, spec.envelope.min_bandwidth_mbps,
             spec.envelope.max_loss_percent, sorted(spec.envelope.legal_tiers),
             sorted(spec.envelope.legal_paths)))
    print("binding          :", json.dumps(spec.binding, indent=2))
    print("gate.check_binding:", "PASS" if gate.ok else f"REJECT — {gate.reason}")
    if not gate.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
