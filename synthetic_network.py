"""
synthetic_network.py — Synthetic potato supply-chain network.

Calibration intent
------------------
The network is sized so that under *baseline* conditions storage nodes
operate at ~55–65 % utilisation — tight enough that a single binding
constraint will saturate one hub within ~8 steps, but loose enough that
the baseline run is stable.

This sizing is deliberately different from an "over-provisioned" logistics
model: the whole point is to sit in a regime where structural transitions
are reachable.

Topology (step ≈ 1 week)
------------------------
  Farms (4):      farm_id (Idaho), farm_wa, farm_or, farm_co
  Storage (4):    stor_id, stor_wa, stor_or, stor_co
  Processors (2): proc_west, proc_east
  Warehouses (3): wh_west, wh_central, wh_east
  Retail (4):     ret_west, ret_mountain, ret_midwest, ret_east

All quantities in tonnes/week.
"""
from __future__ import annotations
import math
from typing import Dict, Tuple

from src.models import AgeBuckets, EdgeState, EdgeType, NodeState, NodeType


# ── baseline production and demand balance ──────────────────────────────────
# Total weekly production at peak ≈ 1 170 t/wk across all farms.
# Total weekly retail demand      ≈ 1 050 t/wk.
# Processor throughput demand     ≈  700 t/wk.
# Storage capacity is set so that at peak production, storage nodes reach
# ~60 % utilisation after ~4 steps of accumulation — triggering the
# accumulation regime before any disruption is applied.

_BASE_PROD    = 300.0   # Idaho peak; others scaled below
_STORAGE_TIGHTNESS = 0.65   # storage_capacity = weekly_inflow / this factor
# (lower = tighter; 0.65 means 6 weeks of inflow fills storage)


