# energia-forecast

[![CI](https://github.com/diogogs/energia-forecast/actions/workflows/ci.yml/badge.svg)](https://github.com/diogogs/energia-forecast/actions/workflows/ci.yml)

Day-ahead forecasting of Portuguese electricity demand and MIBEL wholesale prices: an
end-to-end ML system in continuous production on free-tier infrastructure.

Live: [dashboard](https://energia-forecast-bwwhirmyetaphmsk84dkqg.streamlit.app/) ·
[API](https://energia-forecast-api.onrender.com/docs)
(free-tier hosting; the first request after an idle period may take ~30 s)

## Overview

Every morning at 07:05 UTC the system forecasts the next market day, hour by hour, before
the 12:00 CET day-ahead auction closes:

- **Demand** — Portuguese national consumption (point forecast).
- **Price** — MIBEL PT day-ahead price as a P10/P50/P90 quantile triplet, with the interval
  conformally calibrated (CQR).

Forecasts are stored with their issue timestamp and never revised; realised outcomes are
ingested daily and every forecast is scored against them. On a 71-fold rolling-origin
backtest, the demand model reaches MAPE 2.77% (best naive baseline: 5.95%) and the price
model P50 MAE 13.24 EUR/MWh (persistence baseline: 15.98). A model is only published if it
beats both naive baselines on the same folds.

## How it works

```
REN · OMIE · Open-Meteo · Energy-Charts
        │  GitHub Actions crons (ingest 06:30 · predict 07:05 · backtest weekly · backup weekly)
        ▼
   Neon Postgres — raw → features → predictions (insert-only) + ops logs   [all UTC]
        │
        ├── training / rolling-origin backtest (MLflow tracking)
        ▼
   FastAPI read-only API (Render) ──► Streamlit dashboard (Community Cloud)
        │
        └── monitoring: freshness watchdog · live scoring · data-quality log
```

The central design constraint is temporal integrity: a feature may only use data whose
publication time is at or before the 07:00 UTC issue cutoff, enforced by a single as-of
data-access layer (`AsOfRepo`) with modelled publication times per source. Training weather
is archived forecasts (pinned ECMWF model), not observations. Anti-leakage tests gate every
merge in CI. See ADR-011 and the dashboard's Methodology page for the details.

## Data sources

| Source | Provides | Terms |
|---|---|---|
| [REN Data Hub](https://datahub.ren.pt/) | PT consumption (demand target) + generation mix, 15-min | public API |
| [OMIE](https://www.omie.es/) | MIBEL day-ahead prices PT/ES (price target) | public files |
| [Open-Meteo](https://open-meteo.com/) | Archived ECMWF forecasts: temperature, 100 m wind, radiation | CC BY 4.0 |
| [Energy-Charts](https://www.energy-charts.info/) (Fraunhofer ISE) | Spanish load and generation (features) | CC BY 4.0 |

All sources are re-ingested daily over a sliding 3-day window with idempotent upserts, so
gaps and late revisions self-heal. Each run's outcome is recorded durably in `ops.dq_log`.

## Running locally

Requires [uv](https://docs.astral.sh/uv/); Python 3.12 is managed by it. Configuration is
environment-only — copy `.env.example` to `.env` and fill in the database URLs.

```
uv sync                                                  # install
uv run pytest                                            # test suite (leakage tests included)
uv run uvicorn src.api.main:app                          # read-only API
uv run --group dashboard streamlit run dashboard/app.py  # dashboard (against API_BASE_URL)
```

## Deployment

- **API**: Render free Docker web service via the `render.yaml` blueprint; the only
  environment variable is `DATABASE_URL_RO` (a read-only Neon role). See ADR-012.
- **Dashboard**: Streamlit Community Cloud from `dashboard/requirements.txt`, with
  `API_BASE_URL` set in the app secrets.
- **Scheduling**: GitHub Actions crons (`.github/workflows/`) for ingestion, prediction,
  weekly backtest, weekly `pg_dump` backup, and an API keepalive. Prediction schedules
  include retry entries because insert-only storage makes re-runs idempotent.

## Engineering decisions

Recorded as short ADRs in [docs/decisions/](docs/decisions/):

| ADR | Decision |
|---|---|
| [001](docs/decisions/001-weather-source-open-meteo.md) | Open-Meteo over IPMA for weather |
| [002](docs/decisions/002-price-granularity.md) | Ingest native 15-min prices, model hourly |
| [003](docs/decisions/003-database-neon.md) | Neon Postgres over Supabase |
| [004](docs/decisions/004-scheduling-github-actions.md) | GitHub Actions cron, hardened against its failure modes |
| [005](docs/decisions/005-python-env-uv.md) | uv-managed Python 3.12 |
| [006](docs/decisions/006-omie-parser.md) | Hand-rolled OMIE parser (OMIEData corrupts 15-min files) |
| [007](docs/decisions/007-data-sources-token-free.md) | Token-free Iberian sources; ENTSO-E deferred |
| [008](docs/decisions/008-ren-datahub-source.md) | REN Data Hub as the PT consumption/generation source |
| [009](docs/decisions/009-energy-charts-source.md) | Energy-Charts as the ES features source |
| [010](docs/decisions/010-openmeteo-previous-runs.md) | Archived forecast runs as leakage-free training weather |
| [011](docs/decisions/011-feature-legality-asof.md) | Feature legality: modelled publication times + AsOfRepo |
| [012](docs/decisions/012-api-hosting-render.md) | API hosting on Render (zero-cost constraint) |

## Attribution

- Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
- Spanish power data by [Energy-Charts / Fraunhofer ISE](https://www.energy-charts.info/) (CC BY 4.0).
- Electricity market and system data: [REN Data Hub](https://datahub.ren.pt/), [OMIE](https://www.omie.es/).
