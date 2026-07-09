# ⚡ energia-forecast

**Live day-ahead forecasting of Portuguese electricity demand and MIBEL prices — a zero-cost, always-on ML system.**

> Both targets are modelled and beat their baselines in a leakage-free rolling-origin backtest (consumption MAPE 2.77 %; price P50 MAE 13.24 €/MWh), emitted daily by GitHub Actions crons, and served through a read-only API + Streamlit dashboard. The architecture, decisions, and status live in [CLAUDE.md](CLAUDE.md) and [docs/decisions/](docs/decisions/).

## What this is

- **Real data, ingested daily:** ENTSO-E Transparency Platform (load, generation, day-ahead prices for PT/ES), OMIE market files, Open-Meteo weather forecasts — validated, layered (`raw → clean → features`), idempotent.
- **Forecasts issued *before* the market closes:** next-day hourly national consumption and MIBEL day-ahead prices (P10/P50/P90), issued daily before the 12:00 CET SDAC gate.
- **Every prediction is persisted and scored against reality** — the forecast-vs-actual record is the product.

## Serving & deployment

The serving layer is stateless — all state lives in Neon — and split in two, per the charter:

- **API** (`src/api`, FastAPI, read-only role): `/forecast/{consumption|price}`, `/backtest/{target}`, `/performance/{target}`, `/monitoring/freshness`, `/monitoring/error/{target}`. Run locally with `uv run uvicorn src.api.main:app`; deploy the root `Dockerfile` to a Render web service via the `render.yaml` blueprint (free tier), with `DATABASE_URL_RO` as the only env var (ADR-012 — HF Spaces Docker now requires PRO, so zero-cost serving moves to Render).
- **Dashboard** (`dashboard/app.py`, Streamlit): the latest D+1 forecasts (price with its P10–P90 CQR interval), each model's realised MAE vs baselines, and a data-freshness watchdog. Run with `uv run --group dashboard streamlit run dashboard/app.py`; deploy to Streamlit Community Cloud from `dashboard/requirements.txt` with `API_BASE_URL` pointing at the API.

## Engineering decisions

Architecture decision records live in [docs/decisions/](docs/decisions/). Highlights so far:

| ADR | Decision |
|---|---|
| [001](docs/decisions/001-weather-source-open-meteo.md) | Open-Meteo over IPMA for weather features |
| [002](docs/decisions/002-price-granularity.md) | Ingest native 15-min prices, model hourly |
| [003](docs/decisions/003-database-neon.md) | Neon Postgres over Supabase |
| [004](docs/decisions/004-scheduling-github-actions.md) | GitHub Actions cron, hardened against its failure modes |
| [005](docs/decisions/005-python-env-uv.md) | uv-managed Python 3.12 |
| [006](docs/decisions/006-omie-parser.md) | Hand-rolled OMIE parser (OMIEData silently truncates 15-min files) |

## Attribution

- Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
- Electricity market and system data: [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/), [OMIE](https://www.omie.es/), [REN Data Hub](https://datahub.ren.pt/).
