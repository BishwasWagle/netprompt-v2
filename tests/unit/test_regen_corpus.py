"""E4 corpus guard — the deliberately-broken SFC corpus stays honest (no GPU).

Every reject item must be refused at its declared layer (syntactic -> grammar,
runtime -> gate L2); every recover item's synthesized corrective row must be
grammar-valid, gate-accepted, and restore the route. This is the deterministic
core of the E4 experiment (the `real` model arm lives in the live tests/harness).
"""
import pytest

from runtime.gate import ValidationGate
from runtime.regen import StubLLMClient
from runtime.tools.e4_regen import (
    DEFAULT_CORPUS, _read, eval_recover, eval_reject, load_corpus, parse_table, synth_fix,
)

_MAN, _BASE = load_corpus(DEFAULT_CORPUS)
_REJECT = [it for it in _MAN["items"] if it["kind"] == "reject"]
_RECOVER = [it for it in _MAN["items"] if it["kind"] == "recover"]


def test_corpus_has_both_fault_classes():
    classes = {it["fault_class"] for it in _REJECT}
    assert {"syntactic", "runtime"} <= classes          # both kinds of badness present
    assert _RECOVER                                      # and at least one recover item


@pytest.mark.parametrize("item", _REJECT, ids=[it["id"] for it in _REJECT])
def test_reject_item_refused_at_expected_layer(item):
    r = eval_reject(item, _read(DEFAULT_CORPUS, item["bad"]), _BASE, item["switch"], ValidationGate())
    assert r["pass"], (f"{item['id']}: caught_by={r['reject_layer']} "
                       f"expected={r['expected_layer']} reason={r['reject_reason']!r}")


@pytest.mark.parametrize("item", _RECOVER, ids=[it["id"] for it in _RECOVER])
def test_recover_item_fix_is_valid_safe_and_recovers(item):
    faulty = parse_table(_read(DEFAULT_CORPUS, item["bad"]))
    fix = synth_fix(faulty, item["recovery_target"]["mac"], item["recovery_target"]["right_port"])
    r = eval_recover(item, faulty, item["switch"], ValidationGate(), "stub", StubLLMClient([fix]))
    assert r["grammar_valid"] and r["gate_pass"] and r["recovers"], r
