"""M4 (local half) — deployer command/parse layer against canned CLI output.
Output formats (handle lines, table_dump shape) are M0-spike-verified; the
parsers encapsulate them so a node fix is one regex."""
import pytest

from runtime.contracts import (
    BACKUP, Candidate, DeploymentSpec, Envelope, PRIMARY, REGEN, REROUTE, TUNE,
)
from runtime.deployer import (
    DeployError, Deployer, hex_to_ipv4, hex_to_mac, parse_handles,
    parse_rule_line, parse_table_dump, tc_command,
)
from runtime.fakes import ScriptedRunner

# ---------------- parsers ----------------

def test_parse_handles_in_order():
    out = ("Adding entry to exact match table priority_table\n"
           "Entry has been added with handle 0\n"
           "Entry has been added with handle 1\n"
           "Entry has been added with handle 2\n")
    assert parse_handles(out) == [0, 1, 2]
    assert parse_handles("RuntimeCmd:") == []


def test_hex_conversions():
    assert hex_to_mac("00000000000B") == "00:00:00:00:00:0b"
    assert hex_to_ipv4("0a000064") == "10.0.0.100"
    with pytest.raises(DeployError):
        hex_to_mac("0b")
    with pytest.raises(DeployError):
        hex_to_ipv4("0a00")


FORWARD_DUMP = """==========
TABLE ENTRIES
**********
Dumping entry 0x1
Match key:
* ethernet.dstAddr    : EXACT     000000000001
Action entry: MyIngress.forward - 01
**********
Dumping entry 0x2
Match key:
* ethernet.dstAddr    : EXACT     00000000000b
Action entry: MyIngress.forward - 0b
==========
Dumping default entry
Action entry: MyIngress.drop -
==========
"""


def test_parse_table_dump_skips_default_and_decodes():
    entries = parse_table_dump(FORWARD_DUMP, "forward_table")
    assert len(entries) == 2                          # default entry skipped
    by_key = {e.key: e for e in entries}
    edge = by_key["00:00:00:00:00:0b"]
    assert edge.handle == 2 and edge.action == "forward"
    assert edge.args == ("11",)                       # 0x0b -> decimal port
    assert by_key["00:00:00:00:00:01"].args == ("1",)


def test_parse_rule_line():
    assert parse_rule_line("table_add forward_table forward 00:00:00:00:00:0B => 11") \
        == ("forward_table", "forward", "00:00:00:00:00:0b", ("11",))
    assert parse_rule_line("table_add priority_table set_low_latency_class 10.0.0.100 =>") \
        == ("priority_table", "set_low_latency_class", "10.0.0.100", ())
    assert parse_rule_line("   ") is None
    with pytest.raises(DeployError):
        parse_rule_line("table_madd nonsense")


def test_tc_command_templates():
    assert tc_command("tbf_rate_mbit", 45, "d4-eth0") \
        == "tc qdisc replace dev d4-eth0 root tbf rate 45mbit burst 32kbit latency 50ms"
    assert tc_command("pfifo_limit", 20, "d5-eth0") \
        == "tc qdisc replace dev d5-eth0 root pfifo limit 20"
    with pytest.raises(DeployError):
        tc_command("netem_delay_ms", 5, "d4-eth0")    # impairments are not knobs


# ---------------- deployer rig ----------------

S1_RULES = """table_add priority_table set_low_latency_class 10.0.0.100 =>
table_add forward_table forward 00:00:00:00:00:01 => 1
table_add forward_table forward 00:00:00:00:00:0b => 11
"""
RELAY_RULES = "table_add forward_table forward 00:00:00:00:00:0b => 2\n"


def auto_handles():
    """Default CLI script: one sequential handle line per table_add."""
    counters = {}
    def handler(switch, text):
        if text.startswith("table_dump"):
            return ""
        adds = [l for l in text.splitlines() if l.strip().startswith("table_add")]
        start = counters.get(switch, 0)
        counters[switch] = start + len(adds)
        return "\n".join(f"Entry has been added with handle {start + i}"
                         for i in range(len(adds)))
    return handler


def make_spec(tmp_path, qos=None):
    files = {}
    for key, text in [("access_rules", S1_RULES), ("relay_rules", RELAY_RULES),
                      ("backup_rules", RELAY_RULES)]:
        p = tmp_path / f"{key}.txt"
        p.write_text(text)
        files[key] = str(p)
    binding = {"p4_json": "low_latency.json", "policy_type": "fixture",
               **files}
    if qos is not None:
        binding["qos"] = qos
    env = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                   legal_tiers=frozenset((TUNE, REROUTE)),
                   legal_paths=frozenset((PRIMARY, BACKUP)),
                   knob_ranges={"tbf_rate_mbit": (5, 80)})
    return DeploymentSpec(sfc="LowLatencyVideoSFC", binding=binding,
                          envelope=env, correlation_id="ep1", target_field="F1")


