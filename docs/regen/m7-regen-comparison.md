# M7 #8 — Tier-2 regen multi-model comparison

Harness: `runtime/tools/regen_compare.py`. For each model it runs a fixed set of table-fault
scenarios (a drone mis-ported to a wrong-but-valid port) through **propose → gate**, OFFLINE
(fixture switch state — no testbed), and records:

- **grammar-valid** — fraction of outputs that pass `grammar.validate()`
- **gate-accept** — fraction the sound `ValidationGate` accepts (the "accept rate")
- **recovery** — fraction whose candidate, simulated, restores the mis-ported drone to its
  correct port (recovery-capable)
- **latency** — mean wall-clock per GBNF-constrained greedy generation

Every model is also tagged with a **category** so a cross-family table reports *why* a family is
in or out, not just a bare exception: `runs`, `fail_load`, `fail_no_chat_template`,
`fail_unsupported_tokenizer`, `fail_oom`, `fail_gated`. A weights-free **precheck** loads only the
tokenizer first and decides the two most common cross-family failures (unsupported tokenizer,
missing chat template) without downloading multi-GB weights or touching the GPU. GPU memory is
freed between models, so a long `--models` list can't accumulate weights and OOM one 16 GB P100.

Run (each model uses its **own** latest revision — `--revision main` is the default, so the run
does **not** inherit `NETPROMPT_REGEN_REVISION`, which pins one model's commit and would make
every other repo `fail_load`):
```
python3 -m runtime.tools.regen_compare --device cuda:1 --revision main \
  --out results.json \
  --models Qwen/Qwen2.5-Coder-1.5B-Instruct,deepseek-ai/deepseek-coder-1.3b-instruct,\
microsoft/Phi-3-mini-4k-instruct,ibm-granite/granite-3.0-2b-instruct
```
`--probe` runs 1 scenario/model (a cheap categorization sweep); `--out` also writes a
`{manifest, results}` bundle (library versions + grammar hash) for reproducibility.

## Result — Qwen2.5-Coder size sweep (2026-06-16, P100 cuda:1, FP16, greedy, GBNF-constrained)

| Model | grammar-valid | gate-accept | recovery | mean latency |
|---|---|---|---|---|
| Qwen2.5-Coder-0.5B-Instruct | 100% | 100% | 0% | 7.3 s |
| Qwen2.5-Coder-1.5B-Instruct | 100% | 100% | 0% | 6.1 s |
| Qwen2.5-Coder-3B-Instruct | 100% | 100% | 0% | 8.0 s |

