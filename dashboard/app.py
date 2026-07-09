"""Streamlit dashboard — the public face of the energia-forecast system.

Reads the read-only FastAPI (API_BASE_URL) and shows the latest D+1 forecasts (consumption point
and MIBEL price P10/P50/P90 band), each model's realised MAE, and the simulated-history backtest.
Design follows the dataviz palette: model = blue, realised = orange, baselines = aqua/yellow.
"""

from __future__ import annotations

import os

import altair as alt
import httpx
import pandas as pd
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
LISBON = "Europe/Lisbon"

# Validated categorical palette (dataviz reference slots): realised, model, two baselines.
C_MODEL, C_REALISED, C_PERSIST, C_SEASONAL = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
COLOR = alt.Scale(
    domain=["Realizado", "Modelo", "Persistência", "Sazonal"],
    range=[C_REALISED, C_MODEL, C_PERSIST, C_SEASONAL],
)
_LABEL = {
    "lightgbm": "Modelo",
    "lightgbm_p50": "Modelo",
    "persistence_48h": "Persistência",
    "persistence_24h": "Persistência",
    "seasonal_168h": "Sazonal",
}


@st.cache_data(ttl=300)
def api(path: str) -> object:
    return httpx.get(f"{API_BASE_URL}{path}", timeout=20).json()


def _local(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, utc=True).dt.tz_convert(LISBON)


def forecast_lines(points: list[dict], models: dict[str, str]) -> pd.DataFrame:
    """Long frame (hora, serie, valor) for the named point-forecast models."""
    rows = [p for p in points if p["model_name"] in models and p["quantile"] in ("point", "p50")]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["hora"] = _local(df["target_ts"])
    df["serie"] = df["model_name"].map(_LABEL)
    return df[["hora", "serie", "y_hat"]].rename(columns={"y_hat": "valor"})


def price_band(points: list[dict]) -> pd.DataFrame:
    """Wide frame (hora, p10, p90) for the price interval band."""
    by_ts: dict[str, dict[str, float]] = {}
    for p in points:
        if p["model_name"] == "lightgbm" and p["quantile"] in ("p10", "p90"):
            by_ts.setdefault(p["target_ts"], {})[p["quantile"]] = p["y_hat"]
    df = pd.DataFrame([{"target_ts": ts, **qs} for ts, qs in by_ts.items()])
    if df.empty or "p10" not in df:
        return pd.DataFrame()
    df["hora"] = _local(df["target_ts"])
    return df[["hora", "p10", "p90"]].sort_values("hora")


def perf_frame(perf: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(perf)
    if df.empty:
        return df
    df["serie"] = df["model_name"].map(lambda m: _LABEL.get(m, m))
    df["tipo"] = df["model_name"].map(lambda m: "Modelo ML" if "lightgbm" in m else "Baseline")
    return df


def _line_chart(df: pd.DataFrame, y_title: str) -> alt.Chart:
    hover = alt.selection_point(fields=["hora"], nearest=True, on="mouseover", empty=False)
    base = alt.Chart(df).encode(
        x=alt.X("hora:T", title="Hora (Lisboa)", axis=alt.Axis(format="%H:%M")),
        y=alt.Y("valor:Q", title=y_title, scale=alt.Scale(zero=False)),
        color=alt.Color("serie:N", scale=COLOR, legend=alt.Legend(title=None, orient="top")),
    )
    line = base.mark_line(strokeWidth=2)
    points = base.mark_point(size=45, filled=True).transform_filter(hover)
    rule = (
        alt.Chart(df)
        .mark_rule(color="#898781")
        .encode(x="hora:T", tooltip=["serie:N", alt.Tooltip("valor:Q", format=".1f")])
        .add_params(hover)
    )
    return (line + points + rule).properties(height=340)


def main() -> None:
    st.set_page_config(page_title="Energia Forecast PT", page_icon="⚡", layout="wide")
    st.title("⚡ Observatório e Previsão de Energia — Portugal")
    st.caption(
        "Previsão *day-ahead* do consumo nacional e do preço MIBEL. Dados reais do sistema "
        "elétrico português, atualização diária, backtest rigoroso sem *leakage*."
    )

    try:
        health = api("/health")
        cons_perf = perf_frame(api("/performance/consumption"))
        price_perf = perf_frame(api("/performance/price"))
    except httpx.HTTPError:
        st.error(f"API indisponível em {API_BASE_URL}. Definir API_BASE_URL.")
        return

    assert isinstance(health, dict)
    k = st.columns(4)
    k[0].metric("Última emissão", str(health.get("latest_issue_date") or "—"))
    if not cons_perf.empty:
        mae = cons_perf.loc[cons_perf.model_name == "lightgbm", "mae"].squeeze()
        k[1].metric("Consumo · MAE modelo", f"{mae:.0f} MW")
    if not price_perf.empty:
        mae = price_perf.loc[price_perf.model_name == "lightgbm_p50", "mae"].squeeze()
        k[2].metric("Preço · MAE P50", f"{mae:.2f} €/MWh")
    k[3].metric("Base de dados", "OK ✅" if health.get("database") else "DOWN ❌")

    tab_c, tab_p = st.tabs(["Consumo (Fase 1)", "Preço MIBEL (Fase 2)"])
    with tab_c:
        _render_consumption(cons_perf)
    with tab_p:
        _render_price(price_perf)


def _mae_bars(perf: pd.DataFrame, unit: str) -> alt.Chart:
    return (
        alt.Chart(perf)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("mae:Q", title=f"MAE realizado ({unit})"),
            y=alt.Y("serie:N", sort="x", title=None),
            color=alt.Color(
                "tipo:N",
                scale=alt.Scale(domain=["Modelo ML", "Baseline"], range=[C_MODEL, "#898781"]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=["serie:N", alt.Tooltip("mae:Q", format=".2f"), "n:Q"],
        )
        .properties(height=200)
    )


def _render_consumption(perf: pd.DataFrame) -> None:
    st.subheader("Previsão de consumo D+1")
    points = api("/forecast/consumption")
    assert isinstance(points, dict)
    lines = forecast_lines(points["points"], _LABEL)
    if not lines.empty:
        st.altair_chart(_line_chart(lines, "Consumo (MW)"), use_container_width=True)
        st.caption(f"Emitida em {points['issue_date']} para o dia de mercado seguinte (CET).")
    if not perf.empty:
        st.subheader("Desempenho vs baselines (backtest)")
        st.altair_chart(_mae_bars(perf, "MW"), use_container_width=True)


def _render_price(perf: pd.DataFrame) -> None:
    st.subheader("Previsão de preço MIBEL D+1 (P10 · P50 · P90)")
    points = api("/forecast/price")
    assert isinstance(points, dict)
    lines = forecast_lines(points["points"], _LABEL)
    band = price_band(points["points"])
    if not lines.empty:
        chart = _line_chart(lines, "Preço (€/MWh)")
        if not band.empty:
            area = (
                alt.Chart(band)
                .mark_area(opacity=0.15, color=C_MODEL)
                .encode(x="hora:T", y="p10:Q", y2="p90:Q")
            )
            chart = area + chart
        st.altair_chart(chart, use_container_width=True)
        st.caption("Banda sombreada = intervalo P10-P90 (calibrado por CQR conformal).")
    if not perf.empty:
        st.subheader("Desempenho vs baselines (backtest)")
        st.altair_chart(
            _mae_bars(
                perf[perf.model_name.isin(["lightgbm_p50", "persistence_24h", "seasonal_168h"])],
                "€/MWh",
            ),
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