def rig(tmp_path, qos=None, handler=None):
    runner = ScriptedRunner(handler or auto_handles())
    d = Deployer(runner, host_map={"F1": ["d4", "d5"]})
    snap = d.deploy(make_spec(tmp_path, qos))
    return d, runner, snap


# ---------------- deploy / state ----------------

def test_deploy_tracks_handles_and_active_path(tmp_path):
    d, runner, snap = rig(tmp_path, qos={"tbf_rate_mbit": 80})
    assert [e.handle for e in d.table_state()["s1"]] == [0, 1, 2]
    assert d.state == {"path": PRIMARY, "knobs": {"tbf_rate_mbit": 80}}
    assert snap.active_path == PRIMARY
    tc_calls = [c for _, c in runner.host_calls if c.startswith("tc ")]
    assert len(tc_calls) == 2                         # initial qos on d4, d5
    # deploy also binds the edge identity + drone ARP to the active path
    assert ("edge", "ip route replace 10.0.0.0/24 dev edge-eth0 src 10.0.0.100") \
        in runner.host_calls
    assert ("d7", "arp -s 10.0.0.100 00:00:00:00:00:0b") in runner.host_calls
    assert {sw for sw, _ in runner.cli_calls} == {"s1", "s2", "s3"}


def test_deploy_is_idempotent_clears_residual_first(tmp_path):
    """Episode-start deploy is safe on a resident network (M4-node): residual
    entries are cleared from a live dump before installing, so a re-deploy onto
    already-populated switches can't hit DUPLICATE_ENTRY."""
    def handler(switch, text):
        if text.startswith("table_dump forward_table") and switch == "s2":
            return FORWARD_DUMP                        # s2 still holds 2 entries
        if text.startswith("table_dump"):
            return ""
        return auto_handles()(switch, text)
    runner = ScriptedRunner(handler)
    d = Deployer(runner, host_map={"F1": ["d4"]})
    d.deploy(make_spec(tmp_path))                       # must not raise
    deletes = [c for sw, c in runner.cli_calls if sw == "s2" and "table_delete" in c]
    assert any("table_delete forward_table 1" in c for c in deletes)
    assert any("table_delete forward_table 2" in c for c in deletes)


def test_deploy_with_mismatched_handle_count_fails(tmp_path):
    handler = lambda sw, text: "Entry has been added with handle 0"   # always 1
    runner = ScriptedRunner(handler)
    d = Deployer(runner, host_map={"F1": ["d4"]})
    with pytest.raises(DeployError):
        d.deploy(make_spec(tmp_path))


# ---------------- apply ----------------

def test_apply_tune_targets_the_specs_hosts(tmp_path):
    d, runner, _ = rig(tmp_path, qos={"tbf_rate_mbit": 80})
    d.apply(Candidate(TUNE, ("tbf_rate_mbit", 45)))
    tuned = runner.host_calls[-2:]
    assert {h for h, _ in tuned} == {"d4", "d5"}
    assert all("rate 45mbit" in cmd for _, cmd in tuned)
    assert d.state["knobs"]["tbf_rate_mbit"] == 45


def test_deploy_falls_back_to_sfc_qos_baseline(tmp_path):
    """Issue-2 / Model B: with no binding qos, deploy installs the per-SFC
    baseline knob (make_spec's SFC is LowLatency -> pfifo_limit 20) on the field
    hosts, so the drone-eth0 qdisc is deployer-owned and a TUNE is reversible."""
    d, runner, _ = rig(tmp_path)                          # no qos passed
    assert d.state["knobs"] == {"pfifo_limit": 20}
    pushed = [c for _, c in runner.host_calls if "pfifo limit 20" in c]
    assert len(pushed) == 2                               # d4, d5


def test_tune_rolls_back_to_sfc_baseline_without_binding_qos(tmp_path):
    """The Issue-2 fix: a TUNE over the fallback baseline is reverted to it on
    rollback even though the binding carried no qos (previously the knob would
    persist because the baseline snapshot had no entry to revert to)."""
    d, runner, _ = rig(tmp_path)                          # baseline pfifo_limit 20
    snap = d.capture()
    d.apply(Candidate(TUNE, ("pfifo_limit", 40)))
    assert d.state["knobs"]["pfifo_limit"] == 40
    d.rollback(snap)
    assert d.state["knobs"] == {"pfifo_limit": 20}
    assert any("pfifo limit 20" in c for _, c in runner.host_calls[-2:])


