"""Tier-2 regen grammar (design §7.3, §11 L0; resolves open question §13.3).

Two artifacts, generated from the SAME constants the gate enforces
(gate.KNOWN_TABLES / .PORT_ACTIONS / .NOARG_ACTIONS / config.SWITCH_PORTS), so
grammar and gate stay aligned:

  gbnf(switch) — a llama.cpp/vLLM-style GBNF grammar string for constrained
                decoding on the serving side (M7). It is tightened to the gate's
                L0 for `switch` along three axes:
                  * egress port bounded to SWITCH_PORTS[switch] (exact literals),
                  * match-key type conditioned on the table (forward_table -> MAC,
                    others -> IPv4),
                  * argument SHAPE conditioned on the action (a port action is
                    followed by a port; a no-arg action takes no args).
                NOT yet conditioned: which actions are legal for which table
                (per-table action sets) — a `table_modify priority_table forward`
                is still grammar-valid and the gate L0-rejects it. That residual
                over-accept is bounded and caught by validate()/the gate.
  validate(text, switch) — the local equivalent: True iff every non-blank line
                is a well-formed table_modify/table_add over known tables and
                actions, with an in-range port (no leading zeros) and the right
                key type. The proposer's stand-in for constrained decoding with
                the stub client, and defense-in-depth with the real one.

The grammar is deliberately NARROWER than the gate (no table_delete — entry
removal is never a recovery action; the gate would catch a blackhole anyway,
but the grammar removes the temptation entirely).
"""
from __future__ import annotations

import re

from runtime.config import SWITCH_PORTS
from runtime.gate import KNOWN_TABLES, NOARG_ACTIONS, PORT_ACTIONS

# forward_table keys on MAC, every other table on IPv4 (mirrors the gate's
# _check_table_action_key_args). Tuple so grammar/validate stay in lockstep if a
# second MAC-keyed table is ever added.
_MAC_TABLES = ("forward_table",)

# Non-capturing so _ADD_RE's groups stay clean: 1=table 2=action 3=key 4=args.
_MAC = r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"
_IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"
_NUM = r"\d+"


def _alts(items) -> str:
    return " | ".join(f'"{x}"' for x in sorted(items))


def gbnf(switch: str = "s1") -> str:
    """GBNF grammar for the constrained decoder (M7 serving config), tightened
    to the gate's L0 for `switch` (regen targets s1, the widest port set)."""
    tables = _alts(KNOWN_TABLES)
    mactable = _alts(_MAC_TABLES)
    iptable = _alts(t for t in KNOWN_TABLES if t not in _MAC_TABLES)
    port_act = _alts(PORT_ACTIONS)
    noarg_act = _alts(NOARG_ACTIONS)
    ports = _alts(str(p) for p in SWITCH_PORTS[switch])
    return f"""root      ::= line+
line      ::= (modify | add) "\\n"
modify    ::= "table_modify " table " " mod_op
add       ::= add_mac | add_ip
add_mac   ::= "table_add " mactable " " key_op_mac
add_ip    ::= "table_add " iptable " " key_op_ip
mod_op    ::= port_act " " num " => " port | noarg_act " " num " => "
key_op_mac ::= port_act " " mac " => " port | noarg_act " " mac " => "
key_op_ip ::= noarg_act " " ipv4 " => "
table     ::= {tables}
mactable  ::= {mactable}
iptable   ::= {iptable}
port_act  ::= {port_act}
noarg_act ::= {noarg_act}
port      ::= {ports}
mac       ::= hexpair ":" hexpair ":" hexpair ":" hexpair ":" hexpair ":" hexpair
ipv4      ::= num "." num "." num "." num
hexpair   ::= hex hex
hex       ::= [0-9a-f]
num       ::= [0-9]+
"""


_MODIFY_RE = re.compile(
    rf"^table_modify\s+(\S+)\s+(\S+)\s+{_NUM}\s*(?:=>)?\s*(.*)$")
_ADD_RE = re.compile(
    rf"^table_add\s+(\S+)\s+(\S+)\s+({_MAC}|{_IPV4})\s*=>\s*(.*)$")


def _port_ok(tok: str, switch: str) -> bool:
    """Exact match against the switch's ports — rejects leading zeros (e.g.
    '011'), which the grammar can't emit and simple_switch_CLI may read as
    octal, so validate/gate must not silently accept them either."""
    return tok in {str(p) for p in SWITCH_PORTS[switch]}


def _line_ok(line: str, switch: str) -> bool:
    m = _ADD_RE.match(line)
    if m:
        table, action, key = m.group(1), m.group(2), m.group(3)
        if table not in KNOWN_TABLES or action not in KNOWN_TABLES[table]:
            return False
        if (table in _MAC_TABLES) != (":" in key):     # key type must match table
            return False
        args = m.group(4).split()
    else:
        m = _MODIFY_RE.match(line)
        if not m:
            return False
        table, action = m.group(1), m.group(2)
        if table not in KNOWN_TABLES or action not in KNOWN_TABLES[table]:
            return False
        args = m.group(3).split()
    if action in PORT_ACTIONS:
        return len(args) == 1 and _port_ok(args[0], switch)
    if action in NOARG_ACTIONS:
        return not args
    return False


def validate(text: str, switch: str = "s1") -> bool:
    """True iff text is non-empty and every non-blank line conforms (incl. an
    in-range port for `switch` and the table's required key type)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return bool(lines) and all(_line_ok(l, switch) for l in lines)
