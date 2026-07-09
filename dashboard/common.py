"""Shared helpers for the multipage dashboard: API client, palette, chart builders, footer.

Design system: the validated dataviz palette (model = blue, actual = orange, baselines =
aqua/yellow) with a deliberate visual hierarchy — the model and reality are the heroes
(thick solid lines); baselines recede (thin, dashed). Attribution in the footer is a licence
requirement (Open-Meteo and Energy-Charts are CC BY 4.0).
"""

from __future__ import annotations

import os

import altair as alt
import httpx
import pandas as pd
import streamlit as st

GITHUB_URL = "https://github.com/diogogs/energia-forecast"
LISBON = "Europe/Lisbon"


def _api_base_url() -> str:
    """Resolve the API URL from Streamlit secrets (Cloud), then env var, then localhost."""
    try:
        secret = st.secrets.get("API_BASE_URL")  # raises if no secrets file is configured
        if secret:
            return str(secret)
    except Exception:
        pass
    return os.environ.get("API_BASE_URL", "http://localhost:8000")


API_BASE_URL = _api_base_url()

# Validated categorical palette (dataviz reference slots): actual, model, two baselines.
C_MODEL, C_ACTUAL, C_PERSIST, C_SEASONAL = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
SERIES = ["Actual", "Model", "Persistence", "Weekly seasonal"]
COLOR = alt.Scale(domain=SERIES, range=[C_ACTUAL, C_MODEL, C_PERSIST, C_SEASONAL])
# Hierarchy: heroes thick and solid, baselines thin and dashed — the eye lands on blue/orange.
_WIDTH = alt.Scale(domain=SERIES, range=[2.6, 2.6, 1.3, 1.3])
_DASH = alt.Scale(domain=SERIES, range=[[1, 0], [1, 0], [5, 3], [5, 3]])

LABEL = {
    "lightgbm": "Model",
    "lightgbm_p50": "Model",
    "persistence_48h": "Persistence",
    "persistence_24h": "Persistence",
    "seasonal_168h": "Weekly seasonal",
}


# Generous timeout: the free-tier API spins down after ~15 min idle and cold-starts in 30-60 s
# (a keepalive cron keeps it warm during waking hours), so the first request waits it out.
@st.cache_data(ttl=300, show_spinner=False)
def api(path: str) -> object:
    return httpx.get(f"{API_BASE_URL}{path}", timeout=75).json()


def api_or_none(path: str) -> object | None:
    """api() that absorbs transport errors (cold start, redeploy) instead of raising."""
    try:
        return api(path)
    except httpx.HTTPError:
        return None


def cold_start_stop() -> None:
    """Standard warning + halt for when the API is not reachable yet."""
    st.warning(
        f"The API ({API_BASE_URL}) is waking from its free-tier sleep (~30-60 s). "
        "Refresh this page in a minute."
    )
    st.stop()


def local(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, utc=True).dt.tz_convert(LISBON)


def forecast_lines(points: list[dict], models: dict[str, str]) -> pd.DataFrame:
    """Long frame (hour, series, value) for the named point-forecast models."""
    rows = [p for p in points if p["model_name"] in models and p["quantile"] in ("point", "p50")]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["hour"] = local(df["target_ts"])
    df["series"] = df["model_name"].map(LABEL)
    return df[["hour", "series", "y_hat"]].rename(columns={"y_hat": "value"})


def price_band(points: list[dict]) -> pd.DataFrame:
    """Wide frame (hour, p10, p90) for the price interval band."""
    by_ts: dict[str, dict[str, float]] = {}
    for p in points:
        if p["model_name"] == "lightgbm" and p["quantile"] in ("p10", "p90"):
            by_ts.setdefault(p["target_ts"], {})[p["quantile"]] = p["y_hat"]
    df = pd.DataFrame([{"target_ts": ts, **qs} for ts, qs in by_ts.items()])
    if df.empty or "p10" not in df:
        return pd.DataFrame()
    df["hour"] = local(df["target_ts"])
    return df[["hour", "p10", "p90"]].sort_values("hour")


