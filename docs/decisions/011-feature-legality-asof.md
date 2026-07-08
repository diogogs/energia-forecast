# ADR-011: Feature legality — modelled publication times, AsOfRepo, on-the-fly clean

**Date:** 2026-07-09 · **Status:** accepted

## Context

The clean/features layer begins. Its one non-negotiable job (CLAUDE.md "Modelo temporal") is
that a training feature may only use data with **`published_at ≤ t_issue`**. Getting this wrong
is silent, catastrophic leakage — an over-optimistic backtest that collapses in production. This
ADR fixes how legality is computed and read.

## Decisions

### 1. Modelled publication time (not `first_seen_at`) for history

All current data is **backfilled**, so `first_seen_at` is our July-2026 ingest time — useless
for a 2024 fold (it would exclude everything). We instead **model** each source's real
publication time from the valid time, in `src/features/temporal.py`. Rules are **conservative**
— never earlier than reality — so they can only ever be *stricter* than the truth, never leak:

| Source | Modelled `published_at` | Rationale |
|---|---|---|
| REN realised (consumption, generation) | next **Lisbon** midnight after the value's day | consolidated by the next morning; keeps day D incomplete at 07:00 |
| OMIE day-ahead price | **13:00 CET** on `market_date − 1` | published the day before after SDAC (~12:45 CET) |
| Energy-Charts (ES) | next **UTC** midnight | ENTSO-E-sourced realised; available next day |
| Open-Meteo Previous Runs | **06:00 UTC** on `valid_date − lead_days` | the archived 00Z run's dissemination time |

`first_seen_at` remains the publication proxy for **live** data (the morning `[now-3d, now]`
self-healing window); once live ingestion runs, recent rows carry a real `first_seen_at` and the
two agree. Backtesting uses the modelled rules — deterministic and sound for every fold.

**The load-bearing assumption** is Open-Meteo run availability (06:00 UTC). The 00Z ECMWF HRES
is disseminated before 07:00, so `lead_days=1` (the run from D) is legal for a D+1 forecast at
07:00 UTC of D. If that ever proves optimistic on some days, the as-of read simply falls back to
`lead_days=2` (the D−1 run) — no code change, just a stricter constant. It is one localised,
documented constant (`ECMWF_RUN_AVAILABLE`).

### 2. AsOfRepo is the only read path

`src/features/asof_repo.py` is the sole gateway from feature code to raw data. Given a fixed
`t_issue` it returns each series legally (publication `≤ t_issue`) on the hourly UTC grid.
Feature code never queries raw directly, so legality has exactly one home and one test surface
(the `leakage` marker, a CI merge gate). Coarse SQL filters differ by source semantics:
consumption filters `ts_utc < t_issue` (a realised value is always published after its valid
time); price filters `market_date ≤ D` (a *day-ahead* price is legal even for delivery hours
after `t_issue`). Verified live on the 2024-06-10 fold: consumption ends at the close of Lisbon
day D−1 with zero leakage; PT price legitimately includes day-D hours after 07:00 but never a
D+1 price.

### 3. `t_issue` fixed; delivery grid is the CET civil day

`t_issue = 07:00 UTC of D`, a constant — never `now()`. The delivery day is the **CET** market
day D+1: `delivery_hours_utc` steps in UTC between its CET midnights, yielding 23/24/25 hours on
DST days (the target-vector length follows the civil day).

### 4. "Clean" is computed on the fly, not materialised

The hourly grid is produced by resampling raw inside the AsOfRepo, not stored as `clean.*`
tables. Reasons: Neon free-tier headroom is ~80 MB (ADR-009) and hourly copies of four raw
sources would eat it; and a single source of truth (raw) avoids clean/raw drift. Weekly-retrain
+ daily-predict cadence makes on-read resampling cheap enough. Materialise later only if a real
query proves it necessary.

## Consequences

- One deterministic, testable definition of legality; the `leakage` tests encode the two
  non-negotiables (day-D consumption and D+1 price are both unpublished at `t_issue`).
- Baselines/backtesting build strictly on the AsOfRepo — no path can bypass legality.
- The conservative rules may exclude some genuinely-available data (e.g. same-day REN), but the
  charter's legal lags (consumption ≥48h, price ≥24h) never depend on that margin.
