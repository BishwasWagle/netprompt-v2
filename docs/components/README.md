# Component Reference

Focused, standalone reference cards for every significant component — what it is, its
files, its real interface, how it works, gotchas, and how to run it. For the holistic
design and workflow see [runtime-manager-design.md](../runtime-manager-design.md) (inner
loop) and [planner-design.md](../planner-design.md) (outer loop); for commands see
[usage.md](../usage.md).

## Two loops, two models

```
mission ─▶ SLOW PLANNER (outer loop) ─▶ artifact ─▶ RUNTIME MANAGER (inner loop) ─▶ live P4/BMv2
            └ decision LLM (cuda:0)                   └ deterministic; tier-2 regen LLM (cuda:1) optional
```

## Runtime Manager — inner loop (mostly deterministic)

| Component | Role |
|---|---|
| [runtime-manager.md](runtime-manager.md) | The episode loop / orchestrator (deploy → observe → evaluate → adapt → commit/rollback/escalate). |
| [validation-gate.md](validation-gate.md) | Sound, pre-deploy checks (L0 syntax · L1 envelope · L2 blackhole invariants · L3 dry-install). |
| [deployer.md](deployer.md) | Live re-install of rules/QoS over the persistent BMv2 network; reroute, rollback, recovery. |
| [monitors.md](monitors.md) | System monitor (sound) + network monitor (noisy) → `MonitorReport`; writes switch status to the KG. |
| [evaluator.md](evaluator.md) | The 6-stage post-deploy attribution + commit ladder. |
| [adaptation-engine.md](adaptation-engine.md) | Tiered adapt: tier 0 tune → tier 1 reroute → tier 2 regen, with the domination guard + shared budget. |
| [kg-client.md](kg-client.md) | The Neo4j read/write boundary (envelope composition, verdicts/snapshots, field-id translation). |

## Slow Planner — outer loop (LLM-driven)

| Component | Role |
|---|---|
| [slow-planner.md](slow-planner.md) | The LLM KG-RAG orchestrator: read KG + history → decide → validate → fallback → compile artifact. |

## The two models

| Model | Role |
|---|---|
| [planner-llm.md](planner-llm.md) | **Decision model** (Qwen2.5-1.5B + LoRA, `cuda:0`) — selects the SFC/policy/path; constrained-decoded, retrained, promoted. |
| [regen-llm.md](regen-llm.md) | **Tier-2 regen code model** (Qwen2.5-Coder-1.5B, `cuda:1`) — a *component the RM can call* to regenerate P4 rules; **not** the Runtime Manager. |

## Related docs
- [usage.md](../usage.md) — how to run everything (holistic + each model).
- [runtime-manager-design.md](../runtime-manager-design.md) · [runtime-manager-implementation-plan.md](../runtime-manager-implementation-plan.md) — inner-loop design + plan.
- [planner-design.md](../planner-design.md) · [planner-lora-retrain.md](../planner-lora-retrain.md) · [planner-lora-eval.md](../planner-lora-eval.md) — outer-loop design + the LoRA retrain/eval.
- [runtime-planner-contracts.md](../runtime-planner-contracts.md) — the planner↔runtime handoff + integration log.
- [m7-implementation-plan.md](../m7-implementation-plan.md) · [m7-regen-comparison.md](../m7-regen-comparison.md) — the Tier-2 regen serving.
