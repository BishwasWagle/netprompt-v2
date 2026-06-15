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


def test_complete_lines_returns_empty_when_nothing_terminated():
    """No newline yet == no complete command == treated as a failed attempt
    (empty string fails validate -> proposer retries/escalates, never a
    malformed candidate)."""
    from runtime.regen.llm_client import _complete_lines
    assert _complete_lines("table_modify forward_table forw") == ""
    assert not validate(_complete_lines("partial"))


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
