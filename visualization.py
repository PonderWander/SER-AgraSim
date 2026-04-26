"""
visualization.py — Figures for the potato supply-chain simulation.

Generates:
  1. Node stress over time (line chart per node)
  2. Spoilage over time
  3. Unmet demand over time
  4. Propagation timing map (heatmap: step × node, coloured by regime)
  5. Route utilization (edge load over time)
  6. Effective graph connectivity (active edges per step)
  7. Regime classification (stacked bar chart)
"""
from __future__ import annotations
import os
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.cm as cm
import numpy as np
import pandas as pd


REGIME_COLORS = {
    "normal":       "#4caf50",
    "accumulation": "#ff9800",
    "isolated":     "#f44336",
    "fragmented":   "#9c27b0",
}

NODE_MARKERS = {
    "farm": "^",
    "storage": "s",
    "processor": "D",
    "warehouse": "o",
    "retail": "v",
}


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def plot_node_stress(node_df: pd.DataFrame, out_dir: str, title_suffix: str = ""):
    fig, ax = plt.subplots(figsize=(12, 5))
    for nid, grp in node_df.groupby("node_id"):
        grp = grp.sort_values("step")
        ax.plot(grp["step"], grp["stress"], label=nid, linewidth=1.5)
    ax.set_xlabel("Step")
    ax.set_ylabel("Stress S")
    ax.set_title(f"Node Stress Over Time {title_suffix}")
    ax.legend(fontsize=7, ncol=3)
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(out_dir, "node_stress.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_spoilage(node_df: pd.DataFrame, out_dir: str, title_suffix: str = ""):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Per-step spoilage
    ax = axes[0]
    for nid, grp in node_df.groupby("node_id"):
        grp = grp.sort_values("step")
        if grp["spoilage_this_step"].sum() > 0:
            ax.plot(grp["step"], grp["spoilage_this_step"], label=nid, linewidth=1.5)
    ax.set_xlabel("Step")
    ax.set_ylabel("Spoilage (tonnes/step)")
    ax.set_title(f"Spoilage Per Step {title_suffix}")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=0.3)

    # Cumulative spoilage bar
    ax2 = axes[1]
    last = node_df.groupby("node_id")["cumulative_spoilage"].max().sort_values(ascending=False)
    ax2.barh(last.index, last.values, color="#f44336", alpha=0.8)
    ax2.set_xlabel("Cumulative Spoilage (tonnes)")
    ax2.set_title(f"Total Spoilage by Node {title_suffix}")
    ax2.grid(alpha=0.3, axis="x")

    fig.tight_layout()
    path = os.path.join(out_dir, "spoilage.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_unmet_demand(node_df: pd.DataFrame, out_dir: str, title_suffix: str = ""):
    demand_nodes = node_df[node_df["demand"] > 0]
    if demand_nodes.empty:
        return None
    fig, ax = plt.subplots(figsize=(12, 5))
    for nid, grp in demand_nodes.groupby("node_id"):
        grp = grp.sort_values("step")
        ax.plot(grp["step"], grp["unmet_demand"], label=nid, linewidth=1.8)
    ax.set_xlabel("Step")
    ax.set_ylabel("Unmet Demand (tonnes)")
    ax.set_title(f"Unmet Demand Over Time {title_suffix}")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(out_dir, "unmet_demand.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_propagation_heatmap(node_df: pd.DataFrame, out_dir: str, title_suffix: str = ""):
    """
    Heatmap: steps (y-axis) × nodes (x-axis), colour = stress level.
    Overlaid with regime transitions.
    """
    pivot = node_df.pivot_table(index="step", columns="node_id", values="stress")
    fig, ax = plt.subplots(figsize=(14, 6))
    im = ax.imshow(
        pivot.values,
        aspect="auto",
        cmap="YlOrRd",
        vmin=0, vmax=1,
        origin="upper",
    )
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Step")
    ax.set_title(f"Stress Propagation Heatmap {title_suffix}")
    plt.colorbar(im, ax=ax, label="Stress S")
    fig.tight_layout()
    path = os.path.join(out_dir, "propagation_heatmap.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_route_utilization(edge_df: pd.DataFrame, out_dir: str, title_suffix: str = ""):
    fig, ax = plt.subplots(figsize=(12, 5))
    for eid, grp in edge_df.groupby("edge_id"):
        grp = grp.sort_values("step")
        ax.plot(grp["step"], grp["utilization"], label=eid, linewidth=1.0, alpha=0.75)
    ax.axhline(0.9, color="red", linestyle="--", linewidth=1, label="constraint threshold")
    ax.set_xlabel("Step")
    ax.set_ylabel("Utilization")
    ax.set_title(f"Route Utilization Over Time {title_suffix}")
    ax.set_ylim(0, 1.1)
    ax.legend(fontsize=6, ncol=4)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(out_dir, "route_utilization.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_effective_connectivity(edge_df: pd.DataFrame, out_dir: str, title_suffix: str = ""):
    active_per_step = (
        edge_df[edge_df["is_active"] == True]
        .groupby("step")["edge_id"]
        .count()
        .reset_index()
        .rename(columns={"edge_id": "active_edges"})
    )
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.fill_between(active_per_step["step"], active_per_step["active_edges"], alpha=0.4, color="#2196f3")
    ax.plot(active_per_step["step"], active_per_step["active_edges"], color="#2196f3", linewidth=2)
    ax.set_xlabel("Step")
    ax.set_ylabel("Active Edges")
    ax.set_title(f"Effective Graph Connectivity {title_suffix}")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(out_dir, "connectivity.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_regime_classification(node_df: pd.DataFrame, out_dir: str, title_suffix: str = ""):
    regime_counts = (
        node_df.groupby(["step", "regime"])["node_id"]
        .count()
        .unstack(fill_value=0)
    )
    # Ensure all regimes present
    for r in ["normal", "accumulation", "isolated", "fragmented"]:
        if r not in regime_counts.columns:
            regime_counts[r] = 0

    fig, ax = plt.subplots(figsize=(12, 4))
    bottom = np.zeros(len(regime_counts))
    for regime in ["normal", "accumulation", "isolated", "fragmented"]:
        vals = regime_counts[regime].values
        ax.bar(
            regime_counts.index, vals,
            bottom=bottom,
            label=regime.capitalize(),
            color=REGIME_COLORS[regime],
            alpha=0.85,
        )
        bottom += vals

    ax.set_xlabel("Step")
    ax.set_ylabel("Node Count")
    ax.set_title(f"Regime Classification Over Time {title_suffix}")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.2, axis="y")
    fig.tight_layout()
    path = os.path.join(out_dir, "regime_classification.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_leaked_stress_comparison(
    results: Dict[str, pd.DataFrame],
    out_dir: str,
    nodes_of_interest: Optional[List[str]] = None,
):
    """
    Multi-experiment comparison: leaked stress over time for selected nodes.
    results: {experiment_label: node_df}
    """
    if nodes_of_interest is None:
        # Pick demand nodes
        first_df = next(iter(results.values()))
        nodes_of_interest = first_df[first_df["demand"] > 0]["node_id"].unique().tolist()[:4]

    n_nodes = len(nodes_of_interest)
    fig, axes = plt.subplots(1, n_nodes, figsize=(4 * n_nodes, 4), sharey=True)
    if n_nodes == 1:
        axes = [axes]

    colors = plt.cm.Set2(np.linspace(0, 1, len(results)))

    for ax, nid in zip(axes, nodes_of_interest):
        for (label, df), c in zip(results.items(), colors):
            grp = df[df["node_id"] == nid].sort_values("step")
            ax.plot(grp["step"], grp["leaked_stress"], label=label, color=c, linewidth=1.8)
        ax.set_title(nid, fontsize=9)
        ax.set_xlabel("Step")
        ax.set_ylim(0, 1.05)
        ax.grid(alpha=0.3)

    axes[0].set_ylabel("Leaked Stress L")
    axes[-1].legend(fontsize=7, loc="upper right")
    fig.suptitle("Leaked Stress Comparison Across Experiments", fontsize=11)
    fig.tight_layout()
    path = os.path.join(out_dir, "leaked_stress_comparison.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def save_all_figures(
    node_df: pd.DataFrame,
    edge_df: pd.DataFrame,
    out_dir: str,
    title_suffix: str = "",
) -> List[str]:
    _ensure_dir(out_dir)
    paths = []
    paths.append(plot_node_stress(node_df, out_dir, title_suffix))
    paths.append(plot_spoilage(node_df, out_dir, title_suffix))
    p = plot_unmet_demand(node_df, out_dir, title_suffix)
    if p:
        paths.append(p)
    paths.append(plot_propagation_heatmap(node_df, out_dir, title_suffix))
    paths.append(plot_route_utilization(edge_df, out_dir, title_suffix))
    paths.append(plot_effective_connectivity(edge_df, out_dir, title_suffix))
    paths.append(plot_regime_classification(node_df, out_dir, title_suffix))
    return paths
