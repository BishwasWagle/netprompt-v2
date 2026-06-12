#!/bin/bash

set -e

SCENARIO=$1

if [ -z "$SCENARIO" ]; then
    echo "Usage:"
    echo "./run_netprompt_milestone2_final.sh <scenario>"
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

echo "================================================"
echo "NetPrompt Milestone II Final Runner"
echo "================================================"
echo "Scenario: $SCENARIO"

echo ""

echo ""
echo "Updating KG topology/path state..."
python3 /home/cc/netprompt-milestone-II/experiments/update_topology_state.py "$SCENARIO"

echo "Selecting SFC from KG..."

SELECTION_JSON=$(python3 /home/cc/netprompt-milestone-II/experiments/path_aware_sfc_selector.py "$SCENARIO")

SFC=$(echo "$SELECTION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['selected_sfc'])")
SELECTED_PATH=$(echo "$SELECTION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['selected_path'])")
SELECTED_RELAY=$(echo "$SELECTION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['selected_relay'])")
PATH_REASON=$(echo "$SELECTION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['path_reason'])")

echo "KG-selected SFC: $SFC"
echo "KG-selected Path: $SELECTED_PATH"
echo "KG-selected Relay: $SELECTED_RELAY"
echo "Path Reason: $PATH_REASON"

echo ""


echo "Running experiment..."

if [ "$SFC" = "LowLatencyVideoSFC" ] || [ "$SFC" = "ReliableRelaySFC" ]; then
    echo "Using multi-hop BMv2 topology for SFC: $SFC"

    /home/cc/netprompt-milestone-II/experiments/run_multihop_sfc_p4_auto.sh "$SCENARIO"

else
    echo "Using single-switch dynamic SFC-P4 topology for SFC: $SFC"

    /home/cc/netprompt-milestone-II/experiments/run_dynamic_sfc_p4_auto.sh "$SCENARIO"
fi

echo ""
echo "Parsing results and pushing to Neo4j KG..."

cd /home/cc/netprompt-milestone-II/results

python3 parse_and_push_final_results.py

echo ""
echo "================================================"
echo "NetPrompt Milestone II final pipeline completed."
echo "Scenario: $SCENARIO"
echo "SFC: $SFC"
echo "Results parsed and pushed to Neo4j."
echo "================================================"
