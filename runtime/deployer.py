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
    Candidate, ConfigSnapshot, DeploymentSpec, TableEntry,
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
            self._install_rules(switch, text)
        initial_qos = spec.binding.get("qos", {})
        self._qos = {}
        for host in self.host_map.get(spec.target_field, []):
            self._qos[host] = {}
            for knob, value in initial_qos.items():
                self._set_knob(host, knob, value)
        # Active path: backup-flavored bindings install the 0c identity
        # (milestone-II-latest); align the host side with the rules.
        backup = ("backup" in str(spec.binding.get("policy_type", "")).lower()
                  or (self._find_s1_forward(config.EDGE_MAC_BACKUP) is not None
                      and self._find_s1_forward(config.EDGE_MAC) is None))
        self._set_path(BACKUP if backup else PRIMARY, force=True)
        return self.capture()

    def apply(self, cand: Candidate) -> None:
        if cand.kind == TUNE:
            knob, value = cand.params
            for host in self.host_map[self.spec.target_field]:
                self._set_knob(host, knob, value)
        elif cand.kind == REROUTE:
            # Three-part action (design §10.1, milestone-II-latest mechanics):
            # (1) ensure s1 forwards the target path's edge MAC to its relay
            # port; (2) bind 10.0.0.100 to that path's edge interface;
            # (3) repoint every drone's static ARP at that MAC.
            (path,) = cand.params
            mac, port = config.EDGE_MACS[path], config.EDGE_PORTS[path]
            entry = self._find_s1_forward(mac)
            if entry is None:
                out = self.runner.run_cli("s1", add_command(
                    "forward_table", "forward", mac, (str(port),)))
                handles = parse_handles(out)
                if len(handles) != 1:
                    raise DeployError("reroute: could not parse new entry handle")
                self._tables.setdefault("s1", []).append(TableEntry(
                    "forward_table", mac, "forward", (str(port),), handles[0]))
            elif entry.args != (str(port),):
                self.runner.run_cli("s1", modify_command(
                    entry.table, entry.action, entry.handle, (str(port),)))
                entry.args = (str(port),)
            self._set_path(path)
        elif cand.kind == REGEN:
            switch, rules_text = cand.params
            self.runner.run_cli(switch, rules_text)
            self._refresh_tables(switch)
        else:
            raise DeployError(f"unknown candidate kind {cand.kind!r}")

    def rollback(self, snapshot: ConfigSnapshot) -> None:
        for host, knobs in snapshot.qos_state.items():
            current = self._qos.get(host, {})
            for knob, value in knobs.items():
                if current.get(knob) != value:
                    self._set_knob(host, knob, value)
        for switch, want_entries in snapshot.switch_table_dumps.items():
            self._restore_tables(switch, want_entries)
        if snapshot.active_path != self._path:
            self._set_path(snapshot.active_path)     # host side back too

    def re_push(self, snapshot: ConfigSnapshot) -> None:
        """Same revision, fresh full install (rung-1 system fix)."""
        for switch, key in config.SWITCH_RULES_KEYS.items():
            text = Path(snapshot.binding[key]).read_text()
            self._install_rules(switch, text)
        for host, knobs in snapshot.qos_state.items():
            for knob, value in knobs.items():
                self._set_knob(host, knob, value)
        self._set_path(snapshot.active_path, force=True)

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

    def _find_s1_forward(self, mac: str) -> TableEntry | None:
        for e in self._tables.get("s1", []):
            if e.table == "forward_table" and e.key == mac:
                return e
        return None

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
            f"ip addr add {config.EDGE_IP}/24 dev {iface}",
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
        """Diff current vs snapshot entries; emit modify/delete/add. Re-added
        entries get fresh handles (parsed from output) — semantics restored,
        handle numbers not guaranteed."""
        current = {e.handle: e for e in self._tables.get(switch, [])}
        wanted = {e.handle: e for e in want}

        commands, readds = [], []
        for h, e in current.items():
            w = wanted.get(h)
            if w is None or w.table != e.table or w.key != e.key:
                commands.append(delete_command(e.table, h))
            elif (w.action, tuple(w.args)) != (e.action, tuple(e.args)):
                commands.append(modify_command(w.table, w.action, h, w.args))
        for h, w in wanted.items():
            e = current.get(h)
            if e is None or e.table != w.table or e.key != w.key:
                commands.append(add_command(w.table, w.action, w.key, w.args))
                readds.append(w)

        if not commands:
            return
        out = self.runner.run_cli(switch, "\n".join(commands))
        new_handles = parse_handles(out)
        if len(new_handles) != len(readds):
            raise DeployError(
                f"{switch}: rollback re-added {len(readds)} entries but "
                f"parsed {len(new_handles)} handles")

        rebuilt = []
        for h, w in wanted.items():
            e = current.get(h)
            if e is not None and e.table == w.table and e.key == w.key:
                rebuilt.append(TableEntry(w.table, w.key, w.action,
                                          tuple(w.args), h))
        for w, nh in zip(readds, new_handles):
            rebuilt.append(TableEntry(w.table, w.key, w.action,
                                      tuple(w.args), nh))
        self._tables[switch] = rebuilt
