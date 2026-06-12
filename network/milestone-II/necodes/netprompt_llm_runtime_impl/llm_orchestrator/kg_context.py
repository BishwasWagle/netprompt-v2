from __future__ import annotations

from typing import Any, Dict, List, Optional

from .utils import normalize_text

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - allows importing the package without neo4j installed
    GraphDatabase = None


class Neo4jContextClient:
    """Small wrapper around Neo4j queries used by the NetPrompt LLM runtime."""

    def __init__(self, uri: str, user: str, password: Optional[str]):
        if GraphDatabase is None:
            raise ImportError("neo4j package is not installed. Run: pip install neo4j")
        if not password:
            raise ValueError("Neo4j password is missing. Set NEO4J_PASSWORD or pass it to RuntimeConfig.")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def run_cypher(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result]

    def get_topology_snapshot(self) -> Dict[str, Any]:
        switch_query = """
        MATCH (s:ProgrammableSwitch)
        RETURN
            properties(s).id AS id,
            properties(s).role AS role,
            coalesce(properties(s).status, "unknown") AS status,
            coalesce(properties(s).thrift_port, "unknown") AS thrift_port,
            coalesce(properties(s).device_id, "unknown") AS device_id
        ORDER BY properties(s).id
        """

        # Avoid deprecated id() by requiring id/name properties where possible.
        # Unknown source/target labels are still returned as 'unknown-node'.
        link_query = """
        MATCH (a)-[r]->(b)
        WHERE
            a:ProgrammableSwitch
            OR b:ProgrammableSwitch
            OR a:Drone
            OR b:Drone
            OR type(r) IN [
                "CONNECTED_TO", "HOSTS_SWITCH", "PRIMARY_PATH", "BACKUP_PATH",
                "CONNECTED_TO_EDGE", "STARTS_AT", "SELECTED_RELAY", "CONTAINS_SWITCH"
            ]
        RETURN
            coalesce(properties(a).id, properties(a).name, "unknown-node") AS source,
            labels(a) AS source_labels,
            type(r) AS relation,
            coalesce(properties(b).id, properties(b).name, "unknown-node") AS target,
            labels(b) AS target_labels,
            coalesce(properties(r).status, "active") AS status,
            coalesce(properties(r).bandwidth_mbps, "unknown") AS bandwidth_mbps,
            coalesce(properties(r).delay_ms, properties(r).delay, "unknown") AS delay_ms,
            coalesce(properties(r).loss_percent, properties(r).loss, "unknown") AS loss_percent
        LIMIT 300
        """

        path_query = """
        MATCH (pd:PathDecision)-[:SELECTED_RELAY]->(sw:ProgrammableSwitch)
        RETURN
            properties(pd).scenario AS scenario,
            properties(pd).selected_path AS selected_path,
            properties(pd).reason AS reason,
            properties(sw).id AS selected_relay,
            coalesce(properties(sw).status, "unknown") AS relay_status
        ORDER BY properties(pd).scenario
        """

        switches = self.run_cypher(switch_query)
        links = self.run_cypher(link_query)
        path_decisions = self.run_cypher(path_query)

        normalized_switches = []
        for s in switches:
            role_norm = normalize_text(s.get("role"))
            status_norm = normalize_text(s.get("status"))
            switch_type = "unknown"
            if "access" in role_norm:
                switch_type = "access_switch"
            elif "primary" in role_norm and "relay" in role_norm:
                switch_type = "primary_relay"
            elif "backup" in role_norm and "relay" in role_norm:
                switch_type = "backup_relay"
            elif "relay" in role_norm:
                switch_type = "relay"

            normalized_switches.append(
                {
                    "id": s.get("id"),
                    "role": s.get("role"),
                    "status": s.get("status"),
                    "role_normalized": role_norm,
                    "status_normalized": status_norm,
                    "switch_type": switch_type,
                    "thrift_port": s.get("thrift_port"),
                    "device_id": s.get("device_id"),
                }
            )

        access_switches = [s["id"] for s in normalized_switches if s["switch_type"] == "access_switch"]
        primary_relays = [s["id"] for s in normalized_switches if s["switch_type"] == "primary_relay"]
        backup_relays = [s["id"] for s in normalized_switches if s["switch_type"] == "backup_relay"]
        active_relays = [
            s["id"]
            for s in normalized_switches
            if s["switch_type"] in ["primary_relay", "backup_relay", "relay"]
            and s["status_normalized"] in ["active", "healthy", "online"]
        ]
        standby_relays = [
            s["id"]
            for s in normalized_switches
            if s["switch_type"] in ["primary_relay", "backup_relay", "relay"]
            and s["status_normalized"] in ["standby", "backup", "ready"]
        ]
        unavailable_relays = [
            s["id"]
            for s in normalized_switches
            if s["switch_type"] in ["primary_relay", "backup_relay", "relay"]
            and s["status_normalized"] in ["failed", "offline", "degraded", "down"]
        ]

        forwarding_links = [
            link for link in links if link["relation"] in ["PRIMARY_PATH", "BACKUP_PATH", "CONNECTED_TO_EDGE"]
        ]
        drone_access_links = [
            link for link in links if link["relation"] == "CONNECTED_TO" and link["target"] in access_switches
        ]
        primary_path = [link for link in forwarding_links if link["relation"] == "PRIMARY_PATH"]
        backup_path = [link for link in forwarding_links if link["relation"] == "BACKUP_PATH"]
        edge_links = [link for link in forwarding_links if link["relation"] == "CONNECTED_TO_EDGE"]

        normalized_path_decisions = []
        for pd in path_decisions:
            selected_path_raw = pd.get("selected_path")
            selected_path_norm = normalize_text(selected_path_raw)
            if "backup" in selected_path_norm:
                selected_path = "backup"
            elif "primary" in selected_path_norm:
                selected_path = "primary"
            else:
                selected_path = selected_path_norm
            normalized_path_decisions.append(
                {
                    "scenario": pd.get("scenario"),
                    "selected_path": selected_path,
                    "selected_path_raw": selected_path_raw,
                    "selected_relay": pd.get("selected_relay"),
                    "relay_status": pd.get("relay_status"),
                    "reason": pd.get("reason"),
                }
            )

        return {
            "switches": normalized_switches,
            "topology_summary": {
                "num_switches": len(normalized_switches),
                "num_links": len(links),
                "access_switches": access_switches,
                "primary_relays": primary_relays,
                "backup_relays": backup_relays,
                "active_relays": active_relays,
                "standby_relays": standby_relays,
                "unavailable_relays": unavailable_relays,
            },
            "forwarding_topology": {
                "drone_access_links": drone_access_links,
                "primary_path": primary_path,
                "backup_path": backup_path,
                "edge_links": edge_links,
            },
            "path_decisions": normalized_path_decisions,
            "raw_links": links,
        }

    def get_candidate_sfc_policy_set(self) -> List[Dict[str, Any]]:
        query = """
        MATCH (s:SFCTemplate)-[:REALIZED_BY_P4_POLICY]->(p:P4PolicyMapping)
        RETURN
            properties(s).id AS sfc_id,
            coalesce(properties(s).optimization_goal, "unknown") AS optimization_goal,
            properties(p).policy_type AS policy_type,
            properties(p).p4_program AS p4_program,
            coalesce(properties(p).path_preference, "unknown") AS path_preference
        ORDER BY properties(s).id
        """
        rows = self.run_cypher(query)
        return [
            {
                "sfc_id": row.get("sfc_id"),
                "optimization_goal": row.get("optimization_goal"),
                "policy_type": row.get("policy_type"),
                "p4_program": row.get("p4_program"),
                "path_preference": row.get("path_preference"),
            }
            for row in rows
            if row.get("sfc_id") and row.get("policy_type")
        ]


