"""Tier-2 regen grammar (design §7.3, §11 L0; resolves open question §13.3).

Two artifacts, generated from the SAME constants the gate enforces
(gate.KNOWN_TABLES), so grammar and gate cannot drift:

  gbnf()      — a llama.cpp/vLLM-style GBNF grammar string for constrained
                decoding on the serving side (M7). With it, the model is
                syntactically UNABLE to emit an invalid command.
  validate()  — the local equivalent: True iff every non-blank line is a
                well-formed table_modify/table_add over known tables and
                actions. Used by the proposer as the stand-in for constrained
                decoding with the stub client, and as defense-in-depth with
                the real one.

The grammar is deliberately NARROWER than the gate (no table_delete — entry
removal is never a recovery action; the gate would catch a blackhole anyway,
but the grammar removes the temptation entirely).
"""
from __future__ import annotations

import re

from runtime.gate import KNOWN_TABLES, NOARG_ACTIONS, PORT_ACTIONS

_MAC = r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"
_IPV4 = r"(\d{1,3}\.){3}\d{1,3}"
_NUM = r"\d+"


def _action_alternatives() -> str:
    actions = sorted({a for acts in KNOWN_TABLES.values() for a in acts})
    return " | ".join(f'"{a}"' for a in actions)


def gbnf() -> str:
    """GBNF grammar for the constrained decoder (M7 serving config)."""
    tables = " | ".join(f'"{t}"' for t in sorted(KNOWN_TABLES))
    return f"""root      ::= line+
line      ::= (modify | add) "\\n"
modify    ::= "table_modify " table " " action " " num " => " args
add       ::= "table_add " table " " action " " key " => " args
table     ::= {tables}
action    ::= {_action_alternatives()}
key       ::= mac | ipv4
args      ::= num | ""
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


def _line_ok(line: str) -> bool:
    m = _MODIFY_RE.match(line) or _ADD_RE.match(line)
    if not m:
        return False
    table, action = m.group(1), m.group(2)
    if table not in KNOWN_TABLES or action not in KNOWN_TABLES[table]:
        return False
    args = m.group(m.lastindex).split()
    if action in PORT_ACTIONS:
        return len(args) == 1 and args[0].isdigit()
    if action in NOARG_ACTIONS:
        return not args
    return False


def validate(text: str) -> bool:
    """True iff text is non-empty and every non-blank line conforms."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return bool(lines) and all(_line_ok(l) for l in lines)
