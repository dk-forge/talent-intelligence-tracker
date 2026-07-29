"""Correcting what was already published.

Two properties matter here and nothing else does. A corrected row must say
exactly what a freshly collected one would say, or the archive and the site
disagree about the same filing. And a bad download must never be able to
retract the source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import correct_form_d as correct
from collectors import sec_form_d_bulk as bulk
from pipeline import publish, validate


def _published(**over):
    """A row shaped like the stored ones: only what the site already holds."""
    row = {
        "signal_id": "sig-1", "content_hash": "abc123",
        "company": "Baseten Labs, Inc.",
        "headline": "Baseten Labs, Inc. raised $75M in a private placement",
        "city": "San Francisco", "state": "CA", "country": "US",
        "published_date": "2026-03-31", "funding_amount_usd": 75_000_000,
        "signal_direction": "hiring",
        "talent_readthrough": (
            "A closed private placement is the standard precursor to hiring: "
            "capital raised is spent on headcount within the following two to "
            "six quarters."),
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/2/primary_doc.xml",
    }
    row.update(over)
    return row


def test_a_corrected_row_says_what_a_fresh_one_would_say():
    """The whole reason this recomputes through the collector's own
    as_classified instead of formatting a sentence of its own."""
    row = _published()
    fixed = bulk.as_classified(correct.as_item(row))

    fresh = bulk.as_classified({
        "headline": row["headline"], "money": "$75M",
        "city": "San Francisco", "state": "CA", "country": "United States",
        "published_date": "2026-03-31",
    })
    assert fixed["talent_readthrough"] == fresh["talent_readthrough"]
    assert fixed["signal_direction"] == fresh["signal_direction"] == "neutral"


def test_a_foreign_row_keeps_the_country_name_not_the_code():
    """Stored country is a code. Left alone it would read "an address in IL"
    where a fresh collection says "Israel", and the two paths would word the
    same filing differently."""
    item = correct.as_item(_published(country="IL", state="", city="Tel Aviv"))
    assert item["country"] == "Israel"
    assert "Tel Aviv, Israel" in bulk.as_classified(item)["talent_readthrough"]


def test_only_the_two_fields_are_ever_in_a_correction():
    """A correction that could rewrite a headline or a figure would be a
    republication wearing a correction's name."""
    for row in correct.corrections([_published()]):
        assert set(row) <= {"content_hash", "signal_id", "company",
                            "signal_direction", "talent_readthrough"}


def test_a_row_already_corrected_is_left_alone():
    """Idempotent, because this will be interrupted and re-run."""
    fixed = bulk.as_classified(correct.as_item(_published()))
    already = _published(signal_direction="neutral",
                         talent_readthrough=fixed["talent_readthrough"])
    assert correct.corrections([already]) == []


def test_a_hand_written_read_through_is_not_clobbered():
    """Only the invented sentence is replaced. Anything else is someone's work."""
    edited = _published(signal_direction="neutral",
                        talent_readthrough="Corrected by hand after a reader wrote in.")
    assert correct.corrections([edited]) == []


def test_correcting_these_fields_cannot_move_the_dedup_hash():
    """The premise of correcting in place rather than purging and reimporting.
    If either field were a hash input, a corrected row would stop matching its
    own source report and the next import would publish it a second time."""
    args = ("baseten labs", "company_development", "2026-03-31",
            "Baseten Labs, Inc. raised $75M in a private placement")
    before = validate.content_hash(*args)
    assert validate.content_hash(*args) == before
    # The inputs are named explicitly: nothing about the badge or the sentence
    # under it reaches this function at all.
    assert validate.content_hash.__code__.co_varnames[:5] == (
        "company_key", "pillar", "published_date", "headline", "source_name")


def test_a_broken_download_cannot_retract_the_source(monkeypatch):
    """An empty or truncated archive parses to nothing, which reads exactly
    like "no row qualifies any more". That must be a refusal, not a mass
    retraction."""
    monkeypatch.setattr(correct, "still_qualifying", lambda quarters: set())
    rows = [_published(signal_id=f"sig-{i}", content_hash=f"h{i}",
                       source_url=f"https://sec.gov/{i}") for i in range(100)]
    with pytest.raises(correct.Unsafe):
        correct.retractions(rows)