**Reading.** Constrained decoding + the sound gate make Tier-2 **safe at every size** — every output
is grammar-valid and gate-accepted, so a Tier-2 attempt can never deploy a blackhole regardless of
the model. But **none of the small Coders emit the *corrective* row** (0% recovery), and **size
doesn't help** here (0% at 0.5B → 3B). So Tier-2's demonstrated value is "safe, gate-vetted attempts";
a *committed recovery* depends on the model emitting the exact fix — the model-capability frontier,
matching the live `xfail` (test C in m7-regen-live-demo.md). The recovery *path/machinery* is proven
separately (test D, DoD #3).

## Result — cross-family sweep (2026-06-29, P100 cuda:1, FP16, greedy, GBNF-constrained, max_new_tokens=64)

Eleven models across **6 architecture families** and **4 tokenizer classes**; nine load and run,
two are categorized at the load/decoding frontier. **Each model is pinned to a fixed commit** (12-char
`rev`; full SHAs + raw bundle in the committed artifacts below) — a cross-family run must NOT inherit
one model's `NETPROMPT_REGEN_REVISION` pin across all repos.

| Model | Family | Tokenizer | Rev | Category | grammar-valid | gate-accept | recovery | latency |
|---|---|---|---|---|---|---|---|---|
| Qwen2.5-Coder-0.5B-Instruct | Qwen2 | Qwen2TokenizerFast | `ea3f2471cf1b` | runs | 100% | 100% | 0% | 5.6 s |
| Qwen2.5-Coder-1.5B-Instruct | Qwen2 | Qwen2TokenizerFast | `2e1fd397ee46` | runs | 100% | 100% | 0% | 6.1 s |
| Qwen2.5-Coder-3B-Instruct | Qwen2 | Qwen2TokenizerFast | `488639f1ff80` | runs | 100% | 100% | 0% | 8.0 s |
| Qwen2.5-1.5B-Instruct | Qwen2 | Qwen2TokenizerFast | `989aa7980e4c` | runs | 100% | 0% | 0% | 6.3 s |
| deepseek-coder-1.3b-instruct | Llama | LlamaTokenizerFast | `e063262dac83` | runs | 100% | 100% | 0% | 4.5 s |
| TinyLlama-1.1B-Chat-v1.0 | Llama | LlamaTokenizerFast | `fe8a4ea1ffed` | runs | 0% | 0% | 0% | 4.0 s |
| SmolLM2-1.7B-Instruct | Llama | GPT2TokenizerFast | `31b70e2e869a` | runs | 0% | 0% | 0% | 4.5 s |
| Phi-3-mini-4k-instruct | Phi3 | LlamaTokenizerFast | `f39ac1d28e92` | runs | 100% | 0% | 0% | 7.6 s |
| granite-3.0-2b-instruct | Granite | GPT2TokenizerFast | `5ad66c190631` | runs | 0% | 0% | 0% | 8.0 s |
| stable-code-instruct-3b | StableLm | GPTNeoXTokenizerFast | `20e21f0e817b` | **fail_unsupported_tokenizer** | — | — | — | — |
| starcoder2-3b | Starcoder2 | GPT2TokenizerFast | `733247c55e3f` | **fail_no_chat_template** | — | — | — | — |

Committed artifacts (regenerate with `repro/m7_xfam.sh`): the tidy
[`m7_xfam.csv`](../experiments/results/m7_xfam.csv), the raw
[`m7_xfam.json`](../experiments/results/m7_xfam.json) bundle (manifest + per-model pins), and
figures [`m7_xfam_rates.png`](../experiments/results/plots/m7_xfam_rates.png) (the 6 models with
grammar-valid output; the three 0%-grammar models are omitted there) /
[`m7_xfam_latency.png`](../experiments/results/plots/m7_xfam_latency.png) (all 9 runners). This
table is also the **M7** section of
[experiment-results.md](../experiments/results/experiment-results.md).

**Reading.**
- **The gate's *soundness* holds across every family tried; *coverage* is narrower.** Across all
  nine runners, no row is `gate_pass=true` with `grammar_valid=false` — the sound gate accepted
  **nothing** non-conformant, regardless of family. But only **two** families ever produced a
  gate-*accepted* candidate: `deepseek-coder-1.3b` (Llama) and the three `Qwen2.5-Coder` sizes
  (Qwen2). So the defensible claim is gate soundness ("rejected everything non-conformant"), and the
  modest cross-family evidence that a *non-Qwen* model can clear the gate at all (deepseek/Llama) —
  not a broad per-family safety sweep. Phi-3 and generalist-Qwen produced grammar-valid-but-gate-
  rejected candidates; granite/SmolLM2 produced no grammar-valid output, so the gate was never
  exercised on an accepted candidate from those families.
- **recovery is 0% for every runner.** No family (coder or generalist; Llama/Qwen2/Phi3/Granite)
  emits the *corrective* row. The corrective-fix frontier is model-capability-bound and
  family-independent here — matching the Qwen size-sweep above and the live `xfail` (test C).
- **Three models are *completely unsuccessful* (0% grammar-valid — TinyLlama, SmolLM2, granite),
  for *two distinct* reasons, both caught by `validate()`, neither a harness bug** (they are
  omitted from the rates figure, where every bar would be 0). (a) **Truncation (TinyLlama):** the
  unbounded grammar (`root ::= line+`) lets
  greedy decoding spend the whole 64-token budget on an over-long key field and get cut mid-line
  (`…forward 0000…`, no `=>`/port); `_complete_lines` drops the partial → grammar-invalid (the
  §7.4 fail-safe). Raising `--max_new_tokens` would likely fix this one. (b) **Wrong-action
  over-accept (SmolLM2, granite):** these emit `set_low_latency_class` on `forward_table` — an
  action that table doesn't allow (granite's three outputs are all complete lines ending in `=>`;
  SmolLM2's are too, except one that also overruns the token budget). The GBNF does **not** condition
  the action on the table (a documented residual over-accept in `grammar.py`), so constrained
  decoding *can* form a table/action mismatch; `validate()` rejects it regardless of completeness
  (the same action on `priority_table` validates). Net: even a fully-formed, grammar-shaped candidate
  is caught before the gate — the safety story is stronger, not weaker.
- **The hard frontier is the tokenizer + chat-template, not model quality.**
  `stable-code-instruct-3b` loads fine but its `GPTNeoXTokenizerFast` isn't in transformers-cfg's
  exact-match supported set (no MRO walk), and `starcoder2-3b` is a base model with no chat
  template. Both are categorized cleanly instead of crashing the sweep.

## Notes / limitations

- **transformers pin — corrected.** The cu121/Pascal stack pins `transformers==4.46.3`. Under it,
  **DeepSeek-Coder-1.3B and IBM Granite-3.0 do load and run natively** (both register a causal-LM
  class in 4.46.3) — the earlier "don't load cleanly" note was an artefact of the global
  `NETPROMPT_REGEN_REVISION` pin (Qwen-Coder's commit) leaking into every model's load, plus a
  transient HF-429 negative-cache poisoning (`.no_exist/<commit>/config.json`). The harness now
  defaults to `--revision main`, so each model loads its own latest. Families that genuinely can't
  participate: **unsupported tokenizer** (e.g. GPTNeoX → StableLM/Pythia) and **no chat template**
  (base models). Gemma-2 etc. are `fail_gated` without an HF token.
- **Token budget.** grammar-valid is sensitive to `max_new_tokens` (64 here) given the unbounded
  `line+` grammar; cross-family grammar-valid rates are not directly comparable to capability.
- The harness is per-model fault-isolating: one model failing reports a categorized entry
  (`category` + `error`) and the rest still run. Successful-model JSON keys are unchanged
  (`grammar_valid_rate`, `gate_pass_rate`, `recovery_rate`, `mean_latency_s`, `rows`), with
  `category` added — backward compatible with the Qwen size-sweep tooling above.
