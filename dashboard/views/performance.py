"""Performance page — the receipts: simulated history, MAE vs baselines, interval coverage."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from common import (
    LABEL,
    api_or_none,
    cold_start_stop,
    footer,
    history_chart,
    local,
    mae_bars,
    perf_frame,
)

st.title("📈 Performance — the receipts")
st.markdown(
    "Ten weeks of **simulated history**. For every day shown, the model was retrained using "
    "only data *published before that morning's 07:00 UTC cutoff*, then predicted the next day "
    "— a **rolling-origin backtest** with weekly refresh. No hindsight, no leakage; no chart on "
    "this page ever re-predicts the past with a newer model."
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

    # Actual: y_true is repeated across models per hour — take it once.
    actual = (
        df.dropna(subset=["y_true"])
        .drop_duplicates("target_ts")
        .assign(series="Actual", value=lambda d: d.y_true)[["hour", "series", "value"]]
    )
    wanted = [model_name] + (
        [m for m in df.model_name.unique() if m in LABEL and "lightgbm" not in m]
        if show_baselines
        else []
    )
    lines = df[df.model_name.isin(wanted)].assign(
        series=lambda d: d.model_name.map(LABEL), value=lambda d: d.y_hat
    )[["hour", "series", "value"]]
    st.altair_chart(
        history_chart(pd.concat([actual, lines]), f"{title} ({unit})"),
        width="stretch",
    )
    st.caption(
        f"Each point is a real fold: forecast issued at 07:00 UTC the previous day, "
        f"scored against the outcome. Window: last {days} days of the backtest."
    )

    # ---------------------------------------------------------------- interval coverage (price)
    if target == "price":
        piv = df.pivot_table(index="target_ts", columns="model_name", values="y_hat")
        if {"lightgbm_p10", "lightgbm_p90"} <= set(piv.columns):
            truth = df.drop_duplicates("target_ts").set_index("target_ts")["y_true"]
            piv = piv.join(truth.rename("y_true")).dropna(subset=["y_true"])
            inside = (piv.y_true >= piv.lightgbm_p10) & (piv.y_true <= piv.lightgbm_p90)
            c1, c2 = st.columns([1, 2])
            c1.metric("P10-P90 empirical coverage", f"{inside.mean():.0%}", "target: 80%")
            c2.caption(
                "The interval is conformally calibrated (CQR) on a trailing window. Honest "
                "residual: coverage sits below the 80% target because price regimes shift "
                "faster than the calibration window — documented, not hidden."
            )

# ---------------------------------------------------------------- MAE vs baselines
st.subheader("Model vs naive baselines — whole backtest")
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
            f"Over all 71 held-out days the model's MAE is **{model_mae:,.1f} {unit}** — "
            f"**{1 - model_mae / float(base.min()):.0%} lower** than the best naive baseline "
            f"({float(base.min()):,.1f}). A model that can't beat these rules doesn't ship."
        )

footer()
