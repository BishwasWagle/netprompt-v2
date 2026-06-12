import sys
import json

BASE = "/home/cc/netprompt-milestone-II"

SFC_TO_MULTIHOP = {
    "LowLatencyVideoSFC": {
        "p4_json": f"{BASE}/compiled_p4/low_latency.json",
        "access_rules": f"{BASE}/p4_multihop_rules/low_latency_s1_rules.txt",
        "relay_rules": f"{BASE}/p4_multihop_rules/low_latency_s2_rules.txt",
        "backup_rules": f"{BASE}/p4_multihop_rules/low_latency_s3_rules.txt",
        "policy_type": "primary_path_low_latency"
    },
    "ReliableRelaySFC": {
        "p4_json": f"{BASE}/compiled_p4/reliable_relay.json",
        "access_rules": f"{BASE}/p4_multihop_rules/reliable_relay_s1_rules.txt",
        "relay_rules": f"{BASE}/p4_multihop_rules/reliable_relay_s2_rules.txt",
        "backup_rules": f"{BASE}/p4_multihop_rules/reliable_relay_s3_rules.txt",
        "policy_type": "backup_path_reliable_relay"
    }
}

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 sfc_to_multihop_mapper.py <SFC>")
        sys.exit(1)

    sfc = sys.argv[1]

    if sfc not in SFC_TO_MULTIHOP:
        raise ValueError(
            f"Multi-hop mapping not available for SFC: {sfc}. "
            "Use LowLatencyVideoSFC or ReliableRelaySFC."
        )

    print(json.dumps(SFC_TO_MULTIHOP[sfc], indent=2))
