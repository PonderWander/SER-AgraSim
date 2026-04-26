"""
run_experiments.py — Four experiments with stable demand and throughput stress vector.

Demand policy
-------------
All four experiments use STABLE_DEMAND from src/demand.py — the same
externally-grounded figures for every run.  Retail demand does not vary
with season or with production.  This ensures that regime differences
are caused by structural constraint differences, not demand variation.

Experiment design
-----------------
The structural distinction being tested:

  Single constraint (Exp 2):
    One cap on stor_id storage.  All downstream corridors remain open.
    Expected: saturation accumulates at stor_id, near-expiry fraction rises,
    tp_blocked_ratio rises, but tp_utilisation_no_slack stays LOW because
    the secondary route to proc_east (e011) remains feasible.
    Regime: accumulation only — high S, low L, can_reach_retail stays True.

  Multi-constraint (Exp 3):
    Storage cap + processor bottleneck + primary corridor severed simultaneously.
    Each constraint hits a distinct E component:
      storage cap      → e_spare_storage → 0
      proc bottleneck  → e_clearance_rate → 0  (stor_id can't clear even dwell)
      e007 severed     → e_alt_routes → 0       (secondary route e011 saturates)
    tp_utilisation_no_slack spikes (all edges at threshold, no slack).
    tp_downstream_rejection rises (proc_west full from backlog).
    E collapses multiplicatively; L = S × (1 - E×η) jumps.
    Regime: isolated → fragmented; can_reach_retail → False for farm_id/stor_id.

  Elasticity mitigation (Exp 4):
    Same constraints as Exp 3 + extra storage + lateral routes.
    Extra storage → e_spare_storage recovers partially.
    Lateral routes → e_alt_routes recovers (feasible alternate path exists).
    tp_utilisation_no_slack stays lower (slack via lateral).
    E doesn't fully collapse → L stays below l_fragment.
    Regime: accumulation rather than fragmented.
"""
from __future__ import annotations
import logging
import os
import sys
from typing import Dict

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.engine import SupplyChainEngine
from src.dynamics import DEFAULT_DYNAMICS
from src.synthetic_network import build_potato_network
from src.demand import apply_stable_demand, TOTAL_DEMAND, TOTAL_RETAIL_DEMAND
from src.visualization import save_all_figures, plot_leaked_stress_comparison

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("experiments")

N_STEPS = 52
SEED    = 42
ROOT    = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(ROOT, exist_ok=True)

PRIMARY_CORRIDOR = "e007"   # stor_id → proc_west

REGIME_COLORS = {
    "normal":       "#4caf50",
    "accumulation": "#ff9800",
    "isolated":     "#f44336",
    "fragmented":   "#9c27b0",
}
KEY_NODES = ["stor_id", "farm_id", "proc_west", "wh_west", "ret_west"]


# ── helpers ───────────────────────────────────────────────────────────────────

def _build(extra_storage: float = 0.0, lateral: bool = False):
    """Build network, apply stable demand, return (nodes, edges)."""
    nodes, edges = build_potato_network(
        season_week=40,
        extra_storage_capacity=extra_storage,
        add_lateral_routes=lateral,
    )
    apply_stable_demand(nodes)
    return nodes, edges


def _run(engine: SupplyChainEngine, label: str):
    engine.run(N_STEPS)
    ndf = engine.node_dataframe()
    edf = engine.edge_dataframe()
    out = os.path.join(ROOT, label)
    os.makedirs(out, exist_ok=True)
    ndf.to_csv(os.path.join(ROOT, f"{label}_nodes.csv"), index=False)
    edf.to_csv(os.path.join(ROOT, f"{label}_edges.csv"), index=False)
    save_all_figures(ndf, edf, out, title_suffix=f"[{label}]")
    return ndf, edf


# ── experiments ───────────────────────────────────────────────────────────────

def exp1_baseline():
    logger.info("=== Exp 1: Baseline ===")
    nodes, edges = _build()
    return _run(SupplyChainEngine(nodes, edges, seed=SEED), "exp1_baseline")


