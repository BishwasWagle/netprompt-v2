"""Item 4 — Tier-2 regen plumbing with a stub model. The full path
(proposer -> grammar -> gate -> engine) runs locally; M7 swaps the stub
for a constrained-decoding Qwen-Coder endpoint."""
from runtime.adapt import Budget, adapt
from runtime.config import EDGE_MAC
from runtime.contracts import (
    Candidate, DeploymentSpec, Envelope, PRIMARY, REGEN, TableEntry,
)
from runtime.fakes import FakeDeployer, FakeMonitor
from runtime.fixtures import Ddil, VALID_BINDING
from runtime.gate import ValidationGate
from runtime.regen import RegenProposer, StubLLMClient
from runtime.regen.grammar import gbnf, validate
from runtime.regen.prompt import build_prompt, render_tables

VALID_RULES = "table_modify forward_table forward 10 => 12"


def s1_tables():
    entries = [TableEntry("forward_table", f"00:00:00:00:00:{i:02x}",
                          "forward", (str(i),), i - 1) for i in range(1, 11)]
    entries.append(TableEntry("forward_table", EDGE_MAC, "forward", ("11",), 10))
    return {"s1": entries}


REGEN_ENV = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                     legal_tiers=frozenset((REGEN,)),
                     legal_paths=frozenset((PRIMARY,)))


def regen_spec():
    return DeploymentSpec(sfc="ReliableRelaySFC", binding=dict(VALID_BINDING),
                          envelope=REGEN_ENV, correlation_id="rg1",
                          target_field="F1")


def make_proposer(responses, **kw):
    return RegenProposer(StubLLMClient(responses), table_state_fn=s1_tables, **kw)


def diag_and_state():
    model = Ddil()
    report = FakeMonitor(model, FakeDeployer()).observe_window()
    from runtime.adapt import diagnose
    return diagnose(report), {"path": PRIMARY, "knobs": {}}


# ---------------- grammar ----------------

def test_gbnf_is_generated_from_gate_constants():
    g = gbnf()
    for token in ("forward_table", "priority_table", "relay_policy_table",
                  '"forward"', '"drop"', "table_modify", "table_add"):
        assert token in g
    assert "table_delete" not in g          # narrower than the gate, by design
    assert gbnf() == g                      # deterministic


def test_validate_accepts_wellformed_and_rejects_garbage():
    assert validate(VALID_RULES)
    assert validate("table_add forward_table forward 00:00:00:00:00:02 => 2\n"
                    "table_modify forward_table forward 3 => 12")
    assert not validate("")
    assert not validate("rm -rf / # definitely a table entry")
    assert not validate("table_modify magic_table forward 1 => 2")
    assert not validate("table_modify forward_table explode 1 => 2")
    assert not validate("table_delete forward_table 10")     # not in grammar


# ---------------- prompt ----------------

def test_prompt_is_deterministic_and_complete():
    diag, state = diag_and_state()
    p1 = build_prompt(diag, REGEN_ENV, state, s1_tables()["s1"],
                      ["bad attempt"], "s1", ("forward_table",))
    p2 = build_prompt(diag, REGEN_ENV, state, s1_tables()["s1"],
                      ["bad attempt"], "s1", ("forward_table",))
    assert p1 == p2
    for needle in ("metric=latency", "max_latency_ms=20", "path=primary",
                   f"handle=10 forward_table {EDGE_MAC} -> forward(11)",
                   "- bad attempt"):
        assert needle in p1


# ---------------- proposer ----------------

def test_proposer_returns_grammar_valid_candidate():
    diag, state = diag_and_state()
    cand = make_proposer([VALID_RULES])(diag, REGEN_ENV, state, set())
    assert cand == Candidate(REGEN, ("s1", VALID_RULES))


def test_proposer_retries_past_invalid_output_within_budget():
    diag, state = diag_and_state()
    client_responses = ["definitely not a rule", VALID_RULES]
    proposer = make_proposer(client_responses, max_rejects=3)
    cand = proposer(diag, REGEN_ENV, state, set())
    assert cand is not None and validate(cand.params[1])


def test_proposer_respects_k_cap_from_exclude():
    diag, state = diag_and_state()
    exhausted = {Candidate(REGEN, ("s1", f"table_modify forward_table forward {i} => 12"))
                 for i in range(3)}
    proposer = make_proposer([VALID_RULES], max_rejects=3)
    assert proposer(diag, REGEN_ENV, state, exhausted) is None
    assert proposer.client.prompts == []          # never even asked the model


def test_proposer_does_not_repeat_an_excluded_candidate():
    diag, state = diag_and_state()
    tried = {Candidate(REGEN, ("s1", VALID_RULES))}
    other = "table_modify forward_table forward 9 => 12"
    cand = make_proposer([VALID_RULES, other])(diag, REGEN_ENV, state, tried)
    assert cand == Candidate(REGEN, ("s1", other))


def test_proposer_gives_up_when_model_only_emits_garbage():
    diag, state = diag_and_state()
    assert make_proposer(["nope", "still nope", "nada"])(
        diag, REGEN_ENV, state, set()) is None    # -> engine escalates (fail-safe)


def test_proposer_treats_client_exception_as_failed_generation():
    """§7.4 fail-safe for the REAL serving endpoint: a client that raises
    (timeout / 5xx / OOM) must degrade to None (-> escalate), never propagate
    and crash the episode."""
    class BrokenClient:
        def generate(self, prompt):
            raise RuntimeError("serving endpoint down")
    diag, state = diag_and_state()
    proposer = RegenProposer(BrokenClient(), table_state_fn=s1_tables, max_rejects=3)
    assert proposer(diag, REGEN_ENV, state, set()) is None     # no exception escapes


