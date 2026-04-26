"""
demand.py — Stable, externally-grounded demand layer for the potato network.

Design intent
-------------
Demand is fixed as a stable sink condition, not derived from production or
season.  This ensures that regime differences across experiments are caused
by structural constraint differences, not by demand variation.

Calibration
-----------
US per-capita potato consumption: ~54 kg/year (USDA ERS, 2022 data).
Figures here represent stylised regional weekly demand for fresh + processed
potatoes reaching the distribution network, expressed in tonnes/week.

The four retail regions represent:

  ret_west     — West Coast metro catchment (Los Angeles, San Francisco,
                  Seattle): pop ~22M, demand ~230 t/wk
  ret_mountain — Mountain/High Plains (Denver, Salt Lake, Boise):
                  pop ~10M, demand ~105 t/wk
  ret_midwest  — Midwest (Chicago, Minneapolis, Kansas City):
                  pop ~18M, demand ~190 t/wk
  ret_east     — Northeast/Atlantic (New York, Boston, Philadelphia):
                  pop ~28M, demand ~295 t/wk

Total retail demand: ~820 t/wk.

Processor nodes represent industrial/food-service demand:
  proc_west — Western processing plants (french fry, chip): ~420 t/wk
  proc_east — Eastern processing plants:                    ~315 t/wk

Total system demand: ~1 555 t/wk.
Total system production at peak: ~720 t/wk + 450 t/wk from storage drawdown.

The demand figures are deliberately set slightly below steady-state supply
throughput so that a healthy network clears without accumulation, but tight
enough that any corridor blockage creates measurable unmet demand within
2–3 steps.

Seasonality: demand is NOT seasonal.  Real potato demand has mild seasonality
(higher autumn/winter) but the variation is small (~10 %) relative to supply
variation (~45 %).  Holding demand constant isolates the supply-side structural
effects that are the object of study.

Interface
---------
STABLE_DEMAND: Dict[node_id → tonnes/week]  — use this to set NodeState.demand
               for all nodes that consume inventory.

apply_stable_demand(nodes) patches the demand field on every matching node
in-place.  Call this after build_potato_network() and before running the engine.
"""
from __future__ import annotations
from typing import Dict

# ── Retail: stable per-region weekly demand (tonnes) ─────────────────────────
RETAIL_DEMAND: Dict[str, float] = {
    "ret_west":     230.0,
    "ret_mountain": 105.0,
    "ret_midwest":  190.0,
    "ret_east":     295.0,
}

# ── Processor: stable industrial/food-service weekly demand (tonnes) ─────────
# Processor demand represents the pull from the processing side.
# Set at ~75 % of throughput_capacity so there is visible stress when
# throughput is constrained but the system is not inherently saturated.
PROCESSOR_DEMAND: Dict[str, float] = {
    "proc_west": 420.0,   # 75 % of 560 t/wk throughput_capacity
    "proc_east": 315.0,   # 75 % of 420 t/wk throughput_capacity
}

# ── Combined lookup ───────────────────────────────────────────────────────────
STABLE_DEMAND: Dict[str, float] = {**RETAIL_DEMAND, **PROCESSOR_DEMAND}

# ── Summary figures for README / logging ─────────────────────────────────────
TOTAL_RETAIL_DEMAND    = sum(RETAIL_DEMAND.values())     # 820 t/wk
TOTAL_PROCESSOR_DEMAND = sum(PROCESSOR_DEMAND.values())  # 735 t/wk
TOTAL_DEMAND           = TOTAL_RETAIL_DEMAND + TOTAL_PROCESSOR_DEMAND  # 1 555 t/wk


def apply_stable_demand(nodes: dict, scale: float = 1.0) -> None:
    """
    Patch NodeState.demand for every node listed in STABLE_DEMAND.

    Parameters
    ----------
    nodes : {node_id: NodeState}  — from build_potato_network()
    scale : float — uniform multiplier, default 1.0 (no scaling).
                    Use scale < 1.0 to test under-demand scenarios;
                    scale > 1.0 for demand-surge sensitivity tests.
                    Should NOT be varied between experiments — use 1.0
                    for all structural comparisons.
    """
    for node_id, base_demand in STABLE_DEMAND.items():
        if node_id in nodes:
            nodes[node_id].demand = base_demand * scale
