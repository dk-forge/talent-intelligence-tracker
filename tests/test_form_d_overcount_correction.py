"""Withdrawing the Form D rows whose "amount sold" is not money raised.

Every assertion here is a way one of the three rules could quietly widen into
deleting real raises, which is the failure this correction can have that nobody
would notice: a withdrawal removes a true record and the page looks fine
afterwards. The rescues are named individually on purpose — the seven cash
placements that fund an acquisition, the fresh evergreen offerings, the
separately numbered second offering — because each of them is a row a slightly
lazier rule would have taken.
"""

from __future__ import annotations

import datetime

import correct_form_d_overcount as correct


def _fact(**over):
    fact = {
        "filing_date": "12-MAY-2026", "file_num": "021-571022", "cik": "0002104151",
        "is_bizcomb": "false", "bizcomb_note": "",
        "total_offering": "849999997", "more_than_one_year": "false",
        "sale_date": "2026-01-10",
    }
    fact.update(over)
    return fact


def _row(accession, usd, **over):
    row = {"signal_id": f"sig-{accession}", "content_hash": accession,
           "company": "Fluidstack Ltd", "published_date": "2026-05-12",
           "funding_amount_usd": usd, "accession": accession,
           "source_url": f"https://www.sec.gov/Archives/edgar/data/1/{accession}/primary_doc.xml"}
    row.update(over)
    return row


# --- rule 1: business combinations ----------------------------------------


def test_a_business_combination_is_withdrawn():
    """Danaher acquiring Masimo at $180 a share, published as Masimo raising
    $9.9bn. The field that says so has been in the data set the whole time."""
    assert correct.is_business_combination(_fact(
        is_bizcomb="true",
        bizcomb_note="Issuance of shares in connection with the acquisition of XConn"))


def test_a_cash_raise_that_funds_an_acquisition_survives():
    """The seven rows this rescue exists for, worth $0.75bn. Money came IN and
    was then spent; that is a raise, and the badge on it is true.

    Every phrase is quoted from a filing this correction touched, so the rescue
    cannot drift into wishful matching.
    """
    for note in (
        "A portion of the proceeds of the sale of securities to investors was "
        "used to acquire all outstanding equity of the target.",
        "This offering was made to partially fund the acquisition whereby the "
        "Issuer purchased certain assets.",
        "Funds are being used to acquire a hospital.",
        "Proceeds of the financing are being used as part of an acquisition.",
        "The private placement (PIPE) financing closed concurrently with a "
        "Merger transaction.",
    ):
        assert not correct.is_business_combination(_fact(is_bizcomb="true", bizcomb_note=note)), note


def test_a_filing_that_never_answered_the_question_is_left_alone():
    """`false` and blank are different from `true`, and the overwhelming
    majority of Form D filings are one of the first two."""
    for value in ("false", "", "FALSE ", "no"):
        assert not correct.is_business_combination(_fact(is_bizcomb=value))


# --- rule 2: uncapped continuous offerings ---------------------------------


def test_a_decade_old_uncapped_offering_is_withdrawn():
    """OPTCAPITAL LLC: the fourteenth annual amendment to an offering whose
    first sale was 2012, published as a $1.77bn round."""
    assert correct.is_continuous_offering(_fact(
        total_offering="Indefinite", more_than_one_year="true",
        sale_date="2012-07-22", filing_date="22-JUN-2026"))


def test_a_fresh_uncapped_offering_survives():
    """The cost of the naive version, and why the two extra tests exist.

    "Indefinite" alone would have taken 138 live rows worth $1.70bn, Harvey
    AI's $200m among them: an uncapped offering that opened this quarter is a
    round, and only the years of accumulated sales make it a cumulative total.
    """
    assert not correct.is_continuous_offering(_fact(
        total_offering="Indefinite", more_than_one_year="false",
        sale_date="2026-05-01", filing_date="15-MAY-2026"))
    # Even where the issuer says it will run over a year, it has not yet.
    assert not correct.is_continuous_offering(_fact(
        total_offering="Indefinite", more_than_one_year="true",
        sale_date="2026-05-01", filing_date="15-MAY-2026"))


def test_a_capped_offering_is_never_continuous():
    assert not correct.is_continuous_offering(_fact(
        total_offering="750000000", more_than_one_year="true", sale_date="2012-07-22"))


def test_the_age_test_needs_both_dates():
    """A missing or unparseable date must decline the rule rather than default
    to withdrawing. Absence of evidence is not evidence."""
    assert not correct.is_continuous_offering(_fact(
        total_offering="Indefinite", more_than_one_year="true", sale_date=""))
    assert not correct.is_continuous_offering(_fact(
        total_offering="Indefinite", more_than_one_year="true",
        sale_date="2012-07-22", filing_date=""))


# --- rule 3: an offering already published ---------------------------------


