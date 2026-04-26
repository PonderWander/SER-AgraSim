"""
dynamics.py — S/E/L/R formulas with explicit throughput stress vector.

Throughput stress design
------------------------
The old approach collapsed all throughput effects into a single
"blocked_outflow" or "pass_through" ratio, which made it impossible to
distinguish why throughput was constrained.  The new approach uses five
explicitly-named components that map to distinct physical causes:

  tp_blocked_ratio
    (attempted_dispatch - actual_dispatch) / attempted_dispatch
    A node that tried to send 300t but could only send 114t because e007
    was severed has blocked_ratio = 0.62.  This is the primary signal for
    route disruption.

  tp_queue_persistence
    backlog / backlog_ref (where backlog_ref is a normalising constant).
    A backlog that grows step-over-step indicates the node is structurally
    unable to clear demand, not just transiently short.  Unlike unmet_demand
    (which is this step's shortfall), backlog persistence is the integral
    of unresolved demand over time.

  tp_utilisation_no_slack
    outgoing / throughput_capacity, but only scored when ALL outgoing
    edges are at or above their constraint threshold (no slack available).
    A node running at 95 % utilisation on a single unconstrained edge is
    fine; a node running at 80 % with every downstream corridor saturated
    has no relief valve.  This term captures the "no slack" condition.

  tp_dest_acceptance_failure
    For each physically-open outgoing edge: how much of the edge's potential
    throughput was blocked because the destination node had insufficient storage
    headroom?  Summed across all open edges and normalised by total open
    potential.  This directly measures downstream saturation as a cause of
    dispatch failure, independently of edge-capacity limits or route disruption.
    A node whose primary destination is full scores near 1.0 even if it has
    physical paths available — the path is open but the destination refuses the
    load.  This is the direct support for the isolated regime label in cases
    where topological severance alone does not explain the isolation.

  tp_clearance_credit
    NEGATIVE contribution: when a node successfully dispatches more than its
    incoming flow (drawing down dwell stock), it is actively relieving burden.
    This term reduces S to reflect that successful clearance is the opposite
    of accumulation.  Prevents a high-throughput healthy node from appearing
    stressed just because it is busy.

These five components are summed with individual weights to produce
s_throughput_contrib, which enters S alongside s_saturation_contrib and
s_age_contrib.

Elasticity redesign
-------------------
e_clearance_rate replaces e_pass_through.  It measures:
  dispatched / max(dwell_inventory, 1)
i.e. what fraction of the stock that was physically present at step start
the node successfully cleared.  This is not confounded by zero-inflow steps
(unlike outgoing/incoming) and correctly distinguishes:
  - A node with 500t dwell that sent 450t (clearance = 0.90 → high E)
  - A node with 500t dwell that sent 114t (clearance = 0.23 → low E)

L = S × max(0, 1 − E × η)  (unchanged multiplicative form)

classify_regime: 2D in (S, L) space.  Thresholds recalibrated against the
corrected dwell-inventory engine.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple
from src.models import NodeState


@dataclass
class DynamicsConfig:
    # ── Stress: inventory burden ──────────────────────────────────────────────
    w_saturation:   float = 0.22
    w_age_pressure: float = 0.18

    # ── Stress: throughput vector weights ─────────────────────────────────────
    w_tp_blocked:      float = 0.14   # blocked_ratio — primary disruption signal
    w_tp_queue:        float = 0.16   # queue_persistence — growing backlog
    w_tp_no_slack:     float = 0.10   # utilisation_no_slack
    w_tp_acceptance:   float = 0.14   # dest_acceptance_failure — raised from 0.06 to directly support isolation
    w_tp_clearance:    float = 0.10   # clearance_credit (subtracts from tp sum)

    # ── Stress: demand pressure ───────────────────────────────────────────────
    w_backlog:     float = 0.04   # residual backlog/backlog_ref signal
    w_unmet_demand:float = 0.02

    # ── Elasticity ────────────────────────────────────────────────────────────
    w_spare_storage:   float = 0.28
    w_clearance_rate:  float = 0.30
    w_alt_routes:      float = 0.26
    w_buffer_health:   float = 0.16

    # ── L = S × max(0, 1 − E × η) ────────────────────────────────────────────
    local_efficiency: float = 0.88

    # ── R dynamics ────────────────────────────────────────────────────────────
    r_leak_weight:     float = 0.42
    r_spoilage_weight: float = 0.28
    r_unmet_weight:    float = 0.20
    r_persistence:     float = 0.10
    r_decay:           float = 0.78

    # ── Regime thresholds in (S, L) space ─────────────────────────────────────
    # Calibrated against observed signal range:
    #   - Baseline healthy nodes: S ~ 0.10–0.18, L ~ 0.04–0.10
    #   - Single-constraint accumulation: S ~ 0.28–0.42, L ~ 0.15–0.25
    #   - Multi-constraint isolation: S ~ 0.42–0.65, L ~ 0.28–0.50
    #   - Fragmented (persistent queue + no routes): S ~ 0.55+, L ~ 0.40+
    s_accum:    float = 0.25
    s_isolate:  float = 0.42
    l_isolate:  float = 0.18
    l_fragment: float = 0.35

    # ── Normalisation ─────────────────────────────────────────────────────────
    backlog_ref:  float = 150.0   # fallback for w_backlog term only
    dwell_ref:    float = 150.0   # minimum denominator for clearance_rate


DEFAULT_DYNAMICS = DynamicsConfig()


def compute_stress(
    state: NodeState,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> Tuple[float, Dict[str, float]]:
    """
    Returns (S, components_dict).

    The throughput stress vector is pre-computed by the engine and stored on
    NodeState (tp_blocked_ratio, tp_queue_persistence, etc.).  This function
    reads those pre-computed values and applies the configured weights.

    Clearance credit enters as a NEGATIVE contribution: successfully clearing
    inventory reduces stress rather than being invisible.  The credit is
    capped at the sum of the other throughput terms so it cannot push S below
    the inventory-burden baseline.
    """
    sat_c  = cfg.w_saturation   * state.saturation_ratio
    age_c  = cfg.w_age_pressure * state.inventory.age_pressure()

    # Throughput stress vector (pre-computed by engine, read here)
    tp_pos = (
        cfg.w_tp_blocked    * state.tp_blocked_ratio
        + cfg.w_tp_queue    * state.tp_queue_persistence
        + cfg.w_tp_no_slack * state.tp_utilisation_no_slack
        + cfg.w_tp_acceptance * state.tp_dest_acceptance_failure
    )
    tp_neg = cfg.w_tp_clearance * state.tp_clearance_credit   # clearance reduces stress
    tp_net = max(0.0, tp_pos - tp_neg)

    bl_c    = cfg.w_backlog      * min(1.0, state.backlog / max(cfg.backlog_ref, 1.0))
    unmet_c = cfg.w_unmet_demand * min(1.0, state.unmet_demand / max(state.demand, 1.0))

    s = min(1.0, max(0.0, sat_c + age_c + tp_net + bl_c + unmet_c))

    return s, {
        "s_saturation_contrib":  sat_c,
        "s_age_contrib":         age_c,
        "s_throughput_contrib":  tp_net,
        "s_backlog_contrib":     bl_c,
        "s_unmet_contrib":       unmet_c,
    }


def compute_elasticity(
    state: NodeState,
    alternate_route_count: int,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> Tuple[float, Dict[str, float]]:
    """
    Returns (E, components_dict).

    e_clearance_rate: dispatched / max(dwell_inventory, dwell_ref).
      - dwell_inventory is the stock present at step start (pre-arrival snapshot).
      - Using dwell_inventory avoids the circularity of outgoing/incoming when
        inflow is zero: a node with 400t dwell that clears 360t has
        clearance_rate = 0.90 regardless of whether 400t arrived this step
        or has been sitting for several steps.
      - Normalising against dwell_ref (200t) handles the case where a node
        holds very little stock: a node with 10t dwell that dispatches 9t
        gets credit for high clearance even though the absolute quantity is small.
    """
    ss_c = cfg.w_spare_storage * max(0.0, 1.0 - state.saturation_ratio)

    dwell = max(state.dwell_inventory, cfg.dwell_ref)
    cr    = min(1.0, state.outgoing_flow / dwell)
    cr_c  = cfg.w_clearance_rate * cr

    if   alternate_route_count == 0: alt_val = 0.00
    elif alternate_route_count == 1: alt_val = 0.40
    elif alternate_route_count == 2: alt_val = 0.68
    else:                             alt_val = 0.88
    ar_c = cfg.w_alt_routes * alt_val

    usable = state.inventory.total_usable()
    bh = (state.inventory.fresh + state.inventory.mid_life) / usable if usable > 0 else 0.0
    bh_c = cfg.w_buffer_health * bh

    e = min(1.0, max(0.0, ss_c + cr_c + ar_c + bh_c))
    return e, {
        "e_spare_storage_contrib":  ss_c,
        "e_clearance_rate_contrib": cr_c,
        "e_alt_routes_contrib":     ar_c,
        "e_buffer_health_contrib":  bh_c,
    }


def compute_leaked_stress(
    stress: float,
    elasticity: float,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> Tuple[float, Dict[str, float]]:
    """L = S × max(0, 1 − E × η)."""
    absorption = elasticity * cfg.local_efficiency
    leak_frac  = max(0.0, 1.0 - absorption)
    return min(1.0, stress * leak_frac), {
        "l_absorption":       absorption,
        "l_leakage_fraction": leak_frac,
    }


def compute_reaction(
    prev_reaction: float,
    leaked_stress: float,
    spoilage_ratio: float,
    unmet_demand_ratio: float,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> float:
    impulse = (
        cfg.r_leak_weight      * leaked_stress
        + cfg.r_spoilage_weight * spoilage_ratio
        + cfg.r_unmet_weight    * unmet_demand_ratio
        + cfg.r_persistence     * prev_reaction
    )
    return min(1.0, max(0.0, cfg.r_decay * prev_reaction + (1.0 - cfg.r_decay) * impulse))


def classify_regime(
    stress: float,
    leaked_stress: float,
    cfg: DynamicsConfig = DEFAULT_DYNAMICS,
) -> str:
    """
    Two-dimensional boundary in (S, L) space.

    fragmented:   L ≥ l_fragment  — leaking destructively regardless of S
    isolated:     S ≥ s_isolate AND L ≥ l_isolate  — saturated AND leaking
    accumulation: S ≥ s_accum AND L < l_isolate     — retaining, not yet leaking
    normal:       otherwise
    """
    if leaked_stress >= cfg.l_fragment:
        return "fragmented"
    if stress >= cfg.s_isolate and leaked_stress >= cfg.l_isolate:
        return "isolated"
    if stress >= cfg.s_accum:
        return "accumulation"
    return "normal"
