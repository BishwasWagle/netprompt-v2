import sys
from neo4j import GraphDatabase

URI = "bolt://controller-node:7687"
USER = "neo4j"
PASSWORD = "netprompt123"

SCENARIO_TO_GOAL = {
    "low_latency": "Latency",
    "baseline": "Latency",
    "congestion": "Reliability",
    "ddil": "Reliability",
    "relay_failure": "Reliability",
    "battery_depletion": "Energy"
}


def select_sfc(scenario):
    if scenario not in SCENARIO_TO_GOAL:
        raise ValueError(f"Unknown scenario: {scenario}")

    optimization_goal = SCENARIO_TO_GOAL[scenario]

    driver = GraphDatabase.driver(
        URI,
        auth=(USER, PASSWORD)
    )

    with driver.session() as session:
        result = session.run(
            """
            MATCH (s:SFCTemplate)
            WHERE s.optimization_goal = $goal
            RETURN s.id AS sfc
            LIMIT 1
            """,
            goal=optimization_goal
        )

        record = result.single()

    driver.close()

    if not record:
        raise RuntimeError(
            f"No SFC template found for optimization goal: {optimization_goal}"
        )

    return record["sfc"]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 select_sfc_for_scenario.py <scenario>")
        sys.exit(1)

    scenario = sys.argv[1]
    print(select_sfc(scenario))
