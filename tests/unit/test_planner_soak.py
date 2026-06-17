"""D14 soak driver — the only non-live piece is the path-variant helper."""
from runtime.tools.planner_soak import _variant

BASE = {"selected_sfc": "ReliableRelaySFC",
        "policy_type": "backup_path_reliable_relay",
        "selected_policy": "backup_path_reliable_relay"}


def test_variant_flips_backup_to_primary():
    p = _variant(BASE, primary=True)
    assert p["policy_type"] == "primary_path_reliable_relay"
    assert p["selected_policy"] == "primary_path_reliable_relay"
    assert BASE["policy_type"] == "backup_path_reliable_relay"   # original untouched


def test_variant_keeps_backup_when_not_primary():
    p = _variant(BASE, primary=False)
    assert p["policy_type"] == "backup_path_reliable_relay"
