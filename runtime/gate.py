"""Validation Gate — the sound, pre-deploy authority (design §11).

Layered, cheapest-first:
  L0  syntax/grammar  — parses as simple_switch_CLI; known tables/actions;
                        well-formed keys; valid ports
  L1  envelope bounds — tier in legal_tiers, knob value in knob_ranges,
                        path in legal_paths
  L2  safety invariants — simulate the commands against current table state;
                        every required MAC must remain forwarded to a valid
                        port (forward_table default_action is drop(), so an
                        unrouted or drop-actioned MAC is a blackhole)
  L3  dry-install     — node-only (M0/M4); not implemented here

Reasons are stable "L<n>: ..." strings — they feed the adapt engine's `tried`
set and the escalation trace, so a rejected candidate is reproducibly rejected.

The regen candidate shape is Candidate(REGEN, (switch, rules_text)). The
table_modify parser accepts both `... <handle> => <args>` and
`... <handle> <args>` until the M0 spike pins the CLI's exact syntax.
"""
from __future__ import annotations

import re

from runtime.config import DRONE_MACS, EDGE_MACS, SWITCH_PORTS
from runtime.contracts import (
    BACKUP, PRIMARY, REGEN, REROUTE, TUNE,
    Candidate, DeploymentSpec, Envelope, GateResult, TableEntry,
)

# Tables and their legal actions, from the actual P4 programs (design §10.1).
KNOWN_TABLES = {
    "forward_table": {"forward", "drop", "NoAction"},
    "priority_table": {"set_low_latency_class", "NoAction"},
    "relay_policy_table": {"mark_reliable", "use_backup_relay", "NoAction"},
}
# Actions that take (port,) vs no args.
PORT_ACTIONS = {"forward"}
NOARG_ACTIONS = {"drop", "NoAction", "set_low_latency_class",
                 "mark_reliable", "use_backup_relay"}

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

ALL_PATHS = {PRIMARY, BACKUP}
BINDING_KEYS = {"p4_json", "access_rules", "relay_rules", "backup_rules", "policy_type"}


def _ok() -> GateResult:
    return GateResult(True)


def _reject(reason: str) -> GateResult:
    return GateResult(False, reason)


