"""
dynamics.py — Configurable S/E/L/R formulas for supply-chain stress framework.

All formulas are pure functions of node state and network context.
They can be replaced by subclassing or providing a custom DynamicsConfig.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional
from src.models import NodeState


@dataclass
class DynamicsConfig:
    """
    Weights and parameters for the S/E/L/R computation.
    Override individual fields to experiment with alternative formulations.
    """
    # --- Stress weights ---
    w_backlog: float = 0.30         # weight for backlog ratio in S
    w_unmet_demand: float = 0.25    # weight for unmet-demand ratio
    w_storage_util: float = 0.20    # weight for storage utilization
    w_age_pressure: float = 0.15    # weight for inventory age pressure
    w_throughput_strain: float = 0.10  # weight for throughput near capacity

    # --- Elasticity weights ---
    w_spare_storage: float = 0.30   # weight for spare storage fraction
    w_spare_throughput: float = 0.25
    w_alt_routes: float = 0.25      # weight for alternate-route access
    w_buffer_inv: float = 0.20      # weight for buffer inventory fraction

    # --- L absorption ---
    absorption_base: float = 0.5    # fraction of E that absorbs stress before leaking
    local_efficiency: float = 0.8   # how efficiently elasticity absorbs stress

    # --- R dynamics ---
    r_leak_weight: float = 0.40
    r_spoilage_weight: float = 0.30
    r_unmet_weight: float = 0.20
    r_persistence_weight: float = 0.10
    r_decay: float = 0.85           # R decays each step if no new stress

    # --- Isolation threshold ---
    isolation_stress_threshold: float = 0.85
    fragmentation_leaked_threshold: float = 0.70

    # --- Normalisation references (per node type if desired) ---
    backlog_ref: float = 200.0      # tonnes — backlog that would give ratio = 1
    demand_ref_fraction: float = 1.0  # unmet / demand gives ratio directly


# Default configuration used by the engine unless overridden
DEFAULT_DYNAMICS = DynamicsConfig()


def compute_stress(state: NodeState, cfg: DynamicsConfig = DEFAULT_DYNAMICS) -> float:
    """
    S ∈ [0,1]: local burden.

    Components:
    - backlog_ratio:      backlog / backlog_ref
    - unmet_demand_ratio: unmet_demand / max(demand, 1)
    - storage_util:       inventory / storage_capacity
    - age_pressure:       weighted fraction of near-expiry inventory
    - throughput_strain:  outgoing_flow / throughput_capacity
    """
    backlog_ratio = min(1.0, state.backlog / max(cfg.backlog_ref, 1.0))
    unmet_ratio = min(1.0, state.unmet_demand / max(state.demand, 1.0))
    storage_util = state.inventory_utilization()
    age_press = state.inventory.age_pressure()
    throughput_strain = min(1.0, state.outgoing_flow / max(state.throughput_capacity, 1.0))

    s = (
        cfg.w_backlog * backlog_ratio
        + cfg.w_unmet_demand * unmet_ratio
        + cfg.w_storage_util * storage_util
        + cfg.w_age_pressure * age_press
        + cfg.w_throughput_strain * throughput_strain
    )
    return float(min(1.0, max(0.0, s)))


def compute_elasticity(
    state: NodeState,
    alternate_route_count: int,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> float:
    """
    E ∈ [0,1]: local buffering capacity.

    Components:
    - spare_storage_frac: 1 - storage_util
    - spare_throughput_frac: 1 - throughput_strain
    - alt_route_factor: sigmoid-like from alternate_route_count
    - buffer_inv_frac: usable_inventory / storage_capacity (as a buffer measure)
    """
    storage_util = state.inventory_utilization()
    spare_storage = max(0.0, 1.0 - storage_util)

    throughput_strain = min(1.0, state.outgoing_flow / max(state.throughput_capacity, 1.0))
    spare_throughput = max(0.0, 1.0 - throughput_strain)

    # Alternate routes: 0 routes → 0, 1 → ~0.5, 2+ → approaching 1
    alt_factor = min(1.0, alternate_route_count / 3.0)

    buffer_frac = min(1.0, state.inventory.total_usable() / max(state.storage_capacity * 0.5, 1.0))

    e = (
        cfg.w_spare_storage * spare_storage
        + cfg.w_spare_throughput * spare_throughput
        + cfg.w_alt_routes * alt_factor
        + cfg.w_buffer_inv * buffer_frac
    )
    return float(min(1.0, max(0.0, e)))


def compute_leaked_stress(
    stress: float,
    elasticity: float,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> float:
    """
    L = max(0, S - absorbed_component(E, local_efficiency))

    Absorption capacity = absorption_base * E * local_efficiency
    L is the fraction of S that exceeds local absorption.
    """
    absorption_capacity = cfg.absorption_base * elasticity * cfg.local_efficiency
    l = max(0.0, stress - absorption_capacity)
    return float(min(1.0, l))


def compute_reaction(
    prev_reaction: float,
    leaked_stress: float,
    spoilage_ratio: float,
    unmet_demand_ratio: float,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> float:
    """
    R reflects operational consequences: stockouts, forced reroutes, quality loss.
    Decays geometrically when not sustained.
    """
    impulse = (
        cfg.r_leak_weight * leaked_stress
        + cfg.r_spoilage_weight * spoilage_ratio
        + cfg.r_unmet_weight * unmet_demand_ratio
        + cfg.r_persistence_weight * prev_reaction
    )
    # Decay existing reaction and add new impulse
    r = cfg.r_decay * prev_reaction + (1.0 - cfg.r_decay) * impulse
    return float(min(1.0, max(0.0, r)))


def classify_regime(
    stress: float,
    leaked_stress: float,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> str:
    """
    Classify node into one of four regimes for visualization.
    """
    if leaked_stress >= cfg.fragmentation_leaked_threshold:
        return "fragmented"
    elif stress >= cfg.isolation_stress_threshold:
        return "isolated"
    elif stress >= 0.5:
        return "accumulation"
    else:
        return "normal"
