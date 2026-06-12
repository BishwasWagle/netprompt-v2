from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from .utils import parse_delay_ms, sanitize_for_json

MISSION_MAP = {
    "low_latency": "real_time_pest_detection",
    "baseline": "general_field_survey",
    "congestion": "emergency_alert_relay",
    "relay_failure": "emergency_alert_relay",
    "battery_depletion": "long_term_soil_monitoring",
    "ddil": "emergency_alert_relay",
}

PRIORITY_MAP = {
    "real_time_pest_detection": "high",
    "general_field_survey": "medium",
    "emergency_alert_relay": "critical",
    "long_term_soil_monitoring": "low",
}


def infer_relay_status_from_scenario(scenario: str) -> str:
    if scenario == "relay_failure":
        return "failed"
    if scenario in ["congestion", "ddil"]:
        return "degraded"
    return "healthy"


def infer_selected_path_from_policy(policy_type: Any) -> str:
    text = str(policy_type).lower()
    if "backup" in text or "relay" in text:
        return "backup"
    return "primary"


def normalize_results_row(row: pd.Series | Dict[str, Any]) -> Dict[str, Any]:
    scenario = row.get("scenario", "unknown")
    mission_type = MISSION_MAP.get(scenario, "unknown_mission")
    mission_priority = PRIORITY_MAP.get(mission_type, "medium")
    selected_policy = row.get("policy_type")

    return sanitize_for_json(
        {
            "source_file": row.get("source_file"),
            "scenario_label": scenario,
            "mission_type": mission_type,
            "mission_priority": mission_priority,
            "configured_bandwidth_mbps": row.get("configured_bandwidth_mbps"),
            "configured_delay_ms": parse_delay_ms(row.get("configured_delay")),
            "configured_loss_percent": row.get("configured_loss_percent"),
            "observed_rtt_avg_ms": row.get("rtt_avg_ms"),
            "observed_packet_loss_percent": row.get("measured_packet_loss_percent"),
            "observed_throughput_mbps": row.get("throughput_mbps"),
            "relay_status": infer_relay_status_from_scenario(scenario),
            "selected_sfc": row.get("selected_sfc"),
            "selected_policy": selected_policy,
            "selected_path": infer_selected_path_from_policy(selected_policy),
            "p4_json": row.get("p4_json"),
            "rule_file": row.get("rule_file"),
            "access_rules": row.get("access_rules"),
            "relay_rules": row.get("relay_rules"),
            "backup_rules": row.get("backup_rules"),
            "experiment_type": row.get("experiment_type"),
        }
    )


def load_and_normalize_results(results_csv: Optional[str]) -> pd.DataFrame:
    if not results_csv:
        return pd.DataFrame()
    try:
        raw_df = pd.read_csv(results_csv)
    except FileNotFoundError:
        return pd.DataFrame()
    records = [normalize_results_row(row) for _, row in raw_df.iterrows()]
    return pd.DataFrame(records)


def infer_live_relay_status(topology_context: Dict[str, Any]) -> str:
    topo = topology_context.get("topology_summary", {})
    if topo.get("unavailable_relays"):
        return "failed"
    if not topo.get("active_relays") and topo.get("standby_relays"):
        return "degraded"
    return "healthy"


def build_live_current_row(
    mission_type: str,
    priority: str,
    bandwidth: Optional[float],
    delay_ms: Optional[float],
    loss_percent: Optional[float],
    battery_percent: Optional[float],
    topology_context: Dict[str, Any],
    observed_rtt_ms: Optional[float] = None,
    observed_loss_percent: Optional[float] = None,
    observed_throughput_mbps: Optional[float] = None,
) -> Dict[str, Any]:
    return sanitize_for_json(
        {
            "source_file": None,
            "scenario_label": "live_request",
            "mission_type": mission_type,
            "mission_priority": priority,
            "configured_bandwidth_mbps": bandwidth,
            "configured_delay_ms": delay_ms,
            "configured_loss_percent": loss_percent,
            "observed_rtt_avg_ms": observed_rtt_ms,
            "observed_packet_loss_percent": observed_loss_percent,
            "observed_throughput_mbps": observed_throughput_mbps,
            "relay_status": infer_live_relay_status(topology_context),
            "battery_percent": battery_percent,
        }
    )


def build_historical_context(current_row: Dict[str, Any], history_df: pd.DataFrame, top_k: int = 3) -> Dict[str, Any]:
    if history_df is None or history_df.empty:
        return {
            "similar_prior_runs": [],
            "historical_best_sfc": None,
            "historical_best_policy": None,
            "historical_best_path": None,
            "historical_success_rate": None,
        }

    history = history_df.copy()
    if current_row.get("source_file") and "source_file" in history.columns:
        history = history[history["source_file"] != current_row["source_file"]]

    if history.empty:
        return {
            "similar_prior_runs": [],
            "historical_best_sfc": None,
            "historical_best_policy": None,
            "historical_best_path": None,
            "historical_success_rate": None,
        }

    history["similarity_score"] = 0
    if "mission_type" in history.columns:
        history.loc[history["mission_type"] == current_row.get("mission_type"), "similarity_score"] += 2
    if "relay_status" in history.columns:
        history.loc[history["relay_status"] == current_row.get("relay_status"), "similarity_score"] += 2
    if current_row.get("selected_path") is not None and "selected_path" in history.columns:
        history.loc[history["selected_path"] == current_row.get("selected_path"), "similarity_score"] += 1

    for col in ["observed_packet_loss_percent", "observed_rtt_avg_ms", "observed_throughput_mbps"]:
        if col in history.columns:
            history[col] = pd.to_numeric(history[col], errors="coerce")

    history["success"] = history.get("observed_packet_loss_percent", pd.Series([100] * len(history))).fillna(100) < 5

    ranked = history.sort_values(
        by=["similarity_score", "success", "observed_packet_loss_percent", "observed_rtt_avg_ms"],
        ascending=[False, False, True, True],
        na_position="last",
    )
    top = ranked.head(top_k)
    if top.empty:
        return {
            "similar_prior_runs": [],
            "historical_best_sfc": None,
            "historical_best_policy": None,
            "historical_best_path": None,
            "historical_success_rate": None,
        }

    best = top.iloc[0]
    columns = [
        "mission_type",
        "relay_status",
        "selected_sfc",
        "selected_policy",
        "selected_path",
        "observed_rtt_avg_ms",
        "observed_packet_loss_percent",
        "observed_throughput_mbps",
        "success",
    ]
    available_columns = [col for col in columns if col in top.columns]

    return sanitize_for_json(
        {
            "similar_prior_runs": top[available_columns].to_dict(orient="records"),
            "historical_best_sfc": best.get("selected_sfc"),
            "historical_best_policy": best.get("selected_policy"),
            "historical_best_path": best.get("selected_path"),
            "historical_success_rate": round(float(top["success"].mean()), 2),
        }
    )
