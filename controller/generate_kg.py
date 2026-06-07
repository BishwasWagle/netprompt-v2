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

with open("drone_sfc_kg.json", "w") as f:
    json.dump(kg, f, indent=2)

print("Generated drone_sfc_kg.json")
