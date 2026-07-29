"""Withdrawing the workforce reductions that reached a page promising none.

Four 8-K filings were live on the dashboard — Atlassian (~10% of its
workforce), Groupon (up to 400 positions), IO Biotech and Lyra Therapeutics —
because the scope guard read the HEADLINE and `sec_edgar` stamps one synthetic
headline onto every document it fetches.

The backward sweep has two jobs of equal weight: find every row like them, and
withdraw NOTHING else. The second is the harder one — 3,777 live rows are
sec_edgar leadership changes, and a rule that over-reaches takes the pillar
this product is largest in off the site.
"""

from __future__ import annotations

import pytest

import correct_layoff_scope as scope
from pipeline import schema, store, validate

HEADLINE = "{} 8-K filing (Item 5.02): officer or director change"

# The real filings, in their own words.
ATLASSIAN = (
    "FORM 8-K CURRENT REPORT. Item 2.05 Costs Associated with Exit or Disposal "
    "Activities. On March 11, 2026 Atlassian Corporation announced a "
    "restructuring that results in the elimination of certain roles, impacting "
    "approximately 10% of the Company's workforce."
)
APPOINTMENT = (
    "Item 5.02 Departure of Directors or Certain Officers. On June 29, 2026 the "
    "Board appointed Jane Doe as Chief Financial Officer. Ms. Doe previously led "
    "the finance function through the 2024 layoffs at her former employer, and "
    "will receive severance and termination benefits on a qualifying exit."
)


def raw(company="Acme Corp", body=APPOINTMENT, url=None):
    return {
        "raw_text": f"{HEADLINE.format(company)}\n\n{body}",
        "headline": HEADLINE.format(company),
        "source_url": url or f"https://www.sec.gov/Archives/edgar/data/1/{company[:4]}-8k.htm",
        "source_name": "SEC EDGAR",
        "published_date": "2026-06-29",
        "country": "United States",
    }


def read_as(company="Acme Corp", **over):
    base = {
        "company": company,
        "pillar": "leadership_change",
        "signal_direction": "neutral",
        "confidence": "verified",
        "headline": HEADLINE.format(company),
        "summary": f"{company} appointed a new Chief Financial Officer.",
        "talent_readthrough": "A finance leadership seat has changed hands.",
    }
    base.update(over)
    return base


def stored_row(conn, company="Acme Corp", body=APPOINTMENT, published=True, **over):
    """A row exactly as it went onto the site BEFORE the body arm existed.

    Built through a collector name whose raw_text does not trip the guard, then
    labelled sec_edgar — because that is what these rows are: filings that got
    past a guard that could not see them.
    """
    signal = validate.build_signal(read_as(company, **over),
                                   raw(company, APPOINTMENT), "google_news")
    signal.collector = scope.DOCUMENT_COLLECTOR
    store.store(conn, signal)
    if published:
        conn.execute("UPDATE signals SET published_at = '2026-07-01' "
                     "WHERE signal_id = ?", (signal.signal_id,))
    conn.commit()
    return signal


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


# --- finding them ----------------------------------------------------------

def test_the_document_convicts_a_row_whose_stored_text_never_could(conn):
    """Atlassian's stored summary says "elimination of certain roles", which
    the reduction vocabulary does not match. Judging these rows on what is in
    the database would reproduce the original defect one level up."""
    signal = stored_row(conn, "Atlassian Corp")
    assert scope.stored_verdict(dict(
        conn.execute("SELECT * FROM signals WHERE signal_id = ?",
                     (signal.signal_id,)).fetchone())) is None

    report = scope.sweep(conn, fetch=lambda url: ATLASSIAN)
    assert [row["company"] for row, _ in report["document_hits"]] == ["Atlassian Corp"]
    assert scope.to_withdraw(report)


def test_an_ordinary_appointment_survives_the_sweep(conn):
    stored_row(conn, "Acme Corp")
    report = scope.sweep(conn, fetch=lambda url: APPOINTMENT)
    assert report["document_hits"] == []
    assert scope.to_withdraw(report) == []


def test_a_reduction_the_publisher_headlined_is_found_without_the_network(conn):
    """The free pass exists because not every collector fetches a document. The
    live Verizon row came from google_news with the publisher's own headline."""
    # A row from BEFORE the guard: built clean, then given the text it really
    # carried. It cannot be built with that text now, which is the forward fix
    # working.
    signal = validate.build_signal(
        read_as("Verizon", pillar="company_development", signal_direction="neutral"),
        {"raw_text": "Verizon reports quarterly results.",
         "headline": "Verizon reports quarterly results.",
         "source_url": "https://example.com/business/verizon-cuts",
         "source_name": "Example News", "published_date": "2026-07-23"},
        "google_news")
    signal.headline = "Verizon lays off 3,000 employees in the U.S."
    signal.summary = "Verizon plans to cut 3,000 roles."
    signal.talent_readthrough = "3,000 roles leaving the US market."
    store.store(conn, signal)
    conn.commit()

    report = scope.sweep(conn, stored_only=True)
    assert [row["company"] for row, _ in report["stored_hits"]] == ["Verizon"]
    assert report["fetched"] == 0, "--stored-only must not touch the network"


