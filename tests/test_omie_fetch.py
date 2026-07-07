"""Tests for the marginalpdbc HTTP fetcher (network-free, via httpx.MockTransport).

OMIE can withdraw a day's .1 file and republish only a higher version. The fetcher
takes the lowest version that exists, so normal days still resolve on .1 in one
request while re-issued days (real case: 2025-11-27 only under .2, 2025-10-30 under
.3) are still recovered.
"""

from __future__ import annotations

import datetime as dt

import httpx

from src.ingestion.sources.omie import MarginalpdbcFile, fetch_marginalpdbc

# fetch only checks for the header; the body need not be a full parseable day.
_VALID = "MARGINALPDBC;\n2025;11;27;1;94.07;94.07;\n"


def _client(available: set[str]) -> httpx.Client:
    """A client whose transport serves 200 for filenames in ``available`` and 404 otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        filename = request.url.params.get("filename")
        if filename in available:
            return httpx.Response(200, text=_VALID)
        return httpx.Response(404, text="<html>not found</html>")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_uses_version_1_when_present() -> None:
    with _client({"marginalpdbc_20251127.1"}) as client:
        file = fetch_marginalpdbc(dt.date(2025, 11, 27), client)
    assert file == MarginalpdbcFile(filename="marginalpdbc_20251127.1", text=_VALID)


def test_fetch_falls_back_to_higher_version_when_1_absent() -> None:
    # .1 withdrawn, only .2 published — mirrors 2025-11-27.
    with _client({"marginalpdbc_20251127.2"}) as client:
        file = fetch_marginalpdbc(dt.date(2025, 11, 27), client)
    assert file is not None
    assert file.filename == "marginalpdbc_20251127.2"


def test_fetch_returns_none_when_all_versions_absent() -> None:
    with _client(set()) as client:
        file = fetch_marginalpdbc(dt.date(2025, 10, 30), client)
    assert file is None
