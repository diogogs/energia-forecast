# ADR-008: REN Data Hub as the PT consumption + generation source (schema & time model)

**Date:** 2026-07-08 · **Status:** accepted

## Context

ADR-007 chose REN Data Hub (token-free) as the primary source for the Phase-1 target
(national PT consumption) and PT generation by technology. This ADR records the **API
contract discovered empirically** and the **raw schema + time model** decided by a design
panel and validated against live data.

## API contract (verified live 2026-07-08)

The datahub.ren.pt SPA loads each chart via `POST /service/{data-id}`. The realised
production/consumption diagram is a single endpoint that returns **both** the target and all
generation features on one 15-min grid:

```
POST https://datahub.ren.pt/service/Electricity/ProductionBreakdown/{chart_id}
     ?culture=en-GB&dayToSearchString={ticks}
body: {}   (empty JSON, Content-Type application/json)
```

- `ticks` = **.NET `DateTime.Ticks`** of the target day at midnight =
  `int((datetime(y,m,d) - datetime(1,1,1)).total_seconds()) * 10_000_000`.
- Response is Highcharts JSON: `series: [{name, data: [float|null, ...]}, ...]`, `yAxis` in MW.
- Series: `Consumption` (**the Phase-1 target**, realised MW), `Consumption + Storage`, and
  generation/flow: `Solar, Hydro, Wind, Gas, Coal, Biomass, Other Thermal, Wave, Imports,
  Battery Injection`. `culture=en-GB` is pinned so the labels (the natural key) are stable.
- `data[]` length is **DST-correct by construction**: 96 normal / 92 spring-forward /
  100 fall-back. `xAxis.categories` carries 2 trailing junk labels (`len == N+2`) — ignored;
  `data[]` length is authoritative. Trailing `null`s mark not-yet-published slots.
- History reaches **≥ 2019**. Different `chart_id`s (1266, 1354, …) return identical data.
- There are separate `*Forecast` endpoints (`ConsumptionForecast`, …) — **out of scope** here;
  this table stores realised data only.

## Time model (verified)

REN slots are **Lisbon** civil time (WET/WEST = UTC+0/+1), **not** CET. UTC is computed the
same DST-safe way as the OMIE parser but anchored on `Europe/Lisbon`: `start_utc =
datetime(y,m,d, tz=Lisbon).astimezone(UTC)`, then `ts_utc = start_utc + i*15min` for the N
slots (N encodes the 23/24/25-hour day, so no local-time enumeration is needed).

Decisively confirmed against Energy-Charts PT load (ENTSO-E, natively UTC): on 2024-06-15 the
Lisbon-anchored REN Consumption aligns at **lag 0h with correlation 1.0000** (±1h → 0.85) and
MW match **to the decimal** (5073.3 at 12:00 UTC). The 1h Lisbon↔CET offset vs the OMIE
market day is a **downstream** (clean/features) concern — `local_date` is deliberately the
Lisbon civil day and **must not** be aligned with `omie_price.market_date` (CET).

## Decision — schema

A design panel (3 independent proposals + judge) chose a **tall** table mirroring
`raw.omie_price`, with `series_name` playing the role of `zone`:

- **`raw.ren_realised`** — PK `(series_name, ts_utc, resolution_minutes)`; `value_mw`
  DOUBLE PRECISION **signed** (Imports/Battery may be < 0); `local_date` (Lisbon),
  `period` (1..N), `source_ref` (e.g. `ren:ProductionBreakdown/1266:ticks=…`);
  `first_seen_at` write-once/immutable, `last_seen_at` per upsert. Index `(ts_utc,
  series_name)` for the clean-layer pivot and the `[now-3d, now]` re-ingest.
- **`meta.ren_series`** — non-enforcing dimension (`series_code`, `kind`, `is_target`),
  seeded with the 12 known series. **No FK** from raw, so ingestion never blocks on
  classification: a brand-new REN label lands in raw regardless and is logged as
  unclassified (never silently dropped).

**Why tall (vs wide / split):** one API response ingests atomically via one upsert; a
changing series set (new `Battery Injection`, absent `Coal` in old data) is pure
presence/absence of rows — **zero migrations**; the consumption-vs-generation split is
semantic and belongs in `meta`, not in physical tables. `null` slots emit **no row** so
`first_seen_at` stays an honest publication proxy.

## Consequences

- Upsert is byte-for-byte the OMIE idempotency pattern (`first_seen_at` excluded from
  `DO UPDATE`); the anti-leakage tests and a future `AsOfRepo` template cover both sources.
- The clean/features layer must **pivot** the tall table into per-technology feature columns.
- **Residual revision-leakage** (as-of reads current `value_mw`, gates only on
  `first_seen_at`): neutralised in practice by the ≥48h consumption lag; a bitemporal design
  is deferred, matching the OMIE precedent.
- `meta.ren_series` must be human-curated; an unclassified new series is ingested but invisible
  to feature-building until classified (surfaced via logs / future `ops.dq_log`).
