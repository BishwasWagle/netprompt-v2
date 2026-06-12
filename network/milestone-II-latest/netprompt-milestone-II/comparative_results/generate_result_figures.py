#!/usr/bin/env python3

from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


BASE = Path("/home/cc/netprompt-milestone-II/comparative_results")
CSV = BASE / "comparative_results_clean.csv"
OUT = BASE / "paper_figures"


SCENARIO_ORDER = [
    "baseline",
    "low_latency",
    "battery_depletion",
    "congestion",
    "relay_failure",
    "ddil",
]

SCENARIO_LABELS = {
    "baseline": "Baseline",
    "low_latency": "Low Latency",
    "battery_depletion": "Battery",
    "congestion": "Congestion",
    "relay_failure": "Relay Failure",
    "ddil": "DDIL",
}

METHOD_LABELS = {
    "Rule-Based No-KG": "Rule-Based",
    "Static SFC + Static P4": "Static SFC",
    "Proposed KG-Driven NetPrompt": "NetPrompt",
}


def load_data() -> pd.DataFrame:
    if not CSV.exists():
        raise FileNotFoundError(f"Missing CSV: {CSV}")

    df = pd.read_csv(CSV)
    df = df[df["scenario"].isin(SCENARIO_ORDER)].copy()

    df["scenario"] = pd.Categorical(
        df["scenario"],
        categories=SCENARIO_ORDER,
        ordered=True,
    )

    df["scenario_label"] = df["scenario"].map(SCENARIO_LABELS)
    df["method_label"] = df["method"].map(METHOD_LABELS).fillna(df["method"])

    return df.sort_values(["scenario", "method_label"])


def save_bar_comparison(df: pd.DataFrame, value_col: str, ylabel: str, filename: str):
    pivot = df.pivot_table(
        index="scenario_label",
        columns="method_label",
        values=value_col,
        aggfunc="mean",
        observed=False,
    )

    pivot = pivot.reindex([SCENARIO_LABELS[s] for s in SCENARIO_ORDER])

    ax = pivot.plot(kind="bar", figsize=(9, 4.8))
    ax.set_xlabel("Scenario")
    ax.set_ylabel(ylabel)
    ax.legend(title="Method", loc="best")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()

    plt.savefig(OUT / f"{filename}.pdf")
    plt.savefig(OUT / f"{filename}.png", dpi=300)
    plt.close()


def save_netprompt_throughput(df: pd.DataFrame):
    ndf = df[df["method"] == "Proposed KG-Driven NetPrompt"].copy()
    ndf = ndf.sort_values("scenario")

    plt.figure(figsize=(8, 4.5))
    plt.bar(ndf["scenario_label"], ndf["throughput_mbps"])
    plt.xlabel("Scenario")
    plt.ylabel("Throughput (Mbps)")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()

    plt.savefig(OUT / "figure_netprompt_throughput.pdf")
    plt.savefig(OUT / "figure_netprompt_throughput.png", dpi=300)
    plt.close()


def save_orchestration_overhead(df: pd.DataFrame):
    ndf = df[df["method"] == "Proposed KG-Driven NetPrompt"].copy()
    ndf = ndf.sort_values("scenario")

    cols = [
        "sfc_selection_seconds",
        "kg_update_seconds",
    ]

    plot_df = ndf.set_index("scenario_label")[cols]
    plot_df = plot_df.rename(
        columns={
            "sfc_selection_seconds": "SFC Selection",
            "kg_update_seconds": "KG Update",
        }
    )

    ax = plot_df.plot(kind="bar", figsize=(8, 4.5))
    ax.set_xlabel("Scenario")
    ax.set_ylabel("Time (s)")
    ax.legend(title="Pipeline Stage", loc="best")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()

    plt.savefig(OUT / "figure_orchestration_overhead.pdf")
    plt.savefig(OUT / "figure_orchestration_overhead.png", dpi=300)
    plt.close()



def df_to_simple_latex(df: pd.DataFrame, caption: str, label: str) -> str:
    cols = list(df.columns)
    lines = []
    lines.append("\\begin{table*}[t]")
    lines.append(f"\\caption{{{caption}}}")
    lines.append(f"\\label{{{label}}}")
    lines.append("\\centering")
    lines.append("\\small")
    lines.append("\\begin{tabular}{" + "l" * len(cols) + "}")
    lines.append("\\toprule")
    lines.append(" & ".join(cols) + " \\\\")
    lines.append("\\midrule")

    for _, row in df.iterrows():
        vals = []
        for val in row:
            if pd.isna(val):
                vals.append("--")
            elif isinstance(val, float):
                vals.append(f"{val:.2f}")
            else:
                vals.append(str(val).replace("_", "\\_"))
        lines.append(" & ".join(vals) + " \\\\")

    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table*}")
    return "\n".join(lines)


def export_latex_tables(df: pd.DataFrame):
    ndf = df[df["method"] == "Proposed KG-Driven NetPrompt"].copy()
    ndf = ndf.sort_values("scenario")

    orchestration = ndf[
        ["scenario_label", "selected_sfc", "selected_path", "selected_relay"]
    ].rename(
        columns={
            "scenario_label": "Scenario",
            "selected_sfc": "SFC",
            "selected_path": "Path",
            "selected_relay": "Relay",
        }
    )

    performance = df[
        [
            "method_label",
            "scenario_label",
            "rtt_avg_ms",
            "throughput_mbps",
            "measured_packet_loss_percent",
        ]
    ].rename(
        columns={
            "method_label": "Method",
            "scenario_label": "Scenario",
            "rtt_avg_ms": "RTT (ms)",
            "throughput_mbps": "Throughput (Mbps)",
            "measured_packet_loss_percent": "Packet Loss (\\%)",
        }
    )

    overhead = ndf[
        [
            "scenario_label",
            "sfc_selection_seconds",
            "kg_update_seconds",
            "deployment_seconds",
            "total_pipeline_seconds",
        ]
    ].rename(
        columns={
            "scenario_label": "Scenario",
            "sfc_selection_seconds": "SFC Selection (s)",
            "kg_update_seconds": "KG Update (s)",
            "deployment_seconds": "Deployment (s)",
            "total_pipeline_seconds": "Total Pipeline (s)",
        }
    )

    (OUT / "table_orchestration_decisions.tex").write_text(
        df_to_simple_latex(
            orchestration,
            "Scenario-Aware Orchestration Decisions",
            "tab:orchestration",
        )
    )

    (OUT / "table_comparative_performance.tex").write_text(
        df_to_simple_latex(
            performance,
            "Comparative Network Performance Across Mission Scenarios",
            "tab:performance",
        )
    )

    (OUT / "table_orchestration_overhead.tex").write_text(
        df_to_simple_latex(
            overhead,
            "NetPrompt Orchestration Overhead",
            "tab:overhead",
        )
    )

def main():
    OUT.mkdir(parents=True, exist_ok=True)

    df = load_data()

    save_bar_comparison(
        df,
        value_col="rtt_avg_ms",
        ylabel="Average RTT (ms)",
        filename="figure_rtt_comparison",
    )

    save_bar_comparison(
        df,
        value_col="measured_packet_loss_percent",
        ylabel="Packet Loss (%)",
        filename="figure_packet_loss_comparison",
    )

    save_netprompt_throughput(df)
    save_orchestration_overhead(df)
    export_latex_tables(df)

    print(f"[OK] Generated figures and LaTeX tables in: {OUT}")


if __name__ == "__main__":
    main()
