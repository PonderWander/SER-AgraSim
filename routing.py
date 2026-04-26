"""
routing.py — Modular routing / dispatch policies.

A routing policy decides, given a source node and its available outgoing edges,
how to allocate outgoing shipments across destinations.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple
from src.models import AgeBuckets, EdgeState, NodeState


class RoutingPolicy(ABC):
    """Base class for all routing policies."""

    @abstractmethod
    def allocate(
        self,
        source: NodeState,
        available_edges: List[EdgeState],
        destination_states: Dict[str, NodeState],
    ) -> Dict[str, float]:
        """
        Return a dict mapping edge_id → tonnes_to_ship for this step.
        Total shipment must not exceed min(source.outgoing_flow_budget, edge capacities).
        """
        ...

    def _sort_by_priority(
        self,
        source: NodeState,
        edges: List[EdgeState],
        destinations: Dict[str, NodeState],
    ) -> List[Tuple[EdgeState, float]]:
        """Return (edge, priority_score) sorted descending."""
        raise NotImplementedError


def _max_shippable(source: NodeState, edges: List[EdgeState]) -> float:
    """Usable inventory that can be dispatched."""
    return source.inventory.total_usable()


class FIFOPolicy(RoutingPolicy):
    """
    First-come-first-served: ship in arrival order.
    Allocate proportionally across active edges by capacity.
    """
    def allocate(self, source, available_edges, destination_states):
        active = [e for e in available_edges if e.is_active and e.effective_capacity() > 0]
        if not active:
            return {}
        total_cap = sum(e.effective_capacity() for e in active)
        total_available = _max_shippable(source, active)
        total_to_ship = min(total_available, total_cap)
        result = {}
        for e in active:
            frac = e.effective_capacity() / total_cap
            result[e.edge_id] = total_to_ship * frac
        return result


class OldestFirstPolicy(RoutingPolicy):
    """
    Prioritise shipping oldest (near-expiry, then mid-life) inventory first.
    Otherwise same proportional split across edges.
    """
    def allocate(self, source, available_edges, destination_states):
        # Same edge allocation as FIFO; the 'oldest first' refers to which
        # cohort is dispatched (handled in engine.py _dispatch_cohort).
        return FIFOPolicy().allocate(source, available_edges, destination_states)


class NearestExpiryFirstPolicy(RoutingPolicy):
    """
    Route near-expiry inventory preferentially to nearest (lowest transit_time) destinations.
    """
    def allocate(self, source, available_edges, destination_states):
        active = [e for e in available_edges if e.is_active and e.effective_capacity() > 0]
        if not active:
            return {}
        # Sort by transit time ascending
        active.sort(key=lambda e: e.transit_time)
        total_available = _max_shippable(source, active)
        total_to_ship = min(total_available, sum(e.effective_capacity() for e in active))
        result = {}
        remaining = total_to_ship
        for e in active:
            send = min(remaining, e.effective_capacity())
            result[e.edge_id] = send
            remaining -= send
            if remaining <= 0:
                break
        return result


class HighestDemandFirstPolicy(RoutingPolicy):
    """
    Route more inventory toward destinations with higher unmet demand.
    """
    def allocate(self, source, available_edges, destination_states):
        active = [e for e in available_edges if e.is_active and e.effective_capacity() > 0]
        if not active:
            return {}
        # Weight by destination unmet demand
        weights = []
        for e in active:
            dst = destination_states.get(e.target_id)
            w = max(0.01, dst.unmet_demand if dst else 0.01)
            weights.append(w)
        total_w = sum(weights)
        total_available = _max_shippable(source, active)
        total_to_ship = min(total_available, sum(e.effective_capacity() for e in active))
        result = {}
        for e, w in zip(active, weights):
            frac = w / total_w
            result[e.edge_id] = min(e.effective_capacity(), total_to_ship * frac)
        return result


class LowestCostFirstPolicy(RoutingPolicy):
    """
    Prefer cheapest edges.
    """
    def allocate(self, source, available_edges, destination_states):
        active = [e for e in available_edges if e.is_active and e.effective_capacity() > 0]
        if not active:
            return {}
        active.sort(key=lambda e: e.cost)
        total_available = _max_shippable(source, active)
        total_to_ship = min(total_available, sum(e.effective_capacity() for e in active))
        result = {}
        remaining = total_to_ship
        for e in active:
            send = min(remaining, e.effective_capacity())
            result[e.edge_id] = send
            remaining -= send
            if remaining <= 0:
                break
        return result


POLICIES: Dict[str, RoutingPolicy] = {
    "fifo": FIFOPolicy(),
    "oldest_first": OldestFirstPolicy(),
    "nearest_expiry_first": NearestExpiryFirstPolicy(),
    "highest_demand_first": HighestDemandFirstPolicy(),
    "lowest_cost_first": LowestCostFirstPolicy(),
}
