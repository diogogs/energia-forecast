"""System-status page — data freshness, live scoring, ingestion health, ops."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from common import GITHUB_URL, api_or_none, cold_start_stop, footer, local

st.title("🩺 System status")
st.markdown(
    "The system watches itself: every panel below is served live from the same database the "
    "forecasts are written to. Nothing here is hand-updated."
)

fresh = api_or_none("/monitoring/freshness")
if fresh is None:
    cold_start_stop()

# ---------------------------------------------------------------- freshness
st.subheader("Data freshness — is the system still fed?")
df = pd.DataFrame(fresh if isinstance(fresh, list) else [])
if not df.empty:
    df["status"] = df["stale"].map({True: "⚠️ stale", False: "✅ fresh"})
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
st.subheader("Live scoring — the production model against reality")
cols = st.columns(2)
for col, target, unit in ((cols[0], "consumption", "MW"), (cols[1], "price", "€/MWh")):
    err = api_or_none(f"/monitoring/error/{target}")
    if isinstance(err, dict) and "hours_scored" in err:
        value = f"{err['mae']:.1f} {unit}" if err.get("mae") is not None else "accumulating…"
        col.metric(f"Live MAE — {target} ({err['hours_scored']} h scored)", value)
st.caption(
    "⚖️ Small-sample caveat: the system went live in July 2026, so these numbers cover only "
    "a handful of hours and will be noisy at first. The statistically meaningful benchmark "
    "is the 10-week backtest on the **Performance** page; live and backtest error should "
    "converge as the record grows."
)

# ---------------------------------------------------------------- ingestion log
st.subheader("Ingestion log — last runs per source")
events = api_or_none("/monitoring/dq?limit=12")
if isinstance(events, list) and events:
    ev = pd.DataFrame(events)
    ev["when (Lisbon)"] = local(ev["logged_at"]).dt.strftime("%Y-%m-%d %H:%M")
    ev["result"] = ev["severity"].map({"info": "✅", "warning": "⚠️", "error": "❌"})
    st.dataframe(
        ev[["when (Lisbon)", "source", "result", "rows_written"]].rename(
            columns={"rows_written": "rows"}
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Every daily ingest writes a durable outcome row per source (`ops.dq_log`) — "
        "ingestion health without digging through CI logs."
    )
else:
    st.caption("The ingestion log will appear here once the updated API is deployed.")

# ---------------------------------------------------------------- ops notes
st.subheader("Operational safety nets")
st.markdown(
    f"- **Weekly database backup** — a full `pg_dump` snapshot every Sunday, kept as a "
    f"30-day rolling [GitHub Actions artifact]({GITHUB_URL}/actions/workflows/backup.yml).\n"
    "- **Self-healing ingestion** — every morning re-ingests the last 3 days across all "
    "sources (idempotent), so transient failures and late data revisions repair themselves.\n"
    "- **Insert-only predictions** — the forecast record cannot be rewritten, by schema.\n"
    f"- **Everything as code** — crons, migrations and checks are [in the repo]({GITHUB_URL})."
)

footer()
