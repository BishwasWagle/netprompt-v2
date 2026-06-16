"""M2 — Validation Gate L0-L2 (design §11). Exit criteria: valid binding
passes; out-of-range knob, illegal path, entry-deleting regen, and
unknown-table candidates each rejected with the right layered reason."""
import pytest

from runtime.config import EDGE_MAC
from runtime.contracts import (
    BACKUP, PRIMARY, REGEN, REROUTE, TUNE,
    Candidate, DeploymentSpec, Envelope, TableEntry,
)
from runtime.gate import ValidationGate

ENV = Envelope(
    max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
    legal_tiers=frozenset((TUNE, REROUTE, REGEN)),
    legal_paths=frozenset((PRIMARY, BACKUP)),
    knob_ranges={"tbf_rate_mbit": (5, 80), "pfifo_limit": (10, 50)},
)

PRIMARY_ONLY = Envelope(
    max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
    legal_tiers=frozenset((TUNE,)), legal_paths=frozenset((PRIMARY,)),
    knob_ranges={"pfifo_limit": (10, 50)},
)


def s1_tables():
    """Mirrors low_latency_s1_rules.txt: drones 01..0a -> ports 1..10,
    edge 0b -> port 11 (primary); handles 0..10."""
    entries = [TableEntry("forward_table", f"00:00:00:00:00:{i:02x}",
                          "forward", (str(i),), i - 1)
               for i in range(1, 11)]
    entries.append(TableEntry("forward_table", EDGE_MAC, "forward", ("11",), 10))
    entries.append(TableEntry("priority_table", "10.0.0.100",
                              "set_low_latency_class", (), 11))
    return {"s1": entries}


@pytest.fixture
def gate():
    return ValidationGate()


# ---------------- tune ----------------

def test_tune_in_range_passes(gate):
    assert gate.check(Candidate(TUNE, ("tbf_rate_mbit", 50)), ENV).ok


def test_tune_out_of_range_rejected(gate):
    r = gate.check(Candidate(TUNE, ("tbf_rate_mbit", 200)), ENV)
    assert not r.ok and r.reason.startswith("L1:")


def test_tune_unknown_knob_rejected(gate):
    r = gate.check(Candidate(TUNE, ("netem_delay_ms", 5)), ENV)
    assert not r.ok and r.reason.startswith("L1:")   # impairments are not knobs (§7.3)


# ---------------- reroute ----------------

def test_reroute_to_legal_backup_passes(gate):
    assert gate.check(Candidate(REROUTE, (BACKUP,)), ENV).ok


def test_reroute_outside_legal_paths_rejected(gate):
    r = gate.check(Candidate(REROUTE, (BACKUP,)), PRIMARY_ONLY)
    assert not r.ok and r.reason.startswith("L1:")


def test_reroute_to_nonsense_path_rejected(gate):
    r = gate.check(Candidate(REROUTE, ("scenic",)), ENV)
    assert not r.ok and r.reason.startswith("L0:")


# ---------------- regen: L0 syntax ----------------

def test_regen_valid_reroute_rules_pass(gate):
    cand = Candidate(REGEN, ("s1", "table_modify forward_table forward 10 => 12"))
    assert gate.check(cand, ENV, s1_tables()).ok


def test_regen_l3_dry_install_hook(gate):
    """L3: an injected dry_install_fn runs after L2 and can reject a candidate
    that passed L0-L2; with no fn (default) behavior is unchanged."""
    from runtime.contracts import GateResult
    cand = Candidate(REGEN, ("s1", "table_modify forward_table forward 10 => 12"))
    assert gate.check(cand, ENV, s1_tables()).ok                  # L0-L2 pass, no L3

    rejecting = ValidationGate(dry_install_fn=lambda sw, txt: GateResult(False, "L3: boom"))
    r = rejecting.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L3:")               # L3 rejected it

    seen = {}
    accepting = ValidationGate(
        dry_install_fn=lambda sw, txt: seen.update(sw=sw, txt=txt) or GateResult(True))
    assert accepting.check(cand, ENV, s1_tables()).ok
    assert seen["sw"] == "s1" and "table_modify" in seen["txt"]   # fn saw the live rules

    # L3 is not reached when L2 already rejects (entry-deleting regen -> blackhole)
    blackhole = Candidate(REGEN, ("s1", "table_modify forward_table drop 10 => "))
    assert not rejecting.check(blackhole, ENV, s1_tables()).reason.startswith("L3:")


def test_regen_l2_handles_collide_across_tables(gate):
    """BMv2 handles are NOT unique across tables — a priority_table entry can
    share a handle value with a forward_table entry. L2's simulation must key by
    (table, handle) so the forward entry isn't dropped (a false blackhole). Live
    regression: this had silently rejected EVERY regen candidate on the node."""
    fwd = [TableEntry("forward_table", f"00:00:00:00:00:{i:02x}", "forward", (str(i),), i - 1)
           for i in range(1, 11)]
    fwd.append(TableEntry("forward_table", EDGE_MAC, "forward", ("11",), 10))
    fwd.append(TableEntry("priority_table", "10.0.0.100",          # handle 0 collides with d1
                          "set_low_latency_class", (), 0))
    cand = Candidate(REGEN, ("s1", "table_modify forward_table forward 0 => 2"))
    r = gate.check(cand, ENV, {"s1": fwd})
    assert r.ok, r.reason                          # d1 not dropped by the colliding handle


