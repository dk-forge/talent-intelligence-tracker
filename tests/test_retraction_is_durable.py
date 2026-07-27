"""A retracted record must stay retracted.

A story we withdrew resurfaced on a later run. Dedup only looked at current
rows, so it did not recognise the hash — and the unique index, which spans every
revision, raised IntegrityError and killed the run mid-batch. Had the insert
succeeded it would have been worse: the retraction would have been silently
undone and the bad record republished.
"""

import pytest

from pipeline import dedupe, schema, store, validate


def make(headline="Acme to create 300 jobs in Dublin"):
    return validate.build_signal(
        {"company": "Acme", "pillar": "company_development",
         "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
         "confidence": "reported", "headline": headline,
         "summary": "Acme will add 300 roles.",
         "talent_readthrough": "300 roles entering the Dublin market."},
        {"raw_text": "Acme to create 300 jobs in Dublin",
         "source_url": "https://www.irishtimes.com/business/acme-dublin/",
         "source_name": "The Irish Times", "published_date": "2026-07-20"},
        "google_news",
    )


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "r.db")
    yield c
    c.close()


def test_a_retracted_record_is_not_re_stored(conn):
    s = make()
    assert store.store(conn, s) == "stored"

    conn.execute("UPDATE signals SET is_current = 0, notes = 'retracted: bad source' "
                 "WHERE content_hash = ?", (s.content_hash,))
    conn.commit()

    assert store.store(conn, make()) == "retracted"


def test_re_storing_a_retracted_record_does_not_crash(conn):
    """The original failure was an IntegrityError that killed the whole run."""
    s = make()
    store.store(conn, s)
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = ?",
                 (s.content_hash,))
    conn.commit()
    store.store(conn, make())  # must not raise


def test_the_retraction_survives(conn):
    s = make()
    store.store(conn, s)
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = ?",
                 (s.content_hash,))
    conn.commit()
    store.store(conn, make())

    current = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE content_hash = ? AND is_current = 1",
        (s.content_hash,)).fetchone()[0]
    assert current == 0, "the record must stay withdrawn"


def test_a_live_duplicate_still_reports_as_duplicate(conn):
    s = make()
    store.store(conn, s)
    assert store.store(conn, make()) == "duplicate"


def test_dedupe_reports_which_kind(conn):
    s = make()
    store.store(conn, s)
    assert dedupe.exact_duplicate(conn, s.content_hash) == "duplicate"
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = ?",
                 (s.content_hash,))
    conn.commit()
    assert dedupe.exact_duplicate(conn, s.content_hash) == "retracted"
