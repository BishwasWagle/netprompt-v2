# KRONOS Draft — Recommended Experiment Edits

**Subject.** Concrete, artifact-by-artifact edits to the experimental sections of the
KRONOS / NetPrompt v2 draft (`NetPrompt_V2.pdf`, §IV–V, Tables II–VIII, Fig. 6), derived
from the validity audit and the planner evaluation.

**Sources.** [experiment-validity.md](experiment-validity.md) (measurement-semantics
caveats, cited as "validity §"), [planner-lora-eval.md](../planner/planner-lora-eval.md)
(the planner's set A/B/C decision accuracy), and the adopt / let-go / cut determination in
[experiment-design.md §6](experiment-design.md). This document is the *draft-facing* view
of that determination — what to change in the paper, in edit-ready form.

**How to read the tags.** Each entry is tagged **CUT** (remove as written), **REFRAME**
(keep the experiment, change the framing/labels/precision), **KEEP** (defensible as is),
**ADD** (a missing experiment a claim depends on), or **VERIFY** (blocked on a fact to
confirm before the number can stay).

---

## 0. Blocking question — **RESOLVED (2026-06-24)**

> **Resolved by reproduction.** E1 re-ran the A/B/C confusion matrix with the promoted
> `final_adapter_retrained` (constrained-on): **set A 4/4, sets B+C 0/4, 8/8 valid** —
> identical to the planner eval ([experiment-results.md](results/experiment-results.md)). So the
> promoted adapter *itself* produces the high known-taxonomy accuracy and fails telemetry —
> the draft's **Fig-6 is set-A / known-taxonomy accuracy, not telemetry generalization**, and
> no different/condition-trained model need be posited. **Action: relabel Fig-6 + Table IV
> accordingly; do not claim generalization.** The original framing of the blocking question
> is kept below for the record.

The draft's **Table IV** maps *condition* scenarios (congestion, relay-failure, DDIL) to
ReliableRelaySFC, and **Fig. 6** scores 88.9–100% per SFC class — both implying the planner
reasons from telemetry/condition. Our planner eval found the promoted adapter is **4/4 only
on the four known mission *names*** and **0/4 on telemetry/condition-only missions** (it
defaults to BandwidthOptimized) — [planner-lora-eval §2–3](../planner/planner-lora-eval.md).

**One branch is already forced.** `train_decision_lora.py:85-86` trains the retrained
adapter on **50% generic missions explicitly "to force telemetry use"** (oracle-labelled),
so the model *was* trained on the telemetry/condition case and **still** scores 0/4 on it.
A 88.9–100% Fig. 6 therefore **cannot** be telemetry generalization — it is necessarily on
name-correlated, same-distribution held-out data (**set A**). So the figure stays only if
**relabelled "known-taxonomy (set A) accuracy,"** never "generalization."

**VERIFY which model + training set produced Table IV and Fig. 6** (the only open part —
which adapter), and record the adapter id + base-model revision in §IV; the cleanest
resolution is to **reproduce the confusion matrix under E1's A/B/C split**:

- If a **different model trained on condition labels** produced them → legitimate, but the
  decision-accuracy framing must be tied to *that* model's taxonomy, and the planner-eval
  taxonomy (set A/B/C) must be reconciled with it.
- If the **deterministic oracle** produced the condition→SFC rows → they must be labeled
  "rule-grounded," not "emerged from KG reasoning."

Until this is settled, every Table IV / Fig. 6 number is **VERIFY**, and the edits below
assume the conservative reading.

---

## 1. Priority summary

**P0 — must fix before submission (invalid / overreaching as written):**
1. Abstract "packet-loss rates below 4%" — below measurement resolution (validity §2.2).
2. Abstract "Historical-performance-aware path-selection experiments…" — no result backs it.
3. Table VI throughput presented as goodput — it is access-link offered load (validity §2.1).
4. Table VI / VII fine-grained loss % — below resolution at `ping_count=2` (validity §2.2).
5. Table IV condition rows framed as LLM "emerged from KG reasoning" — the telemetry case the
   planner fails (validity §3; planner-lora-eval §2).

**P1 — relabel / scope (defensible once reframed):**
6. Fig. 6 — label as same-distribution held-out over the *known* taxonomy (set A).
7. §V.D counterfactual — reframe away from telemetry generalization.
8. Tables VI–VIII — disclose monitor mode + repeats + variance; note LLM ≈1 s overhead.

**P2 — strengthen / add:**
9. Add an E2-style runtime section (verdict taxonomy + safety) — the draft has none.
10. Add the historical-path-selection experiment *or* drop the abstract sentence (see #2).

---

## 2. Abstract & Introduction

| Claim (location) | Tag | Recommended edit |
|---|---|---|
| "maintaining packet-loss rates **below 4%** under these scenarios" | **REFRAME / CUT** | The 4% rests on ping-derived loss at `ping_count=2`, which quantizes to ~{0, 50, 100}% per probe — below resolution against a 2% bound (validity §2.2). Re-measure at `ping_count`≥20, **or** replace with a resolution-free claim: "lower packet loss than non-adaptive baselines under degraded conditions." Drop the numeric threshold. |
| "**Historical-performance-aware path-selection experiments** additionally demonstrate the use of graph-maintained operational knowledge during relay-path decisions" | **ADD / CUT** | No §V table demonstrates a path decision that changed *because of* recorded history — §V shows path *migration*, not history-driven selection, and the feedback loop is wired but not exploited (validity §8). Either **add** an experiment where a logged prior verdict demonstrably alters the next path choice, or **cut** the sentence. |
| "graph-reasoning latency **below 8 ms … up to 200 drone entities** after cache warm-up" | **KEEP** | Backed by Table V; defensible. |
| Abstract / conclusion resilience framing (adaptive relay selection, topology-aware reasoning) | **KEEP** | Holds once the loss/throughput numbers are reframed below. |

---

## 3. Methodology (§IV) — additions, not cuts

| Item | Tag | Recommended edit |
|---|---|---|
| Monitor mode per result | **ADD** | State explicitly which tables are **measured** (`--monitor real`) vs **synthesized** (model/scenario). Never present synthesized values as measured (validity §2.4). |
| Repeats + variance | **ADD** | Report N (runs per cell) and std-dev for every measured table. The current single-value cells read as one-shot (validity §5). |
| Model provenance | **ADD** | Pin and report the planner adapter id + base-model revision used for Table IV / Fig. 6, plus `NETPROMPT_LLM_CONSTRAINED` state (validity §5; §0 above). |
| Tables II, III (setup) | **KEEP** | Compared-approaches and scenario-config tables are fine as is. |

---

## 4. Results (§V) — table by table

### Table IV — KG-Driven SFC & Path Reasoning Decisions
- **VERIFY** provenance (§0). 
- **REFRAME:** if the condition→SFC rows are LLM outputs, they are the telemetry-driven case
  the planner fails (planner-lora-eval §2). Either confirm the model was trained on condition
  labels and present set-A-style accuracy, or attribute the condition→SFC mapping to the
  deterministic path logic and rename the claim ("rule-grounded selection," not "emerged from
  KG reasoning").
- **KEEP** the SFC-query / path-query latency columns (1.20–3.20 ms) — these are honest Cypher
  latencies and support the low-overhead claim.

### Table V — KG Reasoning Scalability (10–200 drones)
- **KEEP** — the strongest, lowest-risk result. Label the contexts **synthetic**; the "over 20
  runs" note is good. Optionally add std-dev. No SLA/performance claim attaches here, correctly.

### Table VI — Topology-Equivalent Performance Comparison
- **Throughput column → REFRAME:** relabel **"Offered Load (Mbps)"** (it is measured upstream
  of the shared relay bottleneck; it is not delivered goodput — validity §2.1). Delete the
  "maintained throughput near 10.47 Mbps despite migrating traffic" reading from the prose.
- **Packet-loss column → REFRAME:** replace 1.67 / 2.67 / 3.33 / 6.67% with coarse bounds, or
  re-measure at `ping_count`≥20 before quoting sub-10% figures (validity §2.2).
- **REFRAME (variance):** the uniform values (10.50 across all arms at baseline; KRONOS exactly
  10.47 across congestion/relay/DDIL; KRONOS 0.00% loss everywhere) read as single-run or
  synthesized — reproduce under `--monitor real` with repeats + std-dev, or mark
  "illustrative" (validity §2.4, §5).
- **RTT column → KEEP** as the headline measured dimension (the honest metric).

### Table VII — Impact of KG Removal (ablation)
- **KEEP** the ablation — isolating the KG's contribution is a good story.
- **REFRAME** the loss numbers as coarse / qualitative: "NoKG saw packet loss under relay
  failure and DDIL where KRONOS did not," rather than 2.0% / 6.67% (validity §2.2).

### Table VIII — Control-Plane Timing
- **KEEP.** Add one clause noting SFC selection ≈1.00 s is **LLM inference** (vs rule-based
  ≈µs), so the overhead is attributable and honestly framed — not hidden.

### Fig. 6 — Confusion Matrix
- **KEEP** the figure; **REFRAME** the caption/text: state it is the **same-distribution
  held-out** split over the **known 4-class taxonomy** (set A). Ensure no surrounding sentence
  reads it as telemetry-driven generalization (validity §3).

### §V.D — Adversarial & Counterfactual
- **Adversarial robustness → KEEP/adopt.** Strong. Clarify that JSON parseability / validator
  compliance is **guaranteed by constrained decoding** — present it as a guarantee check, not
  a quality win.
- **Counterfactual sensitivity → REFRAME / CUT.** If the counterfactual set varies *telemetry*
  and claims the model "changed appropriately," that contradicts 0/4 on telemetry-only missions
  (planner-lora-eval §2–3). Restrict counterfactuals to **mission-name** changes (set A), or
  present as an **oracle-agreement** check — not planner generalization (validity §3, §8).

---

## 5. Prose & Conclusion claims to scrub

| Claim | Tag | Recommended edit |
|---|---|---|
| "KRONOS maintained throughput near 10.47 Mbps despite migrating traffic" | **CUT** | Goodput framing of offered load (validity §2.1). |
| "KRONOS maintained 0% packet loss … NoKG experienced 6.67%" | **REFRAME** | Coarse bounds / qualitative (validity §2.2). |
| DDIL described as KRONOS *maintaining communication* | **REFRAME** | DDIL is genuinely infeasible; **honest escalation is the correct outcome**, not a maintained-SLA win (validity §8; experiment-design E3). |
| Closed-loop "support future decisions and learning" | **REFRAME** | Claim the loop **functions** (writes/reads verdicts); do **not** claim it **improves** decisions (validity §8). |

---

## 6. What to add — the runtime story the draft is missing

The draft models relay adaptation only as primary→backup **migration** and reports no verdict
semantics. The strongest available-but-unwritten result is the **Runtime Manager**: the
verdict taxonomy (commit / rollback / escalate / system-fault), **causal-regression
rollback**, the **contention/domination guard**, the **cost-ordered tier ladder**, fail-safe
escalation, and the **soak/recovery** robustness study. The 6-scenario behavioral spec
(commit/rollback/escalate per injected condition) is a clean, fabric-light result.

**ADD** an E2-style subsection (see [experiment-design.md §E2](experiment-design.md)). This is
the part that distinguishes NetPrompt v2 from a "KG picks an SFC" paper, and it is *more*
defensible than the throughput/loss numbers currently carrying §V.

---

## 7. Net effect — the defensible results section after edits

After the edits, the headline rests on the dimensions that survive the audit:

1. **RTT comparison** (Table VI, measured, with variance) — proposed vs static vs rule-based.
2. **KG-reasoning scalability** (Table V) — sub-8 ms to 200 synthetic drones.
3. **Control-plane timing** (Table VIII) — orchestration overhead, LLM cost named.
4. **Decision accuracy on the known taxonomy** (Fig. 6, set-A-scoped) + **adversarial
   robustness** (§V.D).
5. **KG ablation** (Table VII, qualitative resilience).
6. **Runtime verdict + safety story** (new E2 section) — the differentiator.

Receding (reframed or cut): throughput-as-goodput, sub-resolution loss percentages,
telemetry-generalization / counterfactual-sensitivity, and "the loop learns."

---

*See also: [experiment-design.md §6](experiment-design.md) (the adopt/let-go/cut mapping),
[experiment-validity.md](experiment-validity.md) (the controls + measurement caveats),
[planner-lora-eval.md](../planner/planner-lora-eval.md) (set A/B/C accuracy),
`NetPrompt_V2.pdf` (the draft these edits target).*