def build_potato_network(
    season_week: int = 40,
    extra_storage_capacity: float = 0.0,
    add_lateral_routes: bool = False,
) -> Tuple[Dict[str, NodeState], Dict[str, EdgeState]]:
    """
    Build and return (nodes, edges).

    Parameters
    ----------
    season_week            : 0–51, peak at week 40 (harvest season)
    extra_storage_capacity : tonnes added to every storage node (Exp 4)
    add_lateral_routes     : add lateral storage↔storage + warehouse↔warehouse
                             edges (Exp 4)
    """
    nodes: Dict[str, NodeState] = {}
    edges: Dict[str, EdgeState] = {}

    # Seasonal factor: peaks at week 40, troughs at week 14
    season = 0.55 + 0.45 * math.cos(2 * math.pi * (season_week - 40) / 52)

    # ── FARMS ────────────────────────────────────────────────────────────────
    # Production adds as fresh inventory each step.
    # Farm storage is deliberately limited — farms push to storage quickly.
    farm_specs = [
        # (id,       scale, initial_inv_t)
        ("farm_id",  1.00,  80.0),
        ("farm_wa",  0.65,  52.0),
        ("farm_or",  0.45,  36.0),
        ("farm_co",  0.30,  24.0),
    ]
    for fid, scale, init in farm_specs:
        prod = _BASE_PROD * scale * season
        n = NodeState(
            node_id           = fid,
            node_type         = NodeType.FARM,
            storage_capacity  = 250.0 * scale,   # small on-farm buffer
            cold_storage_capacity = 0.0,          # farms: no cold
            throughput_capacity   = 9999.0,       # farms don't bottleneck
            production        = prod,
        )
        n.inventory = AgeBuckets(fresh=init * 0.7, mid_life=init * 0.3)
        nodes[fid] = n

    # ── STORAGE ──────────────────────────────────────────────────────────────
    # Cold storage slows cohort aging by 60 % (_COLD_SLOWDOWN=0.4).
    # Capacity is set tight: inflow from connected farms / _STORAGE_TIGHTNESS.
    # Baseline utilisation target ~55 %.
    storage_specs = [
        # (id,         cap_t,   has_cold,  init_fill_frac)
        ("stor_id",   900.0,   True,       0.50),
        ("stor_wa",   620.0,   True,       0.50),
        ("stor_or",   430.0,   True,       0.50),
        ("stor_co",   300.0,   False,      0.45),
    ]
    for sid, cap, cold, fill in storage_specs:
        n = NodeState(
            node_id               = sid,
            node_type             = NodeType.STORAGE,
            storage_capacity      = cap + extra_storage_capacity,
            cold_storage_capacity = cap * 0.85 if cold else 0.0,
            throughput_capacity   = cap * 0.75,
        )
        # Pre-load: mix of mid-life and near-expiry to make age pressure visible early
        n.inventory = AgeBuckets(
            fresh       = cap * fill * 0.35,
            mid_life    = cap * fill * 0.45,
            near_expiry = cap * fill * 0.20,
        )
        nodes[sid] = n

    # ── PROCESSORS ───────────────────────────────────────────────────────────
    # Processors have a pull-demand: they pull stock to meet throughput.
    # Throughput_capacity is the max they can process per step.
    # demand is set to ~75 % of throughput — leaves headroom but is meaningful.
    proc_specs = [
        ("proc_west",  560.0),
        ("proc_east",  420.0),
    ]
    for pid, tp in proc_specs:
        n = NodeState(
            node_id               = pid,
            node_type             = NodeType.PROCESSOR,
            storage_capacity      = 280.0,
            cold_storage_capacity = 0.0,
            throughput_capacity   = tp,
            demand                = 0.0,  # set by apply_stable_demand()
        )
        n.inventory = AgeBuckets(fresh=60.0, mid_life=30.0)
        nodes[pid] = n

    # ── WAREHOUSES ───────────────────────────────────────────────────────────
    wh_specs = [
        ("wh_west",    720.0),
        ("wh_central", 540.0),
        ("wh_east",    480.0),
    ]
    for wid, cap in wh_specs:
        n = NodeState(
            node_id               = wid,
            node_type             = NodeType.WAREHOUSE,
            storage_capacity      = cap,
            cold_storage_capacity = cap * 0.50,
            throughput_capacity   = cap * 0.65,
        )
        n.inventory = AgeBuckets(
            fresh    = cap * 0.12,
            mid_life = cap * 0.10,
        )
        nodes[wid] = n

    # ── RETAIL ───────────────────────────────────────────────────────────────
    # Demand is set to 0 here; call apply_stable_demand(nodes) after building.
    retail_specs = [
        ("ret_west",     220.0),
        ("ret_mountain", 100.0),
        ("ret_midwest",  185.0),
        ("ret_east",     290.0),
    ]
    for rid, demand in retail_specs:
        n = NodeState(
            node_id               = rid,
            node_type             = NodeType.RETAIL,
            storage_capacity      = 160.0,
            cold_storage_capacity = 0.0,
            throughput_capacity   = 9999.0,
            demand                = 0.0,  # set by apply_stable_demand()
        )
        n.inventory = AgeBuckets(fresh=40.0, mid_life=25.0)
        nodes[rid] = n

    # ── EDGES ─────────────────────────────────────────────────────────────────
    eid_counter = [0]

    def E(src, tgt, etype, cap, transit, cost, refrig=False, rel=0.95) -> EdgeState:
        eid_counter[0] += 1
        return EdgeState(
            edge_id   = f"e{eid_counter[0]:03d}",
            source_id = src,
            target_id = tgt,
            edge_type = etype,
            capacity  = cap,
            transit_time = transit,
            reliability  = rel,
            cost         = cost,
            is_refrigerated = refrig,
            # Constraint binds at 85 % utilisation (tighter than default 90 %)
            active_constraint_threshold = 0.85,
        )

    def add(e: EdgeState):
        edges[e.edge_id] = e

    # Farm → Storage (primary, refrigerated)
    add(E("farm_id", "stor_id", EdgeType.FARM_TO_STORAGE,  280, 1, 1.0, True))
    add(E("farm_wa", "stor_wa", EdgeType.FARM_TO_STORAGE,  180, 1, 1.0, True))
    add(E("farm_or", "stor_or", EdgeType.FARM_TO_STORAGE,  125, 1, 1.0, True))
    add(E("farm_co", "stor_co", EdgeType.FARM_TO_STORAGE,   90, 1, 1.0, False))
    # Cross-farm → storage overflow routes (lower capacity, longer transit)
    add(E("farm_id", "stor_wa", EdgeType.FARM_TO_STORAGE,   80, 2, 1.8, True,  0.88))
    add(E("farm_wa", "stor_id", EdgeType.FARM_TO_STORAGE,   60, 2, 1.8, True,  0.88))

    # Storage → Processor
    # e007: primary Idaho → West Processor corridor (key disruption target)
    add(E("stor_id", "proc_west", EdgeType.STORAGE_TO_PROCESSOR, 300, 2, 2.0, True))   # e007
    add(E("stor_wa", "proc_west", EdgeType.STORAGE_TO_PROCESSOR, 220, 2, 2.0, True))
    add(E("stor_or", "proc_west", EdgeType.STORAGE_TO_PROCESSOR, 150, 3, 2.5, True))
    add(E("stor_co", "proc_east", EdgeType.STORAGE_TO_PROCESSOR, 110, 3, 3.0, False))
    add(E("stor_id", "proc_east", EdgeType.STORAGE_TO_PROCESSOR, 140, 4, 3.5, True,  0.82))

    # Processor → Warehouse
    add(E("proc_west", "wh_west",    EdgeType.PROCESSOR_TO_WAREHOUSE, 270, 1, 1.5, True))
    add(E("proc_west", "wh_central", EdgeType.PROCESSOR_TO_WAREHOUSE, 200, 2, 2.0, True))
    add(E("proc_east", "wh_central", EdgeType.PROCESSOR_TO_WAREHOUSE, 160, 2, 2.0, False))
    add(E("proc_east", "wh_east",    EdgeType.PROCESSOR_TO_WAREHOUSE, 210, 2, 2.0, False))

    # Warehouse → Retail
    add(E("wh_west",    "ret_west",     EdgeType.WAREHOUSE_TO_RETAIL, 230, 1, 1.0, True))
    add(E("wh_west",    "ret_mountain", EdgeType.WAREHOUSE_TO_RETAIL, 110, 2, 1.5, True))
    add(E("wh_central", "ret_midwest",  EdgeType.WAREHOUSE_TO_RETAIL, 200, 1, 1.0, False))
    add(E("wh_central", "ret_mountain", EdgeType.WAREHOUSE_TO_RETAIL,  80, 2, 1.5, False))
    add(E("wh_east",    "ret_east",     EdgeType.WAREHOUSE_TO_RETAIL, 300, 1, 1.0, False))
    add(E("wh_east",    "ret_midwest",  EdgeType.WAREHOUSE_TO_RETAIL,  95, 2, 1.5, False))

    # ── Optional lateral rerouting (Experiment 4 only) ────────────────────
    if add_lateral_routes:
        add(E("stor_id", "stor_co", EdgeType.LATERAL_STORAGE,    70, 3, 3.2, False, 0.80))
        add(E("stor_wa", "stor_or", EdgeType.LATERAL_STORAGE,    55, 2, 2.6, False, 0.80))
        add(E("wh_west", "wh_central", EdgeType.LATERAL_WAREHOUSE, 110, 2, 2.1, False, 0.82))
        add(E("wh_central","wh_east",  EdgeType.LATERAL_WAREHOUSE,  90, 2, 2.1, False, 0.82))

    return nodes, edges
