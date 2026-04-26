"""
engine.py — Time-stepped supply-chain simulation engine.

Step execution order
--------------------
  0.  Snapshot pre-arrival inventory (dwell_inventory) — CRITICAL
  1.  Apply scheduled disruptions
  2.  Age in-node inventory cohorts (cold slows rates by 65 %)
  3.  Age in-transit cohorts on edges
  4.  Deliver arrivals whose transit_time has elapsed
  5.  Farm production (seasonal + 5 % noise)
  6.  Demand consumption — FEFO (oldest-first), updates backlog
  7.  Dispatch from DWELL stock only (capped to inventory_at_step_start)
      — arrivals from this step cannot be reshipped until next step
  8.  Compute throughput stress vector components BEFORE overflow
  9.  Enforce storage capacity — overflow spoils oldest cohort first
  10. Refresh saturation_ratio, near_expiry_fraction, dwell_inventory
  11. Build live feasibility subgraph; compute BFS reachability to retail
  12. Compute alt-route counts (feasible edges only)
  13. Compute S, E, L, R with full component breakdowns
  14. Update is_isolated, regime
  15. Log all state

Dwell discipline (step 0 snapshot)
-----------------------------------
`inventory_at_step_start` is copied from `inventory` BEFORE arrivals.
Dispatch in step 7 is capped to `inventory_at_step_start.total_usable()`.
This means:
  - Inventory that arrives this step dwells at least one full step before
    it can be dispatched.
  - A storage node that receives 280t and has 450t of pre-existing stock
    dispatches up to 450t (capacity permitting), not 730t.
  - This is what allows saturation to accumulate when outflow is blocked.

Throughput stress vector computation (step 8)
----------------------------------------------
Computed AFTER dispatch, BEFORE overflow, so we know exactly what was
attempted and what was blocked:

  tp_blocked_ratio:
    attempted = min(dwell_stock, total_edge_capacity_available)
    blocked   = attempted - dispatched
    ratio     = blocked / max(attempted, 1)

  tp_queue_persistence:
    backlog / backlog_ref — scaled persistent demand deficit

  tp_utilisation_no_slack:
    If any feasible outgoing edges remain, utilisation_no_slack = 0
    (there is slack).  If ALL active edges are at or above their
    constraint threshold, utilisation_no_slack = outgoing / throughput_capacity.

  tp_downstream_rejection:
    For each edge, compare attempted_to_edge vs actual_sent_to_edge.
    If the shortfall is because the destination node has < 10 % storage
    headroom, that portion counts as downstream_rejection.

  tp_clearance_credit:
    If dispatched > 0 and dispatched > 0.5 × dwell_stock (node is actively
    clearing), credit = dispatched / max(dwell_stock, 1).
    This is subtracted from the throughput stress sum.

Age-bucket transition rates
---------------------------
  Non-cold:  fresh→mid 18 %/step, mid→near 22 %/step, near→spoil 28 %/step
  Cold (×0.35 all): effectively 6.3 / 7.7 / 9.8 %/step
  These rates place a unit in near_expiry after ~8 steps (warm) or ~22 (cold).
"""
from __future__ import annotations
import logging
import random
from typing import Dict, List, Set, Tuple

import networkx as nx
import pandas as pd

from src.models import (
    AgeBuckets, EdgeState, EdgeType, NodeState, NodeType, TransitCohort,
)
from src.dynamics import (
    DEFAULT_DYNAMICS, DynamicsConfig,
    classify_regime,
    compute_elasticity, compute_leaked_stress, compute_reaction, compute_stress,
)
from src.routing import RoutingPolicy, POLICIES

logger = logging.getLogger(__name__)

_R_FRESH_TO_MID  = 0.18
_R_MID_TO_NEAR   = 0.22
_R_NEAR_TO_SPOIL = 0.28
_COLD_FACTOR     = 0.35