def build_compact_llm_topology_context(topology_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    topo = topology_snapshot["topology_summary"]
    forwarding = topology_snapshot["forwarding_topology"]
    drone_access_links = forwarding.get("drone_access_links", [])

    connected_drone_ids = sorted(
        list({link["source"] for link in drone_access_links if str(link.get("source", "")).startswith("Drone_")})
    )

    def slim_links(links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "source": link.get("source"),
                "relation": link.get("relation"),
                "target": link.get("target"),
                "status": link.get("status"),
            }
            for link in links
        ]

    return {
        "topology_summary": {
            "num_switches": topo.get("num_switches"),
            "num_links": topo.get("num_links"),
            "access_switches": topo.get("access_switches", []),
            "primary_relays": topo.get("primary_relays", []),
            "backup_relays": topo.get("backup_relays", []),
            "active_relays": topo.get("active_relays", []),
            "standby_relays": topo.get("standby_relays", []),
            "unavailable_relays": topo.get("unavailable_relays", []),
            "connected_drone_count": len(connected_drone_ids),
            "connected_drone_ids": connected_drone_ids,
        },
        "forwarding_paths": {
            "primary_path": slim_links(forwarding.get("primary_path", [])),
            "backup_path": slim_links(forwarding.get("backup_path", [])),
            "edge_links": slim_links(forwarding.get("edge_links", [])),
        },
        "historical_path_decisions_from_kg": topology_snapshot.get("path_decisions", []),
    }


