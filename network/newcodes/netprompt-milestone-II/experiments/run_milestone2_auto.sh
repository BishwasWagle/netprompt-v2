#!/bin/bash

set -e

SCENARIO=$1

if [ -z "$SCENARIO" ]; then
    echo "Usage:"
    echo "./run_milestone2_auto.sh <scenario>"
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

echo "========================================="
echo "Milestone II Auto Runner"
echo "========================================="
echo "Scenario: $SCENARIO"

echo ""
echo "Selecting SFC from Neo4j KG..."
SFC=$(python3 /home/cc/netprompt-milestone-II/experiments/select_sfc_for_scenario.py "$SCENARIO")

echo "KG-selected SFC: $SFC"

echo ""
echo "Cleaning Mininet..."
sudo mn -c

echo ""
echo "Running P4/BMv2 experiment..."
cd /home/cc/netprompt-milestone-II/experiments

sudo python3 advanced_milestone2_experiment.py \
    --scenario "$SCENARIO" \
    --sfc "$SFC"

echo ""
echo "Parsing results..."
cd /home/cc/netprompt-milestone-II/results
python3 parse_milestone2_results.py

echo ""
echo "Pushing results to Neo4j KG..."
python3 push_results_to_kg.py

echo ""
echo "========================================="
echo "Completed Milestone II Auto Pipeline"
echo "Scenario: $SCENARIO"
echo "SFC: $SFC"
echo "========================================="
