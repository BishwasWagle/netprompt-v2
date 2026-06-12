#!/bin/bash

set -e

SCENARIO=$1

if [ -z "$SCENARIO" ]; then
    echo "Usage:"
    echo "./run_dynamic_sfc_p4_auto.sh <scenario>"
    echo ""
    echo "Available scenarios:"
    echo "  low_latency"
    echo "  baseline"
    echo "  congestion"
    echo "  ddil"
    echo "  relay_failure"
    echo "  battery_depletion"
    exit 1
fi

echo "============================================"
echo "Dynamic SFC-P4 Auto Runner"
echo "============================================"
echo "Scenario: $SCENARIO"

echo ""
echo "Selecting SFC from Neo4j KG..."
SFC=$(python3 /home/cc/netprompt-milestone-II/experiments/select_sfc_for_scenario.py "$SCENARIO")

echo "KG-selected SFC: $SFC"

echo ""
echo "Resolving SFC to P4 JSON and rule file..."

MAPPING_JSON=$(python3 /home/cc/netprompt-milestone-II/experiments/sfc_to_p4_mapper.py "$SFC")

P4_JSON=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['p4_json'])")
RULE_FILE=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['rule_file'])")
POLICY_TYPE=$(echo "$MAPPING_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['policy_type'])")

echo "P4 JSON: $P4_JSON"
echo "Rule File: $RULE_FILE"
echo "Policy Type: $POLICY_TYPE"

echo ""
echo "Cleaning Mininet..."
sudo mn -c

echo ""
echo "Running dynamic SFC-P4 experiment..."

sudo python3 /home/cc/netprompt-milestone-II/experiments/dynamic_sfc_p4_experiment.py \
    --scenario "$SCENARIO" \
    --sfc "$SFC" \
    --p4-json "$P4_JSON" \
    --rules "$RULE_FILE" \
    --policy-type "$POLICY_TYPE"

echo ""
echo "Dynamic SFC-P4 experiment completed."

echo "Generated result files:"
ls -lh /home/cc/netprompt-milestone-II/results/dynamic_p4_${SCENARIO}_${SFC}.txt
