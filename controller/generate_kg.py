import json

kg = {
    "knowledge_graph": {
        "name": "AI_Native_Drone_SFC_KG",
        "nodes": [],
        "relationships": []
    }
}

# physical/cloud nodes
cluster_nodes = [
    ("controller-node", "CentralController"),
    ("network-node", "ProgrammableNetworkNode"),
    ("edge-node", "EdgeComputeNode")
]

for node_id, node_type in cluster_nodes:
    kg["knowledge_graph"]["nodes"].append({
        "id": node_id,
        "type": node_type
    })

# drone nodes
for i in range(1, 11):
    if i <= 3:
        role = "RelayDrone"
    elif i <= 6:
        role = "SearchDrone"
    else:
        role = "ComputeOrBackupDrone"

    kg["knowledge_graph"]["nodes"].append({
        "id": f"Drone_{i}",
        "type": "Drone",
        "role": role,
        "battery_percent": 70 + i,
        "compute_capacity": "Medium",
        "network_interface": ["5G", "WiFi"],
        "assigned_field": f"Field_{((i - 1) % 5) + 1}"
    })

# fields
for i in range(1, 6):
    kg["knowledge_graph"]["nodes"].append({
        "id": f"Field_{i}",
        "type": "AgriculturalField",
        "latency_requirement_ms": 20 if i in [1, 4] else 50,
        "bandwidth_requirement_mbps": 40 if i in [1, 3] else 20,
        "priority": "High" if i in [1, 4] else "Medium"
    })

# SFC templates
templates = [
    {
        "id": "LowLatencyVideoSFC",
        "type": "SFCTemplate",
        "max_latency_ms": 20,
        "min_bandwidth_mbps": 40,
        "functions": [
            "TrafficClassification",
            "QoSPrioritization",
            "FastForwarding",
            "TelemetryCollection"
        ],
        "p4_actions": [
            "classify_video",
            "set_high_priority",
            "forward_primary_path"
        ]
    },
    {
        "id": "BandwidthOptimizedSFC",
        "type": "SFCTemplate",
        "max_latency_ms": 60,
        "min_bandwidth_mbps": 80,
        "functions": [
            "Compression",
            "AdaptiveForwarding",
            "TrafficShaping"
        ],
        "p4_actions": [
            "mark_bulk_traffic",
            "bandwidth_aware_forward",
            "rate_limit_background"
        ]
    },
    {
        "id": "ReliableRelaySFC",
        "type": "SFCTemplate",
        "max_latency_ms": 50,
        "reliability_threshold": 0.95,
        "functions": [
            "RelaySelection",
            "BackupPathSelection",
            "PacketRecovery"
        ],
        "p4_actions": [
            "select_best_relay",
            "dynamic_reroute",
            "backup_path_forward"
        ]
    },
    {
        "id": "EnergyAwareSFC",
        "type": "SFCTemplate",
        "battery_threshold_percent": 40,
        "functions": [
            "LocalFiltering",
            "Compression",
            "EnergyAwareForwarding"
        ],
        "p4_actions": [
            "drop_low_priority_packets",
            "compress_payload",
            "forward_to_nearest_relay"
        ]
    }
]

kg["knowledge_graph"]["nodes"].extend(templates)

# relationships
for i in range(1, 11):
    kg["knowledge_graph"]["relationships"].append({
        "source": f"Drone_{i}",
        "relation": "ASSIGNED_TO",
        "target": f"Field_{((i - 1) % 5) + 1}"
    })

for i in range(4, 11):
    kg["knowledge_graph"]["relationships"].append({
        "source": f"Drone_{i}",
        "relation": "CONNECTED_TO",
        "target": "Drone_1"
    })

kg["knowledge_graph"]["relationships"].extend([
    {
        "source": "Drone_1",
        "relation": "CONNECTED_TO",
        "target": "network-node"
    },
    {
        "source": "network-node",
        "relation": "CONNECTED_TO",
        "target": "edge-node"
    },
    {
        "source": "controller-node",
        "relation": "CONTROLS",
        "target": "network-node"
    }
])

# --- Switch topology the LLM orchestrator reads (kg_context.get_topology_snapshot).
# ProgrammableSwitch nodes need a `role` (access/primary_relay/backup_relay) so the
# planner can classify relays + build allowed_relays; status drives availability
# (active/standby/unavailable). s1 access, s2 primary relay, s3 backup relay — the
# milestone-II 3-switch fabric. (The runtime monitor later overwrites `status` with
# live readings — design §5.5 / the contract's monitor-computed status.)
programmable_switches = [
    {"id": "s1", "type": "ProgrammableSwitch", "role": "access_switch",
     "status": "active", "thrift_port": 9090, "device_id": 1},
    {"id": "s2", "type": "ProgrammableSwitch", "role": "primary_relay",
     "status": "active", "thrift_port": 9091, "device_id": 2},
    {"id": "s3", "type": "ProgrammableSwitch", "role": "backup_relay",
     "status": "standby", "thrift_port": 9092, "device_id": 3},
]
kg["knowledge_graph"]["nodes"].extend(programmable_switches)

# P4 policy mappings (SFCTemplate -[:REALIZED_BY_P4_POLICY]-> P4PolicyMapping) so the
# planner's candidate set is KG-driven (get_candidate_sfc_policy_set), not the in-code
# fallback. policy_type/path_preference match the milestone-II experiment labels.
p4_policies = [
    {"id": "policy_low_latency", "type": "P4PolicyMapping",
     "policy_type": "primary_path_low_latency", "p4_program": "low_latency.p4",
     "path_preference": "PrimaryPath"},
    {"id": "policy_bandwidth", "type": "P4PolicyMapping",
     "policy_type": "bandwidth_policy_table_bulk_marking",
     "p4_program": "bandwidth_optimized.p4", "path_preference": "HighBandwidthPath"},
    {"id": "policy_reliable_relay", "type": "P4PolicyMapping",
     "policy_type": "backup_path_reliable_relay", "p4_program": "reliable_relay.p4",
     "path_preference": "BackupPath"},
    {"id": "policy_energy", "type": "P4PolicyMapping",
     "policy_type": "energy_policy_table_essential_only", "p4_program": "energy_aware.p4",
     "path_preference": "EssentialFlowsOnly"},
]
kg["knowledge_graph"]["nodes"].extend(p4_policies)

# Forwarding topology + access links + SFC->policy edges.
topology_rels = [
    {"source": "s1", "relation": "PRIMARY_PATH", "target": "s2"},
    {"source": "s1", "relation": "BACKUP_PATH", "target": "s3"},
    {"source": "s2", "relation": "CONNECTED_TO_EDGE", "target": "edge-node"},
    {"source": "s3", "relation": "CONNECTED_TO_EDGE", "target": "edge-node"},
]
for i in range(1, 11):                       # drones attach to the access switch s1
    topology_rels.append({"source": f"Drone_{i}", "relation": "CONNECTED_TO", "target": "s1"})
for sfc, pol in [("LowLatencyVideoSFC", "policy_low_latency"),
                 ("BandwidthOptimizedSFC", "policy_bandwidth"),
                 ("ReliableRelaySFC", "policy_reliable_relay"),
                 ("EnergyAwareSFC", "policy_energy")]:
    topology_rels.append({"source": sfc, "relation": "REALIZED_BY_P4_POLICY", "target": pol})
kg["knowledge_graph"]["relationships"].extend(topology_rels)

with open("drone_sfc_kg.json", "w") as f:
    json.dump(kg, f, indent=2)

print("Generated drone_sfc_kg.json")
