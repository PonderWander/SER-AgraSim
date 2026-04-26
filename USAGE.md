# Potato Supply-Chain Simulation — Usage Guide

## Overview

This is a time-stepped network simulation of a perishable supply chain,
instantiated as a US potato distribution network. Its primary purpose is
testing whether structural regime changes — **local retention**, **isolation**,
and **fragmentation** — arise from constraint interaction rather than from
demand variation or individual parameter choices.

One simulation step represents one week. All quantities are in tonnes.

---

## Quick start

```bash
# Install dependencies (Python 3.10+)
pip install networkx pandas matplotlib numpy

# Run all four experiments and generate figures
cd potato_supplychain
python run_experiments.py
```

Outputs land in `output/`. The `output/comparison/` subdirectory contains
the cross-experiment figures most useful for paper-quality analysis.

---

## Project structure

```
src/
  models.py           Node and edge data classes; AgeBuckets cohort model
  engine.py           Time-stepped simulation engine
  dynamics.py         S/E/L/R formulas (stress, elasticity, leaked stress, reaction)
  demand.py           Stable, externally-grounded demand constants
  synthetic_network.py  Network topology builder (potato-specific)
  routing.py          Pluggable dispatch policies
  visualization.py    Per-experiment figures

run_experiments.py    Runs Exp 1–4 and comparison plots
```

---

## Running a single experiment

```python
import sys; sys.path.insert(0, '.')
from src.synthetic_network import build_potato_network
from src.demand import apply_stable_demand
from src.engine import SupplyChainEngine

# Build network — demand is intentionally NOT set in the builder
nodes, edges = build_potato_network(season_week=40)

# Apply stable demand — always call this before running
apply_stable_demand(nodes)

# Create engine
engine = SupplyChainEngine(nodes, edges, seed=42)

# Schedule a disruption: sever edge e007 at step 10
engine.schedule_disruption(10, "e007", "disruption_factor", 0.0)

# Run 52 steps (one year)
engine.run(52)

# Access results
node_df = engine.node_dataframe()   # one row per node per step
edge_df = engine.edge_dataframe()   # one row per edge per step
```

---

## Network topology

```
Farms (4)        farm_id, farm_wa, farm_or, farm_co
  └─► Storage (4)    stor_id, stor_wa, stor_or, stor_co     ← primary accumulation nodes
        └─► Processors (2)  proc_west, proc_east
              └─► Warehouses (3)  wh_west, wh_central, wh_east
                    └─► Retail (4)  ret_west, ret_mountain, ret_midwest, ret_east
```

Key edge: **e007** = `stor_id → proc_west` (primary Idaho–West corridor, 300t/wk capacity).
Severing this edge is the primary disruption in Experiments 3 and 4.

---

## Demand layer

Demand is **fixed and identical across all experiments**. It is set by
`apply_stable_demand(nodes)` after network construction and must never
be varied between structural comparisons.

| Node | Demand (t/wk) | Basis |
|---|---|---|
| `ret_west` | 230 | ~22M West Coast population, USDA ERS ~54 kg/yr per capita |
| `ret_mountain` | 105 | ~10M Mountain/Plains population |
| `ret_midwest` | 190 | ~18M Midwest population |
| `ret_east` | 295 | ~28M Northeast/Atlantic population |
| `proc_west` | 420 | 75% of 560t/wk throughput capacity |
| `proc_east` | 315 | 75% of 420t/wk throughput capacity |

**Total retail demand**: 820 t/wk  
**Total system demand**: 1,555 t/wk

Demand is not seasonal. Real potato demand has ~10% seasonal variation
versus ~45% supply variation; holding demand constant isolates supply-side
structural effects.

---

## Scheduling disruptions

`engine.schedule_disruption(step, target_id, attribute, value)` patches any
attribute on any node or edge at the given step.

```python
# Sever a corridor
engine.schedule_disruption(10, "e007", "disruption_factor", 0.0)

# Cap storage capacity
engine.schedule_disruption(8, "stor_id", "storage_capacity", 315.0)

# Halve processor throughput
engine.schedule_disruption(10, "proc_west", "throughput_capacity", 280.0)

# Restore a corridor later
engine.schedule_disruption(20, "e007", "disruption_factor", 1.0)
```

Multiple disruptions can be scheduled at the same step. They are applied
in order at the start of that step.

---

## The four experiments