def test_regen_modify_without_arrow_also_parses(gate):
    cand = Candidate(REGEN, ("s1", "table_modify forward_table forward 10 12"))
    assert gate.check(cand, ENV, s1_tables()).ok    # M0 spike pins exact syntax


def test_regen_unknown_table_rejected(gate):
    cand = Candidate(REGEN, ("s1", "table_add magic_table forward 00:00:00:00:00:0c => 1"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L0:")


def test_regen_wrong_action_for_table_rejected(gate):
    cand = Candidate(REGEN, ("s1", "table_add priority_table forward 10.0.0.100 => 1"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L0:")


def test_regen_invalid_port_rejected(gate):
    cand = Candidate(REGEN, ("s1", "table_modify forward_table forward 10 => 99"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L0:")


def test_regen_malformed_mac_rejected(gate):
    cand = Candidate(REGEN, ("s1", "table_add forward_table forward 00:00:zz => 1"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L0:")


# ---------------- regen: L2 invariants ----------------

def test_regen_deleting_edge_entry_is_a_blackhole(gate):
    cand = Candidate(REGEN, ("s1", "table_delete forward_table 10"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L2:") and "blackhole" in r.reason


def test_regen_dropping_edge_traffic_passes_l0_but_fails_l2(gate):
    # 'drop' is a legal forward_table action (L0 ok) — the blackhole is
    # only caught by the reachability invariant. This is the layering test.
    cand = Candidate(REGEN, ("s1", "table_modify forward_table drop 10"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L2:")


def test_regen_may_drop_primary_edge_identity_if_backup_remains(gate):
    """Dual edge identity (milestone-II-latest): deleting the 0b entry is fine
    when a routable 0c entry exists — the edge is still reachable."""
    tables = s1_tables()
    tables["s1"].append(TableEntry("forward_table", "00:00:00:00:00:0c",
                                   "forward", ("12",), 12))
    cand = Candidate(REGEN, ("s1", "table_delete forward_table 10"))
    assert gate.check(cand, ENV, tables).ok


def test_regen_delete_with_readd_keeps_routability(gate):
    rules = ("table_delete forward_table 10\n"
             f"table_add forward_table forward {EDGE_MAC} => 12")
    assert gate.check(Candidate(REGEN, ("s1", rules)), ENV, s1_tables()).ok


def test_regen_duplicate_add_rejected(gate):
    cand = Candidate(REGEN, ("s1", f"table_add forward_table forward {EDGE_MAC} => 12"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L2:") and "duplicate" in r.reason


def test_regen_unknown_handle_rejected(gate):
    cand = Candidate(REGEN, ("s1", "table_modify forward_table forward 77 => 12"))
    r = gate.check(cand, ENV, s1_tables())
    assert not r.ok and r.reason.startswith("L2:")


def test_regen_without_table_state_is_conservatively_rejected(gate):
    cand = Candidate(REGEN, ("s1", "table_modify forward_table forward 10 => 12"))
    r = gate.check(cand, ENV, current_tables=None)
    assert not r.ok and r.reason.startswith("L2:")   # sound = refuse, don't hope


def test_rejection_is_deterministic(gate):
    cand = Candidate(REGEN, ("s1", "table_delete forward_table 10"))
    assert (gate.check(cand, ENV, s1_tables()).reason
            == gate.check(cand, ENV, s1_tables()).reason)


# ---------------- binding (entry point 1) ----------------

def _spec(binding, env=ENV):
    return DeploymentSpec(sfc="LowLatencyVideoSFC", binding=binding,
                          envelope=env, correlation_id="t", target_field="F1")


VALID_BINDING = {"p4_json": "low_latency.json", "access_rules": "s1.txt",
                 "relay_rules": "s2.txt", "backup_rules": "s3.txt",
                 "policy_type": "primary_path_low_latency"}


def test_valid_binding_passes(gate):
    assert gate.check_binding(_spec(VALID_BINDING)).ok


def test_binding_missing_key_rejected(gate):
    r = gate.check_binding(_spec({"p4_json": "x.json"}))
    assert not r.ok and r.reason.startswith("L0:")


def test_binding_with_inverted_knob_range_rejected(gate):
    bad = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                   legal_tiers=frozenset((TUNE,)), legal_paths=frozenset((PRIMARY,)),
                   knob_ranges={"pfifo_limit": (50, 10)})
    r = gate.check_binding(_spec(VALID_BINDING, bad))
    assert not r.ok and r.reason.startswith("L0:")
