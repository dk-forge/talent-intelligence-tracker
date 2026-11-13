"""NZ NZBN collector: dormant, attributed, and never showing a sole trader.

Fixtures are CONSTRUCTED in the shape of MBIE's NZBN API v5, not captured: the
routes have not been read live (ROUTES_VERIFIED_LIVE is False). Offline.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from unittest import mock

import pytest

from collectors import nz_nzbn as nz
from pipeline import validate, vocab

NOW = datetime(2026, 11, 13, 4, 30, tzinfo=timezone.utc)


def company(**over):
    e = {"nzbn": "9429000000011", "entityName": "Kiwi Software Limited",
         "entityStatusDescription": "Registered", "entityTypeCode": "LTD",
         "entityTypeDescription": "NZ Limited Company",
         "registrationDate": "2026-11-05T00:00:00+13:00",
         "industryClassifications": [{"classificationCode": "J542010",
                                      "classificationDescription": "Software Publishing"}],
         "addresses": {"addressList": [{"addressType": "REGISTERED", "address3": "Wellington"}]}}
    e.update(over)
    return e


def sole_trader(**over):
    return company(entityTypeCode="SOLE_TRADER", entityTypeDescription="Sole Trader",
                   entityName="Jane Citizen", **over)


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}

    def json(self):
        return self._b


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        return self.routes(url, headers or {}, params or {})


def test_dormant_makes_no_request_and_needs_no_key(monkeypatch):
    monkeypatch.delenv("TIT_NZ_NZBN", raising=False)
    monkeypatch.delenv("NZBN_API_KEY_NZ", raising=False)
    s = Session(lambda *a: pytest.fail("a disarmed run made a request"))
    assert nz.collect(session=s) == [] and s.calls == []


def test_armed_refuses_until_routes_verified(monkeypatch):
    monkeypatch.setenv("TIT_NZ_NZBN", "on")
    monkeypatch.setenv("NZBN_API_KEY_NZ", "x")
    with pytest.raises(nz.NzbnError, match="ROUTES_VERIFIED_LIVE"):
        nz.collect(session=Session(lambda *a: None))


def test_row_carries_attribution_with_refresh_timestamp():
    row = nz._row(nz.public_view(company()), retrieved_at=NOW)
    assert ("Data sourced from the Ministry of Business, Innovation and "
            "Employment's NZBN register. Last refreshed 2026-11-13 04:30 UTC.") in row["summary"]
    assert row["summary"] in row["raw_text"]
    assert row["source_url"].startswith("https://www.nzbn.govt.nz/")
    assert row["raw_text"] and row["country"] == "New Zealand"


def test_source_host_is_primary():
    assert "www.nzbn.govt.nz" in vocab.PRIMARY_SOURCE_DOMAINS


@pytest.mark.parametrize("entity", [
    sole_trader(),
    company(entityTypeCode="PARTNERSHIP", entityTypeDescription="Partnership"),
    company(entityTypeCode="", entityTypeDescription=""),      # unknown: fail closed
    company(entityTypeCode="XYZ", entityTypeDescription="Something New"),
])
def test_sole_traders_and_unknown_forms_are_excluded(entity):
    assert nz.public_view(entity) is None
    assert nz._row(nz.public_view(entity), retrieved_at=NOW) is None


def test_refresh_drops_fields_that_became_private():
    before = nz.public_view(company())
    assert "industryClassifications" in before and "addresses" in before
    after = nz.refresh(before, company(industryClassifications=None, addresses={}))
    assert "industryClassifications" not in after and "addresses" not in after


def test_company_that_turns_into_private_sole_trader_form_is_dropped():
    assert nz.refresh(nz.public_view(company()), sole_trader()) is None


def test_industry_filter():
    other = company(industryClassifications=[{"classificationCode": "A011100"}])
    assert nz._row(nz.public_view(other), retrieved_at=NOW) is None


def _armed(monkeypatch):
    monkeypatch.setenv("TIT_NZ_NZBN", "on")
    monkeypatch.setenv("NZBN_API_KEY_NZ", "k")
    monkeypatch.setattr(nz, "ROUTES_VERIFIED_LIVE", True)


def test_incremental_refresh_uses_change_search_and_if_match(monkeypatch):
    _armed(monkeypatch)
    ents = {"1": company(nzbn="1"), "2": company(nzbn="2"), "3": sole_trader(nzbn="3")}

    def routes(url, headers, params):
        if url.endswith("/entities/changes"):
            assert params["change-event-type"] == "NEW_REGISTRATION"
            assert headers["Ocp-Apim-Subscription-Key"] == "k"
            return Resp(200, {"items": [{"nzbn": n} for n in ents]})
        n = url.rsplit("/", 1)[1]
        if headers.get("If-Match") == "etag-2":
            return Resp(304)
        return Resp(200, ents[n], {"ETag": f"etag-{n}"})

    s = Session(routes)
    etags = {"2": "etag-2"}
    rows = nz.collect(session=s, today=date(2026, 11, 13), etags=etags)
    assert [r["nzbn"] for r in rows] == ["1"]          # 2 unchanged, 3 sole trader
    assert etags["1"] == "etag-1"
    urls = [u for u, _ in s.calls]
    assert sum(u.endswith("/entities/changes") for u in urls) == 1
    assert not any(u.endswith("/entities") for u in urls)  # never a full walk


def test_as_classified_validates_shape():
    row = nz._row(nz.public_view(company()), retrieved_at=NOW)
    c = nz.as_classified(row)
    assert c["confidence"] == "verified" and c["pillar"] == "company_development"
    assert "sole trader" in c["talent_readthrough"].lower()
    assert nz.REVISITS_ITS_SOURCE_URL


def test_key_is_never_hardcoded():
    import pathlib
    text = pathlib.Path(nz.__file__).read_text()
    assert "—" not in text