def exp2_single_constraint():
    """
    Storage cap only — one E component (spare_storage) is squeezed.
    All downstream corridors remain open; clearance_rate and alt_routes intact.
    Expected: accumulation + rising near-expiry, NOT isolation.
    """
    logger.info("=== Exp 2: Single-Constraint Retention ===")
    nodes, edges = _build()
    engine = SupplyChainEngine(nodes, edges, seed=SEED)
    cap = nodes["stor_id"].storage_capacity
    for step, frac in [(8, 0.70), (16, 0.50), (24, 0.38), (32, 0.28)]:
        engine.schedule_disruption(step, "stor_id", "storage_capacity", cap * frac)
    return _run(engine, "exp2_single")


def exp3_interacting_constraints():
    """
    Three simultaneous constraints — each zeroes a distinct E component.
    (a) stor_id storage cap → 35 %     removes e_spare_storage
    (b) proc_west tp halved             removes e_clearance_rate (node can't clear)
    (c) e007 severed (factor → 0)       removes e_alt_routes (e011 saturates)
    Expected: isolated/fragmented regime; can_reach_retail False upstream.
    """
    logger.info("=== Exp 3: Interacting Constraints ===")
    nodes, edges = _build()
    engine = SupplyChainEngine(nodes, edges, seed=SEED)
    cap_stor = nodes["stor_id"].storage_capacity
    cap_proc = nodes["proc_west"].throughput_capacity
    on = 10
    engine.schedule_disruption(on, "stor_id",          "storage_capacity",    cap_stor * 0.35)
    engine.schedule_disruption(on, "proc_west",         "throughput_capacity", cap_proc * 0.50)
    engine.schedule_disruption(on, PRIMARY_CORRIDOR,    "disruption_factor",   0.0)
    return _run(engine, "exp3_interacting")


def exp4_elasticity_mitigation():
    """
    Same three constraints as Exp 3, but with extra storage + lateral routes.
    extra_storage restores e_spare_storage.
    lateral routes restore e_alt_routes (feasible alternate path exists).
    Expected: accumulation rather than fragmented; L stays below l_fragment.
    """
    logger.info("=== Exp 4: Elasticity Mitigation ===")
    nodes, edges = _build(extra_storage=450.0, lateral=True)
    engine = SupplyChainEngine(nodes, edges, seed=SEED)
    cap_stor = nodes["stor_id"].storage_capacity
    cap_proc = nodes["proc_west"].throughput_capacity
    on = 10
    engine.schedule_disruption(on, "stor_id",       "storage_capacity",    cap_stor * 0.50)
    engine.schedule_disruption(on, "proc_west",      "throughput_capacity", cap_proc * 0.50)
    engine.schedule_disruption(on, PRIMARY_CORRIDOR, "disruption_factor",   0.0)
    return _run(engine, "exp4_elasticity")


# ── analysis plots ────────────────────────────────────────────────────────────