| Experiment | Constraints | Expected peak regime at `farm_id` |
|---|---|---|
| **Exp 1** Baseline | None | normal |
| **Exp 2** Single constraint | Storage cap on `stor_id` only | accumulation |
| **Exp 3** Interacting constraints | Storage cap + processor bottleneck + e007 severed | **isolated** |
| **Exp 4** Elasticity mitigation | Same as Exp 3 + extra storage + lateral routes | normal |

The key comparison is **Exp 2 vs Exp 3**: identical demand, same topology,
different constraint configuration → retention vs isolation.

---

## Interpreting the output columns

### Regime labels (node log, `regime` column)

Assigned by a 2D boundary in (S, L) space:

| Label | Condition | Meaning |
|---|---|---|
| `normal` | S < s_accum | Node clearing inventory without burden |
| `accumulation` | S ≥ s_accum, L < l_isolate | Retaining burden; elasticity still absorbing |
| `isolated` | S ≥ s_isolate AND L ≥ l_isolate | Saturated and leaking; constraint-driven |
| `fragmented` | L ≥ l_fragment | Stress propagating destructively |

Default thresholds: s_accum=0.25, s_isolate=0.42, l_isolate=0.18, l_fragment=0.35.

### Throughput stress vector (five components, all logged)

| Column | What it measures | What drives it to 1.0 |
|---|---|---|
| `tp_blocked_ratio` | (nominal capacity − dispatched) / nominal | Route disruption or destination full |
| `tp_queue_persistence` | backlog / throughput_capacity | Sustained unmet demand |
| `tp_utilisation_no_slack` | utilisation when ALL edges at capacity | No relief valve available |
| `tp_dest_acceptance_failure` | Open-path potential rejected by dest saturation | Downstream node full |
| `tp_clearance_credit` | Fraction of dwell stock successfully cleared | High clearance → reduces stress |

`tp_dest_acceptance_failure` is the direct signal for isolation via downstream
blockage. A node with `tp_dest_acceptance_failure` near 1.0 and `severed_steps=0`
is isolated by destination saturation on a physically-open path, not by route
loss.

### Connectivity columns

| Column | Definition |
|---|---|
| `can_reach_retail` | BFS on **physically-open** edges: is there any path to a retail node? |
| `feasible_routes_out` | Count of outgoing edges with **spare capacity** (below constraint threshold) |
| `severed_steps` | Steps where `can_reach_retail = False` (path physically broken) |
| `saturated_steps` | Steps where `feasible_routes_out = 0` (all routes at capacity) |

A node can have `saturated_steps > 0` with `severed_steps = 0`: all corridors
are busy but none are broken. This is **accumulation**, not isolation.

---

## Changing routing policy

```python
engine = SupplyChainEngine(nodes, edges, routing_policy="oldest_first")
```

Available policies: `fifo`, `oldest_first`, `nearest_expiry_first`,
`highest_demand_first`, `lowest_cost_first`.

---

## Modifying S/E/L/R dynamics

```python
from src.dynamics import DynamicsConfig
cfg = DynamicsConfig(
    w_saturation=0.30,        # increase weight of storage fill in S
    w_tp_acceptance=0.20,     # increase weight of dest acceptance failure
    l_fragment=0.40,          # raise fragmentation threshold
)
engine = SupplyChainEngine(nodes, edges, dynamics_config=cfg)
```

All weights and thresholds are in `DynamicsConfig`. The `src/dynamics_old.py`
file preserves the original formulation for reference.

---

## Adding lateral routes (elasticity experiment)

```python
nodes, edges = build_potato_network(
    season_week=40,
    extra_storage_capacity=450.0,  # added to every storage node
    add_lateral_routes=True,       # adds stor↔stor and wh↔wh edges
)
```

Lateral routes restore `feasible_routes_out` and `e_alt_routes_contrib`
after primary corridor disruption, delaying or preventing the isolation
transition.

---

## Plugging in real data

The network builder sets capacities, transit times, and topology.
To substitute real data:

1. **Node capacities**: edit `storage_capacity` and `throughput_capacity`
   in `build_potato_network()` using USDA NASS / ERS figures.
2. **Edge transit times and capacities**: edit the `E(...)` calls in
   the edges section using BTS FAF5 freight-flow data.
3. **Demand**: edit `RETAIL_DEMAND` and `PROCESSOR_DEMAND` in `src/demand.py`.
   Do not put demand in the network builder — keep it in `demand.py`.
4. **Production profile**: edit `_BASE_PROD` and `season` formula in
   `build_potato_network()` using USDA NASS acreage and yield data.

Real-data sources: USDA NASS (production), USDA ERS (consumption),
BTS FAF5 (freight flows), USDA AMS PACA (quality/transit constraints).
