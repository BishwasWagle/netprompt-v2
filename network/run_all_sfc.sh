#!/bin/bash

MODES=(
  "LowLatencyVideoSFC"
  "BandwidthOptimizedSFC"
  "ReliableRelaySFC"
  "EnergyAwareSFC"
)

for MODE in "${MODES[@]}"
do
  echo "=========================================="
  echo "Running $MODE"
  echo "=========================================="

  sudo mn -c
  sudo python3 sfc_experiment.py $MODE

  sleep 2
done

echo "All SFC experiments completed."
