"""LLM client seam for Tier-2 regen (design §4).

Protocol: client.generate(prompt: str) -> str (the raw completion).

StubLLMClient is the M6 stand-in: deterministic canned responses, so the
full Tier-2 path (propose -> gate -> apply -> observe) runs end-to-end with
no model. The real client (M7) targets a local open-weights server
(vLLM / llama.cpp serving Qwen-Coder, pinned revision) with GREEDY decoding
and the grammar.gbnf() constraint passed as the guided-decoding parameter —
at which point grammar.validate() becomes defense-in-depth instead of the
primary constraint.
"""
from __future__ import annotations


class StubLLMClient:
    """Replays canned completions in order; returns "" when exhausted
    (an empty completion fails grammar.validate -> proposer gives up ->
    engine escalates: exactly the fail-safe path §7.4 requires)."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.prompts: list = []           # recorded for assertions

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else ""
