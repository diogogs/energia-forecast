# ADR-014: The live track record is a first-class dashboard chapter

**Date:** 2026-07-18 · **Status:** accepted

## Context

A week after launch the dashboard still presented backtest results as its main evidence and
showed the live record only as a single since-launch aggregate ("Live MAE: 313 MW over
227 h") plus a today-only chart that reset every morning. The aggregate was dominated by
the first two mornings (before the 11 July weather-ingestion fix, ADR-013 era): per-day
live MAE went 536 → 185 → 145 → 98 → 128 → 74 → 73 MW, with the last week ~100 MW —
*better* than the 166 MW backtest — while the headline read as "2× worse in production
than in backtest", the classic signature of a leaky backtest. The dashboard was telling
the system's best story backwards, and a visitor had no way to see any past emission.

## Decision

1. **New "Track record" page** — production emissions only, clearly separated from the
   backtest (which stays on Performance, labelled as simulated history): per-market-day
   MAE bars against the dashed backtest benchmark, with the pre-fix first mornings dimmed
   but kept (the record is never trimmed); a replay view for any delivery day (hourly
   forecast-as-issued vs realised, price with its P10-P90 band); and the emission log
   with per-day issue timestamps and a consecutive-punctual-mornings streak.
2. **Market-day aggregation everywhere daily figures appear.** Delivery days are CET/CEST
   market days; grouping by UTC (or Lisbon) calendar dates splits every market day across
   two dates and contaminates daily error at the edges (observed: 2 stray hours on the
   record's first and last days). `market_day()` in the dashboard's common helpers is the
   single implementation.
3. **The Status headline becomes a 7-day window** (the `/monitoring/error` endpoint already
   took `days`); the caption points at the full per-day record instead of averaging the
   incident into invisibility.
4. **New `/emissions` API endpoint** (issue_date, target, min issued_at, late flag, hours):
   the autonomy record, INCLUDING late emissions — punctuality is only a claim if the
   misses are listed next to the hits.
5. **Yesterday scorecard on the landing page**, above tomorrow's detail: proof before
   promise — the only number a visitor can independently judge is realised error.

## Consequences

- Two evidence regimes now have named homes: *Track record* (live) vs *Performance*
  (backtest). Copy on both pages states which is which.
- The pre-fix mornings stay visible forever; honesty about the bad start is the feature,
  not a blemish to be windowed away.
- As the record grows past ~a quarter, the daily bars will need aggregation (weekly bars
  or a rolling line) — deliberately deferred until the data demands it.
