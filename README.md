# SER-AgraSim

A Python simulation applying the **SER Framework** to agricultural supply-chain stress, using potatoes as the test case.

SER-AgraSim models how structural constraints in a perishable food network can produce different system regimes: **normal**, **accumulation**, **isolation** and **fragmentation**.  The project holds demand constant across experiments so that observed regime changes come from constraint interaction rather than demand variation.

## Purpose

This repository explores a core question:

> When does a supply chain move from simple retained stress into true structural isolation?

The simulations tests this through a synthetic U.S. potato distribution network with farms, storage nodes, processors, warehouses and retail demand centers.

## Core Idea

The model seperates:

**Stress (S)** - inventory pressure, backlog, age pressure, unmet demand and throughput blockage
**Elasticity (E)** - spare storage, clearance rate, alternate routes and buffer health
**Leaked Stress (L)** - stress that escapes local absorption and begins propagating system-wide
**Regime State** - the observed condition of a node or network segment

This allows the simulation to distinguish between:
- A node that is burdened but still functionally connected
- A node that is physically reachable but operationally blocked
- A node whose stress begins propagating destructively through the network

## Experiments

The project runs four structural experiments:

| Experiment | Scenario | Expected Result |
|---|---|---|
| Exp 1 | Baseline | Normal flow | 
| Exp 2 | Single storage constraint | Accumulation |
| Exp 3 | Storage cap + processor bottleneck + corridor disruption | Isolation \ Fragmentation |
| Exp 4 | Same as Exp 3 + extra storage and lateral routes | Mitigated stress / restored Elasticity |

The key comparison is **Exp 2 vs Exp 3**: same demand layer, similar network, different constraint interaction.

## Why Potatoes?

Potatoes are a useful test case because they are:
*Perishable
*Storage-dependent
*Regionally concentrated
*Sensitive to routing, processing and timing constraints

This makes them a practical agricultural domain for testing how stress accumulates, leaks, or is absorbed.

## Quick Start

```bash
git clone https://github.com/PonderWander/SER-AgraSim.git
cd SER-AgraSim

pip install networkx pandas matplotlib numpy

python run_experiments.py
```