def _age_buckets(b: AgeBuckets, cold: bool) -> Tuple[AgeBuckets, float]:
    k = _COLD_FACTOR if cold else 1.0
    f_out = b.fresh      * _R_FRESH_TO_MID  * k
    m_out = b.mid_life   * _R_MID_TO_NEAR   * k
    n_out = b.near_expiry* _R_NEAR_TO_SPOIL * k
    return (
        AgeBuckets(
            fresh       = max(0.0, b.fresh      - f_out),
            mid_life    = max(0.0, b.mid_life   + f_out - m_out),
            near_expiry = max(0.0, b.near_expiry + m_out - n_out),
        ),
        max(0.0, n_out),
    )


def _pull(inv: AgeBuckets, qty: float, fefo: bool = True) -> Tuple[AgeBuckets, AgeBuckets]:
    """Pull qty tonnes; fefo=True → near_expiry first. Returns (pulled, remaining)."""
    pulled    = AgeBuckets()
    remaining = inv.clone()
    order = ["near_expiry","mid_life","fresh"] if fefo else ["fresh","mid_life","near_expiry"]
    for b in order:
        avail = getattr(remaining, b)
        take  = min(avail, qty)
        setattr(pulled,    b, getattr(pulled,    b) + take)
        setattr(remaining, b, avail - take)
        qty -= take
        if qty <= 0.0:
            break
    return pulled, remaining


