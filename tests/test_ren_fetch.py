"""Tests for the REN ProductionBreakdown HTTP fetcher (network-free, via httpx.MockTransport)."""

from __future__ import annotations

import datetime as dt
import json

import httpx

from src.ingestion.sources.ren import fetch_production_breakdown, to_ticks

_PAYLOAD = {"xAxis": {"categories": []}, "series": [{"name": "Consumption", "data": [1.0]}]}


def _client(status: int, body: object) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        # The request must POST an empty JSON body to the ProductionBreakdown service.
        assert request.method == "POST"
        assert "ProductionBreakdown" in str(request.url)
        text = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, text=text, headers={"content-type": "application/json"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_returns_payload_and_provenance() -> None:
    with _client(200, _PAYLOAD) as client:
        result = fetch_production_breakdown(dt.date(2024, 6, 1), client)
    assert result is not None
    assert result.payload == _PAYLOAD
    assert (
        result.source_ref == f"ren:ProductionBreakdown/1266:ticks={to_ticks(dt.date(2024, 6, 1))}"
    )


def test_fetch_returns_none_on_404() -> None:
    with _client(404, "<html>not found</html>") as client:
        assert fetch_production_breakdown(dt.date(2024, 6, 1), client) is None


def test_fetch_returns_none_when_payload_lacks_series() -> None:
    with _client(200, {"xAxis": {"categories": []}}) as client:
        assert fetch_production_breakdown(dt.date(2024, 6, 1), client) is None
