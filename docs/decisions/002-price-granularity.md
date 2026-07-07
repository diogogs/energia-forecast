# ADR-002: Ingest prices at native 15-min resolution, model hourly

**Date:** 2026-07-07 · **Status:** accepted

## Context

The Single Day-Ahead Coupling (SDAC), which includes MIBEL, moved from hourly to a **15-minute Market Time Unit on delivery day 2025-10-01**. Day-ahead prices are now 96 quarter-hourly values per day (was 24). The official hourly index is still published as the arithmetic mean of the four quarters. Load for PT remains hourly.

## Options

1. Model 96 quarter-hourly price values natively.
2. Model 24 hourly values (quarter-mean), ingest native resolution for the future.

## Decision

**Option 2.** Raw layer stores whatever resolution the source publishes (a `resolution_minutes` column on every raw table); the clean layer is an hourly modelling grid; prices post-2025-10-01 are averaged 4→1 with an `n_quarters` provenance column.

## Why

- The hourly average gives a **continuous series since 2015** and aligns with hourly load and weather — 4× fewer points, one grid, simpler everywhere.
- The 15-min data is preserved in `raw`, so a quarter-hourly extension is additive, not a rework.
- Charter tiebreaker: simpler-but-in-production beats more-complete.

## Consequences

- Validation must not assume a single grid: `n_quarters = 4` for every UTC hour after the cutover; the 92/100-quarter counts on DST days are a property of the CET market day, checked at market-day level.
- A mixed-resolution boundary (2025-10-01) sits inside the training window; watched for variance shift during error analysis.
