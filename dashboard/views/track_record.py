"""Track record page — the LIVE production record: what was forecast, what happened.

Everything here comes from forecasts emitted in production and scored against outcomes
(pred.predictions + realised data). Nothing is a backtest or a simulation — that history
lives on the Performance page, clearly labelled as such. This page exists because the only
evidence a visitor can actually judge is forecast-versus-reality, day after day.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from common import (
    C_MODEL,
    api_or_none,
    cold_start_stop,
    daily_live_mae,
    footer,
    line_chart,
    live_scored,
    local,
    market_day,
    perf_frame,
)

st.title("Track record")
st.markdown(
    "Every forecast this system has issued **in production**, scored against what actually "
    "happened. No backtests, no simulations — this is the live record, growing one market "
    "day at a time since launch (11 July 2026). Forecasts are written once, with their "
    "emission timestamp, and never revised."
)

# Delivery days up to this date belong to the system's first mornings, before the
# weather-ingestion fix of 11 July (delivery-day weather was missing at issue time).
# They stay on the chart — honestly, dimmed — rather than being trimmed off the record.
FIX_CUTOFF = pd.Timestamp("2026-07-12")

TARGETS = {
    "consumption": ("Demand", "MW", "lightgbm"),
    "price": ("MIBEL price", "€/MWh", "lightgbm_p50"),
}

hist = {t: api_or_none(f"/history/{t}?days=120") for t in TARGETS}
if hist["consumption"] is None and hist["price"] is None:
    cold_start_stop()
backtest_mae = {}
for t, (_, _, model_name) in TARGETS.items():
    perf = perf_frame(api_or_none(f"/performance/{t}"))
    if not perf.empty and model_name in set(perf.model_name):
        backtest_mae[t] = float(perf.loc[perf.model_name == model_name, "mae"].iloc[0])


def _last7(scored: pd.DataFrame) -> tuple[float, int] | None:
    """(MAE, days) over the last 7 fully-representative market days."""
    if scored.empty:
        return None
    days = sorted(scored.day.unique())[-7:]
    window = scored[scored.day.isin(days)]
    return float(window.abs_err.mean()), len(days)


# ---------------------------------------------------------------- headline
cols = st.columns(2)
for col, (t, (title, unit, _)) in zip(cols, TARGETS.items(), strict=True):
    scored = live_scored(hist[t])
    stat = _last7(scored) if not scored.empty else None
    if stat is None:
        col.metric(f"{title} — live MAE", "accumulating…")
        continue
    mae7, ndays = stat
    delta = ""
    if t in backtest_mae:
        delta = f"{mae7 / backtest_mae[t] - 1:+.0%} vs backtest ({backtest_mae[t]:,.0f})"
    col.metric(f"{title} — live MAE, last {ndays} market days", f"{mae7:,.1f} {unit}", delta,
               delta_color="inverse")  # fmt: skip
st.caption(
    "Live error of the production emissions over the most recent market days, next to the "
    "10-week backtest benchmark. The backtest said what the model *should* achieve; this row "
    "is what it *is* achieving."
)

# ---------------------------------------------------------------- per-target record
tab_demand, tab_price = st.tabs(["Demand", "MIBEL price"])

for tab, (t, (title, unit, model_name)) in zip((tab_demand, tab_price), TARGETS.items(),
                                               strict=True):  # fmt: skip
    with tab:
        daily = daily_live_mae(hist[t], model_name if t == "price" else "lightgbm")
        if isinstance(daily, pd.DataFrame) and not daily.empty:
            st.subheader("Error per delivery day")
            daily = daily.assign(
                phase=lambda d: d.day.le(FIX_CUTOFF).map(
                    {True: "First mornings (pre-fix)", False: "Steady state"}
                ),
                partial=lambda d: d.hours < 20,
            )
            bars = (
                alt.Chart(daily)
                .mark_bar(cornerRadiusEnd=3)
                .encode(
                    x=alt.X("day:T", title="Market day", axis=alt.Axis(format="%d %b")),
                    y=alt.Y("mae:Q", title=f"MAE ({unit})"),
                    opacity=alt.condition(
                        alt.datum.phase == "Steady state", alt.value(1.0), alt.value(0.45)
                    ),
                    color=alt.value(C_MODEL),
                    tooltip=[
                        alt.Tooltip("day:T", format="%d %b"),
                        alt.Tooltip("mae:Q", format=",.1f", title=f"MAE ({unit})"),
                        alt.Tooltip("hours:Q", title="hours scored"),
                        alt.Tooltip("phase:N", title="phase"),
                    ],
                )
            )
            chart: alt.LayerChart | alt.Chart = bars
            if t in backtest_mae:
                rule = (
                    alt.Chart(pd.DataFrame({"y": [backtest_mae[t]]}))
                    .mark_rule(strokeDash=[5, 3], color="#898781")
                    .encode(y="y:Q")
                )
                label = (
                    alt.Chart(
                        pd.DataFrame(
                            {"y": [backtest_mae[t]], "day": [daily.day.min()], "text": ["backtest"]}
                        )
                    )
                    .mark_text(align="left", dy=-7, color="#898781", fontSize=11)
                    .encode(x="day:T", y="y:Q", text="text:N")
                )
                chart = bars + rule + label
            with st.container(border=True):
                st.altair_chart(chart.properties(height=260), width="stretch")
            st.caption(
                "One bar per market day, dimmed bars are the system's first mornings "
                "(11-12 July), before the weather-ingestion fix — kept on the record rather "
                "than trimmed off it. The dashed line is the 10-week backtest MAE: bars below "
                "it mean production is beating its own rehearsal. Days with fewer than 20 "
                "scored hours are still filling in."
            )

            # ------------------------------------------------ replay
            st.subheader("Replay a delivery day")
            options = sorted(daily.day.dt.date.unique(), reverse=True)
            day = st.selectbox(
                "Market day",
                options,
                format_func=lambda d: f"{d:%A, %d %B %Y}",
                label_visibility="collapsed",
                key=f"replay_{t}",
            )
            scored = live_scored(hist[t], model_name if t == "price" else "lightgbm")
            day_rows = scored[scored.day.dt.date == day].copy()
            if not day_rows.empty:
                day_rows["hour"] = local(day_rows["target_ts"])
                lines = pd.concat(
                    [
                        day_rows.assign(series="Model", value=day_rows.y_hat),
                        day_rows.assign(series="Actual", value=day_rows.y_true),
                    ]
                )[["hour", "series", "value"]]
                chart = line_chart(lines, f"{title} ({unit})", height=300)
                if t == "price":
                    raw = pd.DataFrame(hist[t] if isinstance(hist[t], list) else [])
                    band = raw[raw.model_name.isin(["lightgbm"])].pivot_table(
                        index="target_ts", columns="quantile", values="y_hat"
                    )
                    if {"p10", "p90"} <= set(band.columns):
                        band = band.reset_index()
                        band = band[market_day(band["target_ts"]).dt.date == day]
                        band["hour"] = local(band["target_ts"])
                        area = (
                            alt.Chart(band)
                            .mark_area(opacity=0.15, color=C_MODEL)
                            .encode(x="hour:T", y="p10:Q", y2="p90:Q")
                        )
                        chart = area + chart
                with st.container(border=True):
                    st.altair_chart(chart, width="stretch")
                mae = day_rows.abs_err.mean()
                vs = (
                    f" — {mae / backtest_mae[t] - 1:+.0%} vs the backtest benchmark"
                    if t in backtest_mae
                    else ""
                )
                st.caption(
                    f"As issued at 07:05 UTC the day before, never revised. MAE for this day: "
                    f"**{mae:,.1f} {unit}** over {len(day_rows)} scored hours{vs}."
                )
        else:
            st.info("The live record appears here as emissions accumulate.")

# ---------------------------------------------------------------- emissions log
st.subheader("Emission log")
emissions = api_or_none("/emissions?days=120")
if isinstance(emissions, list) and emissions:
    em = pd.DataFrame(emissions)
    em["issued_at"] = pd.to_datetime(em["issued_at"], utc=True)
    pivot = em.pivot_table(
        index="issue_date", columns="target_name", values="issued_at", aggfunc="min"
    ).sort_index(ascending=False)
    late_by_day = em.groupby("issue_date")["late_issue"].any().sort_index(ascending=False)

    streak = 0
    for is_late in late_by_day:
        if is_late:
            break
        streak += 1
    c1, c2 = st.columns([1, 2])
    c1.metric("Consecutive punctual mornings", f"{streak}")
    c2.caption(
        "Days in a row, counting back from the latest emission, on which every forecast was "
        "issued on time (07:05 UTC target; anything more than 2 h late is flagged and "
        "excluded from the headline record). Late emissions are listed here, not hidden."
    )

    table = pd.DataFrame(index=pivot.index)
    for t in TARGETS:
        if t in pivot.columns:
            table[f"{t} issued (UTC)"] = pivot[t].dt.strftime("%H:%M:%S")
    table["status"] = late_by_day.map({False: "on time", True: "LATE"})
    st.dataframe(table.reset_index(names="issue day"), hide_index=True, width="stretch")
else:
    st.caption("The emission log will appear here once the updated API is deployed.")

st.caption(
    "Backtest history and interval calibration live on the Performance page; the daily "
    "cycle and temporal-integrity rules, on Methodology."
)

footer()