def test_apply_reroute_is_the_multi_switch_action(tmp_path):
    """milestone-II-latest mechanics (§10.1) + M0/C2: the reroute is
    multi-switch — s1 entry for the backup edge identity AND the destination
    relay (s3) entry for it — plus edge interface rebind + drone ARP repoint.
    Without the s3 entry the frame dies at s3 (a primary SFC's s3 lacks 0c)."""
    d, runner, _ = rig(tmp_path)
    n_host, n_cli = len(runner.host_calls), len(runner.cli_calls)
    d.apply(Candidate(REROUTE, (BACKUP,)))
    cli = runner.cli_calls[n_cli:]
    # (1) the 0c identity gets its s1 entry (low_latency rules lack it) ...
    assert ("s1", "table_add forward_table forward 00:00:00:00:00:0c => 12") in cli
    assert any(e.key == "00:00:00:00:00:0c" and e.args == ("12",)
               for e in d.table_state()["s1"])
    # (2) ... and the destination relay s3 learns it too (port 2 = edge)
    assert ("s3", "table_add forward_table forward 00:00:00:00:00:0c => 2") in cli
    assert any(e.key == "00:00:00:00:00:0c" and e.args == ("2",)
               for e in d.table_state()["s3"])
    # (3) edge binds 10.0.0.100 to edge-eth1; (4) drones repoint ARP at 0c
    swap = runner.host_calls[n_host:]
    assert ("edge", "ip route replace 10.0.0.0/24 dev edge-eth1 src 10.0.0.100") in swap
    assert ("d1", "arp -s 10.0.0.100 00:00:00:00:00:0c") in swap
    assert ("d10", "arp -s 10.0.0.100 00:00:00:00:00:0c") in swap
    assert d.state["path"] == BACKUP


def test_apply_reroute_relay_is_path_specific(tmp_path):
    """The relay-switch step is path-specific: backup learns 0c on s3, primary
    keeps 0b on s2. s2 already has 0b=>2 from the rules, so primary's relay step
    is correctly idempotent (no spurious CLI), but the end state is guaranteed."""
    d, runner, _ = rig(tmp_path)
    d.apply(Candidate(REROUTE, (BACKUP,)))
    assert any(e.key == "00:00:00:00:00:0c" and e.args == ("2",)
               for e in d.table_state()["s3"])
    n_cli = len(runner.cli_calls)
    d.apply(Candidate(REROUTE, (PRIMARY,)))
    # s2 (primary relay) already forwards 0b=>2 from the rules: idempotent no-op
    assert not any(sw == "s2" for sw, _ in runner.cli_calls[n_cli:])
    assert any(e.key == "00:00:00:00:00:0b" and e.args == ("2",)
               for e in d.table_state()["s2"])
    assert d.state["path"] == PRIMARY


def test_apply_regen_refreshes_tables_from_dump(tmp_path):
    def handler(switch, text):
        if text.startswith("table_dump forward_table"):
            return FORWARD_DUMP
        if text.startswith("table_dump"):
            return ""
        return auto_handles()(switch, text)
    runner = ScriptedRunner(handler)
    d = Deployer(runner, host_map={"F1": ["d4"]})
    d.deploy(make_spec(tmp_path))
    d.apply(Candidate(REGEN, ("s1", "table_modify forward_table forward 2 => 12")))
    keys = {e.key for e in d.table_state()["s1"]}
    assert keys == {"00:00:00:00:00:01", "00:00:00:00:00:0b"}   # dump is truth


# ---------------- capture / rollback / re_push ----------------

def entry_semantics(entries):
    return {(e.table, e.key, e.action, tuple(e.args)) for e in entries}


def test_rollback_restores_semantics(tmp_path):
    d, runner, _ = rig(tmp_path, qos={"tbf_rate_mbit": 80})
    snap = d.capture()
    d.apply(Candidate(TUNE, ("tbf_rate_mbit", 45)))
    d.apply(Candidate(REROUTE, (BACKUP,)))

    d.rollback(snap)
    assert d.state == {"path": PRIMARY, "knobs": {"tbf_rate_mbit": 80}}
    # both switches the reroute touched are restored to snapshot semantics
    assert entry_semantics(d.table_state()["s1"]) \
        == entry_semantics(snap.switch_table_dumps["s1"])
    assert entry_semantics(d.table_state()["s3"]) \
        == entry_semantics(snap.switch_table_dumps["s3"])
    # the reroute-added 0c entries on s1 (handle 3) and s3 (handle 1) are deleted
    deletes = [cmd for _, cmd in runner.cli_calls if "table_delete" in cmd]
    assert any("table_delete forward_table 3" in c for c in deletes)   # s1
    assert any("table_delete forward_table 1" in c for c in deletes)   # s3
    # ...the qos restored, and the edge identity swapped back to primary
    assert any("rate 80mbit" in cmd for _, cmd in runner.host_calls)
    assert runner.host_calls[-1] == ("edge", "arp -s 10.0.0.10 00:00:00:00:00:0a")
    assert ("edge", "ip route replace 10.0.0.0/24 dev edge-eth0 src 10.0.0.100") \
        in runner.host_calls[-37:]


