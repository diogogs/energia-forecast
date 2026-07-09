"""How-it-works page — the daily cycle, temporal rigor, data sources, and architecture."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from common import C_ACTUAL, C_MODEL, C_PERSIST, C_SEASONAL, GITHUB_URL, footer

st.title("🔬 How it works")
st.markdown(
    "This is a portfolio project about **ML engineering, not just a model**: a fully "
    "autonomous, zero-cost system that ingests real power-system data daily, forecasts "
    "tomorrow, records every prediction immutably, and grades itself in public. "
    f"Everything — code, tests, decision records — is [open on GitHub]({GITHUB_URL})."
)

# ---------------------------------------------------------------- the daily cycle
st.subheader("One day in the life of the system")

_EVENTS = [
    # label, start hour, end hour (UTC, relative to midnight of issue day D), colour
    ("1 · Ingest: 4 sources, self-healing 3-day window", 6.5, 7.0, C_PERSIST),
    ("2 · Feature cutoff (t_issue): data frozen as-of 07:00", 7.0, 7.1, C_SEASONAL),
    ("3 · Both forecasts issued for D+1 (write-once)", 7.1, 7.6, C_MODEL),
    ("4 · MIBEL day-ahead auction closes (12:00 CET)", 10.0, 10.5, "#898781"),
    ("5 · Delivery: the forecast day D+1 plays out", 22.0, 46.0, C_ACTUAL),
    ("6 · Next morning: outcomes ingested → forecasts scored", 30.5, 31.0, C_PERSIST),
]
timeline = pd.DataFrame(_EVENTS, columns=["event", "start", "end", "colour"])
chart = (
    alt.Chart(timeline)
    .mark_bar(cornerRadius=3, height=16)
    .encode(
        x=alt.X(
            "start:Q",
            title="Hours (UTC) from midnight of issue day D",
            scale=alt.Scale(domain=[0, 48]),
            axis=alt.Axis(values=[0, 6, 12, 18, 24, 30, 36, 42, 48]),
        ),
        x2="end:Q",
        y=alt.Y("event:N", sort=timeline.event.tolist(), title=None),
        color=alt.Color("colour:N", scale=None),
        tooltip=["event:N"],
    )
    .properties(height=220)
)
midnight = (
    alt.Chart(pd.DataFrame({"x": [24]}))
    .mark_rule(color="#898781", strokeDash=[4, 3])
    .encode(x="x:Q")
)
st.altair_chart(chart + midnight, width="stretch")
st.caption(
    "The forecast is committed ~3 hours before the market's own deadline, and ~15 hours "
    "before the delivery day even starts. The dashed line is midnight between D and D+1."
)

# ---------------------------------------------------------------- temporal rigor
st.subheader("The hard part: never letting the future leak in")
st.markdown(
    "Backtests are easy to cheat by accident — using data that *exists* for a past date but "
    "was not *published* yet when the forecast would have been issued. This system's whole "
    "design guards against that:\n\n"
    "- **Legality by publication time.** A feature may only use data whose publication time "
    "is ≤ the 07:00 UTC cutoff — enforced by a single as-of data-access layer, never raw "
    "queries. Ingestion records a `first_seen_at` on insert (never updated) as the "
    "publication proxy.\n"
    "- **Demand lags start at 48 h, not 24 h.** At 07:00 the current day is still incomplete "
    '— "yesterday\'s demand" is unknowable for most hours. Using the 24 h lag scores great '
    "in a naive backtest and is impossible in production. (Price lags *can* start at 24 h: "
    "day-ahead prices are published the afternoon before.)\n"
    "- **Training weather = archived forecasts, not observations.** The model trains on what "
    "the weather forecast *said at the time* (pinned ECMWF model, archived runs), because "
    "that is exactly what it gets in production.\n"
    "- **Write-once predictions.** Every emission is stored with its issue timestamp and "
    "never mutated — an audit trail, not a leaderboard.\n"
    "- **Leakage tests gate every merge** — the CI fails if any feature can see past the "
    "cutoff."
)

# ---------------------------------------------------------------- data sources
st.subheader("Data sources — all public, all free")
st.markdown(
    "| Source | What it provides | Resolution | Terms |\n"
    "|---|---|---|---|\n"
    "| [REN Data Hub](https://datahub.ren.pt/) | PT national consumption (the demand target) "
    "+ generation by technology | 15 min, since 2019 | public API |\n"
    "| [OMIE](https://www.omie.es/) | MIBEL day-ahead prices PT & ES (the price target) | "
    "hourly / 15 min | public files |\n"
    "| [Open-Meteo](https://open-meteo.com/) | Archived ECMWF weather forecasts: temperature, "
    "100 m wind, solar radiation | hourly | CC BY 4.0 |\n"
    "| [Energy-Charts](https://www.energy-charts.info/) (Fraunhofer ISE) | Spanish load & "
    "generation (cross-border features) | 15 min | CC BY 4.0 |\n\n"
    "~1.9 M raw rows and counting; every source re-ingested daily over a sliding 3-day window "
    "(idempotent upserts), so late revisions and gaps self-heal."
)

# ---------------------------------------------------------------- models
st.subheader("Models — deliberately boring, rigorously judged")
st.markdown(
    "- **Demand:** a single LightGBM (`regression_l1`) over calendar features (Lisbon "
    "holidays), legal lags {48, 72, 168, 336 h}, rolling stats and forecast weather "
    "(HDD/CDD, wind³, radiation). Retrained from scratch every morning in seconds.\n"
    "- **Price:** three LightGBM quantile regressors (P10/P50/P90), shallow and heavily "
    "regularised, with the interval **conformally calibrated** (CQR) on a trailing window. "
    "Extra features: the demand forecast *as issued*, price lags & day-D aggregates, the "
    "ES price spread, and renewables proxies.\n"
    "- **The gate:** a model ships only if it beats persistence *and* weekly-seasonal "
    "baselines on the same rolling-origin folds. Otherwise the dashboard shows the baseline, "
    "labelled as such."
)

# ---------------------------------------------------------------- architecture
st.subheader("Architecture — zero-cost, stateless serving")
st.code(
    """REN · OMIE · Open-Meteo · Energy-Charts
        │  GitHub Actions crons (ingest 06:30 · predict 07:05 · backtest weekly · backup weekly)
        ▼
   Neon Postgres  — raw → features → predictions (insert-only) + ops logs   [all UTC]
        │
        ├── training / rolling-origin backtest (MLflow tracking)
        ▼
   FastAPI read-only API (Render)  ──►  this Streamlit dashboard
        │
        └── monitoring: freshness watchdog · live error · data-quality log""",
    language=None,
)
st.markdown(
    "Python 3.12 · uv · pandas · LightGBM · SQLAlchemy/Alembic · pydantic v2 · FastAPI · "
    "Streamlit + Altair · pytest (118 tests, anti-leakage suite gates CI) · ruff · mypy. "
    f"Design decisions live as [ADRs in the repo]({GITHUB_URL}/tree/main/docs/decisions)."
)

# ---------------------------------------------------------------- honesty box
st.subheader("Known limits — kept on purpose")
st.markdown(
    "- The price interval's empirical coverage runs a few points below its 80% target: price "
    "regimes shift faster than any trailing calibration window. Documented, not patched over.\n"
    "- No intraday updates: one forecast per day, before the market closes. That is the "
    "product's contract.\n"
    "- Free tiers everywhere means the API cold-starts after idle periods (a keepalive ping "
    "covers waking hours).\n"
    "- Simplicity beats state-of-the-art here: the point is a **trustworthy system**, not a "
    "leaderboard score."
)

footer()
