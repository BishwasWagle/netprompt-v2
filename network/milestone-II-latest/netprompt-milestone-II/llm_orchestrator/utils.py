from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd


def normalize_text(value: Any) -> str:
    """Normalize a value for robust string comparisons."""
    if value is None:
        return "unknown"
    return str(value).strip().lower()


def sanitize_for_json(obj: Any) -> Any:
    """Recursively convert NaN, inf, pandas NA, numpy types, and Paths to JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [sanitize_for_json(v) for v in obj]

    if isinstance(obj, Path):
        return str(obj)

    if isinstance(obj, np.integer):
        return int(obj)

    if isinstance(obj, np.floating):
        value = float(obj)
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    # pd.isna(list/dict) can be ambiguous, so only call it after collection checks.
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass

    return obj


def safe_json_dumps(obj: Any, indent: Optional[int] = 2) -> str:
    """Dump strict JSON. This rejects NaN values instead of silently emitting invalid JSON."""
    return json.dumps(sanitize_for_json(obj), indent=indent, allow_nan=False)


def parse_json_column(value: Any) -> Any:
    """Parse JSON stored as a string, leaving dictionaries unchanged."""
    if isinstance(value, dict):
        return value
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return json.loads(value)


def parse_delay_ms(value: Any) -> Optional[float]:
    """Convert delay strings like '15ms' into 15.0."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).replace("ms", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def format_delay_ms(value: Any) -> Optional[str]:
    """Format a numeric delay as the NetPrompt experiment scripts commonly expect."""
    if value is None:
        return None
    try:
        return f"{float(value):g}ms"
    except (TypeError, ValueError):
        return str(value)


def as_abs_path(relative_or_absolute_path: Any, root: str | Path) -> Optional[str]:
    """Convert a relative NetPrompt path to an absolute path."""
    if relative_or_absolute_path is None:
        return None
    path = Path(str(relative_or_absolute_path))
    if path.is_absolute():
        return str(path)
    return str(Path(root) / path)
