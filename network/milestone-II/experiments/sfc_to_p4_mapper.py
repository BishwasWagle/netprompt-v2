import sys
import json

SFC_TO_P4 = {
    "LowLatencyVideoSFC": {
        "p4_json": "/home/cc/netprompt-milestone-II/compiled_p4/low_latency.json",
        "rule_file": "/home/cc/netprompt-milestone-II/p4_rules/low_latency_rules.txt",
        "policy_type": "priority_table_low_latency"
    },
    "ReliableRelaySFC": {
        "p4_json": "/home/cc/netprompt-milestone-II/compiled_p4/reliable_relay.json",
        "rule_file": "/home/cc/netprompt-milestone-II/p4_rules/reliable_relay_rules.txt",
        "policy_type": "relay_policy_table_backup_mode"
    },
    "EnergyAwareSFC": {
        "p4_json": "/home/cc/netprompt-milestone-II/compiled_p4/energy_aware.json",
        "rule_file": "/home/cc/netprompt-milestone-II/p4_rules/energy_aware_rules.txt",
        "policy_type": "energy_policy_table_essential_only"
    },
    "BandwidthOptimizedSFC": {
        "p4_json": "/home/cc/netprompt-milestone-II/compiled_p4/bandwidth_optimized.json",
        "rule_file": "/home/cc/netprompt-milestone-II/p4_rules/bandwidth_optimized_rules.txt",
        "policy_type": "bandwidth_policy_table_bulk_marking"
    }
}

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 sfc_to_p4_mapper.py <SFC>")
        sys.exit(1)

    sfc = sys.argv[1]

    if sfc not in SFC_TO_P4:
        raise ValueError(f"Unknown SFC: {sfc}")

    print(json.dumps(SFC_TO_P4[sfc], indent=2))
