"""Tests for the Open-Meteo fetcher (network-free, via httpx.MockTransport)."""

from __future__ import annotations

import datetime as dt
import json

import httpx

from src.ingestion.sources.openmeteo import MODEL, fetch_previous_runs

_ENTRY = {"hourly": {"time": ["2024-06-15T00:00"], "temperature_2m_previous_day1": [20.0]}}


def _client(status: int, body: object) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params.get("models") == MODEL
        # Multiple locations are requested as comma-separated lat/lon.
        assert "," in request.url.params.get("latitude", "")
        text = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, text=text, headers={"content-type": "application/json"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_returns_list_payload_and_provenance() -> None:
    with _client(200, [_ENTRY, _ENTRY, _ENTRY]) as client:
        result = fetch_previous_runs(dt.date(2024, 6, 1), dt.date(2024, 6, 30), client=client)
    assert result is not None
    assert len(result.payload) == 3
    assert result.source_ref == f"openmeteo:previous_runs:{MODEL}:2024-06-01..2024-06-30"


def test_fetch_wraps_single_object_payload() -> None:
    # A single-location response is an object; the fetcher normalises it to a list.
    with _client(200, _ENTRY) as client:
        result = fetch_previous_runs(dt.date(2024, 6, 1), dt.date(2024, 6, 1), client=client)
    assert result is not None
    assert isinstance(result.payload, list) and len(result.payload) == 1


def test_fetch_returns_none_on_404() -> None:
    with _client(404, "not found") as client:
        assert fetch_previous_runs(dt.date(2024, 6, 1), dt.date(2024, 6, 1), client=client) is None


def test_fetch_returns_none_on_non_json_200() -> None:
    with _client(200, "<html>maintenance</html>") as client:
        assert fetch_previous_runs(dt.date(2024, 6, 1), dt.date(2024, 6, 1), client=client) is None
