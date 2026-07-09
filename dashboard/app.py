"""Streamlit entrypoint — multipage navigation for the energia-forecast dashboard.

Pages live in views/ and share helpers from common.py. This file wires navigation and the
sidebar identity block; Streamlit Community Cloud points at it as the app's main module.
Design register is deliberately sober: Material icons (no emoji), plain titles, restrained copy.
"""

from __future__ import annotations

import streamlit as st
from common import API_BASE_URL, GITHUB_URL

st.set_page_config(
    page_title="Energia Forecast",
    page_icon=":material/bolt:",
    layout="wide",
    menu_items={"Get help": GITHUB_URL},
)

nav = st.navigation(
    [
        st.Page("views/forecasts.py", title="Forecasts", icon=":material/bolt:", default=True),
        st.Page("views/performance.py", title="Performance", icon=":material/monitoring:"),
        st.Page("views/how_it_works.py", title="Methodology", icon=":material/menu_book:"),
        st.Page("views/status.py", title="System status", icon=":material/dns:"),
    ]
)

with st.sidebar:
    st.markdown("**Energia Forecast**")
    st.caption(
        "Day-ahead forecasts of Portuguese electricity demand and MIBEL prices, "
        "issued every morning before the market closes and scored against outcomes."
    )
    st.markdown(
        f"[Source code]({GITHUB_URL}) · [API]({API_BASE_URL}/docs)",
    )
    st.caption("All times shown in Europe/Lisbon.")

nav.run()
