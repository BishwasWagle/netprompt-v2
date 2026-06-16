"""LLM client seam for Tier-2 regen (design §4).

Protocol: client.generate(prompt: str) -> str (the raw completion).

StubLLMClient is the M6 stand-in: deterministic canned responses, so the
full Tier-2 path (propose -> gate -> apply -> observe) runs end-to-end with
no model. LocalHFClient is the M7 real client: in-process HF transformers
serving a pinned Qwen-Coder in FP16/greedy with grammar.gbnf() applied as a
transformers-cfg constrained-decoding logits processor (vLLM is unsupported
on the P100's sm_60, so no separate server) — at which point grammar.validate()
becomes defense-in-depth instead of the primary constraint.
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


def _complete_lines(text: str) -> str:
    """Keep only complete (newline-terminated) lines, dropping a trailing
    partial that `max_new_tokens` may have cut mid-command.

    The grammar's `root ::= line+` is unbounded, so greedy decoding keeps
    emitting and gets truncated mid-line at the token cap (M7 spike finding).
    Returning only whole lines makes `grammar.validate()` pass; if nothing is
    complete yet we return "" — which the proposer treats as a failed attempt
    (the §7.4 fail-safe path), never a malformed candidate.
    """
    if "\n" in text:
        text = text[: text.rindex("\n") + 1]   # drop a trailing partial line
    # No interior newline => a single line: keep it and let validate() arbitrate
    # (a clean one-command EOS has no trailing "\n" but is perfectly valid).
    return "\n".join(l for l in text.splitlines() if l.strip())


def _resolve_device(pref: str) -> str:
    """Honour the configured device, but degrade gracefully off the 2nd GPU
    (cuda:1) so a single-GPU or CPU box still runs."""
    import torch
    if not pref.startswith("cuda"):
        return pref
    if not torch.cuda.is_available():
        return "cpu"
    idx = int(pref.split(":")[1]) if ":" in pref else 0
    return pref if idx < torch.cuda.device_count() else "cuda:0"


class LocalHFClient:
    """Real Tier-2 client (M7): in-process HF transformers with a
    transformers-cfg GBNF logits processor (FP16, greedy). Same
    `.generate(prompt) -> str` protocol as StubLLMClient, so it drops into
    RegenProposer unchanged. The model is lazy-loaded once on first call.

    grammar.gbnf() is the guided-decoding constraint, so output is
    syntactically table_modify/table_add over known tables; grammar.validate()
    in the proposer is then defense-in-depth. Generation errors propagate to
    the proposer's try/except (the §7.4 fail-safe), so we do NOT swallow them
    here.
    """

    def __init__(self, model=None, revision=None, device=None,
                 max_new_tokens=None, grammar_str=None, switch="s1"):
        from runtime import config
        from runtime.regen.grammar import gbnf
        self.model_id = model or config.REGEN_MODEL
        self.revision = revision if revision is not None else config.REGEN_REVISION
        self.device_pref = device or config.REGEN_DEVICE
        self.max_new_tokens = max_new_tokens or config.REGEN_MAX_NEW_TOKENS
        self.switch = switch
        self.grammar_str = grammar_str or gbnf(switch)
        self._tok = self._model = self._proc = self._gen_cfg = self._device = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import torch
        from transformers import (AutoTokenizer, AutoModelForCausalLM,
                                  GenerationConfig)
        from transformers_cfg.grammar_utils import IncrementalGrammarConstraint
        from transformers_cfg.generation.logits_process import (
            GrammarConstrainedLogitsProcessor)

        self._device = _resolve_device(self.device_pref)
        self._tok = AutoTokenizer.from_pretrained(self.model_id, revision=self.revision)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id, revision=self.revision, torch_dtype=torch.float16
        ).to(self._device).eval()
        constraint = IncrementalGrammarConstraint(self.grammar_str, "root", self._tok)
        self._proc = GrammarConstrainedLogitsProcessor(constraint)
        # Clean greedy config: clear sampling knobs the model card may set, so
        # decoding is deterministic for the reproducibility kit (DoD #4).
        pad_id = self._tok.pad_token_id
        if pad_id is None:
            pad_id = self._tok.eos_token_id          # a swapped model may lack a pad token
        self._gen_cfg = GenerationConfig(
            do_sample=False, temperature=None, top_p=None, top_k=None,
            num_beams=1, max_new_tokens=self.max_new_tokens,
            pad_token_id=pad_id)

    def generate(self, prompt: str) -> str:
        import torch
        self._ensure_loaded()
        # transformers-cfg holds parser state across tokens; reset per call so
        # one episode's generation never leaks into the next.
        self._proc.reset()
        enc = self._tok.apply_chat_template(
            [{"role": "user", "content": prompt}], add_generation_prompt=True,
            return_tensors="pt", return_dict=True).to(self._device)
        with torch.no_grad():
            out = self._model.generate(**enc, generation_config=self._gen_cfg,
                                       logits_processor=[self._proc])
        text = self._tok.decode(out[0][enc["input_ids"].shape[-1]:],
                                skip_special_tokens=True)
        return _complete_lines(text)


def manifest() -> dict:
    """Reproducibility manifest (M7 DoD #4): everything needed to reproduce a
    Tier-2 candidate from the same inputs — the pinned model+revision, decoding
    params, library versions, and content hashes of the grammar and prompt
    template. Lightweight: reads package versions without importing torch."""
    import hashlib
    from importlib.metadata import PackageNotFoundError, version
    from runtime import config
    from runtime.regen.grammar import gbnf
    from runtime.regen.prompt import TEMPLATE

    def _ver(dist):
        try:
            return version(dist)
        except PackageNotFoundError:
            return None

    def _sha(s):
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    return {
        "model": config.REGEN_MODEL,
        "revision": config.REGEN_REVISION,
        "device": config.REGEN_DEVICE,
        "max_new_tokens": config.REGEN_MAX_NEW_TOKENS,
        "decoding": "greedy (do_sample=False, num_beams=1)",
        "torch": _ver("torch"),
        "transformers": _ver("transformers"),
        "transformers_cfg": _ver("transformers-cfg"),
        "gbnf_sha256_16": _sha(gbnf("s1")),
        "prompt_template_sha256_16": _sha(TEMPLATE),
    }
