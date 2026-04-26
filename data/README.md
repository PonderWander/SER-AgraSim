# Data Sources

All data files in this directory are compiled from published government datasets.
The sandbox environment cannot fetch live data from .gov domains, so values are
transcribed from the published documents cited below.  Each CSV records its
source document, URL, and the derivation from published figures to model constants.

## Directory structure

    data/
      nass/
        nass_potato_production_by_state.csv     State-level production 2022-2024
        nass_model_calibration.csv              Production scale factors for modelled farms
      ers/
        ers_lafa_potato_per_capita.csv           Per-capita consumption breakdown (LAFA)
        ers_regional_demand_derivation.csv       Population × consumption → regional demand
        ers_cold_storage_parameters.csv          Cold-storage shelf life references
      faf5/
        faf5_potato_od_flows.csv                 OD freight flows for key corridors (SCTG 03)
        faf5_edge_capacity_calibration.csv       Edge capacity derivation from FAF5 tonnage
      paca/
        paca_good_delivery_guidelines.csv        PACA quality/transit standards
        paca_aging_rate_calibration.csv          Derivation of aging rate constants
      processed/
        model_parameter_source_table.csv         All 17 model constants linked to sources

## Sources

### USDA NASS — Potatoes 2024 Summary (January 2025)
- **Document**: USDA National Agricultural Statistics Service, "Potatoes 2024 Summary"
- **URL**: https://usda.library.cornell.edu/concern/publications/fx719m14m
- **Table used**: Table 1 — Area Harvested, Yield per Acre, and Production by State, 2022-2024
- **Values transcribed**: Idaho, Washington, Oregon, Colorado, and 6 other states
- **Units**: Area in thousand acres; Yield in cwt/acre; Production in thousand cwt
- **Used for**: Farm node production scales (`_BASE_PROD` and per-farm scale factors)

### USDA ERS — Food Availability (Per Capita) Data System
- **Document**: Loss-Adjusted Food Availability (LAFA) Data, 2022
- **URL**: https://www.ers.usda.gov/data-products/food-availability-per-capita-data-system/
- **Values transcribed**: Total potato consumption 54.1 kg/person/year (fresh 20.0,
  frozen 21.8, chips/dehydrated 11.9, canned 0.4)
- **Used for**: Retail node demand calibration via population × per-capita × retail-share
- **Supplementary**: ERS Vegetables and Pulses Outlook for processor demand context

### USDA ERS / ARS — Commercial Storage Parameters
- **Document**: USDA ARS Agricultural Handbook Number 66 (2016 revision),
  "Commercial Storage of Fruits, Vegetables, and Florist and Nursery Stocks"
- **URL**: https://www.ars.usda.gov/is/np/CommercialStorage/CommercialStorage.pdf
- **Pages used**: Potato section, pp. 453-461
- **Values transcribed**: Optimal storage temperature 7-10°C, storage life 5-8 months cold
  vs 2-3 weeks ambient, transit temperature specification 4-10°C
- **Used for**: `_R_NEAR_TO_SPOIL=0.28`, `_COLD_FACTOR=0.35`, cold storage shelf life

### BTS / FHWA — Freight Analysis Framework Version 5 (FAF5)
- **Document**: FAF5 2022 Origin-Destination Commodity Flow Data
- **URL**: https://ops.fhwa.dot.gov/freight/freight_analysis/faf/
- **Supplementary methodology**: BTS FAF5 Analytical Estimates Methodology Report (April 2025)
  https://www.bts.gov/sites/bts.dot.gov/files/2025-04/BTS_FAF5_AE-methodology-2025_Report_20250401.pdf
- **SCTG code**: 03 — Other fresh vegetables & preparations (includes potatoes)
- **FAF zones used**: 041 (Idaho), 531 (Washington), 081 (Colorado), 062 (Los Angeles),
  171 (Chicago), 191 (New York), 081 (Denver)
- **Values transcribed**: Annual tonnage between zone pairs, modal split (truck ~92% for SCTG 03),
  highway distances from FHWA NHPN network
- **Used for**: Edge transit_time parameters, relative edge capacity calibration

### USDA AMS — PACA Good Delivery Guidelines
- **Document**: PACA Good Delivery Guidelines — Potato section
- **URL**: https://www.ams.usda.gov/rules-regulations/paca/good-delivery
- **Supplementary**: USDA AMS "United States Standards for Grades of Potatoes" (7 CFR 51)
  https://www.ams.usda.gov/grades-standards/potato-grades-and-standards
- **Values transcribed**: Maximum non-refrigerated transit 14 days, temperature spec 4-10°C,
  ≤4% serious damage for U.S. No. 1 grade at destination
- **Used for**: `_R_NEAR_TO_SPOIL` calibration (14-day transit window),
  `is_refrigerated` edge flag semantics, spoilage-as-rejection interpretation

## Important notes on model scale

The model represents a **single sub-regional processing corridor**, not national totals.
Model production and demand are approximately 1.8-2.0% of actual flows for modelled regions.
This scale is chosen so that:
- Storage nodes operate at ~55-65% baseline utilisation (tight but not saturated)
- Single-constraint scenarios produce accumulation without immediate overflow
- Multi-constraint scenarios can drive isolation within a 52-step (1-year) simulation

The 1.9% scale factor is consistent across all modelled regions (production side and
demand side), preserving the supply-demand balance at model scale.
