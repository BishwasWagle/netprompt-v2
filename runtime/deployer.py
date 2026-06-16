"""Deployer — command/parse layer (M4 local half; design §10).

Implements the Deployer protocol (contracts.py) over an injectable Runner:

    runner.run_cli(switch, commands_text) -> str   # simple_switch_CLI stdin
    runner.run_host(host, command) -> str          # tc in the host's namespace

All command construction, output parsing, state tracking, and rollback-diff
logic is pure and tested locally against canned BMv2 CLI output. The node
half (M4) supplies a real Runner (subprocess/SSH + mnexec) and verifies the
output formats against the live CLI (§10.7 spike).

Rollback restores SEMANTICS (key/action/args per table), not handle numbers:
a deleted+re-added entry gets a fresh handle, which the deployer re-parses
and tracks. ConfigSnapshot comparisons should therefore use entry semantics,
not handles.
"""
from __future__ import annotations

import re
from pathlib import Path

from runtime import config
from runtime.contracts import (
    BACKUP, PRIMARY, REGEN, REROUTE, TUNE,
    Candidate, ConfigSnapshot, DeploymentSpec, GateResult, TableEntry,
)


class DeployError(Exception):
    pass


# ---------------- parsers (formats verified by the M0 spike) ----------------

_HANDLE_RE = re.compile(r"Entry has been added with handle (\d+)")
_DUMP_HANDLE_RE = re.compile(r"Dumping entry (0x[0-9a-fA-F]+)")
_DUMP_KEY_RE = re.compile(r"\*\s+\S+\s*:\s*EXACT\s+([0-9a-fA-F]+)")
_DUMP_ACTION_RE = re.compile(r"Action entry:\s*(\S+)\s*-\s*(.*)$")


def parse_handles(output: str) -> list:
    """All entry handles from table_add output, in command order."""
    return [int(h) for h in _HANDLE_RE.findall(output)]


def hex_to_mac(h: str) -> str:
    if len(h) != 12:
        raise DeployError(f"expected 12 hex chars for a MAC, got {h!r}")
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).lower()


def hex_to_ipv4(h: str) -> str:
    if len(h) != 8:
        raise DeployError(f"expected 8 hex chars for an IPv4, got {h!r}")
    return ".".join(str(int(h[i:i + 2], 16)) for i in range(0, 8, 2))


def parse_table_dump(output: str, table: str) -> list:
    """BMv2 `table_dump <table>` output -> [TableEntry]. The default entry
    is skipped (it is the P4 default_action, not an installed entry).
    forward_table keys are MACs; policy-table keys are IPv4s."""
    entries = []
    handle = key = None
    for line in output.splitlines():
        if "Dumping default entry" in line:
            break
        m = _DUMP_HANDLE_RE.search(line)
        if m:
            handle, key = int(m.group(1), 16), None
            continue
        m = _DUMP_KEY_RE.search(line)
        if m and handle is not None:
            raw = m.group(1)
            key = hex_to_mac(raw) if table == "forward_table" else hex_to_ipv4(raw)
            continue
        m = _DUMP_ACTION_RE.search(line)
        if m and handle is not None and key is not None:
            action = m.group(1).split(".")[-1]            # strip control prefix
            raw_args = m.group(2).strip()
            args = tuple(str(int(a.strip(), 16))
                         for a in raw_args.split(",") if a.strip())
            entries.append(TableEntry(table, key, action, args, handle))
            handle = key = None
    return entries


_RULE_LINE_RE = re.compile(
    r"^table_add\s+(\S+)\s+(\S+)\s+(\S+)\s*=>\s*(.*)$")


