#!/bin/bash

set -e

SCENARIO=$1

if [ -z "$SCENARIO" ]; then
    echo "Usage:"
    echo "./run_multihop_sfc_p4_auto.sh <scenario>"
    echo ""
    echo "Good scenarios:"
    echo "  low_latency"
    echo "  congestion"
    echo "  ddil"
    echo "  relay_failure"
    exit 1
fi

echo "============================================"
echo "Multi-Hop Dynamic SFC-P4 Auto Runner"
echo "============================================"
echo "Scenario: $SCENARIO"

echo ""
echo "Selecting SFC from Neo4j KG..."
SFC=$(python3 /home/cc/netprompt-milestone-II/experiments/select_sfc_for_scenario.py "$SCENARIO")

echo "KG-selected SFC: $SFC"

echo ""
echo "Resolving multi-hop SFC mapping..."
MAPPING_JSON=$(python3 /home/cc/netprompt-milestone-II/experiments/sfc_to_multihop_mapper.py "$SFC")

P4_JSON=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['p4_json'])")
ACCESS_RULES=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_rules'])")
RELAY_RULES=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['relay_rules'])")
BACKUP_RULES=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['backup_rules'])")
POLICY_TYPE=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['policy_type'])")

echo "P4 JSON: $P4_JSON"
echo "Access Rules: $ACCESS_RULES"
echo "Relay Rules: $RELAY_RULES"
echo "Backup Rules: $BACKUP_RULES"
echo "Policy Type: $POLICY_TYPE"

echo ""
echo "Cleaning Mininet..."
sudo mn -c

echo ""
echo "Running multi-hop dynamic SFC-P4 experiment..."
sudo python3 /home/cc/netprompt-milestone-II/experiments/dynamic_sfc_p4_multihop_experiment.py \
    --scenario "$SCENARIO" \
    --sfc "$SFC" \
    --p4-json "$P4_JSON" \
    --access-rules "$ACCESS_RULES" \
    --relay-rules "$RELAY_RULES" \
    --backup-rules "$BACKUP_RULES" \
    --policy-type "$POLICY_TYPE"

echo ""
echo "Multi-hop dynamic SFC-P4 experiment completed."

RESULT_FILE="/home/cc/netprompt-milestone-II/results/multihop_${SCENARIO}_${SFC}.txt"
echo "Generated result:"
ls -lh "$RESULT_FILE"