def perf_frame(perf: object) -> pd.DataFrame:
    df = pd.DataFrame(perf if isinstance(perf, list) else [])
    if df.empty:
        return df
    df["series"] = df["model_name"].map(lambda m: LABEL.get(m, m))
    df["kind"] = df["model_name"].map(lambda m: "ML model" if "lightgbm" in m else "Baseline")
    return df


def line_chart(df: pd.DataFrame, y_title: str, height: int = 340) -> alt.Chart:
    """Hour-by-hour lines with the hierarchy scales and a crosshair hover."""
    hover = alt.selection_point(fields=["hour"], nearest=True, on="mouseover", empty=False)
    base = alt.Chart(df).encode(
        x=alt.X("hour:T", title="Hour (Lisbon)", axis=alt.Axis(format="%H:%M")),
        y=alt.Y("value:Q", title=y_title, scale=alt.Scale(zero=False)),
        color=alt.Color("series:N", scale=COLOR, legend=alt.Legend(title=None, orient="top")),
    )
    line = base.mark_line().encode(
        strokeWidth=alt.StrokeWidth("series:N", scale=_WIDTH, legend=None),
        strokeDash=alt.StrokeDash("series:N", scale=_DASH, legend=None),
    )
    points = base.mark_point(size=45, filled=True).transform_filter(hover)
    rule = (
        alt.Chart(df)
        .mark_rule(color="#898781")
        .encode(x="hour:T", tooltip=["series:N", alt.Tooltip("value:Q", format=".1f")])
        .add_params(hover)
    )
    return (line + points + rule).properties(height=height)


def history_chart(df: pd.DataFrame, y_title: str, height: int = 420) -> alt.Chart:
    """Like line_chart but over days: date-aware x axis for the multi-week record."""
    hover = alt.selection_point(fields=["hour"], nearest=True, on="mouseover", empty=False)
    base = alt.Chart(df).encode(
        x=alt.X("hour:T", title="Day", axis=alt.Axis(format="%d %b")),
        y=alt.Y("value:Q", title=y_title, scale=alt.Scale(zero=False)),
        color=alt.Color("series:N", scale=COLOR, legend=alt.Legend(title=None, orient="top")),
    )
    line = base.mark_line().encode(
        strokeWidth=alt.StrokeWidth("series:N", scale=_WIDTH, legend=None),
        strokeDash=alt.StrokeDash("series:N", scale=_DASH, legend=None),
    )
    rule = (
        alt.Chart(df)
        .mark_rule(color="#898781", opacity=0.001)
        .encode(
            x="hour:T",
            tooltip=[
                alt.Tooltip("hour:T", format="%d %b %H:%M"),
                "series:N",
                alt.Tooltip("value:Q", format=".1f"),
            ],
        )
        .add_params(hover)
    )
    return (line + rule).properties(height=height)


def mae_bars(perf: pd.DataFrame, unit: str) -> alt.Chart:
    return (
        alt.Chart(perf)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("mae:Q", title=f"Realised MAE ({unit})"),
            y=alt.Y("series:N", sort="x", title=None),
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(domain=["ML model", "Baseline"], range=[C_MODEL, "#898781"]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=["series:N", alt.Tooltip("mae:Q", format=".2f"), "n:Q"],
        )
        .properties(height=180)
    )


def footer() -> None:
    """Attribution (CC BY licence requirement) + the portfolio links, on every page."""
    st.divider()
    st.caption(
        f"Open source — [code, tests & ADRs on GitHub]({GITHUB_URL}) · "
        f"[API docs]({API_BASE_URL}/docs) · zero-cost stack (GitHub Actions + Neon + Render + "
        "Streamlit Cloud)  \n"
        "Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0) · "
        "Spanish power data by [Energy-Charts / Fraunhofer ISE](https://www.energy-charts.info/) "
        "(CC BY 4.0) · Portuguese system data from the "
        "[REN Data Hub](https://datahub.ren.pt/) · Market prices from "
        "[OMIE](https://www.omie.es/)."
    )
