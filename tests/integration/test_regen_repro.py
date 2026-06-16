"""M7 reproducibility (DoD #4): a pinned model + greedy decoding + the GBNF
constraint means the SAME inputs produce the SAME Tier-2 candidate, run twice.

Needs the GPU + the pinned Qwen-Coder cached (NOT the testbed). Run:
    source deploy/gpu-node/gpu-node.env
    ~/netprompt-venv/bin/python -m pytest tests/integration/test_regen_repro.py -v
"""
import pytest


def _gpu_model_cached():
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        from huggingface_hub import try_to_load_from_cache
        from runtime import config
        hit = try_to_load_from_cache(config.REGEN_MODEL, "config.json",
                                     revision=config.REGEN_REVISION or None)
        return isinstance(hit, str)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _gpu_model_cached(), reason="GPU + pinned Coder model not available")


def _prompt():
    from runtime.regen.prompt import build_prompt
    from runtime.contracts import Envelope, TableEntry, PRIMARY, REGEN
    from runtime.config import EDGE_MAC

    class D:
        who, metric, severity = "F1", "latency", 0.42

    env = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2,
                   legal_tiers=frozenset((REGEN,)), legal_paths=frozenset((PRIMARY,)))
    entries = [TableEntry("forward_table", f"00:00:00:00:00:{i:02x}", "forward", (str(i),), i - 1)
               for i in range(1, 11)]
    entries.append(TableEntry("forward_table", EDGE_MAC, "forward", ("11",), 10))
    return build_prompt(D(), env, {"path": PRIMARY, "knobs": {"tbf_rate_mbit": 25}},
                        entries, [], "s1", ("forward_table",))


def test_same_inputs_same_candidate_twice():
    from runtime.regen import LocalHFClient
    from runtime.regen.grammar import validate
    prompt = _prompt()
    client = LocalHFClient()                       # pinned model+rev+device from config
    first = client.generate(prompt)
    second = client.generate(prompt)
    assert first == second, "greedy + pinned revision must be reproducible"
    assert validate(first, "s1")                   # and a gate-valid candidate


def test_manifest_pins_everything_needed():
    from runtime.regen.llm_client import manifest
    m = manifest()
    assert m["revision"], "the model revision must be pinned for reproducibility"
    assert m["decoding"].startswith("greedy")
