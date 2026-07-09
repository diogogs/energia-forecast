"""Write side of ops.dq_log — the durable data-quality / ingestion event log.

Append-only in practice (identity PK): callers record an event and never mutate it. The read side
(recent events for the monitoring API) lives in ``src.monitoring.watchdog``.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from src.db.models import DqLog

_DETAIL_MAX = 500  # keep detail compact (exception reprs can be long); the table stays small


def record_dq_event(
    session: Session,
    *,
    source: str,
    check_name: str,
    severity: str,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    rows_written: int | None = None,
    detail: str | None = None,
) -> None:
    """Append one data-quality/ingestion event. The caller controls the transaction (commit)."""
    session.add(
        DqLog(
            source=source,
            check_name=check_name,
            severity=severity,
            window_start=window_start,
            window_end=window_end,
            rows_written=rows_written,
            detail=detail[:_DETAIL_MAX] if detail else None,
        )
    )
