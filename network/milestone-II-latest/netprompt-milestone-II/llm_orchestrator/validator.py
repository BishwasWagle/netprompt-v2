from __future__ import annotations

from typing import Any, Dict, List

REQUIRED_DECISION_KEYS = [
    "selected_sfc",
    "selected_policy",
    "selected_path",
    "selected_relay",
    "priority_class",
    "deployment_mode",
]


def validate_generated_decision(input_object: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    constraints = input_object.get("orchestration_constraints", {})

    for key in REQUIRED_DECISION_KEYS:
        if key not in decision:
            errors.append(f"Missing key: {key}")

    if decision.get("selected_sfc") not in constraints.get("allowed_sfc_ids", []):
        errors.append(f"Invalid selected_sfc: {decision.get('selected_sfc')}")

    if decision.get("selected_policy") not in constraints.get("allowed_policy_types", []):
        errors.append(f"Invalid selected_policy: {decision.get('selected_policy')}")

    if decision.get("selected_path") not in constraints.get("allowed_paths", []):
        errors.append(f"Invalid selected_path: {decision.get('selected_path')}")

    if decision.get("selected_relay") not in constraints.get("allowed_relays", []):
        errors.append(f"Invalid selected_relay: {decision.get('selected_relay')}")

    if decision.get("deployment_mode") not in ["single_switch", "multihop"]:
        errors.append(f"Invalid deployment_mode: {decision.get('deployment_mode')}")

    candidate_pairs = {
        (item.get("sfc_id"), item.get("policy_type"))
        for item in input_object.get("candidate_sfc_policy_set", [])
    }
    pair = (decision.get("selected_sfc"), decision.get("selected_policy"))
    if pair not in candidate_pairs:
        errors.append(f"Invalid SFC-policy pair: {pair}")

    return {"valid": len(errors) == 0, "errors": errors}


def _choose_policy(candidate_actions: List[Dict[str, Any]], sfc_id: str, prefer_alias: bool = False) -> str:
    matches = [a for a in candidate_actions if a.get("sfc_id") == sfc_id]
    if not matches:
        raise ValueError(f"No candidate policy found for {sfc_id}")
    if prefer_alias:
        aliases = [a for a in matches if a.get("alias_of")]
        if aliases:
            return aliases[0]["policy_type"]
    non_aliases = [a for a in matches if not a.get("alias_of")]
    return (non_aliases or matches)[0]["policy_type"]


def _choose_relay(selected_path: str, topology_context: Dict[str, Any]) -> str:
    topo = topology_context.get("topology_summary", {})
    if selected_path == "backup":
        for key in ["backup_relays", "standby_relays", "active_relays"]:
            relays = topo.get(key, [])
            if relays:
                return relays[0]
        return "s3"
    for key in ["primary_relays", "active_relays", "standby_relays"]:
        relays = topo.get(key, [])
        if relays:
            return relays[0]
    return "s2"


def fallback_decision(input_object: Dict[str, Any]) -> Dict[str, Any]:
    """Rule-based fallback used when the LLM output is invalid or model loading is disabled."""
    mission = input_object.get("mission_context", {})
    telemetry = input_object.get("telemetry_context", {})
    resources = input_object.get("resource_context", {})
    topology = input_object.get("topology_context", {})
    candidates = input_object.get("candidate_sfc_policy_set", [])

    mission_type = mission.get("mission_type")
    priority = mission.get("priority", "medium")
    delay = telemetry.get("configured_delay_ms") or 999
    loss = telemetry.get("configured_loss_percent") or 999
    observed_loss = telemetry.get("last_observed_packet_loss_percent")
    observed_loss = loss if observed_loss is None else observed_loss
    bandwidth = telemetry.get("configured_bandwidth_mbps") or 0
    battery = resources.get("battery_percent")
    battery = 100 if battery is None else battery
    unavailable = topology.get("topology_summary", {}).get("unavailable_relays", [])

    if mission_type == "emergency_alert_relay" or float(loss) >= 3 or float(observed_loss) >= 5 or unavailable:
        sfc = "ReliableRelaySFC"
        path = "backup"
        mode = "multihop"
        policy = _choose_policy(candidates, sfc, prefer_alias=False)
    elif mission_type == "long_term_soil_monitoring" or float(battery) < 40:
        sfc = "EnergyAwareSFC"
        path = "primary"
        mode = "single_switch"
        policy = _choose_policy(candidates, sfc, prefer_alias=False)
    elif mission_type == "real_time_pest_detection" or float(delay) <= 10:
        sfc = "LowLatencyVideoSFC"
        path = "primary"
        mode = "multihop"
        policy = _choose_policy(candidates, sfc, prefer_alias=False)
    elif float(bandwidth) >= 30:
        sfc = "BandwidthOptimizedSFC"
        path = "primary"
        mode = "single_switch"
        policy = _choose_policy(candidates, sfc, prefer_alias=False)
    else:
        sfc = "ReliableRelaySFC"
        path = "backup"
        mode = "multihop"
        policy = _choose_policy(candidates, sfc, prefer_alias=False)

    return {
        "selected_sfc": sfc,
        "selected_policy": policy,
        "selected_path": path,
        "selected_relay": _choose_relay(path, topology),
        "priority_class": priority,
        "deployment_mode": mode,
    }
