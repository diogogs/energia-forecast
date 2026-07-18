"""Tomorrow page — the information product: what tomorrow looks like, at a glance.

This page answers a reader's questions — when is power cheap tomorrow? is it dearer than
usual? when is the demand peak? how is today tracking? — in plain language, with none of
the methodology vocabulary (that story lives under "About the system"). One deliberate
behaviour: once the day-ahead auction clears (~13h CET) the OFFICIAL prices replace the
morning forecast as this page's truth, labelled as such, with the forecast's miss shown
next to them — the honest thing to display is whichever is the best information now.
"""

from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st
from common import (
    C_ACTUAL,
    C_MODEL,
    api_or_none,
    cold_start_stop,
    footer,
    hero_card,
    live_scored,
    local,
    market_day,
)

# Info-product palette: forecast/official reuse the system heroes; cheap/expensive are
# semantic (traffic-light, muted) and appear only on this page's price strip and shading.
C_CHEAP, C_MID, C_DEAR = "#1baf7a", "#e4e8ed", "#c5524a"
INFO_SERIES = ["Forecast", "Official price", "So far"]
INFO_COLOR = alt.Scale(domain=INFO_SERIES, range=[C_MODEL, C_ACTUAL, C_ACTUAL])

st.title("Tomorrow")

health = api_or_none("/health")
if not isinstance(health, dict) or "status" not in health:
    cold_start_stop()

price_fc = api_or_none("/forecast/price")
cons_fc = api_or_none("/forecast/consumption")
hist_price = api_or_none("/history/price?days=9")
hist_cons = api_or_none("/history/consumption?days=9")


def _forecast_frame(fc: object, quantiles: tuple[str, ...]) -> pd.DataFrame:
    if not isinstance(fc, dict) or "points" not in fc:
        return pd.DataFrame()
    df = pd.DataFrame(
        [p for p in fc["points"] if p["model_name"] == "lightgbm" and p["quantile"] in quantiles]
    )
    if df.empty:
        return df
    df["hour"] = local(df["target_ts"])
    return df


price_pts = _forecast_frame(price_fc, ("p50", "point"))
band_pts = _forecast_frame(price_fc, ("p10", "p90"))
cons_pts = _forecast_frame(cons_fc, ("point", "p50"))
if price_pts.empty or not isinstance(price_fc, dict):
    st.info("Tomorrow's forecast appears here after the morning emission (07:06 UTC).")
    footer()
    st.stop()

tomorrow = dt.date.fromisoformat(price_fc["issue_date"]) + dt.timedelta(days=1)

# ---------------------------------------------------------------- cleared-price switch
# /history pairs every emitted hour with the realised outcome; once the afternoon ingest
# lands the auction results, tomorrow's rows carry y_true — that is the switch signal.
hist_p = pd.DataFrame(hist_price if isinstance(hist_price, list) else [])
cleared = pd.DataFrame()
if not hist_p.empty:
    hp = hist_p[(hist_p.model_name == "lightgbm") & (hist_p["quantile"] == "p50")].copy()
    hp["day"] = market_day(hp["target_ts"])
    tm = hp[hp.day.dt.date == tomorrow].dropna(subset=["y_true"])
    if len(tm) >= 20:
        cleared = tm
auction_closed = not cleared.empty

# The page's working series for tomorrow's prices: official once available, else forecast.
if auction_closed:
    price_now = cleared.rename(columns={"y_true": "value"})[["target_ts", "value"]].copy()
else:
    price_now = price_pts.rename(columns={"y_hat": "value"})[["target_ts", "value"]].copy()
price_now["hour"] = local(price_now["target_ts"])
price_now = price_now.sort_values("hour").reset_index(drop=True)


def _window(series: pd.DataFrame, hours: int, cheapest: bool) -> tuple[str, float]:
    """('13:00-16:00', mean €) for the best/worst ``hours``-long contiguous window."""
    hours = min(hours, len(series))
    roll = series.value.rolling(hours).mean()
    idx = int(roll.idxmin() if cheapest else roll.idxmax())
    start = series.hour.iloc[idx - hours + 1]
    end = series.hour.iloc[idx] + pd.Timedelta(hours=1)
    return f"{start:%H:%M}-{end:%H:%M}", float(roll.iloc[idx])


cheap_label, cheap_mean = _window(price_now, 3, cheapest=True)
dear_label, dear_mean = _window(price_now, 3, cheapest=False)