def test_the_two_passes_never_withdraw_the_same_row_twice(conn):
    signal = stored_row(conn, "Atlassian Corp",
                        summary="Atlassian announces a restructuring plan and a "
                                "workforce reduction.")
    report = scope.sweep(conn, fetch=lambda url: ATLASSIAN)
    assert len(report["stored_hits"]) == 1 and len(report["document_hits"]) == 1
    hits = scope.to_withdraw(report)
    assert [row["signal_id"] for row, _ in hits] == [signal.signal_id]


# --- and the reverse: not over-reaching ------------------------------------

def test_a_document_that_will_not_load_is_unknown_and_never_clean(conn):
    """Reporting unknown as clean is the shape of every defect in this repo's
    incident log."""
    stored_row(conn, "Acme Corp")

    def dead(url):
        raise OSError("connection reset")

    report = scope.sweep(conn, fetch=dead)
    assert len(report["unreadable"]) == 1
    assert report["document_hits"] == []
    assert scope.to_withdraw(report) == []


def test_a_sweep_where_every_fetch_failed_goes_red_rather_than_green(conn, monkeypatch):
    """A document pass that read nothing looks exactly like a clean corpus."""
    stored_row(conn, "Acme Corp")
    monkeypatch.setattr(scope.schema, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(scope.sec_edgar, "fetch_text",
                        lambda url, **k: (_ for _ in ()).throw(OSError("403")))
    assert scope.main(["--dry-run"]) == 1


def test_an_implausible_share_of_matches_is_refused_rather_than_applied(conn):
    """A withdrawal takes rows off the live site. A rule that suddenly matches
    hundreds is a broken rule, not a discovery."""
    for n in range(scope.MIN_ROWS + 10):
        stored_row(conn, f"Acme {n} Corp")
    report = scope.sweep(conn, fetch=lambda url: ATLASSIAN)

    with pytest.raises(scope.Unsafe) as caught:
        scope.to_withdraw(report)
    assert "ceiling" in str(caught.value)
    assert len(scope.to_withdraw(report, force=True)) == scope.MIN_ROWS + 10


def test_a_small_corpus_is_not_blast_radius_checked_into_uselessness(conn):
    """The share guard needs a denominator. On three rows, one match is 33%."""
    stored_row(conn, "Atlassian Corp")
    report = scope.sweep(conn, fetch=lambda url: ATLASSIAN)
    assert scope.to_withdraw(report), "the guard fired on a corpus too small to judge"


# --- how it withdraws ------------------------------------------------------

def test_a_withdrawal_goes_through_the_retract_path_and_deletes_nothing(conn, monkeypatch):
    signal = stored_row(conn, "Atlassian Corp")
    calls = []
    monkeypatch.setattr(scope.retract, "retract_remote",
                        lambda sid, reason: calls.append((sid, reason)) or {"retracted": 1})
    monkeypatch.setattr(scope.schema, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(scope.sec_edgar, "fetch_text", lambda url, **k: ATLASSIAN)

    assert scope.main([]) == 0
    assert [sid for sid, _ in calls] == [signal.signal_id]

    row = conn.execute("SELECT is_current, notes FROM signals WHERE signal_id = ?",
                       (signal.signal_id,)).fetchone()
    assert row["is_current"] == 0, "the row is still live locally"
    assert "workforce reduction" in row["notes"]
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1, (
        "nothing is ever deleted — the corrections log has to be able to say "
        "what was published and when it came down")


def test_the_site_comes_first_so_a_half_failure_is_re_found(conn, monkeypatch):
    """Remote then local. A row out of the database but still on the site is
    invisible to the next run; the reverse is merely re-done."""
    stored_row(conn, "Atlassian Corp")
    monkeypatch.setattr(scope.retract, "retract_remote",
                        lambda sid, reason: (_ for _ in ()).throw(
                            scope.publish.PublishError("503")))
    monkeypatch.setattr(scope.schema, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(scope.sec_edgar, "fetch_text", lambda url, **k: ATLASSIAN)

    assert scope.main([]) == 1, "a failed withdrawal must fail the run"
    assert conn.execute("SELECT is_current FROM signals").fetchone()[0] == 1, (
        "the local row was retired while the site kept publishing it")


def test_a_dry_run_writes_nothing(conn, monkeypatch):
    signal = stored_row(conn, "Atlassian Corp")
    monkeypatch.setattr(scope.retract, "retract_remote",
                        lambda *a: pytest.fail("a dry run reached the site"))
    monkeypatch.setattr(scope.schema, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(scope.sec_edgar, "fetch_text", lambda url, **k: ATLASSIAN)

    assert scope.main(["--dry-run"]) == 0
    assert conn.execute("SELECT is_current FROM signals WHERE signal_id = ?",
                        (signal.signal_id,)).fetchone()[0] == 1


def test_the_predicates_are_the_pipeline_s_own_and_not_a_second_copy(conn):
    """A correction that re-implements the rule it is correcting to drifts away
    from it, and then the two disagree about what is live."""
    from pipeline import prefilter

    assert scope.stored_verdict.__module__ == "correct_layoff_scope"
    source = __import__("inspect").getsource(scope)
    assert "prefilter.filing_reduction_plan" in source
    assert "prefilter.workforce_reduction_term" in source
    assert prefilter.filing_reduction_plan(ATLASSIAN)
