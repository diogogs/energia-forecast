"""Performance page — backtest history, interval calibration, error structure, baselines."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from common import (
    C_MODEL,
    COLOR,
    LABEL,
    api_or_none,
    cold_start_stop,
    footer,
    history_chart,
    local,
    mae_bars,
    perf_frame,
)

st.title("Forecast performance")
st.markdown(
    "All history on this page comes from a rolling-origin backtest: for each day shown, the "
    "model was trained only on data published before that morning's 07:00 UTC cutoff, then "
    "predicted the following day. Past predictions are stored and never regenerated with a "
    "newer model."
)

TARGETS = {
    "consumption": ("Demand", "MW", "lightgbm"),
    "price": ("MIBEL price", "€/MWh", "lightgbm_p50"),
}
target = st.radio(
    "Target",
    list(TARGETS),
    format_func=lambda t: TARGETS[t][0],
    horizontal=True,
    label_visibility="collapsed",
)
title, unit, model_name = TARGETS[target]

days = st.slider("Window (days)", min_value=7, max_value=84, value=21, step=7)
show_baselines = st.toggle("Show baselines", value=False)

bt = api_or_none(f"/backtest/{target}?days={days}")
if bt is None:
    cold_start_stop()

df = pd.DataFrame(bt if isinstance(bt, list) else [])
if df.empty:
    st.info("No backtest history available yet.")
else:
    df["hour"] = local(df["target_ts"])
    scored_all = df.dropna(subset=["y_true"])

    # ---------------------------------------------------------------- history (+ band)
    actual = scored_all.drop_duplicates("target_ts").assign(
        series="Actual", value=lambda d: d.y_true
    )[["hour", "series", "value"]]
    wanted = [model_name] + (
        [m for m in df.model_name.unique() if m in LABEL and "lightgbm" not in m]
        if show_baselines
        else []
    )
    lines = df[df.model_name.isin(wanted)].assign(
        series=lambda d: d.model_name.map(LABEL), value=lambda d: d.y_hat
    )[["hour", "series", "value"]]
    hist = history_chart(pd.concat([actual, lines]), f"{title} ({unit})")

    band = pd.DataFrame()
    if target == "price":
        piv = df.pivot_table(index="hour", columns="model_name", values="y_hat")
        if {"lightgbm_p10", "lightgbm_p90"} <= set(piv.columns):
            truth = scored_all.drop_duplicates("hour").set_index("hour")["y_true"].rename("y_true")
            band = piv[["lightgbm_p10", "lightgbm_p90"]].join(truth)
            area = (
                alt.Chart(band.reset_index())
                .mark_area(opacity=0.14, color=C_MODEL)
                .encode(
                    x="hour:T",
                    y=alt.Y("lightgbm_p10:Q", title=None),
                    y2="lightgbm_p90:Q",
                )
            )
            hist = area + hist
    with st.container(border=True):
        st.altair_chart(hist, width="stretch")
    st.caption(
        f"Each point is a backtest fold: forecast issued at 07:00 UTC the previous day, "
        f"scored against the outcome. Showing the last {days} days."
        + (" The shaded band is the P10-P90 interval as issued." if not band.empty else "")
    )

    # ---------------------------------------------------------------- interval calibration
    if target == "price" and not band.empty:
        st.subheader("Interval calibration")
        scored_band = band.dropna(subset=["y_true"])
        inside = (scored_band.y_true >= scored_band.lightgbm_p10) & (
            scored_band.y_true <= scored_band.lightgbm_p90
        )
        c1, c2 = st.columns([1, 2])
        c1.metric("P10-P90 empirical coverage", f"{inside.mean():.0%}", "target: 80%")
        c1.caption(
            "Conformally calibrated (CQR) on a trailing window; price regimes shift faster "
            "than the calibration window can adapt, hence the gap to target."
        )
        daily = inside.groupby(pd.Index(scored_band.index.date, name="day")).mean()
        rolling = daily.rolling(7, min_periods=4).mean().dropna()
        if not rolling.empty:
            cov = pd.DataFrame(
                {"day": pd.to_datetime(rolling.index), "coverage": rolling.to_numpy()}
            )
            line = (
                alt.Chart(cov)
                .mark_line(color=C_MODEL, strokeWidth=2.4)
                .encode(
                    x=alt.X("day:T", title="Day"),
                    y=alt.Y(
                        "coverage:Q",
                        title="Rolling 7-day coverage",
                        axis=alt.Axis(format=".0%"),
                        scale=alt.Scale(domain=[0, 1]),
                    ),
                    tooltip=[
                        alt.Tooltip("day:T", format="%d %b"),
                        alt.Tooltip("coverage:Q", format=".0%"),
                    ],
                )
            )
            target_rule = (
                alt.Chart(pd.DataFrame({"y": [0.8]}))
                .mark_rule(strokeDash=[5, 3], color="#898781")
                .encode(y="y:Q")
            )
            c2.altair_chart((line + target_rule).properties(height=200), width="stretch")
            c2.caption(
                "Share of hours whose outcome fell inside the issued band, 7-day rolling. "
                "The dashed line is the 80% design target."
            )

    # ---------------------------------------------------------------- error by hour
    prof_pool = scored_all[scored_all.model_name.isin([model_name, *LABEL])].copy()
    base_names = [m for m in prof_pool.model_name.unique() if "lightgbm" not in m]
    if base_names:
        st.subheader("Error by hour of day")

        def _mae(name: str) -> float:
            d = prof_pool[prof_pool.model_name == name]
            return float((d.y_hat - d.y_true).abs().mean())

        best_base = min(base_names, key=_mae)
        prof = prof_pool[prof_pool.model_name.isin([model_name, best_base])].copy()
        prof["series"] = prof.model_name.map(LABEL)
        prof["abs_err"] = (prof.y_hat - prof.y_true).abs()
        prof["hour_of_day"] = prof["hour"].dt.hour
        agg = prof.groupby(["hour_of_day", "series"], as_index=False)["abs_err"].mean()
        with st.container(border=True):
            st.altair_chart(
                alt.Chart(agg)
                .mark_line(point=True, strokeWidth=2.2)
                .encode(
                    x=alt.X("hour_of_day:O", title="Delivery hour (Lisbon)"),
                    y=alt.Y("abs_err:Q", title=f"MAE ({unit})"),
                    color=alt.Color(
                        "series:N",
                        scale=COLOR,
                        # The shared scale fixes hue-per-series; without explicit values the
                        # legend would list the full domain, including series not plotted here.
                        legend=alt.Legend(
                            title=None, orient="top", values=sorted(agg.series.unique())
                        ),
                    ),
                    tooltip=[
                        "series:N",
                        "hour_of_day:O",
                        alt.Tooltip("abs_err:Q", format=".1f"),
                    ],
                )
                .properties(height=240),
                width="stretch",
            )
        st.caption(
            "Mean absolute error per delivery hour over the selected window, model vs the "
            "strongest naive baseline. This is where the model earns (or doesn't earn) its keep."
        )

# ---------------------------------------------------------------- MAE vs baselines
st.subheader("Error vs baselines (full backtest)")
perf = perf_frame(api_or_none(f"/performance/{target}"))
if not perf.empty:
    headline = perf[
        perf.model_name.isin([model_name, *[m for m in perf.model_name if "lightgbm" not in m]])
    ]
    st.altair_chart(mae_bars(headline, unit), width="stretch")
    model_mae = float(perf.loc[perf.model_name == model_name, "mae"].iloc[0])
    base = perf.loc[~perf.model_name.str.contains("lightgbm"), "mae"]
    if not base.empty:
        st.markdown(
            f"Over all 71 held-out days the model's MAE is **{model_mae:,.1f} {unit}**, "
            f"{1 - model_mae / float(base.min()):.0%} lower than the best naive baseline "
            f"({float(base.min()):,.1f}). Models are only published when they outperform "
            "both baselines on the same folds."
        )

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
