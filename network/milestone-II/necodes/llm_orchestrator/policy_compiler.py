from __future__ import annotations

from typing import Any, Dict

from .utils import as_abs_path, format_delay_ms, sanitize_for_json
from .validator import validate_generated_decision

SFC_TO_STEM = {
    "LowLatencyVideoSFC": "low_latency",
    "ReliableRelaySFC": "reliable_relay",
    "EnergyAwareSFC": "energy_aware",
    "BandwidthOptimizedSFC": "bandwidth_optimized",
}

P4_PROGRAM_TO_JSON = {
    "low_latency.p4": "compiled_p4/low_latency.json",
    "reliable_relay.p4": "compiled_p4/reliable_relay.json",
    "energy_aware.p4": "compiled_p4/energy_aware.json",
    "bandwidth_optimized.p4": "compiled_p4/bandwidth_optimized.json",
}

SINGLE_SWITCH_RULES = {
    "LowLatencyVideoSFC": "p4_rules/low_latency_rules.txt",
    "ReliableRelaySFC": "p4_rules/reliable_relay_rules.txt",
    "EnergyAwareSFC": "p4_rules/energy_aware_rules.txt",
    "BandwidthOptimizedSFC": "p4_rules/bandwidth_optimized_rules.txt",
}

MULTIHOP_RULES = {
    "LowLatencyVideoSFC": {
        "access_rules": "p4_multihop_rules/low_latency_s1_rules.txt",
        "primary_relay_rules": "p4_multihop_rules/low_latency_s2_rules.txt",
        "backup_relay_rules": "p4_multihop_rules/low_latency_s3_rules.txt",
    },
    "ReliableRelaySFC": {
        "access_rules": "p4_multihop_rules/reliable_relay_s1_rules.txt",
        "primary_relay_rules": "p4_multihop_rules/reliable_relay_s2_rules.txt",
        "backup_relay_rules": "p4_multihop_rules/reliable_relay_s3_rules.txt",
    },
    "EnergyAwareSFC": {
        "access_rules": "p4_multihop_rules/energy_aware_s1_rules.txt",
        "primary_relay_rules": "p4_multihop_rules/energy_aware_s2_rules.txt",
        "backup_relay_rules": "p4_multihop_rules/energy_aware_s3_rules.txt",
    },
    "BandwidthOptimizedSFC": {
        "access_rules": "p4_multihop_rules/bandwidth_optimized_s1_rules.txt",
        "primary_relay_rules": "p4_multihop_rules/bandwidth_optimized_s2_rules.txt",
        "backup_relay_rules": "p4_multihop_rules/bandwidth_optimized_s3_rules.txt",
    },
}


def find_candidate_action(input_object: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    for action in input_object.get("candidate_sfc_policy_set", []):
        if action.get("sfc_id") == decision.get("selected_sfc") and action.get("policy_type") == decision.get(
            "selected_policy"
        ):
            return action
    raise ValueError(
        f"No candidate action found for SFC={decision.get('selected_sfc')}, policy={decision.get('selected_policy')}"
    )


def infer_p4_json_from_candidate(candidate_action: Dict[str, Any], selected_sfc: str) -> str:
    p4_program = candidate_action.get("p4_program")
    if p4_program in P4_PROGRAM_TO_JSON:
        return P4_PROGRAM_TO_JSON[p4_program]
    if isinstance(p4_program, str) and p4_program.endswith(".json"):
        return p4_program
    stem = SFC_TO_STEM[selected_sfc]
    return f"compiled_p4/{stem}.json"


def compile_llm_decision_to_experiment_config(
    decision: Dict[str, Any],
    input_object: Dict[str, Any],
    netprompt_root: str,
    experiment_type: str = "LLM KG-RAG Dynamic SFC-P4",
) -> Dict[str, Any]:
    validation = validate_generated_decision(input_object, decision)
    if not validation["valid"]:
        raise ValueError(f"Invalid LLM decision: {validation['errors']}")

    selected_sfc = decision["selected_sfc"]
    selected_policy = decision["selected_policy"]
    selected_path = decision["selected_path"]
    selected_relay = decision["selected_relay"]
    deployment_mode = decision["deployment_mode"]

    candidate_action = find_candidate_action(input_object, decision)
    p4_json_relative = infer_p4_json_from_candidate(candidate_action, selected_sfc)
    telemetry = input_object.get("telemetry_context", {})
    mission = input_object.get("mission_context", {})

    deployment: Dict[str, Any] = {
        "netprompt_root": netprompt_root,
        "p4_json": as_abs_path(p4_json_relative, netprompt_root),
    }

    if deployment_mode == "single_switch":
        deployment.update(
            {
                "mode": "single_switch",
                "rule_file": as_abs_path(SINGLE_SWITCH_RULES[selected_sfc], netprompt_root),
                "access_rules": None,
                "relay_rules": None,
                "backup_rules": None,
            }
        )
    elif deployment_mode == "multihop":
        rules = MULTIHOP_RULES[selected_sfc]
        relay_rules = rules["backup_relay_rules"] if selected_path == "backup" else rules["primary_relay_rules"]
        deployment.update(
            {
                "mode": "multihop",
                "rule_file": None,
                "access_rules": as_abs_path(rules["access_rules"], netprompt_root),
                "relay_rules": as_abs_path(relay_rules, netprompt_root),
                "backup_rules": as_abs_path(rules["backup_relay_rules"], netprompt_root),
            }
        )
    else:
        raise ValueError(f"Unsupported deployment_mode: {deployment_mode}")

    config = {
        "experiment_type": experiment_type,
        "orchestrator": "netprompt_qwen_kg_rag_orchestrator",
        "mission_type": mission.get("mission_type"),
        "priority_class": decision.get("priority_class"),
        "selected_sfc": selected_sfc,
        "policy_type": selected_policy,
        "selected_policy": selected_policy,
        "selected_path": selected_path,
        "selected_relay": selected_relay,
        "deployment_mode": deployment_mode,
        "p4_json": deployment["p4_json"],
        "rule_file": deployment.get("rule_file"),
        "access_rules": deployment.get("access_rules"),
        "relay_rules": deployment.get("relay_rules"),
        "backup_rules": deployment.get("backup_rules"),
        "configured_bandwidth_mbps": telemetry.get("configured_bandwidth_mbps"),
        "configured_delay": format_delay_ms(telemetry.get("configured_delay_ms")),
        "configured_delay_ms": telemetry.get("configured_delay_ms"),
        "configured_loss_percent": telemetry.get("configured_loss_percent"),
        "decision": decision,
        "deployment": deployment,
        "topology_summary": input_object.get("topology_context", {}).get("topology_summary", {}),
    }
    return sanitize_for_json(config)
