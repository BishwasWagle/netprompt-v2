#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a clean CSV row for an LLM-driven NetPrompt experiment.")
    parser.add_argument("--config", required=True, help="LLM compiled experiment config JSON")
    parser.add_argument("--result-json", default=None, help="Optional result metrics JSON from experiment runner")
    parser.add_argument("--output-csv", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(Path(args.config).read_text())
    metrics = {}
    if args.result_json:
        metrics = json.loads(Path(args.result_json).read_text())

    row = {
        "experiment_type": config.get("experiment_type"),
        "mission_type": config.get("mission_type"),
        "selected_sfc": config.get("selected_sfc"),
        "policy_type": config.get("policy_type"),
        "selected_path": config.get("selected_path"),
        "selected_relay": config.get("selected_relay"),
        "deployment_mode": config.get("deployment_mode"),
        "p4_json": config.get("p4_json"),
        "rule_file": config.get("rule_file"),
        "access_rules": config.get("access_rules"),
        "relay_rules": config.get("relay_rules"),
        "backup_rules": config.get("backup_rules"),
        "configured_bandwidth_mbps": config.get("configured_bandwidth_mbps"),
        "configured_delay": config.get("configured_delay"),
        "configured_loss_percent": config.get("configured_loss_percent"),
        "rtt_avg_ms": metrics.get("rtt_avg_ms"),
        "measured_packet_loss_percent": metrics.get("measured_packet_loss_percent"),
        "throughput_mbps": metrics.get("throughput_mbps"),
        "experiment_success": metrics.get("experiment_success"),
        "source_file": metrics.get("source_file"),
    }

    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row])
    if output_path.exists():
        old = pd.read_csv(output_path)
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(output_path, index=False)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