def parse_rule_line(line: str):
    """One rule-file line -> (table, action, key, args) or None for blanks."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _RULE_LINE_RE.match(line)
    if not m:
        raise DeployError(f"unparseable rule line: {line!r}")
    table, action, key = m.group(1), m.group(2), m.group(3).lower()
    args = tuple(m.group(4).split())
    return table, action, key, args


# ---------------- command builders ----------------

def tc_command(knob: str, value, dev: str) -> str:
    if knob not in config.TC_TEMPLATES:
        raise DeployError(f"no tc template for knob {knob!r}")
    return config.TC_TEMPLATES[knob].format(dev=dev, value=value)


def modify_command(table: str, action: str, handle: int, args: tuple) -> str:
    arg_str = " ".join(str(a) for a in args)
    return f"table_modify {table} {action} {handle} => {arg_str}".rstrip()


def add_command(table: str, action: str, key: str, args: tuple) -> str:
    arg_str = " ".join(str(a) for a in args)
    return f"table_add {table} {action} {key} => {arg_str}".rstrip()


def delete_command(table: str, handle: int) -> str:
    return f"table_delete {table} {handle}"


# ---------------- the deployer ----------------

def _host_dev(host: str) -> str:
    return f"{host}-eth0"


class Deployer:
    """Tracks installed table entries (with handles), per-host qos, and the
    active path; exposes the protocol surface the engine consumes."""

    def __init__(self, runner, host_map: dict):
        """host_map: {field_id: [host, ...]} — which hosts a TUNE candidate
        touches for a given target field (contracts: candidates are
        host-agnostic; the deployer resolves them from the active spec)."""
        self.runner = runner
        self.host_map = host_map
        self.spec: DeploymentSpec | None = None
        self._tables: dict = {}            # switch -> [TableEntry]
        self._qos: dict = {}               # host -> {knob: value}
        self._path: str = PRIMARY          # active path (host-side fact, §10.1)

    # ---- protocol: state / table_state / capture ----

    @property
    def state(self) -> dict:
        return {"path": self._path, "knobs": self._target_knobs()}

    def table_state(self) -> dict:
        return {sw: [TableEntry(e.table, e.key, e.action, tuple(e.args), e.handle)
                     for e in entries]
                for sw, entries in self._tables.items()}

    def capture(self) -> ConfigSnapshot:
        return ConfigSnapshot(
            correlation_id=self.spec.correlation_id if self.spec else "",
            sfc=self.spec.sfc if self.spec else "",
            binding=dict(self.spec.binding) if self.spec else {},
            switch_table_dumps=self.table_state(),
            qos_state={h: dict(k) for h, k in self._qos.items()},
            active_path=self._path,
        )

    # ---- protocol: deploy / apply / rollback / re_push ----

    def deploy(self, spec: DeploymentSpec) -> ConfigSnapshot:
        self.spec = spec
        self._tables = {}
        for switch, key in config.SWITCH_RULES_KEYS.items():
            text = Path(spec.binding[key]).read_text()
            # Idempotent install: clear any residual entries first so deploy is
            # safe at episode start on a resident network (launch_network.py
            # already populated the tables) — a blind re-add hits DUPLICATE_ENTRY
            # and emits no handle, like re_push. (M4-node finding.)
            self._reset_switch(switch)
            self._install_rules(switch, text)
        # Issue-2 / Model B: establish the field's deployer-owned baseline qdisc
        # so a later TUNE is reversible (rollback reverts to these values). Prefer
        # the binding's own qos; fall back to the per-SFC baseline when it omits
        # one (until the planner supplies it — M-K).
        initial_qos = spec.binding.get("qos") or config.SFC_QOS_BASELINE.get(spec.sfc, {})
        self._qos = {}
        for host in self.host_map.get(spec.target_field, []):
            self._qos[host] = {}
            for knob, value in initial_qos.items():
                self._set_knob(host, knob, value)
        # Active path: backup-flavored bindings install the 0c identity
        # (milestone-II-latest); align the host side with the rules.
        backup = ("backup" in str(spec.binding.get("policy_type", "")).lower()
                  or (self._find_forward("s1", config.EDGE_MAC_BACKUP) is not None
                      and self._find_forward("s1", config.EDGE_MAC) is None))
        self._set_path(BACKUP if backup else PRIMARY, force=True)
        return self.capture()

    def apply(self, cand: Candidate) -> None:
        if cand.kind == TUNE:
            knob, value = cand.params
            for host in self.host_map[self.spec.target_field]:
                self._set_knob(host, knob, value)
        elif cand.kind == REROUTE:
            # Multi-switch action (design §10.1, milestone-II-latest mechanics;
            # M0/C2 finding): (1) s1 forwards the target path's edge MAC to its
            # relay port; (2) the destination RELAY switch forwards that same
            # edge MAC to the edge (a primary SFC's s3 lacks the 0c entry, so
            # without this the frame dies at s3); (3) bind 10.0.0.100 to that
            # path's edge interface; (4) repoint every drone's static ARP — (3)
            # and (4) are _set_path, which also re-arps the edge->drones.
            (path,) = cand.params
            mac = config.EDGE_MACS[path]
            relay, relay_port = config.RELAY_EDGE[path]
            # Order for consistency at every prefix: install the destination
            # relay's edge-MAC entry FIRST (a forward the old path doesn't use —
            # harmless), THEN repoint s1, THEN flip the host side. A failure
            # partway leaves the still-working old path (a superset), not a
            # blackhole; the engine then rolls back from the pre-apply snapshot.
            self._ensure_forward(relay, mac, relay_port)
            self._ensure_forward("s1", mac, config.EDGE_PORTS[path])
            self._set_path(path)
        elif cand.kind == REGEN:
            switch, rules_text = cand.params
            out = self.runner.run_cli(switch, rules_text)
            # simple_switch_CLI exits 0 even when a single line in the batch
            # fails (printing the error to stdout); surface that so the engine
            # rolls back rather than committing a partially-applied (possibly
            # blackholing) config the gate proved safe only as a whole.
            bad = next((l for l in out.splitlines()
                        if any(m in l.lower() for m in
                               ("invalid", "error", "exception",
                                "does not exist", "already exists"))), None)
            if bad:
                raise DeployError(f"{switch}: regen apply error: {bad.strip()[:200]}")
            self._refresh_tables(switch)
        else:
            raise DeployError(f"unknown candidate kind {cand.kind!r}")

    def rollback(self, snapshot: ConfigSnapshot) -> None:
        for host, knobs in snapshot.qos_state.items():
            current = self._qos.get(host, {})
            for knob, value in knobs.items():
                if current.get(knob) != value:
                    self._set_knob(host, knob, value)
            # The qdisc is 'replace root', so applying the snapshot's knob already
            # overwrote any DIFFERENT live knob's root qdisc — sync the bookkeeping
            # so self._qos can't retain a knob the live host no longer has.
            self._qos[host] = dict(knobs)
        for switch, want_entries in snapshot.switch_table_dumps.items():
            self._restore_tables(switch, want_entries)
        if snapshot.active_path != self._path:
            self._set_path(snapshot.active_path)     # host side back too

    def re_push(self, snapshot: ConfigSnapshot) -> None:
        """Same revision, fresh full install (rung-1 system fix). Resets each
        switch from its ACTUAL current entries first, so the install is clean
        regardless of residual state: a restarted switch dumps empty (nothing
        to delete); a switch that kept its tables is cleared. (M4-node: BMv2
        rejects a re-add of an existing key with DUPLICATE_ENTRY and emits no
        handle for it, so a blind reinstall trips the handle-count check.)"""
        for switch, key in config.SWITCH_RULES_KEYS.items():
            text = Path(snapshot.binding[key]).read_text()
            self._reset_switch(switch)
            self._install_rules(switch, text)
        for host, knobs in snapshot.qos_state.items():
            for knob, value in knobs.items():
                self._set_knob(host, knob, value)
        self._set_path(snapshot.active_path, force=True)

    def recover_switch(self, switch: str, snapshot: ConfigSnapshot) -> None:
        """Re-sync ONE switch to `snapshot`'s table state after it was restarted
        (M6 watchdog, rung-1): the process restart is switch_control's job; this
        restores the CURRENT committed config (incl. a reroute's entries), not the
        base binding. Diffs by (table, KEY) — NOT handle — because a restart
        re-numbers every handle, so a handle-based diff would churn (delete-all +
        add-all, racy). When the post-restart base already matches the snapshot
        (the common no-adaptation case) the diff is empty and nothing is touched."""
        self._refresh_tables(switch)                      # live post-restart state
        live = {(e.table, e.key): e for e in self._tables.get(switch, [])}
        want = {(e.table, e.key): e
                for e in snapshot.switch_table_dumps.get(switch, [])}
        commands, n_adds = [], 0
        for tk, w in want.items():
            e = live.get(tk)
            if e is None:
                commands.append(add_command(w.table, w.action, w.key, w.args))
                n_adds += 1
            elif (e.action, tuple(e.args)) != (w.action, tuple(w.args)):
                commands.append(modify_command(w.table, w.action, e.handle, w.args))
        for tk, e in live.items():
            if tk not in want:
                commands.append(delete_command(e.table, e.handle))
        if commands:
            out = self.runner.run_cli(switch, "\n".join(commands))
            if len(parse_handles(out)) != n_adds:     # catch a partial CLI apply
                raise DeployError(
                    f"{switch}: recover re-added {n_adds} entries but parsed "
                    f"{len(parse_handles(out))} handles")
            self._refresh_tables(switch)                  # resync to fresh handles

    def dry_install(self, switch: str, rules_text: str) -> GateResult:
        """Gate L3 hook: apply `rules_text` to the LIVE switch, check it installed
        cleanly, then ROLL BACK — catches a real DUPLICATE_ENTRY / handle-drift
        error the gate's L2 simulation can't. Briefly mutates the switch, so it is
        only wired (ValidationGate(dry_install_fn=...)) when L3 is explicitly on."""
        before = list(self.table_state().get(switch, []))
        try:
            out = self.runner.run_cli(switch, rules_text)
        except Exception as e:                            # noqa: BLE001 (best-effort probe)
            return GateResult(False, f"L3: dry-install failed on {switch}: {e}")
        bad = next((l for l in out.splitlines()
                    if any(m in l.lower() for m in
                           ("invalid", "error", "exception",
                            "does not exist", "already exists"))), None)
        self._refresh_tables(switch)                      # see the post-apply state...
        self._restore_tables(switch, before)              # ...then undo it
        if bad:
            return GateResult(False, f"L3: dry-install error on {switch}: {bad.strip()[:200]}")
        return GateResult(True)

    def _reset_switch(self, switch: str) -> None:
        """Delete every entry currently on `switch`, read from a live dump so
        the handles are real even after a restart re-numbered them."""
        self._refresh_tables(switch)
        for e in list(self._tables.get(switch, [])):
            self.runner.run_cli(switch, delete_command(e.table, e.handle))
        self._tables[switch] = []

    # ---- internals ----

    def _install_rules(self, switch: str, text: str) -> None:
        parsed = [p for p in (parse_rule_line(l) for l in text.splitlines()) if p]
        out = self.runner.run_cli(switch, text)
        handles = parse_handles(out)
        if len(handles) != len(parsed):
            raise DeployError(
                f"{switch}: {len(parsed)} table_add lines but "
                f"{len(handles)} handles in CLI output")
        self._tables[switch] = [
            TableEntry(table, key, action, args, h)
            for (table, action, key, args), h in zip(parsed, handles)]

    def _set_knob(self, host: str, knob: str, value) -> None:
        self.runner.run_host(host, tc_command(knob, value, _host_dev(host)))
        self._qos.setdefault(host, {})[knob] = value

    def _find_forward(self, switch: str, mac: str) -> TableEntry | None:
        for e in self._tables.get(switch, []):
            if e.table == "forward_table" and e.key == mac:
                return e
        return None

    def _ensure_forward(self, switch: str, mac: str, port: int) -> None:
        """Idempotently make `switch` forward `mac` to `port` (add or modify),
        keeping self._tables in sync. The reroute's per-switch primitive (M0/C2:
        both s1 and the destination relay must point the edge identity)."""
        args = (str(port),)
        entry = self._find_forward(switch, mac)
        if entry is None:
            out = self.runner.run_cli(switch, add_command(
                "forward_table", "forward", mac, args))
            handles = parse_handles(out)
            if len(handles) != 1:
                raise DeployError(
                    f"reroute: could not parse new entry handle on {switch}")
            self._tables.setdefault(switch, []).append(TableEntry(
                "forward_table", mac, "forward", args, handles[0]))
        elif entry.args != args:
            out = self.runner.run_cli(switch, modify_command(
                entry.table, entry.action, entry.handle, args))
            if any(m in out.lower() for m in ("invalid", "error", "no entry")):
                raise DeployError(f"{switch}: forward modify failed: {out.strip()[:200]}")
            entry.args = args

    def _set_path(self, path: str, force: bool = False) -> None:
        """Bind the edge identity + drone ARP to `path` (mirrors the
        milestone-II-latest configure_edge_interface_for_path /
        configure_static_arp_for_path mechanics)."""
        if not force and path == self._path:
            return
        mac, iface = config.EDGE_MACS[path], config.EDGE_IFACES[path]
        edge = config.EDGE_HOST
        for cmd in (
            "ip addr flush dev edge-eth0 || true",
            "ip addr flush dev edge-eth1 || true",
            "ip link set dev edge-eth0 up || true",
            "ip link set dev edge-eth1 up || true",
            f"ip link set dev {iface} address {mac} || true",
            # `replace` (not `add`) is idempotent — `add` raises "File exists" and
            # aborts _set_path mid-sequence (leaving stale routes/ARP) if the
            # address is already present.
            f"ip addr replace {config.EDGE_IP}/24 dev {iface}",
            f"ip route replace {config.SUBNET} dev {iface} src {config.EDGE_IP}",
        ):
            self.runner.run_host(edge, cmd)
        for i, drone in enumerate(config.DRONE_HOSTS, start=1):
            self.runner.run_host(drone, "arp -d 10.0.0.100 2>/dev/null || true")
            self.runner.run_host(drone, f"arp -s {config.EDGE_IP} {mac}")
            self.runner.run_host(edge, f"arp -s 10.0.0.{i} 00:00:00:00:00:{i:02x}")
        self._path = path

    def _target_knobs(self) -> dict:
        if not self.spec:
            return {}
        hosts = self.host_map.get(self.spec.target_field, [])
        return dict(self._qos.get(hosts[0], {})) if hosts else {}

    def _refresh_tables(self, switch: str) -> None:
        entries = []
        for table in ("forward_table", "priority_table", "relay_policy_table"):
            out = self.runner.run_cli(switch, f"table_dump {table}")
            entries.extend(parse_table_dump(out, table))
        self._tables[switch] = entries

    def _restore_tables(self, switch: str, want: list) -> None:
        """Diff current vs snapshot by (table, KEY) — NOT handle — and emit
        modify/delete/add. Handles drift across a switch restart, so a
        handle-based diff mis-pairs entries (the same rule recover_switch obeys).
        Modify/delete use the LIVE handle; re-added entries get fresh handles."""
        live = {(e.table, e.key): e for e in self._tables.get(switch, [])}
        wanted = {(e.table, e.key): e for e in want}

        commands, readds = [], []
        for tk, w in wanted.items():
            e = live.get(tk)
            if e is None:
                commands.append(add_command(w.table, w.action, w.key, w.args))
                readds.append(w)
            elif (e.action, tuple(e.args)) != (w.action, tuple(w.args)):
                commands.append(modify_command(w.table, w.action, e.handle, w.args))
        for tk, e in live.items():
            if tk not in wanted:
                commands.append(delete_command(e.table, e.handle))

        if not commands:
            return
        out = self.runner.run_cli(switch, "\n".join(commands))
        new_handles = parse_handles(out)
        if len(new_handles) != len(readds):
            raise DeployError(
                f"{switch}: rollback re-added {len(readds)} entries but "
                f"parsed {len(new_handles)} handles")
        # rebuild by key: kept/modified entries keep their LIVE handle; re-adds
        # take the freshly parsed handles (same wanted-iteration order).
        fresh = iter(new_handles)
        self._tables[switch] = [
            TableEntry(w.table, w.key, w.action, tuple(w.args),
                       live[tk].handle if tk in live else next(fresh))
            for tk, w in wanted.items()]