class SupplyChainEngine:

    def __init__(
        self,
        nodes: Dict[str, NodeState],
        edges: Dict[str, EdgeState],
        dynamics_config: DynamicsConfig = DEFAULT_DYNAMICS,
        routing_policy: str = "fifo",
        seed: int = 42,
    ):
        self.nodes       = nodes
        self.edges       = edges
        self.cfg         = dynamics_config
        self.policy_name = routing_policy if isinstance(routing_policy, str) else "custom"
        self.routing     = (
            routing_policy if isinstance(routing_policy, RoutingPolicy)
            else POLICIES[routing_policy]
        )
        self.rng  = random.Random(seed)
        self.step = 0

        self._build_adj()
        for nid, n in self.nodes.items():
            n.total_routes_out = len(self._out[nid])

        self.node_log: List[dict] = []
        self.edge_log: List[dict] = []
        self.disruptions: Dict[int, List[Tuple[str, str, object]]] = {}
        self._retail_ids: Set[str] = {
            nid for nid, n in nodes.items() if n.node_type == NodeType.RETAIL
        }

    # ── adjacency ─────────────────────────────────────────────────────────────

    def _build_adj(self):
        self._out: Dict[str, List[str]] = {nid: [] for nid in self.nodes}
        self._in:  Dict[str, List[str]] = {nid: [] for nid in self.nodes}
        for eid, e in self.edges.items():
            self._out[e.source_id].append(eid)
            self._in[e.target_id].append(eid)

    # ── disruption API ────────────────────────────────────────────────────────

    def schedule_disruption(self, step: int, target_id: str, attr: str, value):
        self.disruptions.setdefault(step, []).append((target_id, attr, value))

    def _apply_disruptions(self):
        for tid, attr, val in self.disruptions.get(self.step, []):
            if tid in self.nodes:
                setattr(self.nodes[tid], attr, val)
                logger.info("step %d | node %s.%s ← %s", self.step, tid, attr, val)
            elif tid in self.edges:
                setattr(self.edges[tid], attr, val)
                logger.info("step %d | edge %s.%s ← %s", self.step, tid, attr, val)
            else:
                logger.warning("disruption target '%s' not found", tid)

    # ── main loop ─────────────────────────────────────────────────────────────

    def run(self, n_steps: int):
        for _ in range(n_steps):
            self._step()
        logger.info("done: %d steps | %d node rows | %d edge rows",
                    self.step, len(self.node_log), len(self.edge_log))

    def _step(self):
        # 0. Snapshot dwell inventory BEFORE any arrivals or disruptions
        self._snapshot_dwell()

        # 1. Disruptions
        self._apply_disruptions()

        # 2–3. Age
        self._age_nodes()
        self._age_transit()

        # 4. Deliver arrivals (add to inventory, set incoming_flow)
        arrivals = self._collect_arrivals()
        self._receive(arrivals)

        # 5. Farm production
        self._produce()

        # 6. Demand consumption (FEFO, updates backlog / unmet_demand)
        self._consume()

        # 7. Dispatch from DWELL stock only
        dispatch_report = self._dispatch()

        # 8. Compute throughput stress vector
        self._compute_tp_vector(dispatch_report)

        # 9. Overflow → spoilage
        self._overflow()

        # 10. Refresh derived saturation fields
        self._refresh_sat()

        # 11–12. Reachability + alt-route counts
        reachable   = self._compute_reachability()
        alt_routes  = self._compute_alt_routes(reachable)

        # 13. S/E/L/R
        self._update_ser(reachable, alt_routes)

        # 14. Log
        self._log(reachable)
        self.step += 1

    # ── substeps ─────────────────────────────────────────────────────────────

    def _snapshot_dwell(self):
        """Copy current inventory to inventory_at_step_start (dwell snapshot)."""
        for n in self.nodes.values():
            n.inventory_at_step_start = n.inventory.clone()
            n.prev_backlog            = n.backlog

    def _age_nodes(self):
        for n in self.nodes.values():
            cold = n.cold_storage_capacity > 0
            n.inventory, spoiled = _age_buckets(n.inventory, cold)
            n.spoilage_this_step  = spoiled
            n.cumulative_spoilage += spoiled

    def _age_transit(self):
        for e in self.edges.values():
            for c in e.in_transit:
                c.age_buckets, _ = _age_buckets(c.age_buckets, e.is_refrigerated)
                c.steps_remaining -= 1

    def _collect_arrivals(self) -> Dict[str, AgeBuckets]:
        arrivals: Dict[str, AgeBuckets] = {}
        for e in self.edges.values():
            live = []
            for c in e.in_transit:
                if c.steps_remaining <= 0:
                    a = arrivals.setdefault(e.target_id, AgeBuckets())
                    a.fresh       += c.age_buckets.fresh
                    a.mid_life    += c.age_buckets.mid_life
                    a.near_expiry += c.age_buckets.near_expiry
                else:
                    live.append(c)
            e.in_transit = live
        return arrivals

    def _receive(self, arrivals: Dict[str, AgeBuckets]):
        for nid, n in self.nodes.items():
            if nid in arrivals:
                a = arrivals[nid]
                n.inventory.fresh       += a.fresh
                n.inventory.mid_life    += a.mid_life
                n.inventory.near_expiry += a.near_expiry
                n.incoming_flow = a.fresh + a.mid_life + a.near_expiry
            else:
                n.incoming_flow = 0.0

    def _produce(self):
        for n in self.nodes.values():
            if n.node_type == NodeType.FARM and n.production > 0:
                n.inventory.fresh += n.production * (1.0 + self.rng.uniform(-0.05, 0.05))

    def _consume(self):
        for n in self.nodes.values():
            if n.demand <= 0:
                continue
            needed    = n.demand + n.backlog
            available = n.inventory.total_usable()
            fulfilled = min(available, needed)
            if fulfilled > 0:
                _, n.inventory = _pull(n.inventory, fulfilled, fefo=True)
            n.unmet_demand = max(0.0, n.demand - min(available, n.demand))
            n.backlog      = max(0.0, needed - available)

    def _dispatch(self) -> Dict[str, Dict[str, float]]:
        """
        Dispatch from DWELL stock only (inventory_at_step_start).

        Returns dispatch_report: {node_id: {edge_id: qty_sent}}.
        Also records attempted_flow on each node.
        """
        fefo   = self.policy_name in ("oldest_first", "nearest_expiry_first")
        report: Dict[str, Dict[str, float]] = {}

        for nid, n in self.nodes.items():
            active = [self.edges[eid] for eid in self._out[nid] if self.edges[eid].is_active]
            if not active:
                n.outgoing_flow     = 0.0
                n.attempted_flow    = 0.0
                n.active_routes_out = 0
                for eid in self._out[nid]:
                    self.edges[eid].current_load = 0.0
                report[nid] = {}
                continue

            # Dispatch budget = dwell stock (pre-arrival snapshot)
            dwell_budget = n.inventory_at_step_start.total_usable()

            # Use a temporary copy of dwell inventory for the dispatch pull
            dispatch_inv = n.inventory_at_step_start.clone()

            dest_states = {e.target_id: self.nodes[e.target_id] for e in active}
            allocation  = self.routing.allocate(n, active, dest_states)

            sent      = 0.0
            attempted = 0.0
            node_report: Dict[str, float] = {}

            for e in active:
                edge_cap = e.effective_capacity()
                alloc    = allocation.get(e.edge_id, 0.0)
                want     = min(alloc, edge_cap, dispatch_inv.total_usable())
                attempted += min(alloc, edge_cap)   # what we intended to send

                if want <= 0.0:
                    e.current_load = 0.0
                    node_report[e.edge_id] = 0.0
                    continue

                # Check destination headroom
                dest = self.nodes[e.target_id]
                dest_headroom = max(0.0, dest.storage_capacity - dest.inventory.total_usable())
                actual = min(want, dest_headroom) if dest_headroom < want else want

                if actual <= 0.0:
                    e.current_load = 0.0
                    node_report[e.edge_id] = 0.0
                    continue

                batch, dispatch_inv = _pull(dispatch_inv, actual, fefo=fefo)
                # Also pull from the live inventory (they are the same dwell stock)
                _, n.inventory = _pull(n.inventory, actual, fefo=fefo)

                e.in_transit.append(TransitCohort(age_buckets=batch, steps_remaining=e.transit_time))
                e.current_load    = actual
                sent             += actual
                node_report[e.edge_id] = actual

            n.outgoing_flow     = sent
            n.attempted_flow    = min(attempted, dwell_budget)
            n.active_routes_out = sum(1 for e in active if e.current_load > 0)
            report[nid] = node_report

        return report

    def _compute_tp_vector(self, dispatch_report: Dict[str, Dict[str, float]]):
        """
        Compute and store throughput stress components on each node.
        Called AFTER dispatch, BEFORE overflow.
        """
        for nid, n in self.nodes.items():
            dwell = n.inventory_at_step_start.total_usable()
            sent  = n.outgoing_flow
            att   = n.attempted_flow

            # ── blocked_ratio ─────────────────────────────────────────────
            # Compares what the node could send under healthy conditions
            # (dwell vs nominal edge capacity, ignoring disruption/saturation)
            # against what was actually dispatched.  This captures blockage
            # caused by both route disruption (e007 severed) and downstream
            # saturation (e011 at 100 % utilisation).
            nominal_cap = sum(
                self.edges[eid].capacity
                for eid in self._out[nid]
                if self.edges[eid].is_active
            )
            healthy_sendable = min(dwell, nominal_cap)
            if healthy_sendable > 1.0:
                n.tp_blocked_ratio = min(1.0, max(0.0, (healthy_sendable - sent) / healthy_sendable))
            else:
                n.tp_blocked_ratio = 0.0

            # ── queue_persistence ─────────────────────────────────────────
            # Use node's own throughput_capacity as reference so that a
            # processor with 420t demand and 900t backlog reads 900/420=2→1.0
            # rather than 900/100=capped at 1.0 but never meaningful.
            # For storage nodes with no demand, fall back to storage_capacity.
            queue_ref = max(n.throughput_capacity, n.demand, self.cfg.backlog_ref, 1.0)
            n.tp_queue_persistence = min(1.0, n.backlog / queue_ref)

            # ── utilisation_no_slack ─────────────────────────────────────
            active_edges = [self.edges[eid] for eid in self._out[nid] if self.edges[eid].is_active]
            # has_headroom() = is_physically_open() AND utilization < threshold
            # If any active edge has headroom, the node has a relief valve.
            has_slack = any(e.has_headroom() for e in active_edges)
            if not has_slack and n.throughput_capacity > 0:
                n.tp_utilisation_no_slack = min(1.0, sent / n.throughput_capacity)
            else:
                n.tp_utilisation_no_slack = 0.0

            # ── dest_acceptance_failure ────────────────────────────────────
            # Precise per-edge measure: fraction of this node's physically-open
            # dispatch potential that was rejected because the destination's
            # storage was insufficient — not because the edge lacked capacity.
            #
            # For each physically-open outgoing edge:
            #   edge_potential  = min(eff_capacity, dwell)   max edge could take
            #   dest_headroom   = dest.storage_cap - dest.inv_total
            #   failure_qty     = max(0, edge_potential - dest_headroom)
            #                     (what the edge could carry but dest can't absorb)
            #
            # Normalised by total open potential so a fully-blocked destination
            # on the primary route reads near 1.0 even if a secondary route exists.
            # This is independent of tp_blocked_ratio: tp_blocked captures ALL
            # blockage (disruption + saturation + rejection); this metric isolates
            # only the destination-saturation component on physically-open paths.
            total_open_potential  = 0.0
            total_acceptance_fail = 0.0
            for eid in self._out[nid]:
                e = self.edges[eid]
                if not e.is_physically_open():
                    continue
                edge_potential = min(e.effective_capacity(), dwell)
                total_open_potential += edge_potential
                dest = self.nodes[e.target_id]
                dest_headroom = max(0.0, dest.storage_capacity - dest.inventory.total_usable())
                # How much of edge_potential cannot be absorbed by the destination?
                fail = max(0.0, edge_potential - dest_headroom)
                total_acceptance_fail += fail

            if total_open_potential > 1.0:
                n.tp_dest_acceptance_failure = min(
                    1.0, total_acceptance_fail / total_open_potential
                )
            else:
                n.tp_dest_acceptance_failure = 0.0
            # Keep deprecated field zeroed for log compatibility
            n.tp_downstream_rejection = 0.0

            # ── clearance_credit ──────────────────────────────────────────
            # Credit for actively drawing down dwell stock
            if dwell > 1.0 and sent > dwell * 0.3:
                n.tp_clearance_credit = min(1.0, sent / dwell)
            else:
                n.tp_clearance_credit = 0.0

    def _overflow(self):
        for n in self.nodes.values():
            overflow = n.inventory.total_usable() - n.storage_capacity
            if overflow > 0:
                _, n.inventory        = _pull(n.inventory, overflow, fefo=True)
                n.spoilage_this_step  += overflow
                n.cumulative_spoilage += overflow

    def _refresh_sat(self):
        for n in self.nodes.values():
            n.saturation_ratio     = n.inventory_utilization()
            usable = n.inventory.total_usable()
            n.near_expiry_fraction = (n.inventory.near_expiry / usable) if usable > 0 else 0.0
            n.dwell_inventory      = n.inventory_at_step_start.total_usable()

    # ── effective connectivity ────────────────────────────────────────────────

    def _compute_reachability(self) -> Dict[str, bool]:
        """
        BFS on the physically-open subgraph to test whether a retail node
        is reachable from each source node.

        Uses is_physically_open() — not has_headroom() — so that a fully-loaded
        but intact corridor still counts as a path.  A node whose only outgoing
        edges are disrupted to near-zero is structurally severed; a node whose
        edges are merely saturated is not.  Those two conditions produce different
        structural states and must be distinguished here.
        """
        open_adj: Dict[str, Set[str]] = {nid: set() for nid in self.nodes}
        for e in self.edges.values():
            if e.is_physically_open():
                open_adj[e.source_id].add(e.target_id)

        reachable: Dict[str, bool] = {}
        for start in self.nodes:
            if start in self._retail_ids:
                reachable[start] = True
                continue
            visited: Set[str] = set()
            queue = [start]
            found = False
            while queue and not found:
                cur = queue.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                if cur in self._retail_ids:
                    found = True
                    break
                for nxt in open_adj.get(cur, set()):
                    if nxt not in visited:
                        queue.append(nxt)
            reachable[start] = found
        return reachable

    def _compute_alt_routes(self, reachable: Dict[str, bool]) -> Dict[str, int]:
        """
        Count outgoing routes with spare capacity (has_headroom) per node.

        has_headroom() requires the edge to be both physically open AND below
        its utilisation threshold.  A saturated-but-open edge reduces routing
        flexibility (it cannot absorb additional volume) even though it is not
        severed.  This is the correct input for e_alt_routes elasticity: what
        matters for elasticity is whether there is a relief valve available,
        not merely whether a path exists in the topology.

        feasible_routes_out is set to the headroom count for logging.
        """
        alt: Dict[str, int] = {}
        for nid, n in self.nodes.items():
            with_headroom = [
                eid for eid in self._out[nid]
                if self.edges[eid].has_headroom()
            ]
            n.feasible_routes_out = len(with_headroom)
            alt[nid] = max(0, len(with_headroom) - 1)
        return alt

    def _global_open_fraction(self) -> float:
        """Fraction of edges that are physically open (not severed by disruption)."""
        n = len(self.edges)
        return sum(1 for e in self.edges.values() if e.is_physically_open()) / n if n else 1.0

    def _global_headroom_fraction(self) -> float:
        """Fraction of edges that are open AND have spare capacity below constraint threshold."""
        n = len(self.edges)
        return sum(1 for e in self.edges.values() if e.has_headroom()) / n if n else 1.0

    # ── S/E/L/R ───────────────────────────────────────────────────────────────

    def _update_ser(self, reachable: Dict[str, bool], alt_routes: Dict[str, int]):
        for nid, n in self.nodes.items():
            spoil_ratio = min(1.0, n.spoilage_this_step / max(n.storage_capacity * 0.1, 1.0))
            unmet_ratio = min(1.0, n.unmet_demand / max(n.demand, 1.0))

            s, sc = compute_stress(n, self.cfg)
            e, ec = compute_elasticity(n, alt_routes.get(nid, 0), self.cfg)
            l, lc = compute_leaked_stress(s, e, self.cfg)
            r     = compute_reaction(n.reaction, l, spoil_ratio, unmet_ratio, self.cfg)

            n.stress        = s
            n.elasticity    = e
            n.leaked_stress = l
            n.reaction      = r

            # S components
            n.s_saturation_contrib = sc["s_saturation_contrib"]
            n.s_age_contrib        = sc["s_age_contrib"]
            n.s_throughput_contrib = sc["s_throughput_contrib"]
            n.s_backlog_contrib    = sc["s_backlog_contrib"]
            n.s_unmet_contrib      = sc["s_unmet_contrib"]

            # E components
            n.e_spare_storage_contrib  = ec["e_spare_storage_contrib"]
            n.e_clearance_rate_contrib = ec["e_clearance_rate_contrib"]
            n.e_alt_routes_contrib     = ec["e_alt_routes_contrib"]
            n.e_buffer_health_contrib  = ec["e_buffer_health_contrib"]

            # L breakdown
            n.l_absorption       = lc["l_absorption"]
            n.l_leakage_fraction = lc["l_leakage_fraction"]

            n.can_reach_retail = reachable.get(nid, True)
            regime = classify_regime(s, l, self.cfg)
            n.is_isolated = regime in ("isolated", "fragmented") or not n.can_reach_retail

    # ── logging ───────────────────────────────────────────────────────────────

    def _log(self, reachable: Dict[str, bool]):
        global_open     = self._global_open_fraction()
        global_headroom = self._global_headroom_fraction()
        for nid, n in self.nodes.items():
            d = n.to_dict()
            d["step"]                    = self.step
            d["regime"]                  = classify_regime(n.stress, n.leaked_stress, self.cfg)
            d["global_open_fraction"]    = global_open      # physically intact paths / total
            d["global_headroom_fraction"]= global_headroom  # spare-capacity paths / total
            self.node_log.append(d)
        for e in self.edges.values():
            d = e.to_dict()
            d["step"] = self.step
            self.edge_log.append(d)

    def node_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.node_log)

    def edge_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.edge_log)
