from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


DEPLOYMENT_PATH_KEYS = ["p4_json", "rule_file", "access_rules", "relay_rules", "backup_rules"]


def check_compiled_config_artifacts(config: Dict[str, Any]) -> Dict[str, Any]:
    """Check that the compiled P4 JSON and rule files exist before launching BMv2/Mininet."""
    missing: List[Dict[str, str]] = []
    existing: List[Dict[str, str]] = []

    for key in DEPLOYMENT_PATH_KEYS:
        value = config.get(key) or config.get("deployment", {}).get(key)
        if not value:
            continue
        path = Path(str(value))
        record = {"key": key, "path": str(path)}
        if path.exists():
            existing.append(record)
        else:
            missing.append(record)

    return {
        "ok": len(missing) == 0,
        "existing": existing,
        "missing": missing,
    }


def raise_if_artifacts_missing(config: Dict[str, Any]) -> None:
    result = check_compiled_config_artifacts(config)
    if not result["ok"]:
        missing_text = "\n".join(f"- {item['key']}: {item['path']}" for item in result["missing"])
        raise FileNotFoundError(f"Missing deployment artifacts:\n{missing_text}")
