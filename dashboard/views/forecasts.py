"""Forecasts page — tomorrow's forecasts, today's forecast vs reality, headline quality."""

from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st
from common import (
    LABEL,
    LISBON,
    api_or_none,
    cold_start_stop,
    footer,
    forecast_lines,
    line_chart,
    local,
    perf_frame,
    price_band,
)

st.title("Day-ahead forecasts")
st.markdown(
    "Hourly forecasts of Portuguese electricity demand and the MIBEL day-ahead price for "
    "tomorrow's market day, issued each morning at 07:05 UTC, before the 12:00 CET auction. "
    "Every forecast is recorded at issue time and scored against the observed outcome."
)

health = api_or_none("/health")
if not isinstance(health, dict) or "status" not in health:
    cold_start_stop()

cons = api_or_none("/forecast/consumption")
price = api_or_none("/forecast/price")
cons_perf = perf_frame(api_or_none("/performance/consumption"))
price_perf = perf_frame(api_or_none("/performance/price"))


def _model_points(forecast: object) -> pd.DataFrame:
    if not isinstance(forecast, dict) or "points" not in forecast:
        return pd.DataFrame()
    df = pd.DataFrame(
        [
            p
            for p in forecast["points"]
            if p["model_name"] == "lightgbm" and p["quantile"] in ("point", "p50")
        ]
    )
    if not df.empty:
        df["hour"] = local(df["target_ts"])
    return df


def _mae_delta(perf: pd.DataFrame, model_name: str) -> tuple[float, str] | None:
    """(model MAE, 'against best baseline' delta string) from the /performance frame."""
    if perf.empty or model_name not in set(perf.model_name):
        return None
    model_mae = float(perf.loc[perf.model_name == model_name, "mae"].iloc[0])
    baselines = perf.loc[perf.model_name != model_name, "mae"]
    if baselines.empty:
        return model_mae, ""
    return model_mae, f"{model_mae / float(baselines.min()) - 1:+.0%} vs best baseline"


# ---------------------------------------------------------------- hero
cons_pts, price_pts = _model_points(cons), _model_points(price)
if not cons_pts.empty and isinstance(cons, dict):
    market_day = dt.date.fromisoformat(cons["issue_date"]) + dt.timedelta(days=1)
    peak = cons_pts.loc[cons_pts.y_hat.idxmax()]
    headline = (
        f"#### {market_day:%A, %d %B}: peak demand **{peak.y_hat / 1000:.1f} GW** "
        f"around {peak.hour:%H:%M}"
    )
    if not price_pts.empty:
        p50 = price_pts.y_hat.mean()
        band = price_band(price["points"]) if isinstance(price, dict) else pd.DataFrame()
        if not band.empty:
            headline += (
                f", average price **{p50:.0f} €/MWh** "
                f"(80% interval {band.p10.mean():.0f}-{band.p90.mean():.0f})"
            )
        else:
            headline += f", average price **{p50:.0f} €/MWh**"
    st.markdown(headline)
    issued = pd.to_datetime(cons.get("issued_at")) if cons.get("issued_at") else None
    when = f"at {issued:%H:%M} UTC" if issued is not None else "this morning"
    st.caption(
        f"Issued {when}, before the 12:00 CET day-ahead auction. Forecasts are stored with "
        "their issue timestamp and never revised."
    )

# ---------------------------------------------------------------- today vs actual


