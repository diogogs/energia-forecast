# ADR-010: Open-Meteo Previous Runs as leakage-free training weather

**Date:** 2026-07-08 · **Status:** accepted

## Context

Weather drives both targets (temperature → demand; wind/radiation → renewables → price). The
project's identity is temporal rigor: a training feature may only use data **published ≤
t_issue**. Using reanalysis or observed weather as a training feature would be catastrophic
leakage — at issue time we only ever have a *forecast*. ADR-001 chose Open-Meteo; this ADR
pins how we ingest it leakage-free.

## API contract (verified live 2026-07-08)

```
GET https://previous-runs-api.open-meteo.com/v1/forecast
    ?latitude=…&longitude=…            (comma-separated → one entry per point, in request order)
    &hourly={var}_previous_day{N},…    &models=ecmwf_ifs025
    &start_date=…&end_date=…&timezone=UTC
```

- The **Previous Runs** API archives past model runs. For each valid time T it returns
  `{var}_previous_dayN` = T's value from the run initialised **N days before T's date**.
- **Verified leakage-safe:** `previous_day1/2` for a fixed T are byte-identical regardless of
  when queried — they are tied to the valid time's prior runs, not query time. The no-suffix
  `{var}` (most-recent run) is a near-analysis short-lead value → **leakage; never stored**.
- Times are UTC (naive ISO, `timezone=UTC`). Coverage from ~2024-03. Multi-location + monthly
  ranges in one call. Units native: °C, km/h, W/m².

## Decision

**`raw.openmeteo_forecast`** — tall, UTC-native:
- PK `(location, variable, lead_days, ts_utc)`. `value` + native `unit`; `source_ref`;
  immutable `first_seen_at`. No secondary index (storage; ADR-009 stance).
- **Store only `lead_days` 1 and 2** (never the leaky current run). Model **pinned**
  `ecmwf_ifs025` (same in training and production).
- **Locations:** `lisbon`, `porto`, `evora` (coastal N/centre + southern interior — PT demand
  and a renewable spread). Slugs map to response entries by request order; Open-Meteo snaps to
  its grid. Widen the set + re-run the idempotent backfill if needed.
- **Variables:** `temperature_2m` (demand HDD/CDD), `wind_speed_100m` (wind proxy),
  `shortwave_radiation` (solar proxy).

**Legality semantics (consumed downstream, not enforced in raw).** For a D+1 forecast issued
at 07:00 UTC of day D: `lead_days=1` is the run from D — the freshest run legal by the 07:00
cutoff; `lead_days=2` is the run from D-1 — always legal, and a forecast-revision feature. The
`build_features` / AsOfRepo layer selects the lead per `t_issue` legality (backfilled-series
rule, not `first_seen_at`). Raw stores both faithfully.

## Consequences

- Training and production consume the **same** archived-forecast surface and pinned model — no
  train/serve skew, no reanalysis leakage.
- ERA5 reanalysis stays **monitoring-only** (never a feature), per the charter.
- Tall + few locations/variables keeps this table small (~0.36 M rows); a real spatial upgrade
  (more points, or capacity-weighted) is a later, reversible widening.
- If `lead_days=1`'s run (D-00Z) is not yet disseminated by exactly 07:00 UTC on some days, the
  features layer can fall back to `lead_days=2`; the cutoff choice lives there, not in raw.