# ---------------- end-to-end through the engine ----------------

def test_engine_runs_regen_candidate_through_gate_and_rolls_back():
    model = Ddil()
    deployer = FakeDeployer()
    monitor = FakeMonitor(model, deployer)
    proposer = make_proposer([VALID_RULES])
    budget = Budget(6)

    res = adapt(regen_spec(), monitor.observe_window(), budget,
                deployer, monitor, ValidationGate(),
                active_capacity_ok=lambda c: True,
                current_tables=s1_tables(), regen_proposer=proposer)

    assert not res.success and res.reason == "all tiers exhausted"
    applied = [a for a in res.trace if a.candidate.kind == REGEN and a.applied]
    assert len(applied) == 1                      # gate passed, it ran
    assert budget.spent == 1
    assert deployer.rollbacks == 1                # ddil: no improvement -> undone
    assert len(proposer.client.prompts) >= 1      # the model was consulted


def test_engine_escalates_safely_when_llm_is_down():
    """LLM unavailable == proposer returns None == escalate sooner (§7.4)."""
    model = Ddil()
    deployer = FakeDeployer()
    monitor = FakeMonitor(model, deployer)
    budget = Budget(6)
    res = adapt(regen_spec(), monitor.observe_window(), budget,
                deployer, monitor, ValidationGate(),
                regen_proposer=make_proposer([]))   # nothing to say
    assert not res.success and budget.spent == 0
    assert deployer.applied == []                 # network untouched


# ---------------- LocalHFClient output handling (M7, no GPU) ----------------

def test_complete_lines_drops_trailing_partial():
    """root::=line+ is unbounded, so greedy truncates mid-line at the token cap;
    LocalHFClient must return only whole lines so validate() passes."""
    from runtime.regen.llm_client import _complete_lines
    raw = "table_modify forward_table forward 10 => 12\ntable_add forward_table forw"
    out = _complete_lines(raw)
    assert out == "table_modify forward_table forward 10 => 12"
    assert validate(out)                              # the kept text is gate-valid


def test_complete_lines_keeps_single_line_and_lets_validate_arbitrate():
    """A clean one-command EOS has no trailing newline but is valid — keep it
    (review #9). A truncated single line is also kept, but validate() rejects it,
    so the proposer still treats it as a failed attempt — never a bad candidate."""
    from runtime.regen.llm_client import _complete_lines
    one = "table_modify forward_table forward 10 => 12"
    assert _complete_lines(one) == one and validate(one)
    assert _complete_lines("table_modify forward_table forw") == "table_modify forward_table forw"
    assert not validate("table_modify forward_table forw")


def test_complete_lines_keeps_multiple_and_strips_blanks():
    from runtime.regen.llm_client import _complete_lines
    assert _complete_lines("\n\nx => 1\n\ny => 2\n") == "x => 1\ny => 2"


def test_localhfclient_conforms_to_generate_protocol():
    """Structural: the real client is a drop-in for StubLLMClient (same
    .generate seam) and constructs without loading a model (lazy)."""
    from runtime.regen import LocalHFClient
    c = LocalHFClient(model="dummy/model", device="cpu")
    assert callable(c.generate)
    assert c._model is None                           # nothing loaded at construction


# ---------------- grammar tightening (M7 #4: port + key-type) ----------------

def test_gbnf_bounds_port_to_switch_ports():
    """Port is constrained to the switch's valid egress ports, so the model
    can't emit out-of-range / leading-zero ports (the over-accept #4 fixes)."""
    from runtime.config import SWITCH_PORTS
    g1 = gbnf("s1")
    assert "port      ::=" in g1 and '"12"' in g1     # s1 reaches port 12
    g2 = gbnf("s2")
    assert '"12"' not in g2                            # s2 ports are {1, 2}
    assert all(f'"{p}"' in g2 for p in SWITCH_PORTS["s2"])


def test_validate_rejects_out_of_range_port():
    assert validate("table_modify forward_table forward 1 => 12", "s1")
    assert not validate("table_modify forward_table forward 1 => 13", "s1")   # > s1 max
    assert validate("table_modify forward_table forward 1 => 2", "s2")
    assert not validate("table_modify forward_table forward 1 => 5", "s2")    # not on s2


def test_validate_enforces_key_type_per_table():
    assert validate("table_add forward_table forward 00:00:00:00:00:01 => 1", "s1")
    assert not validate("table_add forward_table forward 10.0.0.1 => 1", "s1")        # MAC table, IP key
    assert validate("table_add relay_policy_table mark_reliable 10.0.0.1 => ", "s1")
    assert not validate("table_add relay_policy_table mark_reliable 00:00:00:00:00:01 => ", "s1")


def test_validate_rejects_leading_zero_port():
    # '011' int-parses to 11 but the grammar can't emit it and simple_switch_CLI
    # may read it as octal — validate/gate must reject it (review #10).
    assert validate("table_modify forward_table forward 1 => 11", "s1")
    assert not validate("table_modify forward_table forward 1 => 011", "s1")


def test_validate_enforces_action_arg_shape():
    # a port action needs exactly one port; a no-arg action takes none. gbnf now
    # conditions args on action so the model can't waste the K-cap on these.
    assert not validate("table_modify forward_table forward 1 => ", "s1")   # forward, no port
    assert not validate("table_modify forward_table drop 1 => 5", "s1")     # drop, stray arg
    assert validate("table_modify forward_table drop 1 => ", "s1")          # drop, no arg


def test_gbnf_conditions_args_on_action():
    g = gbnf("s1")
    assert 'mod_op    ::= port_act " " num " => " port | noarg_act " " num " => "' in g
    assert 'key_op_ip ::= noarg_act " " ipv4 " => "' in g       # ip tables: noarg only
