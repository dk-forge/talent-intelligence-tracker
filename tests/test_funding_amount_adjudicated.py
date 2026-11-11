"""A figure the referees (or the owner) settled is not the parser's to undo.

`funding_amount` keeps the publisher's wording forever, so a row whose string
is the VALUATION ("$24 billion" for Mistral) still reads as $24bn to
vocab.parse_funding_usd after adjudicate_guardrail.py corrected the raise to
$3.5bn. Measured 2026-11-11 against main: 15 of the 19 rows the dry run would
move were adjudicated rows, every one of them put back to the figure the
referees had just removed.
"""

from __future__ import annotations

import sqlite3

import pytest

import adjudicate_guardrail
import correct_funding_amount as correct
from pipeline import schema, store, validate


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def _row(conn, signal_id) -> dict:
    conn.row_factory = sqlite3.Row
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


def valuation_row(conn, amount="$24 billion"):
    signal = validate.build_signal(
        {"company": "Mistral", "pillar": "company_development",
         "signal_direction": "hiring", "confidence": "reported",
         "city": "Paris", "headline": "Mistral raises at a $24 billion valuation",
         "summary": "Mistral raised money at a $24 billion valuation.",
         "talent_readthrough": "Hiring in Paris.", "funding_amount": amount},
        {"raw_text": "Mistral raised money at a $24 billion valuation in Paris.",
         "headline": "Mistral raises at a $24 billion valuation",
         "source_url": "https://example.test/mistral",
         "source_name": "Example", "published_date": "2026-09-01"},
        "google_news")
    assert store.store(conn, signal) == "stored"
    conn.execute("UPDATE signals SET funding_amount = ?, funding_amount_usd = ? "
                 "WHERE signal_id = ?",
                 (amount, correct.vocab.parse_funding_usd(amount), signal.signal_id))
    conn.commit()
    return signal


def adjudicate(conn, signal, amount):
    row = _row(conn, signal.signal_id)
    fixed = correct.corrected_signal(row, amount)
    store.revise(conn, signal.signal_id, fixed,
                 f"{correct.ADJUDICATED_PREFIX} two-model adjudication: edit")
    conn.commit()


def test_an_adjudicated_figure_is_not_put_back_to_the_valuation(conn):
    signal = valuation_row(conn)
    adjudicate(conn, signal, 3_500_000_000)
    assert correct.targets(correct.current_rows(conn)) == []


def test_an_adjudicated_clear_is_not_refilled(conn):
    signal = valuation_row(conn)
    adjudicate(conn, signal, None)
    assert correct.targets(correct.current_rows(conn)) == []


def test_a_later_unrelated_revision_keeps_the_ruling(conn):
    signal = valuation_row(conn)
    adjudicate(conn, signal, 3_500_000_000)
    row = _row(conn, signal.signal_id)
    store.revise(conn, signal.signal_id, correct.corrected_signal(row, 3_500_000_000),
                 "city corrected")
    conn.commit()
    assert correct.targets(correct.current_rows(conn)) == []


def test_a_new_funding_string_after_the_ruling_is_re_derived(conn):
    """The ruling was about the old string; a new one is the parser's again."""
    signal = valuation_row(conn)
    adjudicate(conn, signal, 3_500_000_000)
    row = _row(conn, signal.signal_id)
    fixed = correct.corrected_signal(row, 3_500_000_000)
    fixed.funding_amount = "$2 billion"
    store.revise(conn, signal.signal_id, fixed, "new source")
    conn.commit()
    found = correct.targets(correct.current_rows(conn))
    assert [ch for _r, ch in found] == [(3_500_000_000, 2_000_000_000)]


def test_a_stale_figure_nobody_ruled_on_is_still_found(conn):
    signal = valuation_row(conn, amount="$1.265 Million")
    conn.execute("UPDATE signals SET funding_amount = '$1.265 Million', "
                 "funding_amount_usd = 1265000000 WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()
    found = correct.targets(correct.current_rows(conn))
    assert [ch for _r, ch in found] == [(1_265_000_000, 1_265_000)]


def test_the_adjudicator_writes_the_prefix_this_pass_reads():
    import inspect
    src = inspect.getsource(adjudicate_guardrail.apply_decision)
    assert "correct_funding_amount.ADJUDICATED_PREFIX" in src