# Last-7-market-days realised average price — the reader's "is tomorrow dear?" reference.
week_avg = None
if not hist_p.empty:
    past = hp[(hp.day.dt.date < tomorrow)].dropna(subset=["y_true"])
    last7 = sorted(past.day.unique())[-7:]
    if last7:
        week_avg = float(past[past.day.isin(last7)].y_true.mean())

# ---------------------------------------------------------------- hero + KPIs
src_word = "official auction prices" if auction_closed else "this morning's forecast"
hero_card(
    f"Tomorrow · {tomorrow:%A, %d %B}",
    f"Cheapest power <b>{cheap_label}</b> &nbsp;·&nbsp; dearest <b>{dear_label}</b>",
    f"Based on {src_word}. Times in Europe/Lisbon.",
)

avg_now = float(price_now.value.mean())
k1, k2, k3, k4 = st.columns(4)
delta = f"{avg_now / week_avg - 1:+.0%} vs last 7 days" if week_avg else ""
k1.metric("Average price", f"{avg_now:.0f} €/MWh", delta, delta_color="inverse")
k2.metric("Cheapest 3 hours", cheap_label, f"~{cheap_mean:.0f} €/MWh", delta_color="off")
k3.metric("Most expensive 3 hours", dear_label, f"~{dear_mean:.0f} €/MWh", delta_color="off")
if not cons_pts.empty:
    peak = cons_pts.loc[cons_pts.y_hat.idxmax()]
    k4.metric("Demand peak", f"{peak.y_hat / 1000:.1f} GW", f"around {peak.hour:%H:%M}",
              delta_color="off")  # fmt: skip

# ---------------------------------------------------------------- price, hour by hour
st.subheader("Electricity price, hour by hour")
if auction_closed:
    miss = float((cleared.y_hat - cleared.y_true).abs().mean())
    st.caption(
        f"The day-ahead auction for tomorrow has **closed** — the chart shows the official "
        f"prices. This morning's forecast, issued before the auction, was off by "
        f"~{miss:.0f} €/MWh on average; every miss is kept on the public record."
    )

lines = [price_now.assign(series="Official price" if auction_closed else "Forecast")]
if auction_closed:
    lines.append(price_pts.rename(columns={"y_hat": "value"}).assign(series="Forecast"))
price_lines = pd.concat(lines)[["hour", "series", "value"]]

layers: list[alt.Chart] = []
terciles = price_now.value.quantile([1 / 3, 2 / 3])
strip = price_now.assign(
    band=pd.cut(
        price_now.value,
        [-float("inf"), terciles.iloc[0], terciles.iloc[1], float("inf")],
        labels=["Cheap", "Mid", "Expensive"],
    ).astype(str)
)
layers.append(
    alt.Chart(strip)
    .mark_rect(opacity=0.10)
    .encode(
        x="hour:T",
        x2="hour_end:T",
        color=alt.Color(
            "band:N",
            scale=alt.Scale(domain=["Cheap", "Mid", "Expensive"], range=[C_CHEAP, C_MID, C_DEAR]),
            legend=None,
        ),
    )
    .transform_calculate(hour_end="datum.hour + 3600000")
)
if not auction_closed and not band_pts.empty:
    band = band_pts.pivot_table(index="hour", columns="quantile", values="y_hat").reset_index()
    if {"p10", "p90"} <= set(band.columns):
        layers.append(
            alt.Chart(band)
            .mark_area(opacity=0.15, color=C_MODEL)
            .encode(x="hour:T", y="p10:Q", y2="p90:Q")
        )
hover = alt.selection_point(fields=["hour"], nearest=True, on="mouseover", empty=False)
base = alt.Chart(price_lines).encode(
    x=alt.X("hour:T", title="Hour (Lisbon)", axis=alt.Axis(format="%H:%M")),
    y=alt.Y("value:Q", title="€/MWh", scale=alt.Scale(zero=False)),
    color=alt.Color("series:N", scale=INFO_COLOR, legend=alt.Legend(title=None, orient="top")),
)
# When the auction has closed, the official line is solid and the kept-for-comparison
# forecast goes dashed; before that there is only one series, drawn solid.
dash = (
    alt.condition(alt.datum.series == "Forecast", alt.value([5, 3]), alt.value([1, 0]))
    if auction_closed
    else alt.value([1, 0])
)
layers.append(base.mark_line(strokeWidth=2.6).encode(strokeDash=dash))
layers.append(base.mark_point(size=45, filled=True).transform_filter(hover))
layers.append(
    alt.Chart(price_lines)
    .mark_rule(color="#898781", opacity=0.001)
    .encode(x="hour:T", tooltip=["series:N", alt.Tooltip("value:Q", format=".1f")])
    .add_params(hover)
)
with st.container(border=True):
    st.altair_chart(alt.layer(*layers).properties(height=320), width="stretch")
