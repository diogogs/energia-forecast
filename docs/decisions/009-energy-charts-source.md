# ADR-009: Energy-Charts as the ES features source (schema & time model)

**Date:** 2026-07-08 · **Status:** accepted

## Context

ADR-007 chose Energy-Charts (Fraunhofer ISE, token-free, CC-BY 4.0) to fill the ES gap that
REN (PT-only) cannot: Spanish load + generation by technology, used as **features only**,
never a target. This ADR records the verified API contract and the raw schema.

## API contract (verified live 2026-07-08)

```
GET https://api.energy-charts.info/public_power?country=es&start=YYYY-MM-DD&end=YYYY-MM-DD
Response: {"unix_seconds": [...], "production_types": [{"name", "data": [float|null]}, ...],
           "deprecated": bool}
```

- **`unix_seconds` are UTC instants** — no local-midnight anchoring, DST is automatic (a 23h
  spring-forward day simply returns 92 slots; verified 2026-03-29). This is the *easy* time
  case, unlike OMIE (CET) and REN (Lisbon).
- ES is **15-min from 2024 on** (hourly before — out of our 2024-04 modelling window). The
  slot resolution is **derived from the timestamp spacing** per slot, so a future resolution
  change needs no code change.
- `production_types` grows over time (19 → 21 types observed); values are MW, **signed**
  (`Cross border electricity trading`, `Hydro pumped storage consumption` go negative).
- `end` is **inclusive**; wide ranges work (a full year = 35 136 slots in one call), so we
  backfill per calendar month — few polite requests, granular error isolation.
- History reaches back to ~2015.

## Decision — schema

**`raw.energy_charts_power`** — tall, mirroring the OMIE/REN pattern:

- PK `(country, production_type, ts_utc, resolution_minutes)`. `country` is in the key (ES
  today) so the table generalises to a future PT cross-validation feed with zero change.
- `value_mw` DOUBLE PRECISION **signed**; `source_ref` (the range query); `first_seen_at`
  write-once/immutable, `last_seen_at` per upsert. Index `(ts_utc, production_type)` for the
  clean-layer pivot.
- **No `local_date`/`period`** (unlike REN): timestamps are UTC-native, so those Lisbon-day
  columns would be meaningless here.
- **No companion meta dimension** (unlike REN's `ren_series`): every series here is a feature
  (no target to flag), and the clean layer selects production types by name directly.

**Scope — curated feature set (not an exhaustive mirror).** We keep only the ES series that
carry forecasting signal: `Load`, `Solar`, `Wind onshore`, `Cross border electricity trading`
(demand + renewable proxies + net position). The other ~13 production types (and the two
percentage `Renewable share…` series, which aren't MW) are dropped at parse time. Rationale:
1. **Temporal legality:** at `t_issue` the target-day ES generation is unpublished, so these
   series only ever enter models as *lagged* features — fine-grained generation-by-technology
   adds little a few well-chosen proxies don't.
2. **Zero-cost budget:** the full 17-type table at 15-min was ~400 MB; with `raw.ren_realised`
   (~209 MB) that blows the Neon free-tier **512 MB** limit (hit live during the first
   backfill). The curated set keeps ES features to ~70 MB.
Reversible — widen `FEATURE_TYPES` and re-run the idempotent backfill.

**Storage decisions (this table + a companion change).** To live inside 512 MB:
- **No secondary indexes** on `raw.energy_charts_power`; and `raw.ren_realised`'s
  `(ts_utc, series_name)` index was dropped (migration 0004) — it roughly doubled that table's
  index footprint and its consumer (the clean-layer pivot) doesn't exist yet. The PK serves
  per-series range scans; a purpose-built index goes in when the clean layer's real queries
  justify it. Net effect: DB fell from 491 MB → 248 MB, leaving headroom for Open-Meteo.
- **Known future lever (not done yet):** if we hit the ceiling again, normalise the repeated
  string dimensions (`series_name`, `production_type`, `source_ref`) to small surrogate keys —
  the biggest remaining win, deferred to keep the raw layer join-free for now.

## Consequences

- ES generation/load align with OMIE (both UTC) directly; no timezone reconciliation needed.
- The clean/features layer pivots the tall table into per-technology ES feature columns and
  selects `Load` as the ES-demand feature and `Solar`/`Wind onshore` etc. as renewable proxies.
- **Reliability boundary:** an Energy-Charts outage degrades ES *features* only — never the
  PT targets (REN consumption, OMIE price) — matching ADR-007's third-party-mirror stance.
- `production_type` labels depend on Energy-Charts' stable naming; a rename would fork history
  into a new series (same caveat as REN), mitigated downstream by normalisation.
