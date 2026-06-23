# Experiment Validity Assessment

**Question.** Can we start running experiments, or do outstanding issues from the
code review affect the *validity* of the results?

> Companion: **[experiment-design.md](experiment-design.md)** — the three
> experiments (slow planner · runtime manager · whole system) that operate under
> the controls established here.

**Bottom line.** **Yes — start experimenting**, subject to the **preconditions in
§7**. An adversarial six-axis validity audit found **no hard validity blocker** in
the code. The senior-engineering review
([../architecture-review/](../architecture-review/00-README.md)) was a *code-quality*
lens (maintainability / scalability / hygiene); its open items (Phase 2/3) are
**operational or scope** concerns, not result-invalidating bugs. **But** validity
has its own axes the review did not cover, and the audit surfaced several
**measurement-semantics caveats and configuration preconditions you must control
for** — none of which are "patch the code," all of which are "configure correctly
and scope your claims." One item (SLA calibration) genuinely *was* a validity
problem and is already fixed — provided you re-seed the KG (§4).

> Grounding: an adversarial audit of six validity axes against the live code
> (`runtime/`, `controller/`, `llm_orchestrator`) and the review docs. Every claim
> below cites `file:line`. Companion: [05-evolution §7](../architecture-review/05-evolution-from-original.md)
> (known model gaps), [02-critical-problems](../architecture-review/02-critical-problems.md)
> (the open Phase 2/3 items), [planner-lora-eval.md](../planner/planner-lora-eval.md).

---

## 1. Validity vs. code quality — why this is a separate question

The code review asked *"is the code clean, maintainable, scalable?"* This document
asks a different question: *"will the recorded numbers be correct, the decisions
sound, and the runs reproducible?"* A system can be imperfectly engineered yet
produce perfectly valid experimental data — and vice versa. So the right way to
read the review's open items is: **none of them makes a measured number wrong.**
The validity risks live elsewhere, and they are mostly about *what the metrics
mean* and *how you configure a run*, not about defects.

| Axis | Verdict | Headline |
|---|---|---|
| Measurement correctness | caveat (scope claims) | Arithmetic correct; but throughput = *offered load*, loss is coarse, default monitor is *synthesized* |
| Decision validity | caveat (scope claims) | Correct on the **4 known missions**; silently *wrong-but-valid* on novel/telemetry-only ones |
| Reproducibility / determinism | caveat (scope claims) | Within-run deterministic; **planner base model not revision-pinned** (cross-host gap) |
| SLA calibration & escalation | caveat (scope claims) | **Was** a blocker (all-escalate); fixed — *must re-seed the calibrated KG* |
| Run-completion robustness | operational only | `--with-regen` can hang (P3); default no-LLM loop is fine |
| Outstanding review items | caveat (scope claims) | Phase 2/3 items are operational/scope; the user's claim broadly **holds** |

**No axis returned a `validity_blocker`.**

---

## 2. Measurement correctness — *the numbers are honest, but mind what they mean*

The recorded arithmetic is correct: `throughput_mbps` clamps `≥0` over a positive
`dt` (`pipeline.py:90-95`), unreachable ping maps to a latency *violation* + 100%
loss rather than a perfect 0 ms (`network_monitor.py:182-184`,
`_UNREACHABLE_RTT_MS=10000`), and `parse_ping` clamps duplicate-reply negative loss
to 0 (`pipeline.py:137`). Nothing silently records 0, double-counts, or mislabels.
**But four scope caveats matter for claims:**

1. **Throughput is access-link *offered load*, not delivered goodput.** It is
   measured upstream of the shared relay bottleneck (drone→s1 `rx` on `s1-eth{N}`).
   The implementation plan says so explicitly: *"shared-link contention is
   invisible to per-field throughput (both fields read full demand regardless of
   shaping)"* (`runtime-manager-implementation-plan.md:207`). A field can read
   `≥ min_bandwidth_mbps` ("met") while the relay drops/queues that traffic.
   **→ Do not claim end-to-end throughput is measured/guaranteed; attribute
   contention to the latency/loss dimensions.**
2. **Loss is coarse with `ping_count=2`.** Loss comes entirely from ping
   (`network_monitor.py:83,106`; `soak.py:118` leaves it at 2). Two packets
   quantize per-probe loss to {0, 50, 100}% → ~10% granularity over the M=5 window.
   Against a 2% bound (`config.DEFAULT_MAX_LOSS_PERCENT=2.0`), the loss dimension is
   *below measurement resolution* — the M5 integration test concedes this and works
   around it with a 20% bound (`test_m5_monitor_node.py:181-182`). A counter-based
   `link_loss_percent` exists but is **never wired in** (`pipeline.py:109`).
   **→ Do not claim faithful sub-10% loss; raise `ping_count` (≥20) for
   loss-sensitive runs or restrict loss claims to coarse bounds.**
