"""
models.py — Node and edge data models for the perishable supply-chain simulation.

Key design decisions
--------------------
AgeBuckets.age_pressure()
  Nonlinear in near_expiry fraction.  A node fully blocked from dispatching
  accumulates near-expiry cohorts over several steps; the quadratic term
  amplifies this into a strong stress signal once near_expiry exceeds ~40 %
  of usable stock.

NodeState — dwell inventory discipline
  `inventory` is the stock present at the END of the step (after arrivals,
  consumption, dispatch, and overflow).
  `inventory_at_step_start` is snapshotted BEFORE arrivals each step by the
  engine.  Dispatch is capped to this snapshot so that arrivals from the
  current step cannot be reshipped in the same step — they must dwell at
  least one step.  This is essential for saturation to accumulate correctly.

NodeState — throughput stress vector
  Rather than a single blocked_outflow term, five distinct components capture
  how throughput interacts with stress:

  tp_blocked_ratio        — (attempted - dispatched) / attempted, where
                            attempted = min(dwell_inv, sum of edge capacities).
                            Measures how much the node tried to send but
                            couldn't due to edge constraints.
  tp_queue_persistence    — backlog / backlog_ref.  Backlog that persists
                            across steps indicates a structural imbalance,
                            not a transient fluctuation.
  tp_utilisation_no_slack — (outgoing / throughput_capacity) when edges are
                            at or above constraint threshold.  High utilisation
                            with no slack is different from high utilisation
                            with spare capacity.
  tp_downstream_rejection — fraction of attempted shipments that were
                            capped by destination storage saturation rather
                            than edge capacity.  Captures the case where
                            downstream nodes are full.
  tp_clearance_credit     — negative contribution: successful clearance
                            (dispatched > 0 and dwell stock reduced) reduces
                            stress.  A node that is actively moving inventory
                            is under less latent burden than one that is stuck.

  These are computed by the engine and stored on NodeState for logging.

EdgeState.is_feasible()
  The key predicate for effective-connectivity computation.  An edge is
  feasible when it is active, has usable capacity (disruption_factor > 0.05),
  and current utilisation is below active_constraint_threshold.  Infeasible
  edges are removed from the BFS reachability graph, potentially severing
  upstream nodes from retail destinations.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
from enum import Enum


class NodeType(Enum):
    FARM      = "farm"
    STORAGE   = "storage"
    PROCESSOR = "processor"
    WAREHOUSE = "warehouse"
    RETAIL    = "retail"


class EdgeType(Enum):
    FARM_TO_STORAGE        = "farm_to_storage"
    STORAGE_TO_PROCESSOR   = "storage_to_processor"
    PROCESSOR_TO_WAREHOUSE = "processor_to_warehouse"
    WAREHOUSE_TO_RETAIL    = "warehouse_to_retail"
    LATERAL_STORAGE        = "lateral_storage"
    LATERAL_WAREHOUSE      = "lateral_warehouse"


@dataclass
class AgeBuckets:
    """
    Four-cohort inventory representation.  Units: tonnes.

    Cohort shelf-life fractions (commercial potato):
      fresh       — weeks 1–3 in cold store (~first third)
      mid_life    — weeks 4–8   (~middle third)
      near_expiry — weeks 9–12  (~final third; still sellable)
      spoiled     — past shelf life; tracked for accounting only
    """
    fresh:       float = 0.0
    mid_life:    float = 0.0
    near_expiry: float = 0.0
    spoiled:     float = 0.0

    def total_usable(self) -> float:
        return self.fresh + self.mid_life + self.near_expiry

    def total(self) -> float:
        return self.total_usable() + self.spoiled

    def age_pressure(self) -> float:
        """
        Latent age burden ∈ [0, 1].

        Nonlinear in near_expiry_frac so that a storage node whose outflow
        is blocked escalates from mild pressure to strong pressure over
        several steps as cohorts age forward — autonomous regime escalation.
        """
        total = self.total_usable()
        if total <= 0.0:
            return 0.0
        near_frac = self.near_expiry / total
        mid_frac  = self.mid_life   / total
        return min(1.0, 0.10 * mid_frac + near_frac + 0.90 * near_frac ** 2)

    def near_expiry_frac(self) -> float:
        total = self.total_usable()
        return self.near_expiry / total if total > 0 else 0.0

    def clone(self) -> "AgeBuckets":
        return AgeBuckets(self.fresh, self.mid_life, self.near_expiry, self.spoiled)


@dataclass
class NodeState:
    node_id:   str
    node_type: NodeType

    # ── inventory ────────────────────────────────────────────────────────────
    inventory:               AgeBuckets = field(default_factory=AgeBuckets)
    # Snapshotted by engine BEFORE arrivals each step; dispatch is capped to this.
    inventory_at_step_start: AgeBuckets = field(default_factory=AgeBuckets)

    # ── flow (current step) ──────────────────────────────────────────────────
    incoming_flow:  float = 0.0
    outgoing_flow:  float = 0.0
    attempted_flow: float = 0.0   # what dispatch tried to send (before caps)

    # ── capacities ───────────────────────────────────────────────────────────
    storage_capacity:      float = 1000.0
    cold_storage_capacity: float = 0.0
    throughput_capacity:   float = 500.0

    # ── demand / backlog ─────────────────────────────────────────────────────
    demand:       float = 0.0
    unmet_demand: float = 0.0
    backlog:      float = 0.0
    prev_backlog: float = 0.0   # backlog from previous step (for queue_persistence)

    # ── spoilage ─────────────────────────────────────────────────────────────
    spoilage_this_step:  float = 0.0
    cumulative_spoilage: float = 0.0

    # ── production (farms only) ──────────────────────────────────────────────
    production: float = 0.0

    # ── S/E/L/R scalars ──────────────────────────────────────────────────────
    stress:        float = 0.0
    elasticity:    float = 0.0
    leaked_stress: float = 0.0
    reaction:      float = 0.0

    # ── S components: inventory burden ───────────────────────────────────────
    s_saturation_contrib:  float = 0.0   # storage fill level
    s_age_contrib:         float = 0.0   # near-expiry cohort pressure

    # ── S components: throughput stress vector ───────────────────────────────
    # These five terms replace the old single blocked_outflow term.
    tp_blocked_ratio:         float = 0.0  # (attempted - dispatched) / attempted
    tp_queue_persistence:     float = 0.0  # backlog / backlog_ref (persisting queue)
    tp_utilisation_no_slack:  float = 0.0  # high utilisation with no spare edge headroom
    tp_downstream_rejection:  float = 0.0  # DEPRECATED — kept for log compatibility
    # Replaces tp_downstream_rejection with a precise per-edge measure:
    # fraction of physically-open dispatch potential blocked by destination saturation
    tp_dest_acceptance_failure: float = 0.0
    tp_clearance_credit:        float = 0.0  # NEGATIVE: successful clearance reduces stress
    # Weighted sum of the above five — stored for decomposition plots
    s_throughput_contrib:     float = 0.0

    # ── S components: demand pressure ────────────────────────────────────────
    s_backlog_contrib: float = 0.0
    s_unmet_contrib:   float = 0.0

    # ── E components ─────────────────────────────────────────────────────────
    e_spare_storage_contrib:   float = 0.0
    e_clearance_rate_contrib:  float = 0.0   # renamed from pass_through; cleaner semantics
    e_alt_routes_contrib:      float = 0.0
    e_buffer_health_contrib:   float = 0.0

    # ── L breakdown ──────────────────────────────────────────────────────────
    l_absorption:      float = 0.0
    l_leakage_fraction:float = 0.0

    # ── graph connectivity ────────────────────────────────────────────────────
    active_routes_out:   int  = 0
    total_routes_out:    int  = 0   # stamped at build time
    feasible_routes_out: int  = 0
    is_isolated:         bool = False
    can_reach_retail:    bool = True

    # ── derived saturation / age signals ─────────────────────────────────────
    saturation_ratio:     float = 0.0
    near_expiry_fraction: float = 0.0
    dwell_inventory:      float = 0.0   # inventory_at_step_start total, logged

    def inventory_utilization(self) -> float:
        cap = self.storage_capacity
        return min(1.0, self.inventory.total_usable() / cap) if cap > 0 else 1.0

    def to_dict(self) -> dict:
        return {
            # identity
            "node_id":   self.node_id,
            "node_type": self.node_type.value,
            # inventory cohorts (end-of-step)
            "inv_fresh":        self.inventory.fresh,
            "inv_mid":          self.inventory.mid_life,
            "inv_near_expiry":  self.inventory.near_expiry,
            "inv_spoiled":      self.inventory.spoiled,
            "inv_total_usable": self.inventory.total_usable(),
            # dwell (start-of-step, what dispatch was capped to)
            "dwell_inventory":      self.dwell_inventory,
            # derived signals
            "saturation_ratio":     self.saturation_ratio,
            "near_expiry_fraction": self.near_expiry_fraction,
            # flows
            "incoming_flow":  self.incoming_flow,
            "outgoing_flow":  self.outgoing_flow,
            "attempted_flow": self.attempted_flow,
            # capacities
            "storage_capacity":      self.storage_capacity,
            "cold_storage_capacity": self.cold_storage_capacity,
            "throughput_capacity":   self.throughput_capacity,
            # demand / backlog
            "demand":       self.demand,
            "unmet_demand": self.unmet_demand,
            "backlog":      self.backlog,
            "prev_backlog": self.prev_backlog,
            # spoilage
            "spoilage_this_step":  self.spoilage_this_step,
            "cumulative_spoilage": self.cumulative_spoilage,
            # production
            "production": self.production,
            # S/E/L/R scalars
            "stress":        self.stress,
            "elasticity":    self.elasticity,
            "leaked_stress": self.leaked_stress,
            "reaction":      self.reaction,
            # S — inventory burden
            "s_saturation_contrib": self.s_saturation_contrib,
            "s_age_contrib":        self.s_age_contrib,
            # S — throughput stress vector (explicit components)
            "tp_blocked_ratio":        self.tp_blocked_ratio,
            "tp_queue_persistence":    self.tp_queue_persistence,
            "tp_utilisation_no_slack": self.tp_utilisation_no_slack,
            "tp_downstream_rejection": self.tp_downstream_rejection,  # kept for compat
            "tp_dest_acceptance_failure": self.tp_dest_acceptance_failure,
            "tp_clearance_credit":     self.tp_clearance_credit,
            "s_throughput_contrib":    self.s_throughput_contrib,
            # S — demand pressure
            "s_backlog_contrib": self.s_backlog_contrib,
            "s_unmet_contrib":   self.s_unmet_contrib,
            # E components
            "e_spare_storage_contrib":  self.e_spare_storage_contrib,
            "e_clearance_rate_contrib": self.e_clearance_rate_contrib,
            "e_alt_routes_contrib":     self.e_alt_routes_contrib,
            "e_buffer_health_contrib":  self.e_buffer_health_contrib,
            # L breakdown
            "l_absorption":       self.l_absorption,
            "l_leakage_fraction": self.l_leakage_fraction,
            # connectivity
            "active_routes_out":   self.active_routes_out,
            "feasible_routes_out": self.feasible_routes_out,
            "total_routes_out":    self.total_routes_out,
            "can_reach_retail":    self.can_reach_retail,
            "is_isolated":         self.is_isolated,
        }


@dataclass
class TransitCohort:
    age_buckets:     AgeBuckets = field(default_factory=AgeBuckets)
    steps_remaining: int = 1


@dataclass
class EdgeState:
    edge_id:   str
    source_id: str
    target_id: str
    edge_type: EdgeType

    capacity:    float = 200.0
    transit_time:int   = 1
    reliability: float = 1.0
    cost:        float = 1.0
    is_refrigerated: bool  = False
    active_constraint_threshold: float = 0.85

    current_load: float = 0.0
    in_transit:   List[TransitCohort] = field(default_factory=list)
    is_active:    bool  = True
    disruption_factor: float = 1.0

    def effective_capacity(self) -> float:
        return max(0.0, self.capacity * self.reliability * self.disruption_factor)

    def utilization(self) -> float:
        cap = self.effective_capacity()
        if cap <= 0:
            return 1.0
        return min(1.0, self.current_load / cap)

    def is_physically_open(self) -> bool:
        """
        True iff this edge is not physically severed.

        Used exclusively for BFS reachability: a fully-loaded edge is still
        physically open — material flows through it, so the path exists.
        Only disruption_factor near zero or is_active=False constitutes
        actual structural severance.
        """
        if not self.is_active:
            return False
        if self.effective_capacity() < self.capacity * 0.05:
            return False
        return True

    def has_headroom(self) -> bool:
        """
        True iff this edge has spare capacity this step.

        Used for e_alt_routes elasticity and tp_utilisation_no_slack:
        a saturated-but-open edge provides no routing flexibility even though
        it is not severed. This is the accumulation condition, not isolation.
        """
        return self.is_physically_open() and (
            self.utilization() < self.active_constraint_threshold
        )

    def to_dict(self) -> dict:
        return {
            "edge_id":    self.edge_id,
            "source_id":  self.source_id,
            "target_id":  self.target_id,
            "edge_type":  self.edge_type.value,
            "capacity":           self.capacity,
            "effective_capacity": self.effective_capacity(),
            "current_load":       self.current_load,
            "utilization":        self.utilization(),
            "transit_time":       self.transit_time,
            "reliability":        self.reliability,
            "cost":               self.cost,
            "is_refrigerated":    self.is_refrigerated,
            "is_active":          self.is_active,
            "disruption_factor":  self.disruption_factor,
            "is_physically_open": self.is_physically_open(),
            "has_headroom":       self.has_headroom(),
            "constraint_binding": self.utilization() >= self.active_constraint_threshold,
        }