FLUIDSTACK = {
    # The January D and the May D/A are ONE offering: same file number, same
    # first sale. The D/A carries the whole running total.
    "aaa": _fact(filing_date="23-JAN-2026", file_num="021-571022"),
    "bbb": _fact(filing_date="12-MAY-2026", file_num="021-571022"),
    # June opened a genuinely separate offering under a NEW file number.
    "ccc": _fact(filing_date="30-JUN-2026", file_num="021-589157", sale_date="2026-06-15"),
}


def test_the_earlier_filing_for_one_offering_is_withdrawn():
    rows = [_row("aaa", 450_000_000), _row("bbb", 842_478_689), _row("ccc", 729_999_546)]
    dropped = correct.superseded(rows, FLUIDSTACK)
    assert [r["accession"] for r in dropped] == ["aaa"]


def test_the_latest_filing_is_what_survives_not_the_largest_and_not_the_first():
    """An amendment restates the offering's running total, so the last filing
    is the whole raise. In one of the 66 offerings this touches the issuer
    revised its own total DOWN, and its latest answer is still the right one:
    keeping the largest would republish a figure the filer has withdrawn.
    """
    facts = {"aaa": _fact(filing_date="17-APR-2026"), "bbb": _fact(filing_date="15-MAY-2026")}
    dropped = correct.superseded([_row("aaa", 4_985_000), _row("bbb", 4_335_000)], facts)
    assert [r["accession"] for r in dropped] == ["aaa"]


def test_a_second_offering_by_the_same_issuer_is_not_a_duplicate():
    """Keyed on the SEC file number, never on the issuer. Fluidstack's June
    offering is real money and grouping by CIK would have deleted $730m of it.
    """
    dropped = correct.superseded(
        [_row("bbb", 842_478_689), _row("ccc", 729_999_546)], FLUIDSTACK)
    assert dropped == []


def test_one_offering_published_once_is_never_touched():
    assert correct.superseded([_row("bbb", 842_478_689)], FLUIDSTACK) == []


# --- the buckets, and what may not overlap ---------------------------------


def test_a_row_is_withdrawn_for_one_reason_only():
    """Otherwise the counts on the corrections page add up to more rows than
    exist, which is the shape of the defect this correction is fixing."""
    rows = [_row("aaa", 450_000_000), _row("bbb", 842_478_689)]
    facts = {"aaa": _fact(filing_date="23-JAN-2026", is_bizcomb="true"),
             "bbb": _fact(filing_date="12-MAY-2026")}
    buckets = correct.to_withdraw(rows, facts)
    everything = [r["accession"] for bucket in buckets.values() for r in bucket]
    assert len(everything) == len(set(everything))
    # And the row taken by rule 1 does not then make the survivor a duplicate.
    assert buckets["bizcomb"] and not buckets["superseded"]


def test_a_row_with_no_filing_in_the_archives_is_left_alone():
    """The current quarter has no data set until it ends. Those rows stay
    published rather than being withdrawn for want of evidence."""
    buckets = correct.to_withdraw([_row("zzz", 1_000_000)], FLUIDSTACK)
    assert all(not bucket for bucket in buckets.values())


# --- the guard -------------------------------------------------------------


def test_the_share_that_stops_the_run_is_above_the_measured_one():
    """A truncated archive reads as "none of it qualifies". The measured
    withdrawal is 11% of the published Form D rows; the refusal sits at 30%, far
    enough not to trip on ordinary drift and near enough to catch a bad
    download."""
    assert 0.11 < correct.MAX_WITHDRAWAL_SHARE < 0.5


def test_every_bucket_has_a_reason_a_reader_could_check():
    """"data quality" satisfies a schema and tells a reader nothing. Each reason
    says what the filing states, because it is shown on the record."""
    assert set(correct.REASONS) == {"bizcomb", "continuous", "superseded"}
    for name, reason in correct.REASONS.items():
        assert len(reason) > 60, name
        assert "filing" in reason or "offering" in reason or "amendment" in reason, name


def test_a_run_of_failures_stops_instead_of_hammering_a_host_that_is_down():
    """The host fell over twice on 2026-07-30. A withdrawal loop that keeps
    going turns one outage into three hundred failed requests."""
    assert 1 < correct.CONSECUTIVE_FAILURE_LIMIT <= 10
    assert correct.PAUSE_SECONDS > 0


def test_the_filing_date_is_read_in_the_format_the_archive_uses():
    """DD-MMM-YYYY is Oracle's default and nothing else in the pipeline speaks
    it. Read wrongly, every offering sorts by accession and rule 3 keeps an
    arbitrary row."""
    assert correct.filed_on({"filing_date": "12-MAY-2026"}) == datetime.date(2026, 5, 12)
    assert correct.filed_on({"filing_date": ""}) is None
    assert correct.first_sale_on({"sale_date": "2026-01-10"}) == datetime.date(2026, 1, 10)
