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
