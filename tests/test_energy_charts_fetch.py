"""Tests for the Energy-Charts fetcher (network-free, via httpx.MockTransport)."""

from __future__ import annotations

import datetime as dt
import json

import httpx

from src.ingestion.sources.energy_charts import fetch_public_power

_PAYLOAD = {"unix_seconds": [1_700_000_000], "production_types": [{"name": "Load", "data": [1.0]}]}


def _client(status: int, body: object) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "public_power" in str(request.url)
        assert request.url.params.get("country") == "es"
        text = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, text=text, headers={"content-type": "application/json"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_returns_payload_and_provenance() -> None:
    with _client(200, _PAYLOAD) as client:
        result = fetch_public_power(dt.date(2024, 1, 1), dt.date(2024, 1, 31), client=client)
    assert result is not None
    assert result.payload == _PAYLOAD
    assert result.source_ref == "energy_charts:public_power:es:2024-01-01..2024-01-31"


def test_fetch_returns_none_on_404() -> None:
    with _client(404, "not found") as client:
        assert fetch_public_power(dt.date(2024, 1, 1), dt.date(2024, 1, 1), client=client) is None


def test_fetch_returns_none_on_non_json_200() -> None:
    with _client(200, "<html>maintenance</html>") as client:
        assert fetch_public_power(dt.date(2024, 1, 1), dt.date(2024, 1, 1), client=client) is None


def test_fetch_returns_none_when_payload_lacks_unix_seconds() -> None:
    with _client(200, {"production_types": []}) as client:
        assert fetch_public_power(dt.date(2024, 1, 1), dt.date(2024, 1, 1), client=client) is None
