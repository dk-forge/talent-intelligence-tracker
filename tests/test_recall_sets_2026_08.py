"""The August 2026 reference set and the labels that admitted every row.

The first European set landed on 2026-09-12 (the worldwide and United States
drafts for the same window were assembled beside it and did NOT clear their
own guards, so they were not sealed; TECHLOG 2026-09-12), and for the first time the per-item judgement ("is this event in
scope, is the amount the one the page states") was made by two independent
AI referees rather than by a person. That is only defensible if the judgement
is recorded beside the set and can be re-read: so every item in each set must
carry, in the set's labels file, two verdicts that both accept it, and every
candidate the referees did NOT both accept must be listed there rather than
quietly disappearing.
"""

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.recall import family, goldset  # noqa: E402

RECALL = os.path.join(ROOT, "analysis", "recall")

SETS = {
    "2026-08-eu-v1": (os.path.join(RECALL, "eu", "goldset-eu-2026-08.json"), family.EU),
}
REFEREES = ("anthropic/claude-sonnet-4.5", "openai/gpt-4o")
ACCEPT_KEYS = ("in_scope", "date_in_window", "country_ok", "amount_or_role_ok")
DASHES = re.compile("[–—]")


@pytest.fixture(scope="module", params=sorted(SETS))
def sealed(request):
    path, fam = SETS[request.param]
    data = goldset.load(path)
    data["_family"] = fam
    return data


def _labels_for(data):
    path = os.path.join(ROOT, data["labels"])
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_each_set_is_valid_and_declares_its_family(sealed):
    assert goldset.validate(sealed) == [], sealed["version"]
    assert sealed["family"] == sealed["_family"].id
    assert sealed["window"] == {"start": "2026-08-01", "end": "2026-08-31"}
    assert sealed["sealed"] is True
    assert sealed["assembled_on"] >= "2026-09-12"
    assert sealed["version"] in SETS
    assert sealed["_path"] == sealed["_family"].latest_goldset(), (
        "the August set must be the newest one in its family's directory, "
        "or it is not the one being measured")


def test_every_item_was_accepted_by_both_referees(sealed):
    labels = _labels_for(sealed)
    assert labels["set"] == sealed["version"]
    assert tuple(labels["referees"]) == REFEREES
    assert "definition_given_to_referees" in labels and "leadership" in labels["definition_given_to_referees"]
    for item in sealed["items"]:
        entry = labels["items"].get(item["id"])
        assert entry, f"{item['id']} has no recorded verdicts"
        assert entry["source_url"] == item["source_url"]
        assert entry["accepted_by_both"] is True
        for model in REFEREES:
            verdict = entry["verdicts"][model]
            assert "error" not in verdict, f"{item['id']}: {model} gave no verdict"
            assert verdict["confidence"] >= 50, f"{item['id']}: {model} was not confident"
            for key in ACCEPT_KEYS:
                assert verdict[key] is True, f"{item['id']}: {model} said {key}={verdict[key]}"


def test_a_disagreement_is_listed_and_never_kept(sealed):
    labels = _labels_for(sealed)
    kept_urls = {i["source_url"] for i in sealed["items"]}
    for entry in labels["not_kept"]:
        assert not entry["accepted_by_both"] or entry.get("skipped") or (
            entry["source_url"] not in kept_urls), (
            "a candidate both referees accepted and the set does not carry must "
            "have been dropped by a selection cap or as a duplicate, never silently")
        if entry["accepted_by_both"] is False and entry["verdicts"]:
            assert any(
                "error" in v or v.get("confidence", 0) < 50 or
                not all(v.get(k) is True for k in ACCEPT_KEYS)
                for v in entry["verdicts"].values()), entry["company"]
    assert labels["counts"]["kept"] == len(labels["items"]) == len(sealed["items"])
    assert labels["counts"]["not_kept"] == len(labels["not_kept"])


def test_every_row_was_fetched_and_carries_its_evidence(sealed):
    for item in sealed["items"]:
        assert item.get("verified"), f"{item['id']} has no verification note"
        assert item["source_url"].startswith("http")
        assert "asktherecruiter.com" not in item["source_url"]
        assert item["source_type"] in goldset.VALID_SOURCE_TYPES
    for dropped in sealed.get("dropped_unreachable", []):
        assert dropped["source_url"] not in {i["source_url"] for i in sealed["items"]}


def test_no_row_cites_a_deal_or_people_database(sealed):
    from collectors import national_press
    people_data = ("owler", "zoominfo", "apollo.io", "rocketreach",
                   "linkedin.com", "signalhire", "lusha", "clearbit",
                   "peopledatalabs", "wellfound", "growjo")
    forbidden = tuple(national_press._AGGREGATOR_HOSTS) + people_data
    for item in sealed["items"]:
        blob = " ".join(str(item.get(key, "")) for key in
                        ("source_url", "source_name", "detail", "verified")).lower()
        for host in forbidden:
            assert host not in blob, (
                f"{item['id']} cites a blocked commercial database or people-data "
                f"host (entry {forbidden.index(host)} of the collectors' blocklist)")


def test_the_us_set_was_not_enumerated_from_edgar(sealed):
    """The US family's held-out rule, enforced against the file: a Form D on
    sec.gov is what our own collector walks."""
    if sealed["family"] != "us":
        pytest.skip("worldwide and European sets may carry filings")
    for item in sealed["items"]:
        assert "sec.gov" not in item["source_url"], item["id"]
    assert sealed["signal_types"] == ["funding"]
    for item in sealed["items"]:
        assert item["metro"] in {"San Francisco", "New York", "Austin", "Rest of US"}


def test_the_eu_set_is_european_and_nothing_else(sealed):
    if sealed["family"] != "eu":
        pytest.skip("only the European set carries the country list")
    for item in sealed["items"]:
        assert item["country"] in goldset.EU_COUNTRIES, item["id"]
    assert len({i["country"] for i in sealed["items"]}) >= 10


def test_no_dashes_reach_the_page_copy(sealed):
    """The set's method, caveats and details reach the recall page."""
    blob = json.dumps({k: v for k, v in sealed.items() if not k.startswith("_")},
                      ensure_ascii=False)
    assert not DASHES.search(blob), sealed["version"]
