# ADR-007: Token-free Iberian data sources; ENTSO-E RESTful API deferred to cross-validation

**Date:** 2026-07-07 · **Status:** accepted

## Context

The conventional source for European electricity data is the ENTSO-E Transparency Platform RESTful API (`entsoe-py`). Verified 2026-07-07: obtaining its token still requires emailing `transparency@entsoe.eu` (subject "Restful API access") and waiting ~3 working days — the "self-service token" is only a *generate* button that appears *after* manual approval. For a portfolio project this is avoidable friction, and day-1 verification found token-free sources that fully cover the data needs.

## Decision

Primary sources, all **no auth / no token**, all verified working on 2026-07-07:

| Data | Source | Access |
|---|---|---|
| **PT consumption (Phase-1 target)** + PT generation by type | **REN Data Hub** JSON API (`servicebus.ren.pt/datahubapi`) | dedicated `Consumption` series, 15-min, 96 slots/day, history ≥2019; no key |
| **PT/ES day-ahead price (Phase-2 target + feature)** | **OMIE** `marginalpdbc` files (own parser, ADR-006) | public files, no key |
| **ES load + generation (features only)** | **Energy-Charts** API (`api.energy-charts.info`) | `/public_power?country=es`, `/price?bzn=ES`; no key; CC-BY 4.0; history ~2015 |
| Weather forecasts | Open-Meteo (ADR-001) | no key |

**ENTSO-E RESTful API:** deferred. The access email was sent 2026-07-07 but the pipeline does **not** depend on it. If/when the token arrives it becomes an optional **canonical cross-validation** source (compare REN load / OMIE price against the pan-European record) — never on the critical path.

## Why

- **Zero friction on the critical path** — no email/token blocks the build.
- **Strongest portfolio narrative:** official Iberian sources (REN = the Portuguese TSO, OMIE = the market operator) demonstrate real data-engineering (awkward formats, multi-source cross-validation) better than a single clean mirror API — which the charter explicitly values ("dados desarrumados").
- **Energy-Charts fills only the ES gap** that REN (PT-only) cannot; it re-exposes ENTSO-E data with no key.

## Consequences

- Two bespoke source modules (`ren.py` JSON, `energy_charts.py`) instead of one `entsoe-py` client — each verified day-1.
- **Attribution:** Energy-Charts CC-BY 4.0 in README (alongside Open-Meteo).
- **Reliability boundary:** Energy-Charts is a third-party mirror used only for ES *features* (never a target), so an outage degrades features, never the labels.
- **Definitional caveat:** REN `Consumption` ≠ ENTSO-E bidding-zone load (losses/pumping treated differently). Documented; never mix sources within one target series. If ENTSO-E is later adopted for the target, rebuild the whole clean window from one source.
- Modelling-matrix start stays **2024-04-01** (bound by Open-Meteo Previous Runs coverage, not by these sources, which reach further back).
