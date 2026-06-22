import sys
import subprocess
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "netprompt123"

# This driver runs the single-switch SFC harness REMOTELY: the command below
# does `cd {NETWORK_PATH} && sudo python3 sfc_experiment.py {template}` on
# cc@network-node, where NETWORK_PATH is that host's dir — NOT this repo's
# network/ tree. Do NOT add a local network/sfc_experiment.py; it would be dead
# code (the in-repo reference copy is network/milestone-II/experiments/
# sfc_experiment.py). The live in-repo fabric is the 3-switch BMv2/P4 topology
# built by runtime/tools/launch_network.py, which supersedes the single switch.
NETWORK_NODE = "network-node"
NETWORK_PATH = "/home/cc/netprompt-network"
SSH_KEY = "FC_key.pem"


def select_template_for_field(field_id):
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASS)
    )

    query = """
    MATCH (f:AgriculturalField {id:$field_id})
    RETURN
        f.id AS field,
        f.latency_requirement_ms AS latency_requirement,
        f.bandwidth_requirement_mbps AS bandwidth_requirement,
        f.priority AS priority
    """

    with driver.session() as session:
        record = session.run(query, field_id=field_id).single()

    driver.close()

    if not record:
        return {
            "field": field_id,
            "latency_requirement": None,
            "bandwidth_requirement": None,
            "priority": None,
            "template": "ReliableRelaySFC",
            "template_latency": None
        }

    latency = record["latency_requirement"]
    bandwidth = record["bandwidth_requirement"]
    priority = record["priority"]

    # Multi-constraint SFC selection logic
    if priority == "High" and latency <= 20:
        template = "LowLatencyVideoSFC"

    elif bandwidth >= 40 and latency <= 60:
        template = "BandwidthOptimizedSFC"

    elif priority == "Medium" and latency <= 50:
        template = "ReliableRelaySFC"

    else:
        template = "EnergyAwareSFC"

    return {
        "field": record["field"],
        "latency_requirement": latency,
        "bandwidth_requirement": bandwidth,
        "priority": priority,
        "template": template,
        "template_latency": latency
    }

#def select_template_for_field(field_id):
#    driver = GraphDatabase.driver(
#        NEO4J_URI,
#        auth=(NEO4J_USER, NEO4J_PASS)
#    )
#
#    query = """
#    MATCH (f:AgriculturalField {id:$field_id})
#    MATCH (s:SFCTemplate)
#    WHERE s.max_latency_ms <= f.latency_requirement_ms
#    RETURN
#        f.id AS field,
#        f.latency_requirement_ms AS latency_requirement,
#        f.bandwidth_requirement_mbps AS bandwidth_requirement,
#        f.priority AS priority,
#        s.id AS template,
#        s.max_latency_ms AS template_latency
#    ORDER BY s.max_latency_ms ASC
#    LIMIT 1
#    """
#    with driver.session() as session:
#        record = session.run(query, field_id=field_id).single()
#    driver.close()
#
#    if record:
#        return {
#            "field": record["field"],
#            "latency_requirement": record["latency_requirement"],
#            "bandwidth_requirement": record["bandwidth_requirement"],
#            "priority": record["priority"],
#            "template": record["template"],
#            "template_latency": record["template_latency"]
#        }

    # fallback
#   return {
#       "field": field_id,
#       "latency_requirement": None,
#      "bandwidth_requirement": None,
#        "priority": None,
#        "template": "ReliableRelaySFC",
#        "template_latency": None
#    }


def run_remote_experiment(template):
    command = f"""
    cd {NETWORK_PATH} &&
    sudo mn -c &&
    sudo python3 sfc_experiment.py {template}
    """

    subprocess.run(
        [
            "ssh",
            "-i",
            SSH_KEY,
            f"cc@{NETWORK_NODE}",
            command
        ],
        check=True
    )


def fetch_result_file(template, field_id):
    remote_file = f"{NETWORK_PATH}/results_{template}.txt"
    local_file = f"results_{field_id}_{template}_from_network_node.txt"

    subprocess.run(
        [
            "scp",
            "-i",
            SSH_KEY,
            f"cc@{NETWORK_NODE}:{remote_file}",
            local_file
        ],
        check=True
    )

    return local_file


def main():
    if len(sys.argv) != 2:
        print("Usage:")
        print("python3 run_selected_sfc.py <Field_ID>")
        print()
        print("Examples:")
        print("python3 run_selected_sfc.py Field_1")
        print("python3 run_selected_sfc.py Field_2")
        print("python3 run_selected_sfc.py Field_4")
        sys.exit(1)

    field_id = sys.argv[1]

    decision = select_template_for_field(field_id)
    template = decision["template"]

    print("KG Decision")
    print("=" * 50)
    print(f"Field: {decision['field']}")
    print(f"Latency Requirement: {decision['latency_requirement']} ms")
    print(f"Bandwidth Requirement: {decision['bandwidth_requirement']} Mbps")
    print(f"Priority: {decision['priority']}")
    print(f"Selected SFC Template: {template}")
    print("=" * 50)

    print("\nRunning remote experiment on network-node...")
    run_remote_experiment(template)

    print("\nFetching result file...")
    local_file = fetch_result_file(template, field_id)

    print("\nCompleted successfully.")
    print(f"Fetched result file: {local_file}")


if __name__ == "__main__":
    main()
