from neo4j import GraphDatabase

driver = GraphDatabase.driver(
    "bolt://localhost:7687",
    auth=("neo4j","netprompt123")
)

latency_requirement = 20

with driver.session() as session:

    result = session.run(
        """
        MATCH (s:SFCTemplate)
        WHERE s.max_latency_ms <= $lat
        RETURN s.id
        ORDER BY s.max_latency_ms
        LIMIT 1
        """,
        lat=latency_requirement
    )

    record = result.single()

    if record:
        print(
            "Selected Template:",
            record["s.id"]
        )
    else:
        print("No template found")

driver.close()