def plot_tp_vector(df: pd.DataFrame, node_id: str, label: str, out_dir: str):
    """
    Stacked-bar chart of throughput stress vector components for one node.
    Shows exactly which tp_ component drove each step's stress contribution,
    with clearance_credit shown as a downward bar (stress relief).
    """
    os.makedirs(out_dir, exist_ok=True)
    grp = df[df["node_id"] == node_id].sort_values("step")
    if grp.empty:
        return None
    steps = grp["step"].values

    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True)

    # ── Panel 1: full S component stack ──────────────────────────────────────
    ax = axes[0]
    s_pos = [
        ("s_saturation_contrib",  "#e53935", "Saturation"),
        ("s_age_contrib",         "#fb8c00", "Age pressure"),
        ("s_throughput_contrib",  "#6d4c41", "Throughput (net)"),
        ("s_backlog_contrib",     "#fdd835", "Backlog"),
        ("s_unmet_contrib",       "#8e24aa", "Unmet demand"),
    ]
    bottom = np.zeros(len(steps))
    for col, clr, lbl in s_pos:
        if col in grp.columns:
            v = grp[col].values
            ax.bar(steps, v, bottom=bottom, color=clr, alpha=0.85, label=lbl, width=0.9)
            bottom += v
    ax.plot(steps, grp["stress"].values, color="black", lw=2, label="S total", zorder=5)
    for _, r in grp.iterrows():
        ax.axvspan(r["step"]-0.5, r["step"]+0.5, color=REGIME_COLORS.get(r["regime"],"#ccc"), alpha=0.10)
    ax.set_ylim(0, 1.05); ax.set_ylabel("Stress S", fontsize=9)
    ax.legend(fontsize=7, ncol=3, loc="upper left")
    ax.set_title(f"{label} | {node_id} — Full S decomposition", fontsize=10)
    ax.grid(alpha=0.2)

    # ── Panel 2: throughput vector breakdown ──────────────────────────────────
    ax2 = axes[1]
    tp_pos_cols = [
        ("tp_blocked_ratio",        "#d32f2f", "Blocked ratio"),
        ("tp_queue_persistence",    "#e64a19", "Queue persistence"),
        ("tp_utilisation_no_slack", "#f57c00", "Utilisation/no-slack"),
        ("tp_downstream_rejection", "#7b1fa2", "Downstream rejection"),
    ]
    bottom = np.zeros(len(steps))
    for col, clr, lbl in tp_pos_cols:
        if col in grp.columns:
            v = grp[col].values
            ax2.bar(steps, v, bottom=bottom, color=clr, alpha=0.85, label=lbl, width=0.9)
            bottom += v
    # Clearance credit as downward bar
    if "tp_clearance_credit" in grp.columns:
        credit = grp["tp_clearance_credit"].values
        ax2.bar(steps, -credit, bottom=0, color="#1b5e20", alpha=0.75, label="Clearance credit (↓)", width=0.9)
    ax2.axhline(0, color="black", lw=0.8)
    ax2.plot(steps, grp["s_throughput_contrib"].values, color="black", lw=1.8, ls="--", label="Net tp contribution")
    ax2.set_ylabel("TP stress components", fontsize=9)
    ax2.legend(fontsize=7, ncol=3)
    ax2.grid(alpha=0.2)

    # ── Panel 3: E component stack with L and S overlay ───────────────────────
    ax3 = axes[2]
    e_cols = [
        ("e_spare_storage_contrib",  "#1565c0", "Spare storage"),
        ("e_clearance_rate_contrib", "#0288d1", "Clearance rate"),
        ("e_alt_routes_contrib",     "#00897b", "Alt routes"),
        ("e_buffer_health_contrib",  "#558b2f", "Buffer health"),
    ]
    bottom = np.zeros(len(steps))
    for col, clr, lbl in e_cols:
        if col in grp.columns:
            v = grp[col].values
            ax3.bar(steps, v, bottom=bottom, color=clr, alpha=0.82, label=lbl, width=0.9)
            bottom += v
    ax3.plot(steps, grp["elasticity"].values,    color="black",   lw=2, label="E total")
    ax3.plot(steps, grp["leaked_stress"].values, color="#b71c1c", lw=2, ls="--", label="L (leaked)")
    ax3.set_xlabel("Step (week)", fontsize=9)
    ax3.set_ylabel("Elasticity E", fontsize=9)
    ax3.set_ylim(0, 1.05)
    ax3.legend(fontsize=7, ncol=3)
    ax3.grid(alpha=0.2)

    fig.tight_layout()
    path = os.path.join(out_dir, f"tp_vector_{label}_{node_id}.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    logger.info("Saved %s", path)
    return path


def plot_regime_panel(results: Dict[str, pd.DataFrame], out_dir: str):
    """One row per experiment, one column per key node. S/L/E + regime bands."""
    os.makedirs(out_dir, exist_ok=True)
    labels = list(results.keys())
    fig, axes = plt.subplots(len(labels), len(KEY_NODES),
                             figsize=(3.8*len(KEY_NODES), 3.0*len(labels)),
                             sharex=True, sharey=True)
    if len(labels) == 1:
        axes = [axes]

    for row, label in enumerate(labels):
        df = results[label]
        for col, nid in enumerate(KEY_NODES):
            ax = axes[row][col]
            g  = df[df["node_id"]==nid].sort_values("step")
            if g.empty:
                ax.set_visible(False); continue
            for _, r in g.iterrows():
                ax.axvspan(r["step"]-0.5, r["step"]+0.5,
                           color=REGIME_COLORS.get(r["regime"],"#ccc"), alpha=0.30)
            ax.plot(g["step"], g["stress"],        "#1565c0", lw=1.8, label="S")
            ax.plot(g["step"], g["leaked_stress"],  "#b71c1c", lw=1.8, label="L")
            ax.plot(g["step"], g["elasticity"],     "#2e7d32", lw=1.2, ls="--", label="E")
            ax.set_ylim(0, 1.05)
            if col == 0:  ax.set_ylabel(label, fontsize=8)
            if row == 0:  ax.set_title(nid, fontsize=9, fontweight="bold")
            if row == len(labels)-1: ax.set_xlabel("Step", fontsize=8)
            ax.tick_params(labelsize=7); ax.grid(alpha=0.2)

    handles = [
        plt.Line2D([0],[0], color="#1565c0", lw=2, label="Stress S"),
        plt.Line2D([0],[0], color="#b71c1c", lw=2, label="Leaked L"),
        plt.Line2D([0],[0], color="#2e7d32", lw=1.5, ls="--", label="Elasticity E"),
    ] + [plt.Rectangle((0,0),1,1, fc=c, alpha=0.4, label=r.capitalize())
         for r, c in REGIME_COLORS.items()]
    fig.legend(handles=handles, loc="lower center", ncol=7, fontsize=8,
               bbox_to_anchor=(0.5,-0.01))
    fig.suptitle("Regime comparison (stable demand, same across all experiments)", fontsize=11)
    fig.tight_layout(rect=[0,0.04,1,0.97])
    path = os.path.join(out_dir, "regime_comparison.png")
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved %s", path)
    return path


def plot_reachability(results: Dict[str, pd.DataFrame], out_dir: str):
    """
    Three-panel connectivity figure.

    Panel 1: can_reach_retail — fraction of nodes with a physically-open path
             (no severed corridor) to any retail node.  Uses is_physically_open().
    Panel 2: global_open_fraction — fraction of all edges that are physically open.
             Drops only when edges are severed by disruption.
    Panel 3: global_headroom_fraction — fraction of all edges below their
             utilisation threshold.  Drops when corridors saturate under load.
             This is the elasticity signal, not a reachability signal.

    The split between panels 2 and 3 makes the structural distinction visible:
    in Exp3, open_fraction drops (e007 severed) AND headroom_fraction drops
    (e011 saturated).  In Exp4, open_fraction also drops (e007 still severed)
    but headroom_fraction recovers because lateral routes provide spare capacity.
    """
    os.makedirs(out_dir, exist_ok=True)
    colors = ["#4caf50","#ff9800","#f44336","#2196f3"]
    fig, axes = plt.subplots(3, 1, figsize=(12,8), sharex=True)
    for (label, ndf), clr in zip(results.items(), colors):
        steps = sorted(ndf["step"].unique())
        grp   = ndf.groupby("step")
        reach   = [grp.get_group(s)["can_reach_retail"].mean() for s in steps]
        g_open  = ndf.groupby("step")["global_open_fraction"].mean()
        g_head  = ndf.groupby("step")["global_headroom_fraction"].mean()
        axes[0].plot(steps, reach,          label=label, color=clr, lw=2.2)
        axes[1].plot(g_open.index, g_open,  label=label, color=clr, lw=2.2)
        axes[2].plot(g_head.index, g_head,  label=label, color=clr, lw=2.2)

    axes[0].set_ylabel("Nodes with open\npath to retail", fontsize=9)
    axes[0].set_ylim(-0.05,1.1); axes[0].axhline(1,color="grey",ls=":",lw=1)
    axes[0].legend(fontsize=8); axes[0].grid(alpha=0.25)
    axes[0].set_title(
        "Connectivity: physical openness vs spare-capacity headroom\n"
        "(demand identical across all experiments; differences are constraint-driven)",
        fontsize=10)

    axes[1].set_ylabel("Physically open\nedge fraction", fontsize=9)
    axes[1].set_ylim(-0.05,1.1); axes[1].axhline(1,color="grey",ls=":",lw=1)
    axes[1].legend(fontsize=8); axes[1].grid(alpha=0.25)
    axes[1].annotate("drops when edges are SEVERED", xy=(0.02,0.08),
                     xycoords="axes fraction", fontsize=8, color="grey", style="italic")

    axes[2].set_ylabel("Spare-capacity\nedge fraction", fontsize=9)
    axes[2].set_xlabel("Step (week)", fontsize=9)
    axes[2].set_ylim(-0.05,1.1); axes[2].axhline(1,color="grey",ls=":",lw=1)
    axes[2].legend(fontsize=8); axes[2].grid(alpha=0.25)
    axes[2].annotate("drops when edges are SATURATED", xy=(0.02,0.08),
                     xycoords="axes fraction", fontsize=8, color="grey", style="italic")

    fig.tight_layout()
    path = os.path.join(out_dir, "reachability_connectivity.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    logger.info("Saved %s", path)


def plot_near_expiry_heatmap(results: Dict[str, pd.DataFrame], out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    labels = list(results.keys())
    all_nodes = sorted(next(iter(results.values()))["node_id"].unique())
    fig, axes = plt.subplots(1, len(labels), figsize=(5*len(labels), 6), sharey=True)
    if len(labels)==1: axes=[axes]
    for ax, label in zip(axes, labels):
        pivot = results[label].pivot_table(
            index="node_id", columns="step", values="near_expiry_fraction")
        pivot = pivot.reindex(all_nodes, fill_value=0.0)
        im = ax.imshow(pivot.values, aspect="auto", cmap="YlOrRd", vmin=0, vmax=1, origin="upper")
        ax.set_yticks(range(len(all_nodes)))
        ax.set_yticklabels(all_nodes, fontsize=8)
        ax.set_xlabel("Step", fontsize=9)
        ax.set_title(label, fontsize=9, fontweight="bold")
        plt.colorbar(im, ax=ax, label="Near-expiry fraction")
    fig.suptitle("Near-expiry fraction — age pressure concentration (stable demand)", fontsize=10)
    fig.tight_layout()
    path = os.path.join(out_dir, "near_expiry_heatmap.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    logger.info("Saved %s", path)


def print_summary(results: Dict[str, pd.DataFrame]):
    """
    Summary table with separated connectivity metrics.

    severed_steps  = steps where can_reach_retail is False
                     (physically-open path to retail was broken by disruption)
    saturated_steps = steps where feasible_routes_out == 0
                     (all outgoing edges are at capacity, no spare headroom)

    These are distinct conditions.  A node can have saturated_steps > 0 while
    severed_steps == 0 (busy but connected) — that is accumulation.  A node
    with severed_steps > 0 has lost structural connectivity.
    """
    print("\n" + "="*100)
    print("DEMAND LAYER:  identical across all experiments")
    print(f"  Total retail demand:    {TOTAL_RETAIL_DEMAND:.0f} t/wk  (stable, not season-adjusted)")
    print(f"  Total system demand:    {TOTAL_DEMAND:.0f} t/wk")
    print("="*100)

    nodes_of_interest = ["stor_id", "farm_id", "proc_west"]
    hdr = (f"\n{'Experiment':<22} {'Node':<12} {'Peak S':>7} {'Peak L':>7} {'Min E':>7} "
           f"{'Peak regime':<14} {'tp_blocked':>10} {'severed_steps':>14} {'saturated_steps':>16}")
    print(hdr)
    print("-"*115)
    for label, df in results.items():
        for i, nid in enumerate(nodes_of_interest):
            grp = df[df["node_id"] == nid]
            if grp.empty:
                continue
            row        = grp.loc[grp["stress"].idxmax()]
            peak_l     = grp["leaked_stress"].max()
            min_e      = grp["elasticity"].min()
            peak_regime= grp.loc[grp["stress"].idxmax(), "regime"]
            tp_blocked = grp["tp_blocked_ratio"].max()
            # severed: physically-open path to retail is broken
            severed    = (grp["can_reach_retail"] == False).sum()
            # saturated: all outgoing edges at capacity (no headroom for routing)
            saturated  = (grp["feasible_routes_out"] == 0).sum()
            exp_col    = label if i == 0 else ""
            print(f"{exp_col:<22} {nid:<12} {row['stress']:>7.3f} {peak_l:>7.3f} {min_e:>7.3f} "
                  f"{peak_regime:<14} {tp_blocked:>10.3f} {severed:>14} {saturated:>16}")
        print()

    print("="*100)
    print("\nCONNECTIVITY RECONCILIATION (severed = broken path; saturated = no headroom)")
    print(f"  {'Experiment':<22} {'Node':<12} {'peak_regime':<16} {'severed_steps':>14} {'saturated_steps':>16}  interpretation")
    print("-"*100)
    for label, df in results.items():
        for nid in ["stor_id", "farm_id"]:
            grp  = df[df["node_id"] == nid]
            if grp.empty: continue
            peak     = grp.loc[grp["stress"].idxmax(), "regime"]
            severed  = (grp["can_reach_retail"] == False).sum()
            saturated= (grp["feasible_routes_out"] == 0).sum()
            # Also check downstream: if this node's regime is isolated/fragmented
            # but severed_steps==0, isolation is driven by S/E/L (downstream
            # saturation of first-hop target blocks dispatch), not path loss.
            if severed == 0 and saturated == 0 and peak not in ("isolated","fragmented"):
                interp = "healthy — open path, spare capacity, normal S/E/L"
            elif severed == 0 and saturated > 0:
                interp = "accumulation — all local routes saturated, path intact"
            elif severed > 0 and saturated > 0:
                interp = "isolated — path severed AND local routes saturated"
            elif severed > 0 and saturated == 0:
                interp = "severed path, local capacity available (unusual)"
            elif peak in ("isolated","fragmented") and severed == 0:
                interp = "isolated via S/E/L — downstream blockage, path physically intact"
            else:
                interp = "healthy — open path, spare capacity, normal S/E/L"
            print(f"  {label:<22} {nid:<12} {peak:<16} {severed:>14} {saturated:>16}  {interp}")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ndf1, _ = exp1_baseline()
    ndf2, _ = exp2_single_constraint()
    ndf3, _ = exp3_interacting_constraints()
    ndf4, _ = exp4_elasticity_mitigation()

    results = {
        "Exp1 Baseline":   ndf1,
        "Exp2 Single":     ndf2,
        "Exp3 Multi":      ndf3,
        "Exp4 Elasticity": ndf4,
    }

    comp = os.path.join(ROOT, "comparison")
    os.makedirs(comp, exist_ok=True)

    plot_regime_panel(results, comp)
    plot_near_expiry_heatmap(results, comp)
    plot_reachability(results, comp)

    for label, df in results.items():
        slug = label.replace(" ","_")
        plot_tp_vector(df, "stor_id", slug, comp)

    plot_leaked_stress_comparison(
        results, comp,
        nodes_of_interest=["stor_id", "proc_west", "wh_west", "ret_west"],
    )

    print_summary(results)
    print(f"All outputs: {ROOT}")
