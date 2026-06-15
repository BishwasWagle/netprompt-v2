"""Tier-2 regen grammar (design §7.3, §11 L0; resolves open question §13.3).

Two artifacts, generated from the SAME constants the gate enforces
(gate.KNOWN_TABLES / config.SWITCH_PORTS), so grammar and gate cannot drift:

  gbnf(switch) — a llama.cpp/vLLM-style GBNF grammar string for constrained
                decoding on the serving side (M7). With it, the model is
                syntactically UNABLE to emit an invalid command. Tightened to
                the gate's L0 for `switch`: the egress port is bounded to
                SWITCH_PORTS[switch] and the match-key type is conditioned on
                the table (forward_table -> MAC, others -> IPv4), so the model
                can't waste the K-cap on grammar-valid output the gate L0-rejects.
  validate(text, switch) — the local equivalent: True iff every non-blank line
                is a well-formed table_modify/table_add over known tables and
                actions, with an in-range port and the right key type. Used by
                the proposer as the stand-in for constrained decoding with the
                stub client, and as defense-in-depth with the real one.

The grammar is deliberately NARROWER than the gate (no table_delete — entry
removal is never a recovery action; the gate would catch a blackhole anyway,
but the grammar removes the temptation entirely).
"""
from __future__ import annotations

import re

from runtime.config import SWITCH_PORTS
from runtime.gate import KNOWN_TABLES, NOARG_ACTIONS, PORT_ACTIONS

# forward_table keys on MAC, every other table on IPv4 (mirrors the gate's
# _check_table_action_key_args). Kept as a tuple so the grammar/validate stay
# in lockstep if a second MAC-keyed table is ever added.
_MAC_TABLES = ("forward_table",)

# Non-capturing so _ADD_RE's groups stay clean: 1=table 2=action 3=key 4=args.
_MAC = r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"
_IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"
_NUM = r"\d+"


def _action_alternatives() -> str:
    actions = sorted({a for acts in KNOWN_TABLES.values() for a in acts})
    return " | ".join(f'"{a}"' for a in actions)


def gbnf(switch: str = "s1") -> str:
    """GBNF grammar for the constrained decoder (M7 serving config), tightened
    to the gate's L0 for `switch` (regen targets s1, the widest port set)."""
    tables = " | ".join(f'"{t}"' for t in sorted(KNOWN_TABLES))
    mac_tables = " | ".join(f'"{t}"' for t in sorted(_MAC_TABLES))
    ip_tables = " | ".join(f'"{t}"' for t in sorted(KNOWN_TABLES)
                           if t not in _MAC_TABLES)
    ports = " | ".join(f'"{p}"' for p in sorted(SWITCH_PORTS[switch]))
    return f"""root      ::= line+
line      ::= (modify | add) "\\n"
modify    ::= "table_modify " table " " action " " num " => " args
add       ::= add_mac | add_ip
add_mac   ::= "table_add " mactable " " action " " mac " => " args
add_ip    ::= "table_add " iptable " " action " " ipv4 " => " args
table     ::= {tables}
mactable  ::= {mac_tables}
iptable   ::= {ip_tables}
action    ::= {_action_alternatives()}
args      ::= port | ""
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


def _line_ok(line: str, switch: str) -> bool:
    m = _ADD_RE.match(line)
    if m:
        table, action, key = m.group(1), m.group(2), m.group(3)
        if table not in KNOWN_TABLES or action not in KNOWN_TABLES[table]:
            return False
        # key type must match the table (forward_table -> MAC, else IPv4)
        if (table in _MAC_TABLES) != (":" in key):
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
        return (len(args) == 1 and args[0].isdigit()
                and int(args[0]) in SWITCH_PORTS[switch])
    if action in NOARG_ACTIONS:
        return not args
    return False


def validate(text: str, switch: str = "s1") -> bool:
    """True iff text is non-empty and every non-blank line conforms (incl. an
    in-range port for `switch` and the table's required key type)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return bool(lines) and all(_line_ok(l, switch) for l in lines)