def test_rollback_is_a_noop_when_nothing_changed(tmp_path):
    d, runner, _ = rig(tmp_path, qos={"tbf_rate_mbit": 80})
    snap = d.capture()
    n_cli, n_host = len(runner.cli_calls), len(runner.host_calls)
    d.rollback(snap)
    assert (len(runner.cli_calls), len(runner.host_calls)) == (n_cli, n_host)


def test_rollback_deletes_regen_added_entries(tmp_path):
    extra_dump = FORWARD_DUMP.replace(
        "==========\nDumping default entry",
        """**********
Dumping entry 0x5
Match key:
* ethernet.dstAddr    : EXACT     000000000002
Action entry: MyIngress.forward - 02
==========
Dumping default entry""")
    priority_dump = """Dumping entry 0x0
Match key:
* ipv4.dstAddr    : EXACT     0a000064
Action entry: MyIngress.set_low_latency_class -
"""
    def handler(switch, text):
        if text.startswith("table_dump forward_table"):
            return extra_dump
        if text.startswith("table_dump priority_table"):
            return priority_dump
        if text.startswith("table_dump"):
            return ""
        return auto_handles()(switch, text)

    runner = ScriptedRunner(handler)
    d = Deployer(runner, host_map={"F1": ["d4"]})
    d.deploy(make_spec(tmp_path))
    snap = d.capture()
    d.apply(Candidate(REGEN, ("s1", "table_add forward_table forward 00:00:00:00:00:02 => 2")))
    assert len(d.table_state()["s1"]) == 4            # extra entry tracked

    d.rollback(snap)
    assert "table_delete forward_table 5" in runner.cli_calls[-1][1]
    assert entry_semantics(d.table_state()["s1"]) \
        == entry_semantics(snap.switch_table_dumps["s1"])


def test_re_push_reinstalls_everything(tmp_path):
    d, runner, snap = rig(tmp_path, qos={"tbf_rate_mbit": 80})
    n_cli, n_host = len(runner.cli_calls), len(runner.host_calls)
    d.re_push(snap)
    # one install batch (table_add text) per switch; dumps are empty here so no
    # deletes — the reset is a no-op on an already-empty (restarted) switch
    installs = [c for sw, c in runner.cli_calls[n_cli:] if c.startswith("table_add")]
    assert len(installs) == 3
    after = runner.host_calls[n_host:]
    assert any("rate 80mbit" in cmd for _, cmd in after)        # qos re-applied
    assert any("edge-eth0" in cmd for _, cmd in after)          # path re-bound


def test_recover_switch_restores_committed_state_after_restart(tmp_path):
    """M6 watchdog: after a switch restarts with EMPTY tables (dump returns
    nothing), recover_switch re-adds the snapshot's committed entries — only that
    switch, and the snapshot's state, not the base binding."""
    d, runner, snap = rig(tmp_path, qos={"tbf_rate_mbit": 80})
    want = snap.switch_table_dumps["s1"]
    assert len(want) >= 1
    n_cli = len(runner.cli_calls)
    d.recover_switch("s1", snap)                          # dump now empty -> re-add all
    batch = [c for sw, c in runner.cli_calls[n_cli:] if sw == "s1" and "table_add" in c]
    assert len(batch) == 1
    assert batch[0].count("table_add") == len(want)
    assert all(sw == "s1" for sw, _ in runner.cli_calls[n_cli:])   # other switches untouched


def test_re_push_clears_residual_entries_first(tmp_path):
    """The M4-node fix: re_push is idempotent. If a switch kept its tables
    (only some restarted), the residual entries are deleted from a live dump
    before reinstalling, so BMv2 never sees a DUPLICATE_ENTRY re-add."""
    def handler(switch, text):
        if text.startswith("table_dump forward_table") and switch == "s1":
            return FORWARD_DUMP                        # 2 stale entries: handles 1,2
        if text.startswith("table_dump"):
            return ""
        return auto_handles()(switch, text)
    runner = ScriptedRunner(handler)
    d = Deployer(runner, host_map={"F1": ["d4"]})
    snap = d.deploy(make_spec(tmp_path, qos={"tbf_rate_mbit": 80}))
    n_cli = len(runner.cli_calls)
    d.re_push(snap)
    deletes = [c for sw, c in runner.cli_calls[n_cli:] if "table_delete" in c]
    assert any("table_delete forward_table 1" in c for c in deletes)
    assert any("table_delete forward_table 2" in c for c in deletes)


def test_table_state_is_isolated_from_mutation(tmp_path):
    d, runner, _ = rig(tmp_path)
    view = d.table_state()
    view["s1"][0].args = ("99",)
    assert d.table_state()["s1"][0].args != ("99",)
