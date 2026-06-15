"""RegenProposer — the Tier-2 plug for the engine's regen_proposer seam
(adapt.propose; design §7.3).

Stateless across calls: the K-cap (config.REGEN_MAX_REJECTS) is computed
from the engine's `exclude` set (how many REGEN candidates were already
tried this episode), so one proposer instance serves many episodes without
reset bookkeeping. The gate remains the authority on every candidate this
returns — the proposer only guarantees grammar-valid, non-repeated text.
"""
from __future__ import annotations

from runtime import config
from runtime.contracts import Candidate, REGEN
from runtime.gate import KNOWN_TABLES
from runtime.regen.grammar import validate
from runtime.regen.prompt import build_prompt


class RegenProposer:

    def __init__(self, client, table_state_fn, switch: str = "s1",
                 max_rejects: int = config.REGEN_MAX_REJECTS):
        """client: .generate(prompt) -> str.
        table_state_fn: () -> {switch: [TableEntry]} (the deployer's
        table_state); the prompt shows the model what is installed NOW."""
        self.client = client
        self.table_state_fn = table_state_fn
        self.switch = switch
        self.max_rejects = max_rejects

    def __call__(self, diag, env, state, exclude):
        prior = [c for c in exclude if c.kind == REGEN]
        if len(prior) >= self.max_rejects:
            return None                              # K-cap: stop asking
        entries = self.table_state_fn().get(self.switch, [])
        rejected = [c.params[1] for c in prior]
        budget = self.max_rejects - len(prior)
        for _ in range(budget):
            try:
                text = self.client.generate(build_prompt(
                    diag, env, state, entries, rejected,
                    self.switch, KNOWN_TABLES)).strip()
            except Exception:
                # §7.4 fail-safe: a real serving endpoint that hangs/errors
                # (timeout, 5xx, OOM) must degrade to escalate-sooner, NOT crash
                # the episode. Treat any generation failure as a rejected attempt.
                rejected.append("(generation error)")
                continue
            if not validate(text, self.switch):
                rejected.append(text or "(empty)")
                continue
            cand = Candidate(REGEN, (self.switch, text))
            if cand in exclude:
                rejected.append(text)
                continue
            return cand
        return None