def _today_vs_actual(target: str, y_title: str) -> None:
    """Today's delivery (forecast issued yesterday) with actuals overlaid as they arrive."""
    hist = api_or_none(f"/history/{target}?days=3")
    if not isinstance(hist, list) or not hist:
        st.caption("The live forecast-vs-actual view will appear here as the record builds.")
        return
    df = pd.DataFrame(hist)
    # NB: df["quantile"] — attribute access would hit the DataFrame.quantile method.
    df = df[(df.model_name == "lightgbm") & (df["quantile"].isin(["point", "p50"]))]
    df["hour"] = local(df["target_ts"])
    today = pd.Timestamp.now(tz=LISBON).date()
    df = df[df.hour.dt.date == today]
    if df.empty:
        st.caption("No forecast covers today yet; the record fills in from tomorrow morning.")
        return
    lines = pd.concat(
        [
            df.assign(series="Model", value=df.y_hat)[["hour", "series", "value"]],
            df.dropna(subset=["y_true"]).assign(series="Actual", value=df.y_true)[
                ["hour", "series", "value"]
            ],
        ]
    )
    st.altair_chart(line_chart(lines, y_title, height=300), width="stretch")
    if df.y_true.notna().any():
        scored = df.dropna(subset=["y_true"])
        mae = (scored.y_hat - scored.y_true).abs().mean()
        st.caption(
            f"Yesterday's forecast for today, scored live: MAE {mae:.0f} over "
            f"{len(scored)} h so far. Actuals continue to arrive through the day."
        )
    else:
        st.caption("Actuals for today arrive with the next data ingest.")


# ---------------------------------------------------------------- tabs
tab_demand, tab_price = st.tabs(["Demand (D+1)", "MIBEL price (D+1)"])

with tab_demand:
    st.subheader("Hourly forecast for tomorrow")
    if isinstance(cons, dict) and "points" in cons:
        lines = forecast_lines(cons["points"], LABEL)
        if not lines.empty:
            st.altair_chart(line_chart(lines, "Demand (MW)"), width="stretch")
    delta = _mae_delta(cons_perf, "lightgbm")
    if delta:
        c1, c2 = st.columns([1, 2])
        c1.metric("Backtest MAE (10 weeks)", f"{delta[0]:.0f} MW", delta[1], delta_color="inverse")
        c2.caption(
            "Mean absolute error over 71 held-out days (rolling-origin backtest, see the "
            "Performance page). The model roughly halves the error of the strongest naive "
            "baseline; MAPE is about 2.8%."
        )
    st.subheader("Today: forecast vs actuals")
    _today_vs_actual("consumption", "Demand (MW)")

with tab_price:
    st.subheader("Hourly forecast for tomorrow")
    if isinstance(price, dict) and "points" in price:
        lines = forecast_lines(price["points"], LABEL)
        band = price_band(price["points"])
        if not lines.empty:
            chart = line_chart(lines, "Price (€/MWh)")
            if not band.empty:
                area = (
                    alt.Chart(band)
                    .mark_area(opacity=0.15, color="#2a78d6")
                    .encode(x="hour:T", y="p10:Q", y2="p90:Q")
                )
                chart = area + chart
            st.altair_chart(chart, width="stretch")
            st.caption(
                "The shaded band is the P10-P90 interval, conformally calibrated (CQR). "
                "The point forecast is the P50 of a quantile-regression triplet."
            )
    delta = _mae_delta(price_perf, "lightgbm_p50")
    if delta:
        c1, c2 = st.columns([1, 2])
        c1.metric(
            "Backtest P50 MAE (10 weeks)", f"{delta[0]:.2f} €/MWh", delta[1], delta_color="inverse"
        )
        c2.caption(
            "Prices are considerably noisier than demand: negative prices, renewables-driven "
            "spikes, regime shifts. Beating persistence is the meaningful bar here."
        )
    st.subheader("Today: forecast vs cleared prices")
    _today_vs_actual("price", "Price (€/MWh)")

with st.expander("About the baselines"):
    st.markdown(
        "- **Persistence**: tomorrow repeats the most recent legally usable day — demand at "
        "the same hour 48 h earlier (the current day is still incomplete at the 07:00 UTC "
        "issue time), price 24 h earlier (day-ahead prices are published the day before).\n"
        "- **Weekly seasonal**: tomorrow repeats the same hour one week earlier.\n\n"
        "These naive rules are strong benchmarks in power systems. A model is only published "
        "if it beats both on the same folds; otherwise the dashboard would show the baseline, "
        "labelled as such."
    )

footer()
