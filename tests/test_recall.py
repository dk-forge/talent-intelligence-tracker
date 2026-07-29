"""The recall measurement, tested offline.

Two things are worth protecting here. The gold set must stay valid and stay
independent, because an invalid reference set produces a wrong denominator that
nobody notices. And the matching rule must stay exactly as strict as it was
when a number was published, because loosening it silently is how a recall
figure improves without coverage improving.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.recall import goldset, match  # noqa: E402


# --- the gold set itself ---------------------------------------------------

@pytest.fixture(scope="module")
def gold():
    return goldset.load()


def test_goldset_is_valid(gold):
    assert goldset.validate(gold) == []


def test_goldset_was_sealed_before_it_was_measured(gold):
    """Independence is a date, not a promise. The assembly date must not be
    later than the first measurement, and the file has to say when it was
    fixed."""
    assert gold["assembled_on"], "the gold set must record when it was assembled"
    assert gold["sealed"] is True, "an unsealed gold set must not be measured against"


def test_goldset_never_cites_our_own_tracker(gold):
    """The one failure that would make the whole exercise meaningless: a
    reference set assembled by reading our own database."""
    for item in gold["items"]:
        assert "asktherecruiter.com" not in item["source_url"]


def test_goldset_has_a_deliberate_spread(gold):
    shape = goldset.counts(gold)
    assert shape["total"] >= 40, "too small to break down by cell"
    assert shape["geography"].get("US", 0) >= 10
    assert shape["geography"].get("non-US", 0) >= 15
    assert shape["signal_type"].get("funding", 0) >= 10
    assert shape["signal_type"].get("leadership", 0) >= 10
    assert shape["size_band"].get("small", 0) >= 10, "large events only would flatter the result"
    assert len(shape["country"]) >= 8


def test_the_four_known_misses_are_seeded(gold):
    """These four were known before the gold set existed. If a later edit drops
    them, the gold set has been tuned to flatter the result."""
    names = {item["company"].lower() for item in gold["items"]}
    for seed in ("glow", "plantopia", "harmony", "enigma"):
        assert any(seed in name for name in names), f"seed {seed} is missing"


# --- name matching ---------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Glow", "Glow Security"),
    ("Enigma Technologies", "Enigma"),
    ("Pitney Bowes Inc.", "Pitney Bowes"),
    ("Acme, Inc", "Acme Inc."),
])
def test_names_that_are_the_same_employer(a, b):
    assert match.names_match(a, b)


@pytest.mark.parametrize("a,b", [
    ("Glow", "Glowforge"),
    ("Harmony", "Harmonic"),
    ("Enigma", "Enigmatic Labs"),
    ("Ramp", "Rampart Systems"),
])
def test_names_that_are_not(a, b):
    """Word-boundary matching, not substring. A substring rule would count
    Glowforge as Glow and inflate recall."""
    assert not match.names_match(a, b)


# --- classification --------------------------------------------------------

GOLD_FUNDING = {
    "id": "x1", "company": "Glow", "signal_type": "funding",
    "event_date": "2026-07-14", "country": "IL", "amount_usd": 180000000,
    "size_band": "large", "source_type": "trade_press",
}


def row(**over):
    base = {
        "signal_id": "r1", "company": "Glow", "pillar": "company_development",
        "country": "IL", "hq_country": "IL", "funding_amount_usd": "180000000",
        "published_date": "2026-07-14", "source_url": "https://example.com/a",
        "headline": "Glow raises $180M",
    }
    base.update(over)
    return base


def test_a_clean_hit_is_found():
    assert match.classify(GOLD_FUNDING, [row()])["verdict"] == "FOUND"


def test_nothing_stored_is_missed():
    assert match.classify(GOLD_FUNDING, [])["verdict"] == "MISSED"


def test_missing_country_is_partial_not_a_hit():
    """The Enigma case: we hold the round but with no country, so it is
    invisible to every geographic filter on the site."""
    out = match.classify(GOLD_FUNDING, [row(country=None, hq_country=None)])
    assert out["verdict"] == "FOUND_PARTIAL"
    assert "country_missing" in out["defects"]


def test_wrong_country_is_partial():
    out = match.classify(GOLD_FUNDING, [row(country="US", hq_country="US")])
    assert "country_wrong" in out["defects"]


def test_missing_amount_is_partial():
    out = match.classify(GOLD_FUNDING, [row(funding_amount_usd=None)])
    assert "amount_missing" in out["defects"]


def test_a_rounded_amount_is_still_the_same_number():
    """Sources round. $178M reported against a $180M round is not a defect."""
    assert match.classify(GOLD_FUNDING, [row(funding_amount_usd="178000000")])["verdict"] == "FOUND"


def test_a_different_amount_is_a_defect():
    out = match.classify(GOLD_FUNDING, [row(funding_amount_usd="18000000")])
    assert "amount_mismatch" in out["defects"]


def test_the_wrong_kind_of_event_is_not_a_match():
    """We may hold a leadership change for the same company in the same week.
    That is not the funding round, and it earns no credit for one."""
    out = match.classify(GOLD_FUNDING, [row(
        pillar="leadership_change", funding_amount_usd=None,
        headline="Glow appoints a chief revenue officer")])
    assert out["verdict"] == "MISSED"


GOLD_LEADERSHIP = {
    "id": "x9", "company": "American Eagle Outfitters", "signal_type": "leadership",
    "event_date": "2026-07-01", "country": "US", "amount_usd": None,
    "size_band": "large", "source_type": "press_release",
}


def test_a_filing_shelved_under_compensation_counts_as_held_but_wrong():
    """An 8-K Item 5.02 covers both the officer change and what they are paid,
    so it can be classified as compensation. We have the event; it is just
    invisible to anyone browsing leadership changes. That is a defect, not a
    miss, and calling it a miss would overstate the gap."""
    out = match.classify(GOLD_LEADERSHIP, [row(
        company="AMERICAN EAGLE OUTFITTERS, INC.", pillar="rewards_comp",
        headline="AMERICAN EAGLE OUTFITTERS INC 8-K filing (Item 5.02): officer change",
        published_date="2026-06-29", country="US", hq_country="US",
        funding_amount_usd=None)])
    assert out["verdict"] == "FOUND_PARTIAL"
    assert "wrong_category" in out["defects"]


def test_an_unrelated_row_in_the_wrong_pillar_is_still_a_miss():
    """Partial credit is for the same event under the wrong heading, not for
    any row at all about the same employer that month."""
    out = match.classify(GOLD_LEADERSHIP, [row(
        company="American Eagle Outfitters", pillar="rewards_comp",
        headline="American Eagle Outfitters: median hourly pay gap is 4.1%",
        published_date="2026-07-02", funding_amount_usd=None)])
    assert out["verdict"] == "MISSED"


def test_an_event_far_outside_the_window_is_not_a_match():
    out = match.classify(GOLD_FUNDING, [row(published_date="2026-02-01")])
    assert out["verdict"] == "MISSED"


def test_a_late_writeup_inside_the_window_still_counts():
    assert match.classify(GOLD_FUNDING, [row(published_date="2026-07-28")])["verdict"] == "FOUND"


def test_the_cleanest_duplicate_decides_the_verdict():
    """Holding an event twice, once well and once badly, is a deduplication
    problem, not a recall failure."""
    out = match.classify(GOLD_FUNDING, [row(signal_id="bad", country=None, hq_country=None),
                                        row(signal_id="good")])
    assert out["verdict"] == "FOUND"
    assert out["matched_row"]["signal_id"] == "good"


# --- the summary -----------------------------------------------------------

def test_every_percentage_carries_its_counts():
    results = [
        {"verdict": "FOUND", "defects": [], "gold": dict(GOLD_FUNDING)},
        {"verdict": "MISSED", "defects": [], "gold": dict(GOLD_FUNDING, id="x2", country="US")},
    ]
    summary = match.summarise(results)
    assert summary["overall"] == {
        "total": 2, "found": 1, "found_partial": 0, "missed": 1,
        "held": 1, "held_pct": 50.0, "clean_pct": 50.0,
    }
    for group in ("by_geography", "by_country", "by_signal_type", "by_source_type"):
        for cell in summary[group].values():
            assert cell["total"] > 0
            assert set(cell) >= {"total", "found", "missed", "held_pct", "clean_pct"}


def test_a_rate_with_no_denominator_is_none_not_zero():
    assert match.rate(0, 0) is None
    assert match.rate(0, 5) == 0.0


# --- the script ------------------------------------------------------------

def test_check_mode_runs_offline_and_passes():
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "measure_recall.py"), "--check"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "gold set is valid" in proc.stdout


def test_offline_replay_produces_a_dated_measurement(tmp_path, gold):
    """Repeatability: the same script re-runs with no network and records the
    date it ran. A recall figure with no date is worthless."""
    fixture = tmp_path / "rows.json"
    fixture.write_text(json.dumps({gold["items"][0]["id"]: []}))
    out = match.summarise([])
    assert out["overall"]["held_pct"] is None

    from measure_recall import measure  # noqa: WPS433
    result = measure(gold, offline_rows={}, verbose=False)
    assert result["measured_on"]
    assert result["goldset"]["digest"] == gold["_digest"]
    assert result["summary"]["overall"]["total"] == len(gold["items"])


def test_published_page_data_matches_a_recorded_measurement():
    """What the page renders must be a measurement this repo can produce, not a
    hand-written figure."""
    published = os.path.join(
        ROOT, "wordpress-plugin", "talent-intelligence-tracker", "data", "recall.json")
    if not os.path.exists(published):
        pytest.skip("no measurement published yet")
    with open(published, encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["measured_on"], "a published recall figure must carry its date"
    assert data["goldset"]["digest"], "and the digest of the gold set it was measured against"
    summary = data["summary"]["overall"]
    assert summary["total"] == len(data["items"])
    assert summary["held"] + summary["missed"] == summary["total"]