3. **RTT/loss are a single-drone proxy.** Sampled from the field's *first* drone
   only (`network_monitor.py:103`), while throughput aggregates *all* the field's
   drones. Optimistic if drones in a field have heterogeneous paths; benign under
   the documented single-active-path topology.
4. **The default `--monitor model` is *synthesized*, not measured.**
   `run_episode.py` defaults to `model` (`run_episode.py:97-99`), which installs
   `FakeMonitor.observe_window() = model.report(...)` (`fakes.py:74-77`) — scenario
   values, not fabric measurements. **→ Only `--monitor real` / `soak.py` produce
   measured data; never report model-mode output as "measured."**

---

## 3. Decision validity — *sound on the known taxonomy, silently wrong off it*

Under the production default (constrained decoding ON, promoted
`final_adapter_retrained`), the planner is **correct on the four known mission
names** but **defaults to BandwidthOptimizedSFC on novel/telemetry-only missions**
(`planner-lora-eval.md:48-58`: 4/4 set A, 0/4 sets B+C). Two validity consequences:

- **Silent wrong-but-valid SFC.** The grammar binds output to a *KG-valid* SFC/policy
  pair, so a wrong decision is still grammatically valid and `llm_parse_status` is
  *always* `parsed_json` — **there is no error signal** distinguishing a trustworthy
  known-mission decision from a possibly-wrong novel-mission one. The candidate set
  doesn't rescue it: `get_candidate_sfc_policy_set()` returns *all* SFCs with no
  mission filtering (`kg_context.py:186-209`).
- **→ Restrict the mission set to the 4 verified names** (emergency→Reliable,
  bulk→Bandwidth, pest→LowLatency, soil→Energy), **or report set A / B / C
  separately — never a single blended accuracy.** If telemetry-driven decisions are
  in scope, use the deterministic `fallback_decision` oracle (correct on
  delay/battery/loss thresholds) or run an **oracle-vs-LLM agreement check** per
  episode and report it.

---

## 4. SLA calibration — *the one item that was a validity problem (now fixed)*

Pre-calibration, the original bounds (20 ms LowLatency / 50 ms ReliableRelay) sat
**below the fabric's ~40 ms primary / ~52 ms backup floor**, so *every* real-monitor
episode escalated — you'd have measured nothing useful. This **was** a genuine
validity problem. It is fixed: `controller/generate_kg.py` now seeds 45 ms / 60 ms
(`generate_kg.py:48-53,62-101`), converting a guaranteed-escalate regime into one
where the deterministic adapt ladder can close on a **commit** (typically
*marginal*, occasionally *healthy*).

- **→ MUST re-seed the live Neo4j KG with the calibrated bounds before any valid
  run** (`run_from_planner --deploy` derives the envelope live from the KG via
  `build_envelope`). **Verify after seeding:** `SFCTemplate.max_latency_ms`
  (LowLatency=45, ReliableRelay=60) and `AgriculturalField.latency_requirement_ms`
  (Field_1/4=45, others=60).
- **Expect a `marginal`-biased verdict mix** — headroom on the calibrated bounds is
  thin by construction (`HEADROOM_TAU=0.15`), so latency-bound flows commit
  *marginal* rather than *healthy*. That is correct behavior, not a bug; frame the
  claim accordingly.

---

## 5. Reproducibility / determinism — *within-run fine; pin a few things for cross-run*

Within a single run the decision/adaptation path is deterministic (greedy
constrained decoding, caller-supplied timestamps) and metrics are honest live
measurements, so **completed numbers are not biased.** The gaps are *cross-run /
cross-host* reproducibility:

- **The planner base model is NOT revision-pinned.** `llm_runner.py:72-75,104-107`
  call `from_pretrained` with no `revision=`, and there is no `NETPROMPT_LLM_REVISION`
  env var. (The *regen* model is pinned only in `gpu-node.env`; the code default is
  `None` → HF `main`.) **→ Pin the planner base-model revision and record the
  resolved commit per run.**
- **Env-dependent device/dtype fallback** changes greedy output across hosts
  (`_resolve_device` cuda:1→cuda:0→cpu, `llm_client.py:47-56`; planner
  `device_map='auto'`). **→ Hold device + dtype + quantization fixed and identical
  across compared runs; always `source gpu-node.env`.**
- **Non-deterministic record keys**: `run_from_planner.py:145` uses
  `datetime.now(...)` and `--correlation-id` defaults to a `uuid4`. **→ Pass an
  explicit `--correlation-id` (and ideally a fixed timestamp) per recorded run.**
