#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an existing NetPrompt experiment command with an LLM-compiled config."
    )
    parser.add_argument("--config", required=True, help="Path to llm_generated_experiment_config.json")
    parser.add_argument(
        "--runner",
        required=True,
        help=(
            "Runner command template. Use {config} as placeholder, e.g. "
            "'python3 run_multihop_experiment.py --config {config}'"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        raise FileNotFoundError(config_path)

    # Load once to fail early if JSON is invalid.
    config = json.loads(config_path.read_text())
    print("Loaded config for:", config.get("selected_sfc"), config.get("deployment_mode"))

    command = args.runner.format(config=str(config_path))
    print("Runner command:", command)
    if args.dry_run:
        return

    subprocess.run(shlex.split(command), check=True)


if __name__ == "__main__":
    main()