if not auction_closed:
    st.caption(
        "The shaded blue area is the likely range — 8 times out of 10 the price lands "
        "inside it. Official prices replace this forecast when the auction closes "
        "(~13:00 Lisbon). Background tint: green = cheapest third of the day, red = most "
        "expensive third."
    )
else:
    st.caption(
        "Background tint: green = cheapest third of the day, red = most expensive third. "
        "The dashed blue line is the morning forecast, kept for comparison."
    )

# ---------------------------------------------------------------- demand
st.subheader("Electricity demand")
if not cons_pts.empty:
    dem = cons_pts.rename(columns={"y_hat": "value"}).assign(series="Forecast")
    with st.container(border=True):
        st.altair_chart(
            alt.Chart(dem[["hour", "series", "value"]])
            .mark_line(strokeWidth=2.6, color=C_MODEL)
            .encode(
                x=alt.X("hour:T", title="Hour (Lisbon)", axis=alt.Axis(format="%H:%M")),
                y=alt.Y("value:Q", title="Demand (MW)", scale=alt.Scale(zero=False)),
                tooltip=[
                    alt.Tooltip("hour:T", format="%H:%M"),
                    alt.Tooltip("value:Q", format=",.0f"),
                ],
            )
            .properties(height=220),
            width="stretch",
        )
    st.caption(
        "National electricity demand forecast for Portugal, mainland. Demand shapes the "
        "price: the evening peak is usually the expensive window; the solar hours, the cheap one."
    )

# ---------------------------------------------------------------- today so far
st.subheader("Today so far")
today_cols = st.columns(2)
for col, hist, title, unit in (
    (today_cols[0], hist_cons, "Demand", "MW"),
    (today_cols[1], hist_price, "Price", "€/MWh"),
):
    df = pd.DataFrame(hist if isinstance(hist, list) else [])
    if df.empty:
        continue
    df = df[(df.model_name == "lightgbm") & (df["quantile"].isin(["point", "p50"]))].copy()
    df["day"] = market_day(df["target_ts"])
    today_market = pd.Timestamp.now(tz="Europe/Madrid").date()
    day_df = df[df.day.dt.date == today_market].copy()
    if day_df.empty:
        continue
    day_df["hour"] = local(day_df["target_ts"])
    frame = pd.concat(
        [
            day_df.assign(series="Forecast", value=day_df.y_hat),
            day_df.dropna(subset=["y_true"]).assign(series="So far", value=day_df.y_true),
        ]
    )[["hour", "series", "value"]]
    with col:
        with st.container(border=True):
            st.altair_chart(
                alt.Chart(frame)
                .mark_line(strokeWidth=2.2)
                .encode(
                    x=alt.X("hour:T", title=None, axis=alt.Axis(format="%H:%M")),
                    y=alt.Y("value:Q", title=f"{title} ({unit})", scale=alt.Scale(zero=False)),
                    color=alt.Color(
                        "series:N", scale=INFO_COLOR, legend=alt.Legend(title=None, orient="top")
                    ),
                    tooltip=["series:N", alt.Tooltip("value:Q", format=",.1f")],
                )
                .properties(height=210),
                width="stretch",
            )
        scored = day_df.dropna(subset=["y_true"])
        if not scored.empty:
            off = (scored.y_hat - scored.y_true).abs().mean()
            st.caption(
                f"{title}: tracking within ~{off:,.0f} {unit} of yesterday's forecast "
                f"({len(scored)} h in)."
            )

# ---------------------------------------------------------------- accuracy, in words
acc_bits = []
for hist, unit, fmt in ((hist_cons, "MW", ",.0f"), (hist_price, "€/MWh", ",.1f")):
    scored = live_scored(hist)
    if not scored.empty:
        days7 = sorted(scored.day.unique())[-7:]
        mae = scored[scored.day.isin(days7)].abs_err.mean()
        acc_bits.append(f"~{mae:{fmt}} {unit}")
if len(acc_bits) == 2:
    st.markdown(
        f"Over the last 7 days these forecasts were typically within **{acc_bits[0]}** "
        f"(demand) and **{acc_bits[1]}** (price) of the outcome — every day since launch "
        "is scored in public."
    )
st.page_link("views/track_record.py", label="Full track record", icon=":material/fact_check:")
st.caption(
    "New forecasts every morning at 07:06 UTC, before the day-ahead auction. "
    "How it all works: see About the system."
)

footer()