- **Soak episode count is wall-clock-bound** (`soak.py:139`). **→ Report per-episode
  (normalized) outcomes and pin `--minutes`/`--kill-every`/`--kill`; don't compare
  raw counts across hosts.**

---

## 6. Run-completion robustness — *operational, not validity*

These affect whether a run *finishes*, not whether finished results are correct:

- **P3 (the headline risk, only with `--with-regen`):** `LocalHFClient.generate()`
  runs in-process under `torch.no_grad()` with **no wall-clock timeout**
  (`llm_client.py:111-125`); the proposer `try/except` only catches a *raised*
  exception, not a hang (`proposer.py:43`). **→ For long unattended `--with-regen`
  soaks, add an external watchdog/timeout and warm-load the model before `t0`.**
- **P1 (subprocess storm):** slow but *bounded* — `observe_window()` re-runs per
  applied attempt (`adapt.py:217`); correct, just costly. Not a hang.
- **S1 (in-memory `last_good`):** lost on a mid-run process restart
  (`runtime_manager.py:35`). **→ Run each soak as one uninterrupted process**, or
  accept a re-baseline-from-fresh-deploy on restart.
- **The default inner loop needs no LLM** (Tier-2 stubbed → escalate), so the bulk
  of experiments carry none of these risks.

---

## 7. Preconditions for valid experiments (the checklist)

Do these before collecting real data:

1. **Re-seed the KG** with the calibrated bounds and **verify** the seeded values
   (`python -m runtime.tools.seed_kg`; check the SFCTemplate / AgriculturalField
   latency fields).
2. **`source deploy/gpu-node/gpu-node.env`** every session (FP16, `cuda:0`, promoted
   `final_adapter_retrained`, pinned regen revision); **confirm
   `NETPROMPT_LLM_CONSTRAINED=1`** and the adapter id, and **pin the planner base
   revision**.
3. **Measure with `--monitor real` / `soak.py`** — never report `--monitor model`
   (synthesized) as measured data.
4. **Keep representative `iperf` UDP load flowing through baseline *and* observation**
   on every measured field, or throughput reads 0 and harm never fires.
5. **Restrict planner missions to the 4 known names** (or report set A/B/C
   separately); for telemetry-driven decisions, use/compare the rule oracle.
6. **Pass an explicit `--correlation-id`** per recorded run; **record decision
   provenance** (`selected_sfc`, `llm_parse_status`, adapter id, fallback-fired) and
   **`kg_write_failures`** (a non-zero value flags incomplete data).
7. **For `--with-regen`:** external watchdog + warm-load; treat the run as
   interruptible; report Tier-2 results only over runs that completed without a hang.

---

## 8. What you can and cannot claim

**Can claim (valid as built):**
- The deterministic adapt ladder (tune→reroute→regen) closes on a **commit** within
  the SLA envelope on the calibrated fabric, with a sound attribution ladder
  (system-fault / causal rollback / escalate).
- The planner makes **KG-grounded, grammar-valid decisions** that are correct on the
  **known mission taxonomy**.
- Calibration converts a guaranteed-escalate regime into a commit-capable one.
- End-to-end orchestration is realized on a **real P4/BMv2 testbed** with live rule
  install/rollback (a few real-monitor episodes, not just `--monitor model`).

**Cannot claim (yet — would overreach):**
- End-to-end **goodput** guarantee (throughput is access-link offered load).
- Faithful **sub-10% loss** measurement (with `ping_count=2`).
- **Telemetry-reasoning generalization** of the planner (names, not numbers).
- **Closed-loop *learning*** improving decisions (the feedback path is wired but
  the 1.5B model doesn't exploit it; escalation-rate conflates wrong-SFC with
  unachievable-SLA — `05-evolution §7.6`).
- **Cross-host reproducibility** without pinning the planner base revision + device.

---

## 9. Recommended readiness sequence (before collecting data)

```
1. seed_kg  → verify calibrated bounds in the KG
2. pytest tests/unit            (212 green — sanity)
3. on network-node: launch_network + the M5/M6 integration tests   (measurement + loop sanity)
4. one short real-monitor soak with continuous iperf               (confirm a sensible commit/marginal/escalate MIX, not all-escalate; kg_write_failures==0)
5. spot-check decision provenance for the planned mission set      (selected_sfc matches the oracle on known names)
→ then collect.
```

**Verdict: GO for experiments on the known mission taxonomy with a re-seeded
calibrated KG and `--monitor real`, scoping claims per §8.** Continue *patching*
only if you (a) need telemetry-driven missions (then close the planner gap or use
the oracle), (b) need loss-sensitive measurements (raise `ping_count` / wire
`link_loss_percent`), or (c) plan long unattended `--with-regen` soaks (then apply
the P3 timeout). None of these blocks starting now on the in-scope experiments.
