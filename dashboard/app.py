"""Streamlit entrypoint — multipage navigation for the energia-forecast dashboard.

Pages live in views/ and share helpers from common.py. This file only wires navigation;
Streamlit Community Cloud points at it as the app's main module.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Energy Forecast — Portugal",
    page_icon="⚡",
    layout="wide",
    menu_items={"Get help": "https://github.com/diogogs/energia-forecast"},
)

nav = st.navigation(
    [
        st.Page("views/forecasts.py", title="Forecasts", icon="⚡", default=True),
        st.Page("views/performance.py", title="Performance", icon="📈"),
        st.Page("views/how_it_works.py", title="How it works", icon="🔬"),
        st.Page("views/status.py", title="System status", icon="🩺"),
    ]
)
nav.run()