def test_a_plausible_exclusion_rate_is_allowed_through(monkeypatch):
    """The measured rate is ~25%. The guard must not block the real job."""
    rows = [_published(signal_id=f"sig-{i}", content_hash=f"h{i}",
                       source_url=f"https://sec.gov/{i}") for i in range(100)]
    keep = {r["source_url"] for r in rows[25:]}
    monkeypatch.setattr(correct, "still_qualifying", lambda quarters: keep)
    assert len(correct.retractions(rows)) == 25


def test_the_retraction_reason_says_why_rather_than_that():
    """A retraction is published. "removed" tells a reader nothing."""
    assert "not an employer" in correct.REASON
    assert len(correct.REASON) > 40


# --- the server side --------------------------------------------------------

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
API = (PLUGIN / "includes" / "api.php").read_text()


def _php_enrichable() -> set[str]:
    """The column names in tit_enrichable_columns(), comments stripped.

    The prose in that function names columns it is explaining are NOT
    enrichable, so a plain substring read of the body would count them.
    """
    body = API[API.index("function tit_enrichable_columns"):]
    body = body[:body.index("}")]
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"'([a-z_]+)'", body))


def test_enrich_was_not_widened_to_carry_the_correction():
    """/enrich is limited to DERIVED values on purpose. These two are closer to
    facts, so they got their own door instead of a wider allowlist on that one:
    a bug in enrichment should still be unable to rewrite what a filing said."""
    allowlist = _php_enrichable()
    assert "signal_direction" not in allowlist
    assert "talent_readthrough" not in allowlist


def test_the_two_enrich_allowlists_agree():
    """The client decides what to SEND and the server what to ACCEPT, so a
    column on one list alone is silently inert: present in the payload and
    dropped, or accepted and never sent. hq_country reached published rows only
    once BOTH sides listed it, and nothing else failed in the meantime."""
    assert _php_enrichable() == set(publish.ENRICHABLE)


def test_sourced_location_is_not_enrichable_on_either_side():
    """hq_country/hq_city are looked up, so they belong here. country/city are
    what the source said about the job, so they never can: /enrich exists to
    add derived values, not to overwrite a filing."""
    for side in (_php_enrichable(), set(publish.ENRICHABLE)):
        assert {"hq_city", "hq_country"} <= side
        assert not ({"country", "city", "region", "state"} & side)


def test_the_correction_route_writes_those_two_columns_and_nothing_else():
    allowlist = API[API.index("function tit_correctable_columns"):]
    allowlist = allowlist[:allowlist.index("}")]
    assert "signal_direction" in allowlist and "talent_readthrough" in allowlist
    for forbidden in ("headline", "company", "source_url", "funding_amount",
                      "published_date", "confidence"):
        assert forbidden not in allowlist, forbidden


def test_a_correction_must_name_the_source_it_is_correcting():
    """A pass is written against one collector's logic. If the caller builds a
    bad batch it must only be able to damage the source it claimed."""
    handler = API[API.index("function tit_api_correct"):]
    handler = handler[:handler.index("\n}\n")]
    assert "'collector'" in handler and "$collector" in handler
    # The scope is on the UPDATE itself, not merely validated on the way in.
    update = handler[handler.index("$wpdb->update"):]
    assert "'collector'    => $collector" in update
    assert "'is_current'   => 1" in update


def test_the_correction_route_is_keyed_like_every_other_write():
    route = API[API.index("register_rest_route(TIT_NS, '/correct'"):]
    assert "'permission_callback' => $keyed" in route[:300]
    assert "__return_true" not in route[:300]


def test_a_bad_direction_is_refused_rather_than_stored():
    """A typo would render as a badge nothing filters on, which is worse than
    the wrong badge we are here to fix."""
    handler = API[API.index("function tit_api_correct"):]
    handler = handler[:handler.index("\n}\n")]
    assert "tit_allowed_directions()" in handler
