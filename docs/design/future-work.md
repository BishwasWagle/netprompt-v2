# Future Work & Things to Remember

A standalone, act-cold record of what's left to build and the non-obvious lessons that bit
us — so neither has to be rediscovered. Companion to [usage.md](usage.md) (how to run),
[components/](components/README.md) (per-component reference), and the design docs.

## Status snapshot (2026-06-19)

Functionally complete for its milestones and live-verified on the consolidated GPU node:
- **Runtime Manager** M0–M7 — M6 acceptance 3/3 live (reroute / escalate / harm), ≥1h soak, M7 Tier-2 regen.
- **Slow planner ↔ runtime integration** — file-transport adapter, KG-seeded, end-to-end episode on hardware.
- **Slow planner** — runs; constrained decoding; LoRA **retrained + promoted** (correct on the known mission taxonomy).
- **Learning loop closed** — runtime verdicts → analytics → planner `runtime_feedback`.
- **Docs** — design + implementation plan + usage + 12 component cards.

"Not broken anywhere," but not "nothing left to develop." See below.

## Open work (sorted cheapest → most expensive)

Effort scale: **Trivial < Small < Medium < Large**. **Cost ≠ value** — the two cheapest
(CLI fix, bounds calibration) *and* the most expensive (retrain) are all high-value; items
marked **[HIGH]** are worth doing regardless of where they fall on cost.

| # | Item | Effort | Value |
|---|---|---|---|
| 1 | ~~orchestrate CLI env-override fix~~ **✅ done 2026-06-21** | Trivial | low (papercut) |
| 2 | ~~Calibrate SLA bounds to hardware~~ **✅ done 2026-06-22** | Small | **HIGH** |
| 3 | rung-1 `re_push` in the live loop | Small | medium |
| 4 | Wire real Tier-2 regen into default loop | Small–Medium | low (per M7) |
| 5 | Autonomous trigger / driver | Medium | **HIGH** |
| 6 | KG `PlannedDeployment` transport | Medium | medium |
| 7 | Hardening backlog / soaks / scale | Medium–Large | low–medium |
| 8 | Feedback-aware + telemetry retrain | Large | **HIGH** |

1. **orchestrate.py CLI env-override footgun — Trivial. ✅ DONE (2026-06-21).** Its `--no-4bit`
   was `store_true`, so it always passed a concrete bool to `from_env`, clobbering
   `NETPROMPT_LLM_USE_4BIT` (the P100 footgun). `--device-map` already deferred to the env.
   Fix: made 4-bit a tri-state selector (`--4bit` / `--no-4bit`, `store_const`, default `None`),
   so omitting both defers to `NETPROMPT_LLM_USE_4BIT`. Verified: env-sourced run with no flags
   loads FP16 and returns `ReliableRelaySFC` / `parsed_json`; 212 unit tests green.

2. **Calibrate SLA bounds to the hardware — Small. [HIGH] ✅ DONE (2026-06-22).** *(Design §6
   blockquote / §13.7 — the biggest gap.)* Episodes were **escalating** because the bounds were
   physically unachievable on this fabric (LowLatency 20 ms, ReliableRelay/F2 50 ms vs measured
   ~40 ms primary / ~52 ms backup). Fix applied in [generate_kg.py](controller/generate_kg.py):
   LowLatency SFC / high-priority fields **20 → 45 ms**, ReliableRelay SFC / medium fields
   **50 → 60 ms** (config + calibration pass, no new code). Re-seeded; `build_envelope`
   ReliableRelaySFC/F2 now reports `lat<=60 ms`. Node-verified live: the planner-driven
   `emergency_alert_relay` **reroutes to primary (tier 1) and commits `marginal` (headroom
   0.082)** instead of escalating — "adapts and commits." (Loss headroom is transiently noisy
   post-reroute on the real wire; steady-state baseline is 0 % — see design §6.)

3. **rung-1 `re_push` in the live loop — Small.** (design §13b backlog) — the watchdog only
   covers switch-death; transient process/install faults aren't re-pushed in the live loop yet.
   Wire the existing `re_push` primitive into the loop + a test.

4. **Wire real Tier-2 regen into the default loop — Small–Medium.** Today stubbed → escalate;
   runs only via `soak --with-regen` / M7 tests. Inject the existing `RegenProposer`/
   `LocalHFClient` seam (loads the cuda:1 model). Caveat: M7 showed the small Coder models
   emit 0% corrective rows ([m7-regen-comparison.md](m7-regen-comparison.md)) — limited payoff
   without a better regen model.

