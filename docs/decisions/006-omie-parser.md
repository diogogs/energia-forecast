# ADR-006: Hand-rolled OMIE parser (the OMIEData library silently corrupts 15-min files)

**Date:** 2026-07-07 · **Status:** accepted

## Context

OMIE publishes day-ahead marginal prices as public `marginalpdbc_YYYYMMDD.1` text files (no auth) — the token-free price source for this project. The community library `OMIEData` was the planned client.

Day-1 verification spike (2026-07-07):

- **Pre-transition file (2025-09-01):** parses correctly — `PRICE_PT`/`PRICE_SP`/`ENER_IB` rows, columns `H1..H24`.
- **Post-transition file (2026-07-01):** the raw file has **96 quarter-hourly rows**; `OMIEData` returned columns `H1..H25` populated with the **first 25 quarter-hour values silently relabelled as hours**, dropped the remaining 71 values, and lost the `ENER_IB` rows. No error, no warning.

Silent mislabelling of quarter-hours as hours is the worst possible failure mode for a time-series project.

## Decision

Write our own parser in `src/ingestion/sources/omie.py`. The format is trivial (semicolon-separated: `YYYY;MM;DD;period;price1;price2;`, header line + `*` terminator, ~40 lines of code including validation). Period semantics are resolution-aware: 1–24 = hours before 2025-10-01, 1–96 = quarter-hours after (23/25 h and 92/100 q on DST days). Column order (PT vs ES price) is pinned by fixture tests against dates where the two markets decoupled, and cross-checked against ENTSO-E in ongoing validation.

## Consequences

- One fewer dependency; full control over resolution handling and DST edge cases.
- Fixture tests must cover: pre-transition day, post-transition day, both 2026 DST days, and a PT≠ES decoupled day.
