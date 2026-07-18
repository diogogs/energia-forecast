"""System-status page — data freshness, live scoring, ingestion health, ops."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from common import GITHUB_URL, api_or_none, cold_start_stop, footer, local

st.title("System status")
st.markdown(
    "Every panel on this page is read live from the production database; nothing is hand-updated."
)

fresh = api_or_none("/monitoring/freshness")
if fresh is None:
    cold_start_stop()

# ---------------------------------------------------------------- freshness
st.subheader("Data freshness")
df = pd.DataFrame(fresh if isinstance(fresh, list) else [])
if not df.empty:
    df["status"] = df["stale"].map({True: "STALE", False: "fresh"})
    df["ingested (h ago)"] = df["hours_since_ingest"].round(1)
    df["latest data (Lisbon)"] = local(df["latest_data_ts"]).dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(
        df[["source", "latest data (Lisbon)", "ingested (h ago)", "status"]],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "A source is flagged stale if nothing new landed within 30 h (the daily ingest runs "
        "every 24 h at 06:30 UTC)."
    )

# ---------------------------------------------------------------- live error
st.subheader("Live scoring")
cols = st.columns(2)
for col, target, unit in ((cols[0], "consumption", "MW"), (cols[1], "price", "€/MWh")):
    # 7-day window, deliberately: a since-launch aggregate would be dominated by the first
    # mornings (pre weather-fix) and misrepresent the current system. The full per-day
    # record, first mornings included, lives on the Track record page.
    err = api_or_none(f"/monitoring/error/{target}?days=7")
    if isinstance(err, dict) and "hours_scored" in err:
        value = f"{err['mae']:.1f} {unit}" if err.get("mae") is not None else "accumulating…"
        col.metric(f"Live MAE, last 7 days — {target} ({err['hours_scored']} h)", value)
st.caption(
    "Realised error of the production emissions over the last 7 days. The first mornings "
    "after launch (11-12 July) ran before a weather-ingestion fix and were considerably "
    "worse; they are part of the permanent record on the Track record page, where every "
    "delivery day is scored individually against the 10-week backtest benchmark."
)

# ---------------------------------------------------------------- ingestion log
st.subheader("Ingestion log")
events = api_or_none("/monitoring/dq?limit=12")
if isinstance(events, list) and events:
    ev = pd.DataFrame(events)
    ev["when (Lisbon)"] = local(ev["logged_at"]).dt.strftime("%Y-%m-%d %H:%M")
    ev["result"] = ev["severity"].map({"info": "ok", "warning": "warning", "error": "ERROR"})
    st.dataframe(
        ev[["when (Lisbon)", "source", "result", "rows_written"]].rename(
            columns={"rows_written": "rows"}
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("Each daily ingest writes a durable outcome row per source (ops.dq_log).")
else:
    st.caption("The ingestion log will appear here once the updated API is deployed.")

# ---------------------------------------------------------------- ops notes
st.subheader("Operational safeguards")
st.markdown(
    f"- **Weekly database backup**: a full `pg_dump` snapshot every Sunday, kept as a "
    f"30-day rolling [GitHub Actions artifact]({GITHUB_URL}/actions/workflows/backup.yml).\n"
    "- **Self-healing ingestion**: every morning re-ingests the last 3 days across all "
    "sources (idempotent), so transient failures and late data revisions repair themselves.\n"
    "- **Insert-only predictions**: the forecast record cannot be rewritten, by schema.\n"
    f"- **Everything as code**: crons, migrations and checks are [in the repo]({GITHUB_URL})."
)

footer()