class ValidationGate:

    def __init__(self, required_macs: tuple = DRONE_MACS,
                 edge_macs: tuple = tuple(EDGE_MACS.values())):
        self.required_macs = tuple(m.lower() for m in required_macs)
        self.edge_macs = tuple(m.lower() for m in edge_macs)

    # ---------------- entry point 1: pre-deploy binding ----------------

    def check_binding(self, spec: DeploymentSpec) -> GateResult:
        missing = BINDING_KEYS - set(spec.binding)
        if missing:
            return _reject(f"L0: binding missing keys {sorted(missing)}")
        env = self._check_envelope(spec.envelope)
        if not env.ok:
            return env
        return _ok()

    def _check_envelope(self, env: Envelope) -> GateResult:
        if min(env.max_latency_ms, env.min_bandwidth_mbps, env.max_loss_percent) <= 0:
            return _reject("L0: envelope bounds must be positive")
        if not set(env.legal_paths) <= ALL_PATHS or not env.legal_paths:
            return _reject(f"L0: legal_paths must be a non-empty subset of {sorted(ALL_PATHS)}")
        if not set(env.legal_tiers) <= {TUNE, REROUTE, REGEN}:
            return _reject("L0: unknown tier in legal_tiers")
        for knob, (lo, hi) in env.knob_ranges.items():
            if lo > hi:
                return _reject(f"L0: knob_ranges[{knob}] has lo > hi")
        return _ok()

    # ---------------- entry point 2: adapt candidates ----------------

    def check(self, cand: Candidate, env: Envelope,
              current_tables: dict | None = None) -> GateResult:
        if cand.kind == TUNE:
            return self._check_tune(cand, env)
        if cand.kind == REROUTE:
            return self._check_reroute(cand, env)
        if cand.kind == REGEN:
            return self._check_regen(cand, env, current_tables)
        return _reject(f"L0: unknown candidate kind {cand.kind!r}")

    def _check_tune(self, cand: Candidate, env: Envelope) -> GateResult:
        if TUNE not in env.legal_tiers:
            return _reject("L1: tune tier not legal for this SFC")
        knob, value = cand.params
        if knob not in env.knob_ranges:
            return _reject(f"L1: unknown knob {knob!r}")
        lo, hi = env.knob_ranges[knob]
        if not lo <= value <= hi:
            return _reject(f"L1: {knob}={value} outside range ({lo}, {hi})")
        return _ok()

    def _check_reroute(self, cand: Candidate, env: Envelope) -> GateResult:
        (path,) = cand.params
        if path not in ALL_PATHS:
            return _reject(f"L0: unknown path {path!r}")
        if REROUTE not in env.legal_tiers:
            return _reject("L1: reroute tier not legal for this SFC")
        if path not in env.legal_paths:
            return _reject(f"L1: path {path!r} not in legal_paths")
        return _ok()

    # ---------------- regen: parse, simulate, invariant ----------------

    def _check_regen(self, cand: Candidate, env: Envelope,
                     current_tables: dict | None) -> GateResult:
        if REGEN not in env.legal_tiers:
            return _reject("L1: regen tier not legal for this SFC")
        switch, rules_text = cand.params
        if switch not in SWITCH_PORTS:
            return _reject(f"L0: unknown switch {switch!r}")

        commands = []
        for line in rules_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = self._parse_command(line, switch)
            if isinstance(parsed, GateResult):
                return parsed
            commands.append(parsed)
        if not commands:
            return _reject("L0: regen contains no commands")

        if current_tables is None or switch not in current_tables:
            # Sound = conservative: without state we cannot prove the
            # invariant, so we refuse rather than hope.
            return _reject("L2: no current table state for invariant check")
        return self._simulate(commands, current_tables[switch], switch)

    def _parse_command(self, line: str, switch: str):
        """Returns a command tuple, or a GateResult rejection."""
        t = line.split()
        cmd = t[0]
        try:
            if cmd == "table_add":
                sep = t.index("=>") if "=>" in t else -1
                if sep < 4:
                    return _reject(f"L0: malformed table_add: {line!r}")
                table, action, keys, args = t[1], t[2], t[3:sep], tuple(t[sep + 1:])
                if len(keys) != 1:
                    return _reject(f"L0: expected exactly one match key: {line!r}")
                bad = self._check_table_action_key_args(table, action, keys[0], args, switch)
                if bad:
                    return bad
                return ("add", table, action, keys[0].lower(), args)
            if cmd == "table_modify":
                table, action, handle = t[1], t[2], int(t[3])
                rest = t[4:]
                if rest and rest[0] == "=>":
                    rest = rest[1:]
                args = tuple(rest)
                bad = self._check_table_action_key_args(table, action, None, args, switch)
                if bad:
                    return bad
                return ("modify", table, action, handle, args)
            if cmd == "table_delete":
                return ("delete", t[1], int(t[2]))
            return _reject(f"L0: unsupported command {cmd!r}")
        except (IndexError, ValueError):
            return _reject(f"L0: malformed command: {line!r}")

    def _check_table_action_key_args(self, table, action, key, args, switch):
        if table not in KNOWN_TABLES:
            return _reject(f"L0: unknown table {table!r}")
        if action not in KNOWN_TABLES[table]:
            return _reject(f"L0: action {action!r} not valid for {table}")
        if key is not None:
            if table == "forward_table" and not _MAC_RE.match(key.lower()):
                return _reject(f"L0: malformed MAC key {key!r}")
            if table != "forward_table" and not _IPV4_RE.match(key):
                return _reject(f"L0: malformed IPv4 key {key!r}")
        if action in PORT_ACTIONS:
            if len(args) != 1 or not args[0].isdigit():
                return _reject(f"L0: {action} needs one numeric port arg, got {args}")
            # exact-string match rejects leading zeros (e.g. '011', which
            # simple_switch_CLI may read as octal 9 != the 11 we'd validate)
            if args[0] not in {str(p) for p in SWITCH_PORTS[switch]}:
                return _reject(f"L0: port {args[0]} not valid on {switch}")
        elif action in NOARG_ACTIONS and args:
            return _reject(f"L0: {action} takes no args, got {args}")
        return None

    def _simulate(self, commands, entries: list, switch: str) -> GateResult:
        """Apply commands to a copy of the switch's entries, then check that
        every required MAC is still forwarded to a valid port (L2)."""
        by_handle = {e.handle: TableEntry(e.table, e.key, e.action, tuple(e.args), e.handle)
                     for e in entries}
        next_handle = max(by_handle, default=-1) + 1

        for c in commands:
            if c[0] == "add":
                _, table, action, key, args = c
                if any(e.table == table and e.key == key for e in by_handle.values()):
                    return _reject(f"L2: duplicate entry for {key} in {table}")
                by_handle[next_handle] = TableEntry(table, key, action, args, next_handle)
                next_handle += 1
            elif c[0] == "modify":
                _, table, action, handle, args = c
                e = by_handle.get(handle)
                if e is None or e.table != table:
                    return _reject(f"L2: no entry with handle {handle} in {table}")
                by_handle[handle] = TableEntry(table, e.key, action, args, handle)
            elif c[0] == "delete":
                _, table, handle = c
                e = by_handle.get(handle)
                if e is None or e.table != table:
                    return _reject(f"L2: no entry with handle {handle} in {table}")
                del by_handle[handle]

        # str() tolerance: real table dumps (M4) may carry ports as ints.
        routable = {e.key for e in by_handle.values()
                    if e.table == "forward_table" and e.action == "forward"
                    and e.args and str(e.args[0]).isdigit()
                    and int(str(e.args[0])) in SWITCH_PORTS[switch]}
        for mac in self.required_macs:
            if mac not in routable:
                return _reject(f"L2: {mac} not routable after regen (blackhole)")
        # Dual edge identity (milestone-II-latest, design §10.1): the edge must
        # stay reachable on at least one of its MACs. Which one is ACTIVE is a
        # host-side fact the post-deploy monitor verifies (sound vs noisy).
        if not any(m in routable for m in self.edge_macs):
            return _reject("L2: edge unreachable on every edge MAC (blackhole)")
        return _ok()
