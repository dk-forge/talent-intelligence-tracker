"""GDELT: the archive window, the honest payload, and the loud failures.

Written against the 2026-07-28 diagnosis of why the collector had stored three
records in its whole life. Each test below pins one of the findings, so a
future edit cannot quietly undo it. No network: every response is a captured
shape.
"""

import json
import urllib.parse
from datetime import date, datetime

import pytest

import backfill_gdelt_2026 as backfill
from collectors import gdelt


def params(url: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


ARTICLE = {
    "url": "https://www.scoop.co.nz/stories/BU2607/nz-airports.htm",
    "url_mobile": "",
    "title": "NZ Airports Appoints New Chief Executive",
    "seendate": "20260105T041500Z",
    "socialimage": "",
    "domain": "scoop.co.nz",
    "language": "English",
    "sourcecountry": "New Zealand",
}


def payload(*articles) -> bytes:
    return json.dumps({"articles": list(articles)}).encode()


# --- the archive window ----------------------------------------------------
#
# The whole point of the exercise: DOC 2.0 is the only news route with an
# archive, and the collector hardcoded a rolling 3-day window, so it had never
# been used.

def test_explicit_dates_replace_the_rolling_window():
    url = gdelt.build_query_url("x", startdatetime="2026-01-01",
                                enddatetime="2026-02-01")
    got = params(url)
    assert got["startdatetime"] == "20260101000000"
    assert got["enddatetime"] == "20260201000000"
    # Sending both would be ambiguous: GDELT treats them as alternatives.
    assert "timespan" not in got


def test_a_rolling_window_is_still_the_default():
    assert params(gdelt.build_query_url("x"))["timespan"] == "3d"
    assert "startdatetime" not in params(gdelt.build_query_url("x"))


@pytest.mark.parametrize("value,expected", [
    ("2026-01-05", "20260105000000"),
    ("20260105", "20260105000000"),
    ("20260105143000", "20260105143000"),
    (date(2026, 1, 5), "20260105000000"),
    (datetime(2026, 1, 5, 14, 30), "20260105143000"),
])
def test_every_date_shape_a_caller_might_hold(value, expected):
    assert gdelt.as_stamp(value) == expected


def test_a_date_it_cannot_read_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        gdelt.as_stamp("last tuesday")


# --- the payload -----------------------------------------------------------

def test_the_outlet_country_is_carried_instead_of_discarded():
    """It was dropped, and it is the single best geography hint on offer —
    which is why the live product reads "1 country"."""
    item = gdelt.parse(payload(ARTICLE))[0]
    assert item["source_country"] == "New Zealand"
    assert "New Zealand" in item["raw_text"]


def test_the_outlet_country_is_context_and_never_a_sourced_country():
    """validate.py reads raw["country"] as a sourced value. A Thai business
    site reporting a US company would then file a US appointment in Thailand."""
    item = gdelt.parse(payload(ARTICLE))[0]
    assert not item.get("country")


def test_the_headline_stays_clean_of_the_dateline():
    item = gdelt.parse(payload(ARTICLE))[0]
    assert item["headline"] == ARTICLE["title"]
    assert item["raw_text"].startswith(ARTICLE["title"])


# --- syndication -----------------------------------------------------------
#
# 335 URLs were 212 stories on the measured run; one wire item arrived on 34
# domains. URL-dedup passed all 34 to the paid classifier.

def test_the_same_wire_item_tokenised_differently_shares_a_key():
    assert gdelt.title_key("Coca - Cola Names New CEO") == \
           gdelt.title_key("Coca-Cola names new CEO")


def test_a_syndicated_copy_is_dropped_before_anything_is_paid(monkeypatch):
    mirror = dict(ARTICLE, url="https://floridastatesman.com/nz-airports",
                  domain="floridastatesman.com", sourcecountry="United States")
    monkeypatch.setattr(gdelt.time, "sleep", lambda *_: None)
    monkeypatch.setattr(gdelt, "fetch", lambda *a, **k: gdelt.parse(payload(ARTICLE, mirror)))

    gdelt.reset_stats()
    items = gdelt.collect(["q"])
    assert len(items) == 1
    assert gdelt.STATS["syndicated"] == 1


def test_de_duplication_can_be_carried_across_windows(monkeypatch):
    """The backfill pays for a wire item once for the month, not once a day."""
    monkeypatch.setattr(gdelt.time, "sleep", lambda *_: None)
    monkeypatch.setattr(gdelt, "fetch", lambda *a, **k: gdelt.parse(payload(ARTICLE)))

    urls, titles = set(), set()
    first = gdelt.collect(["q"], seen_urls=urls, seen_titles=titles)
    second = gdelt.collect(["q"], seen_urls=urls, seen_titles=titles)
    assert len(first) == 1 and second == []


# --- failures that used to look like a quiet news day ----------------------

class FakeResponse:
    def __init__(self, content, status=200, ctype="application/json"):
        self.content = content
        self.status_code = status
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        pass


def test_a_refused_query_raises_instead_of_reading_as_zero_results(monkeypatch):
    """GDELT answers a bad query with HTTP 200 and one line of plain text.
    Parsed naively that is an empty result set, which is indistinguishable from
    a day on which nothing happened."""
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: FakeResponse(
        b"Invalid/Unsupported Language.\n", ctype="text/html"))
    with pytest.raises(gdelt.QueryRejected):
        gdelt.fetch("q sourcelang:eng")


def test_a_rate_limit_raises_instead_of_reading_as_zero_results(monkeypatch):
    monkeypatch.setattr(gdelt.time, "sleep", lambda *_: None)
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: FakeResponse(
        b"Please limit requests to one every 5 seconds", status=429, ctype="text/plain"))
    with pytest.raises(gdelt.RateLimited):
        gdelt.fetch("q")


def test_hitting_the_record_cap_is_counted_as_a_coverage_hole(monkeypatch):
    """DOC 2.0 caps at 250 and has no pagination, so a full response is a
    truncated window, not a good day."""
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: FakeResponse(
        payload(*[dict(ARTICLE, url=f"https://x/{i}", title=f"T{i}") for i in range(3)])))
    gdelt.reset_stats()
    gdelt.fetch("q", records=3)
    assert gdelt.STATS["truncated"] == 1


# --- the backfill ----------------------------------------------------------

def test_windows_are_one_day_and_cover_the_last_day_inclusive():
    windows = list(backfill.iter_windows(date(2026, 1, 1), date(2026, 1, 31)))
    assert len(windows) == 31
    assert windows[0][0] == datetime(2026, 1, 1)
    assert windows[-1][1] == datetime(2026, 2, 1)
    # Half-open and contiguous: no article can fall between two windows.
    for (_, hi), (lo, _) in zip(windows, windows[1:]):
        assert hi == lo


def test_a_month_of_world_news_that_came_back_empty_exits_non_zero(monkeypatch, tmp_path):
    """The first SEC backfill dispatch exited 0 after five silent 403s and
    looked exactly like a successful run that found nothing."""
    monkeypatch.setattr(backfill.gdelt, "collect", lambda *a, **k: [])
    monkeypatch.setattr(backfill.schema, "connect",
                        lambda *a, **k: __import__("sqlite3").connect(":memory:"))
    monkeypatch.setattr(backfill.publish, "publish", lambda *a, **k: None)
    monkeypatch.setattr(
        backfill.sys, "argv",
        ["backfill", "--start", "2026-01-01", "--end", "2026-01-03", "--fetch-only"])
    assert backfill.main() == 1
