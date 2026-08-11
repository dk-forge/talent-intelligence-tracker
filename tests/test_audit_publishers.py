"""Every publisher the rejection audit named is wired, or refused in writing.

`data/recall_rejection_audit.json` classifies 81 gold-set misses. Two of its
buckets are a worklist rather than a statistic:

    publisher_not_wired   a publisher we had researched and never wired
    publisher_unknown     a publisher nobody on this project had heard of

The failure this file exists to prevent is the quiet one: a publisher gets
looked at, turns out to be awkward, and the session ends without recording
anything — so the next session probes the same 15 paths, and the audit keeps
naming it. A refusal with evidence is a finished piece of work. Silence is not.

So the assertion is deliberately weak about the OUTCOME and strict about the
RECORD: each named domain must appear in `data/sources_catalogue.csv` either
with a feed we fetched, or with a `feed_checked` verdict and a note saying what
was tried. Nothing here says a publisher must be wired; several of them must
not be, and two of those refusals are the publisher's own robots.txt.

Offline: reads two files in `data/` and calls nothing.

Matching is on the REGISTRABLE DOMAIN, imported from the collector rather than
written a third time. There are already two implementations in this repo
(`collectors/national_press.py` and `analysis/recall/rejection_audit.py`) and a
third deciding which publishers count as handled would be the one that goes
stale first.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from collectors.national_press import registrable_domain

ROOT = Path(__file__).parent.parent
AUDIT = ROOT / "data" / "recall_rejection_audit.json"
CATALOGUE = ROOT / "data" / "sources_catalogue.csv"

ACTIONABLE = ("publisher_not_wired", "publisher_unknown")


def _audit_domains() -> dict[str, int]:
    """{registrable domain: how many gold misses it accounts for}."""
    audit = json.loads(AUDIT.read_text())
    counts: dict[str, int] = {}
    for item in audit["items"]:
        if item["stage"] in ACTIONABLE:
            domain = registrable_domain(item["domain"])
            counts[domain] = counts.get(domain, 0) + 1
    return counts


def _catalogue_rows() -> list[dict]:
    with CATALOGUE.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _rows_for(domain: str, rows: list[dict]) -> list[dict]:
    return [r for r in rows
            if domain in {registrable_domain(r.get("url") or ""),
                          registrable_domain(r.get("rss") or "")}]


def test_the_audit_still_gives_this_file_something_to_measure():
    """If the audit is regenerated and the actionable buckets vanish, this file
    is measuring nothing and should be read again rather than left green.

    Read again on 2026-08-10, as instructed. `publisher_unknown` had gone 12 ->
    0 while `publisher_not_wired` went 12 -> 16, and the drain is the work
    landing, not the measurement breaking: `publisher_unknown` means "domain is
    not in the catalogue at all", so researching a publisher necessarily empties
    it. All 12 that the 2026-08-03 run listed were answered on 2026-08-04 - 8
    wired to live feeds (which is why they now classify as
    `feed_read_item_missed`, a feed-depth problem rather than a source one) and
    4 refused with the status code seen (renewable-carbon.eu 500,
    commersant.ge / ctee.com.tw / sharesansar.com 404), all 4 of which remain in
    `publisher_not_wired` and so are still covered by the tests below.

    So the bucket-by-bucket form of this guard asserted that the project must
    permanently hold at least one unresearched publisher, and would fail exactly
    when the worklist was finished. What it was really protecting is that the
    tests below iterate over something; that is asserted directly here, on the
    same set they iterate, which is strictly closer to the hazard than counting
    one bucket was. Emptying BOTH buckets still fails, loudly.
    """
    audit = json.loads(AUDIT.read_text())
    actionable = sum(audit["stages"][bucket] for bucket in ACTIONABLE)
    assert actionable > 0, (
        "no publisher is in either actionable bucket, so every assertion below "
        "iterates an empty set and this file has stopped measuring recall loss")
    assert _audit_domains(), (
        "the actionable buckets are populated but resolve to zero domains, so "
        "the tests below are vacuous")


def test_every_publisher_the_audit_named_is_in_the_catalogue():
    rows = _catalogue_rows()
    missing = [d for d in _audit_domains() if not _rows_for(d, rows)]
    assert not missing, (
        "the rejection audit names these publishers and the catalogue has "
        f"never heard of them: {sorted(missing)}")


def test_every_publisher_the_audit_named_is_wired_or_refused_in_writing():
    """Wired means a feed URL. Refused means a dated `feed_checked` verdict.

    An empty `rss` AND an empty `feed_checked` is the outcome this test exists
    to fail on: it is indistinguishable from nobody having looked.
    """
    rows = _catalogue_rows()
    unanswered = []
    for domain, misses in sorted(_audit_domains().items()):
        matches = _rows_for(domain, rows)
        wired = any((r.get("rss") or "").startswith("http") for r in matches)
        refused = any((r.get("feed_checked") or "").strip() for r in matches)
        if not (wired or refused):
            unanswered.append(f"{domain} ({misses} gold miss(es))")
    assert not unanswered, (
        "these publishers cost us gold-set events and the catalogue records "
        f"neither a feed nor a reason: {unanswered}")


def test_a_refusal_says_what_was_tried_and_not_merely_that_it_failed():
    """`feed_checked` is a verdict; the evidence lives in `notes`.

    A bare 'not live' teaches the next session nothing and invites the same
    probe. Every refused publisher the audit named carries a note long enough
    to hold what was tried.
    """
    rows = _catalogue_rows()
    thin = []
    for domain in sorted(_audit_domains()):
        matches = _rows_for(domain, rows)
        if any((r.get("rss") or "").startswith("http") for r in matches):
            continue
        if not any(len((r.get("notes") or "").strip()) >= 200 for r in matches):
            thin.append(domain)
    assert not thin, (
        "refused with no written evidence, so the next session repeats the "
        f"work: {thin}")


def test_no_publisher_from_the_audit_was_wired_on_an_aggregator_domain():
    """The wires (Business Wire, GlobeNewswire, PR Newswire, Presseportal) are
    NOT aggregators here: a release they carry is the company's own
    announcement. Yahoo Finance is, and the block is by registrable domain, so
    a feed listed for it would be refused at load time with a daily line in the
    run log.

    Asserted against the loader's own sets, so this cannot drift from the rule
    it is quoting.
    """
    from collectors.national_press import _AGGREGATOR_DOMAINS, _AGGREGATOR_HOSTS

    rows = _catalogue_rows()
    for domain in sorted(_audit_domains()):
        for row in _rows_for(domain, rows):
            rss = (row.get("rss") or "").strip()
            if not rss.startswith("http"):
                continue
            assert registrable_domain(rss) not in _AGGREGATOR_DOMAINS, (
                f"{row['name']} is wired on an aggregator domain")

    # The precedent this reasoning rests on, pinned so a later edit that
    # blocked the wires by analogy would have to change it deliberately.
    assert not any("newswire" in h or "businesswire" in h or "presseportal" in h
                   for h in _AGGREGATOR_HOSTS), (
        "a press-release wire carries the company's own announcement and is "
        "not a discovery pointer; blocking one here would also have to explain "
        "the businesswire.com and prnewswire.com rows already in the database")


def test_the_feeds_wired_from_the_audit_record_what_they_returned():
    """A wired feed's `feed_checked` carries the numbers that justify it.

    'A feed that returns nothing is degraded, not coverage' — so the count and
    the age of the newest item are the evidence, and they are written down at
    the moment of wiring rather than inferred later from a health ledger that
    only holds the last run.
    """
    rows = _catalogue_rows()
    for domain in sorted(_audit_domains()):
        for row in _rows_for(domain, rows):
            if not (row.get("rss") or "").startswith("http"):
                continue
            checked = (row.get("feed_checked") or "").strip()
            assert "items" in checked and "newest" in checked, (
                f"{row['name']} was wired without recording what it returned: "
                f"{checked!r}")
            assert (row.get("feed_role") or "").strip() == "direct"
            assert (row.get("feed_kind") or "").strip() in ("rss", "atom")
