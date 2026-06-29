# Repeat-KRONOS Results

Results and figures for the **repeated** Kiran & Bishwas KRONOS (NetPrompt v2) experiments,
as planned in [../repeat-kronos-experiments-plan.md](../repeat-kronos-experiments-plan.md).

**Scope.** This directory holds *only* the outputs of the repeat campaign (the builds in the
plan's §5). It is kept separate from [../results/](../results/), which holds the original
E1–E4 results (2026-06-24). Plans/designs live one level up in `docs/experiments/`; **only
results + figures are sorted in here.**

**Narrative:** [repeat-results.md](repeat-results.md) — per-build detail & findings ·
[scorecard.md](scorecard.md) — the consolidated draft-vs-re-measured scorecard.

```
repeat-results/
├── README.md          (this file)
├── *.csv              repeat-experiment result tables
└── plots/             rendered figures (matplotlib, headless)
```

## Expected outputs by build (per the plan)

| Build | Experiment | Result artifacts (planned) |
|---|---|---|
| #1 | Table VIII — control-plane timing (4-stage) | `table_viii_timing.csv` + `plots/table_viii_timing.png` |
| #2 | Table IV — decision provenance + KG query latency | `table_iv_decisions.csv` + `plots/table_iv_kg_latency.png` |
| #3 | §V.D(2) — adversarial robustness (5 perturbations) | `vd2_robustness.csv` + `plots/vd2_robustness.png` |
| #4 | §V.D(3) — counterfactual sensitivity | `vd3_counterfactual.csv` |
| #5 | Table VII — NoKG ablation arm | `table_vii_ablation.csv` + `plots/table_vii_ablation.png` |
| #6 | Fig. 6 — full per-class confusion matrix | `fig6_confusion.csv` + `plots/fig6_confusion.png` |
| #7 | Table V — KG scalability sweep *(de-prioritized)* | `table_v_scalability.csv` + `plots/table_v_scalability.png` |
| #8 | Historical-path A/B *(build-or-drop)* | `historical_path_ab.csv` + `plots/historical_path_ab.png` |

Filenames above are the convention to follow; each build's driver/repro script should write
its CSV here and render its figure into `plots/`. Re-measure all values ourselves — never
import the draft's numbers.