def expand_candidate_actions_with_policy_aliases(candidate_actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add deployment-specific policy aliases used by Milestone-II experiment labels."""
    expanded: List[Dict[str, Any]] = []
    for action in candidate_actions:
        expanded.append(action)
        sfc_id = action.get("sfc_id")
        if sfc_id == "LowLatencyVideoSFC":
            expanded.append(
                {
                    "sfc_id": "LowLatencyVideoSFC",
                    "optimization_goal": action.get("optimization_goal", "Latency"),
                    "policy_type": "priority_table_low_latency",
                    "p4_program": action.get("p4_program", "low_latency.p4"),
                    "path_preference": "PrimaryPath",
                    "policy_scope": "single_switch_queue_policy",
                    "alias_of": action.get("policy_type"),
                }
            )
        elif sfc_id == "ReliableRelaySFC":
            expanded.append(
                {
                    "sfc_id": "ReliableRelaySFC",
                    "optimization_goal": action.get("optimization_goal", "Reliability"),
                    "policy_type": "relay_policy_table_backup_mode",
                    "p4_program": action.get("p4_program", "reliable_relay.p4"),
                    "path_preference": "BackupPath",
                    "policy_scope": "single_switch_relay_policy",
                    "alias_of": action.get("policy_type"),
                }
            )

    seen = set()
    deduped = []
    for action in expanded:
        key = (action.get("sfc_id"), action.get("policy_type"))
        if key not in seen and key[0] and key[1]:
            seen.add(key)
            deduped.append(action)
    return deduped


def fallback_candidate_actions() -> List[Dict[str, Any]]:
    """Fallback candidate action set used only when KG mappings are not populated yet."""
    return expand_candidate_actions_with_policy_aliases(
        [
            {
                "sfc_id": "BandwidthOptimizedSFC",
                "optimization_goal": "Throughput",
                "policy_type": "bandwidth_policy_table_bulk_marking",
                "p4_program": "bandwidth_optimized.p4",
                "path_preference": "HighBandwidthPath",
            },
            {
                "sfc_id": "EnergyAwareSFC",
                "optimization_goal": "Energy",
                "policy_type": "energy_policy_table_essential_only",
                "p4_program": "energy_aware.p4",
                "path_preference": "EssentialFlowsOnly",
            },
            {
                "sfc_id": "LowLatencyVideoSFC",
                "optimization_goal": "Latency",
                "policy_type": "primary_path_low_latency",
                "p4_program": "low_latency.p4",
                "path_preference": "PrimaryPath",
            },
            {
                "sfc_id": "ReliableRelaySFC",
                "optimization_goal": "Reliability",
                "policy_type": "backup_path_reliable_relay",
                "p4_program": "reliable_relay.p4",
                "path_preference": "BackupPath",
            },
        ]
    )