5. **Autonomous trigger / driver — Medium. [HIGH]** Today the loop is *manual* (run the
   planner, then `run_from_planner --deploy`). There is no daemon that watches for a new
   artifact (or a KG `PlannedDeployment` node) and runs the episode. Build a small driver over
   `run_from_planner` to turn the assembled pieces into a running system. (Relates to the
   contracts §5 transport question.)

6. **KG `PlannedDeployment` transport — Medium.** Instead of file (the design's "KG-hub"
   option; contracts §5/§6a): the planner writes a node, the runtime polls. The adapter seam
   already isolates this from the rest of the runtime.

7. **Hardening backlog / longer soaks / scale — Medium–Large.** (design §13b): longer regen
   soaks, multi-handoff scale, the near-bound-inefficiency items. A collection of mostly-small
   items; all polish, not unsafety.

8. **Feedback-aware + telemetry-weighted planner retrain — Large. [HIGH]** The promoted LoRA
   is correct on the known mission taxonomy but (a) defaults to BandwidthOptimized on
   novel/telemetry-only missions and (b) does **not exploit** the `runtime_feedback` we wired
   in. Retrain (`train_decision_lora.py`) with telemetry up front + `runtime_feedback` in the
   input, labeled to separate "wrong SFC" from "unachievable SLA" — or use a larger model
   behind the same constrained-decoding seam (~hour of training + eval iteration). See
   [planner-lora-eval.md](planner-lora-eval.md).

## Key insights to remember (conceptual)

- **Constrained decoding BYPASSES the fallback.** The deterministic fallback only fires when
  the LLM output is *invalid*; the GBNF grammar makes it always valid, so the LLM's choice is
  *used*. Consequence: decision quality rests entirely on the adapter — a mode-collapsed
  adapter ships the wrong SFC under the production default. (Why the retrained adapter was
  promoted.) [planner-lora-eval.md §4](planner-lora-eval.md).
- **Escalation-rate conflates "wrong SFC" with "unachievable SLA."** ReliableRelay's 100%
  escalation is the bounds gap (item 1), not unsuitability — so `runtime_feedback` is
  *advisory*, never a hard "avoid this SFC" rule.
- **Two models, two loops, two GPUs.** Planner *decision* model (Qwen2.5-1.5B + LoRA, `cuda:0`)
  vs runtime *Tier-2 regen* code model (Qwen2.5-Coder-1.5B, `cuda:1`). They are distinct.
- **The Runtime Manager is mostly deterministic.** The Tier-2 LLM is an optional component it
  *can* call, default-stubbed — it is **not** the Runtime Manager. The RM runs fine with no LLM.
- **The retrained adapter is the promoted default** (`final_adapter_retrained`); the original is
  preserved + tracked (`final_adapter` / `final_adapter_original_backup`) — rollback is one env line.

## Operational lessons (gotchas that cost time)

- **Launch the testbed with the P4 program matching what you'll deploy.** The deployer installs
  table *rules*, not the P4 *program* (fixed at switch launch). Deploying low_latency rules onto
  a reliable_relay-launched switch → `DeployError: N table_add lines but N-1 handles`. Use
  `launch_network.py --p4-json <X>.json --sfc <X>` for the SFC you'll run.
- **Run live tests with traffic DETACHED** (`sudo setsid mnexec -a <pid> iperf ...`). Inline
  `&` iperf jobs get killed when the shell exits, taking pytest with them → exit 144 before
  the summary.
- **pkill/pgrep self-match.** `pkill -f "launch_network"` also matches the *current shell* if
  its command contains "launch_network" (e.g. the launch line, or an `echo` label). Use the
  bracket trick `[l]aunch_network`, and never combine the kill with the relaunch in one
  command. `pgrep -fc` likewise matches echo labels — verify with `pgrep -af` (eyeball) or
  check `simple_switch`/thrift/veths instead.
- **KG seeding: `seed_kg.py` is non-destructive (MERGE); `controller/import_kg.py` WIPES**
  (`MATCH (n) DETACH DELETE n`). Use `seed_kg.py` on a live KG to keep runtime records.
- **`ProgrammableSwitch` is a shared KG node** — the seed sets `role`, the runtime monitor
  overwrites `status`. Don't let `--reset-strategic` delete it.
- **P100 (sm_60):** no bf16; bitsandbytes 4-bit is flaky → train fp32, serve fp16, pass
  `--no-4bit`. `PIP_BREAK_SYSTEM_PACKAGES=1` for PEP 668.

## Pointers
[usage.md](usage.md) · [components/](components/README.md) · [runtime-manager-design.md](runtime-manager-design.md) §13/§13b ·
[runtime-planner-contracts.md](runtime-planner-contracts.md) §5/§6 · [planner-design.md](planner-design.md) §8 ·
[planner-lora-eval.md](planner-lora-eval.md) · [m7-regen-comparison.md](m7-regen-comparison.md).
