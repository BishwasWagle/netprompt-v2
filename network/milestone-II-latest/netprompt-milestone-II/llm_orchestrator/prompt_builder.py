from __future__ import annotations

from typing import Any, Dict, List

from .utils import safe_json_dumps, sanitize_for_json

SYSTEM_PROMPT = """
You are NetPrompt, a constrained SDN/P4 orchestration planner for autonomous drone-edge networks.

You must select the best orchestration action using:
1. mission context,
2. telemetry context,
3. topology context from the Neo4j knowledge graph,
4. resource context,
5. candidate SFC/P4 policy set,
6. KG-RAG historical context from previous experiment runs.

Return JSON only.

Rules:
- Do not generate raw P4 code.
- Do not generate shell commands.
- Do not invent SFC names.
- Do not invent policy names.
- Do not invent relay names.
- Select only from the candidate SFC/P4 policy set.
- Select only from allowed relays and allowed paths.
""".strip()


def build_llm_input_object(
    row: Dict[str, Any],
    compact_topology_context: Dict[str, Any],
    candidate_actions: List[Dict[str, Any]],
    historical_context: Dict[str, Any],
    runtime_feedback: Dict[str, Any] = None,
) -> Dict[str, Any]:
    allowed_sfc_ids = sorted({item["sfc_id"] for item in candidate_actions})
    allowed_policy_types = sorted({item["policy_type"] for item in candidate_actions})
    topo_summary = compact_topology_context.get("topology_summary", {})
    allowed_relays = sorted(
        set(
            topo_summary.get("active_relays", [])
            + topo_summary.get("standby_relays", [])
            + topo_summary.get("unavailable_relays", [])
        )
    )

    input_object = {
        "mission_context": {
            "mission_type": row.get("mission_type"),
            "priority": row.get("mission_priority", row.get("priority", "medium")),
        },
        "telemetry_context": {
            "configured_bandwidth_mbps": row.get("configured_bandwidth_mbps"),
            "configured_delay_ms": row.get("configured_delay_ms"),
            "configured_loss_percent": row.get("configured_loss_percent"),
            "last_observed_rtt_avg_ms": row.get("observed_rtt_avg_ms"),
            "last_observed_packet_loss_percent": row.get("observed_packet_loss_percent"),
            "last_observed_throughput_mbps": row.get("observed_throughput_mbps"),
        },
        "topology_context": compact_topology_context,
        "resource_context": {
            "battery_percent": row.get("battery_percent"),
            "edge_compute_status": row.get("edge_compute_status", "available"),
        },
        "candidate_sfc_policy_set": candidate_actions,
        "kg_rag_historical_context": historical_context,
        # The learning-loop signal: per-SFC reliability from the runtime's own outcomes.
        "runtime_feedback": runtime_feedback or {},
        "orchestration_constraints": {
            "allowed_sfc_ids": allowed_sfc_ids,
            "allowed_policy_types": allowed_policy_types,
            "allowed_paths": ["primary", "backup"],
            "allowed_relays": allowed_relays,
            "do_not_generate": [
                "raw P4 code",
                "shell commands",
                "unknown SFC names",
                "unknown policy names",
                "unknown relays",
            ],
        },
    }
    return sanitize_for_json(input_object)


def build_inference_prompt(input_object: Dict[str, Any]) -> str:
    return f"""<|system|>
{SYSTEM_PROMPT}
<|user|>
Select the best orchestration action for the following NetPrompt network state.

Input:
{safe_json_dumps(input_object)}
<|assistant|>
"""
