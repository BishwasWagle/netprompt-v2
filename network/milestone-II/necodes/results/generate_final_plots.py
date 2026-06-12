import os
import pandas as pd
import matplotlib.pyplot as plt

RESULTS_CSV = "/home/cc/netprompt-milestone-II/results/final_milestone2_results_clean.csv"
PLOTS_DIR = "/home/cc/netprompt-milestone-II/results/plots"

os.makedirs(PLOTS_DIR, exist_ok=True)

df = pd.read_csv(RESULTS_CSV)

# Keep latest result per scenario + SFC if duplicates exist
df = df.drop_duplicates(
    subset=["scenario", "selected_sfc", "experiment_type"],
    keep="last"
)

# Sort for consistent plot order
scenario_order = [
    "low_latency",
    "baseline",
    "congestion",
    "ddil",
    "relay_failure",
    "battery_depletion"
]

df["scenario"] = pd.Categorical(
    df["scenario"],
    categories=scenario_order,
    ordered=True
)

df = df.sort_values("scenario")


def save_plot(filename):
    path = os.path.join(PLOTS_DIR, filename)
    plt.tight_layout()
    plt.savefig(path, dpi=300)
    plt.close()
    print(f"Saved: {path}")


# -------------------------------
# Plot 1: RTT by Scenario
# -------------------------------
plt.figure(figsize=(10, 5))
plt.bar(df["scenario"].astype(str), df["rtt_avg_ms"])
plt.xlabel("Scenario")
plt.ylabel("Average RTT (ms)")
plt.title("Average RTT by Scenario")
plt.xticks(rotation=30, ha="right")
save_plot("plot_1_rtt_by_scenario.png")


# -------------------------------
# Plot 2: Packet Loss by Scenario
# -------------------------------
plt.figure(figsize=(10, 5))
plt.bar(df["scenario"].astype(str), df["measured_packet_loss_percent"])
plt.xlabel("Scenario")
plt.ylabel("Packet Loss (%)")
plt.title("Measured Packet Loss by Scenario")
plt.xticks(rotation=30, ha="right")
save_plot("plot_2_packet_loss_by_scenario.png")


# -------------------------------
# Plot 3: Throughput by Scenario
# -------------------------------
if "throughput_mbps" in df.columns:
    plt.figure(figsize=(10, 5))
    plt.bar(df["scenario"].astype(str), df["throughput_mbps"])
    plt.xlabel("Scenario")
    plt.ylabel("Throughput (Mbps)")
    plt.title("Throughput by Scenario")
    plt.xticks(rotation=30, ha="right")
    save_plot("plot_3_throughput_by_scenario.png")


# -------------------------------
# Plot 4: RTT vs Packet Loss
# -------------------------------
plt.figure(figsize=(8, 5))
plt.scatter(df["rtt_avg_ms"], df["measured_packet_loss_percent"])

for _, row in df.iterrows():
    plt.text(
        row["rtt_avg_ms"],
        row["measured_packet_loss_percent"],
        str(row["scenario"]),
        fontsize=8
    )

plt.xlabel("Average RTT (ms)")
plt.ylabel("Packet Loss (%)")
plt.title("RTT vs Packet Loss Across Scenarios")
save_plot("plot_4_rtt_vs_packet_loss.png")


# -------------------------------
# Plot 5: SFC Selected per Scenario
# -------------------------------
sfc_counts = df["selected_sfc"].value_counts()

plt.figure(figsize=(8, 5))
plt.bar(sfc_counts.index.astype(str), sfc_counts.values)
plt.xlabel("Selected SFC")
plt.ylabel("Number of Scenarios")
plt.title("SFC Selection Frequency")
plt.xticks(rotation=30, ha="right")
save_plot("plot_5_sfc_selection_frequency.png")


# -------------------------------
# Plot 6: Experiment Type Distribution
# -------------------------------
if "experiment_type" in df.columns:
    exp_counts = df["experiment_type"].value_counts()

    plt.figure(figsize=(8, 5))
    plt.bar(exp_counts.index.astype(str), exp_counts.values)
    plt.xlabel("Experiment Type")
    plt.ylabel("Count")
    plt.title("Experiment Type Distribution")
    plt.xticks(rotation=30, ha="right")
    save_plot("plot_6_experiment_type_distribution.png")


# -------------------------------
# Plot 7: Policy Type by Scenario
# -------------------------------
if "policy_type" in df.columns:
    policy_df = df[["scenario", "policy_type"]].dropna()

    plt.figure(figsize=(11, 5))
    plt.bar(policy_df["scenario"].astype(str), range(1, len(policy_df) + 1))
    plt.xlabel("Scenario")
    plt.ylabel("Policy Index")
    plt.title("Policy Type Applied per Scenario")
    plt.xticks(rotation=30, ha="right")

    for idx, row in enumerate(policy_df.itertuples(), start=1):
        plt.text(
            idx - 1,
            idx,
            row.policy_type,
            fontsize=8,
            rotation=20,
            ha="center"
        )

    save_plot("plot_7_policy_type_by_scenario.png")


# -------------------------------
# Summary table
# -------------------------------
summary_cols = [
    "scenario",
    "experiment_type",
    "selected_sfc",
    "policy_type",
    "rtt_avg_ms",
    "measured_packet_loss_percent",
    "throughput_mbps",
    "source_file"
]

existing_cols = [c for c in summary_cols if c in df.columns]
summary = df[existing_cols]

summary_path = os.path.join(PLOTS_DIR, "final_results_summary_table.csv")
summary.to_csv(summary_path, index=False)
print(f"Saved summary table: {summary_path}")

print("\nGenerated all plots successfully.")
