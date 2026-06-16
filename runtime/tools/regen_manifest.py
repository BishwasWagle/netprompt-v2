"""Emit the Tier-2 regen reproducibility manifest (M7 DoD #4).

    source deploy/gpu-node/gpu-node.env      # pins model/revision/device
    python3 -m runtime.tools.regen_manifest  # -> JSON on stdout
"""
import json

from runtime.regen.llm_client import manifest

if __name__ == "__main__":
    print(json.dumps(manifest(), indent=2, sort_keys=True))
