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

# Injected once from app.py. Navy sidebar and hero echo the print CV and the personal site;
# metrics become cards; Streamlit chrome recedes. Selectors use stable data-testid hooks.
BRAND_CSS = """
<style>
/* layout */
.block-container { padding-top: 2.4rem; max-width: 62rem; }

/* headings: tighter, branded ink, no anchor-link icons */
h1 { font-weight: 700; letter-spacing: -0.01em; }
[data-testid="stHeadingWithActionElements"] a { display: none; }

/* sidebar: navy identity block, light text */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #1e2a38 0%, #274a70 130%);
}
[data-testid="stSidebar"] * { color: #dfe7f0; }
[data-testid="stSidebar"] a { color: #9ec4e8 !important; }
[data-testid="stSidebarNav"] a span { color: #eef3f9; font-weight: 500; }
[data-testid="stSidebarNav"] a:hover { background: rgba(255, 255, 255, 0.08); }
[data-testid="stSidebarNav"] a[aria-current="page"] {
  background: rgba(255, 255, 255, 0.14);
  border-radius: 6px;
}

/* metrics as cards with an accent spine */
[data-testid="stMetric"] {
  background: #f7f9fb;
  border: 1px solid #e2e6eb;
  border-left: 3px solid #2a5a8c;
  border-radius: 8px;
  padding: 0.85rem 1rem;
}
[data-testid="stMetricLabel"] p {
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #6b7686;
}
[data-testid="stMetricValue"] { font-size: 1.55rem; }

/* tabs: stronger selected state */
button[data-baseweb="tab"] { font-weight: 600; }

/* dataframes and bordered containers: soft card edges */
[data-testid="stDataFrame"] { border: 1px solid #e2e6eb; border-radius: 8px; }
[data-testid="stVerticalBlockBorderWrapper"] > div {
  border-radius: 10px;
}

/* hero card (Forecasts page) */
.ef-hero {
  background: linear-gradient(135deg, #1e2a38 0%, #2a5a8c 100%);
  color: #ffffff;
  border-radius: 12px;
  padding: 1.3rem 1.5rem 1.2rem;
  margin: 0.3rem 0 1.1rem;
}
.ef-hero .kicker {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: #9ec4e8;
  margin-bottom: 0.35rem;
}
.ef-hero .headline {
  font-size: 1.35rem;
  font-weight: 650;
  line-height: 1.35;
}
.ef-hero .headline b { color: #ffffff; }
.ef-hero .sub {
  font-size: 0.82rem;
  color: #c6d3e2;
  margin-top: 0.5rem;
}
</style>
"""


def hero_card(kicker: str, headline_html: str, sub: str) -> None:
    """The Forecasts page's headline block, in the shared navy identity."""
    st.markdown(
        f'<div class="ef-hero"><div class="kicker">{kicker}</div>'
        f'<div class="headline">{headline_html}</div>'
        f'<div class="sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )


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


# The delivery day is the CET/CEST market day (charter): grouping hours by UTC or Lisbon
# date would split every market day across two dates and contaminate daily error figures.
MARKET_TZ = "Europe/Madrid"


def market_day(ts: pd.Series) -> pd.Series:
    """CET/CEST calendar date of each delivery hour — the market day one emission covers."""
    return pd.to_datetime(ts, utc=True).dt.tz_convert(MARKET_TZ).dt.normalize().dt.tz_localize(None)


def live_scored(hist: object, model: str = "lightgbm") -> pd.DataFrame:
    """Scored point-forecast rows of a /history payload, with market-day and abs error."""
    df = pd.DataFrame(hist if isinstance(hist, list) else [])
    if df.empty:
        return df
    df = df[(df.model_name == model) & (df["quantile"].isin(["point", "p50"]))]
    df = df.dropna(subset=["y_true"]).copy()
    if df.empty:
        return df
    df["day"] = market_day(df["target_ts"])
    df["abs_err"] = (df.y_hat - df.y_true).abs()
    return df


def daily_live_mae(hist: object, model: str = "lightgbm") -> pd.DataFrame:
    """(day, mae, hours) per market day — the production error record, one bar per delivery."""
    df = live_scored(hist, model)
    if df.empty:
        return df
    return df.groupby("day", as_index=False).agg(mae=("abs_err", "mean"), hours=("abs_err", "size"))


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
    base = alt.Chart(perf).encode(
        x=alt.X("mae:Q", title=f"Realised MAE ({unit})"),
        y=alt.Y("series:N", sort="x", title=None, axis=alt.Axis(labelLimit=160)),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=20).encode(
        color=alt.Color(
            "kind:N",
            scale=alt.Scale(domain=["ML model", "Baseline"], range=[C_MODEL, "#898781"]),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=["series:N", alt.Tooltip("mae:Q", format=".2f"), "n:Q"],
    )
    labels = base.mark_text(align="left", dx=5).encode(text=alt.Text("mae:Q", format=",.0f"))
    return (bars + labels).properties(height=190)


def footer() -> None:
    """Attribution (CC BY licence requirement) + project links, on every page."""
    st.divider()
    st.caption(
        f"[Source code]({GITHUB_URL}) · [API documentation]({API_BASE_URL}/docs)  \n"
        "Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0) · "
        "Spanish power data by [Energy-Charts / Fraunhofer ISE](https://www.energy-charts.info/) "
        "(CC BY 4.0) · Portuguese system data from the "
        "[REN Data Hub](https://datahub.ren.pt/) · Market prices from "
        "[OMIE](https://www.omie.es/)."
    )
