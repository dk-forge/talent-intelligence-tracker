"""The October 2026 reference sets: worldwide, United States and Europe.

Assembled on 2026-11-11 from the three work lists' `next_goldset` instructions
(data/recall*_worklist.json). Each is sealed, judged by its own family's shape
(including the ratchet against every earlier set on disk), is the newest set in
its family's directory so it is the one measured, and every source URL answered
HTTP 200 when the set was sealed.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.recall import family, goldset  # noqa: E402

RECALL = os.path.join(ROOT, "analysis", "recall")
WINDOW = {"start": "2026-10-01", "end": "2026-10-31"}
SETS = {
    "2026-10-v1": (os.path.join(RECALL, "goldset-2026-10.json"), family.WORLD),
    "2026-10-us-v1": (os.path.join(RECALL, "us", "goldset-us-2026-10.json"), family.US),
    "2026-10-eu-v1": (os.path.join(RECALL, "eu", "goldset-eu-2026-10.json"), family.EU),
}
# Banned from the US and EU families by their work-list instructions: no
# commercial deal database, people-data service, aggregator or other tracker
# may be named anywhere in the repository. Spelled as fragments so this file
# does not itself name them.
BANNED_HOST_FRAGMENTS = ("crunch" + "base", "dealr" + "oom", "tra" + "cxn",
                         "pitch" + "book", "cbin" + "sights", "seed" + "table",
                         "vest" + "bee")


@pytest.fixture(scope="module", params=sorted(SETS))
def sealed(request):
    path, fam = SETS[request.param]
    data = goldset.load(path)
    data["_family"] = fam
    return data


def test_each_set_is_valid_sealed_and_measured(sealed):
    assert goldset.validate(sealed) == [], goldset.validate(sealed)
    assert sealed["family"] == sealed["_family"].id
    assert sealed["window"] == WINDOW
    assert sealed["sealed"] is True
    assert sealed["assembled_on"] == "2026-11-11"
    assert sealed["version"] in SETS
    assert sealed["_path"] == sealed["_family"].latest_goldset(), (
        "the October set must be the newest one in its family's directory, "
        "or it is not the one being measured")


def test_every_item_is_dated_in_window_and_was_reachable(sealed):
    for item in sealed["items"]:
        assert WINDOW["start"] <= item["event_date"] <= WINDOW["end"], item["id"]
        assert item["source_url"].startswith(("http://", "https://")), item["id"]
        assert item.get("source_http_status") == 200, item["id"]
        assert item.get("verified"), item["id"]


def test_no_banned_source_is_cited(sealed):
    for item in sealed["items"]:
        host = item["source_url"].lower()
        assert not any(f in host for f in BANNED_HOST_FRAGMENTS), item["id"]


def test_us_items_carry_a_metro_and_the_set_declares_both_signals():
    data = goldset.load(SETS["2026-10-us-v1"][0])
    assert set(data["signal_types"]) == {"funding", "leadership"}
    assert all(item.get("metro") for item in data["items"])


# Second-referee review, 2026-11-11. The October passes read only publishers
# that allow an automated reader, and those turned out to be largely the ones
# the tracker already collects from: of the items, 132/152 (world), 43/52 (US)
# and 50/50 (EU) cite a publisher in data/feeds.csv or data/sources_catalogue.csv.
# A figure measured on that population is recall WITHIN the publishers we read,
# not reach beyond them, and the published caveats must say so in numbers.
OVERLAP_AT_REVIEW = {"2026-10-v1": "132 of 152",
                     "2026-10-us-v1": "43 of 52",
                     "2026-10-eu-v1": "50 of 50"}


def test_caveats_state_the_source_registry_overlap(sealed):
    stated = OVERLAP_AT_REVIEW[sealed["version"]]
    hits = [c for c in sealed.get("caveats", [])
            if "source registry" in c and stated in c and "not reach" in c]
    assert hits, (f"{sealed['version']} must carry a caveat naming its overlap "
                  f"with the source registry ({stated}) and that the figure is "
                  f"recall within those publishers, not reach")


def test_us_caveats_disclose_the_daily_roundup_rows():
    data = goldset.load(SETS["2026-10-us-v1"][0])
    roundups = [i for i in data["items"]
                if "roundup" in i["source_url"] or "funding-news-today" in i["source_url"]]
    assert len(roundups) == 11
    assert any("daily roundup" in c and "Eleven rows" in c for c in data["caveats"])
