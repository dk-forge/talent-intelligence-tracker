"""merge_db.py must keep BOTH writers' rows.

tests/test_workflows.py asserts the workflows call this. These assert it
actually merges, using the shapes that were really destroyed on 2026-07-28/29:
a concurrent writer's signals, a human's employer_identity cache, and a
retraction that must not come back.
"""

from __future__ import annotations

import sqlite3

import pytest

import merge_db
from pipeline import schema, store, validate


def _signal(conn, company, headline, *, pillar="company_development",
            published="2026-07-01", collector="test"):
    """Insert one row the way the write path would, and return its hash."""
    chash = validate.content_hash(company.lower(), pillar, published, headline)
    conn.execute(
        """
        INSERT INTO signals (signal_id, headline, summary, talent_readthrough,
                             company, company_key, pillar, signal_direction,
                             confidence, source_url, source_name, published_date,
                             captured_at, as_of, content_hash, collector)
        VALUES (?, ?, 'summary', 'readthrough', ?, ?, ?, 'neutral', 'reported',
                ?, 'Test', ?, '2026-07-01T00:00:00', '2026-07-01T00:00:00', ?, ?)
        """,
        (chash, headline, company, company.lower(), pillar,
         f"https://example.com/{chash}", published, chash, collector),
    )
    return chash


@pytest.fixture
def two_writers(tmp_path):
    """A shared starting point, then two databases that diverge from it.

    `theirs` is what landed on main while we worked; `ours` is the runner's
    copy. This is exactly the situation every losing commit was in.
    """
    base = tmp_path / "base.db"
    conn = schema.connect(base)
    _signal(conn, "Shared Co", "Shared Co raises a round")
    conn.commit()
    conn.close()

    theirs = tmp_path / "theirs.db"
    ours = tmp_path / "ours.db"
    theirs.write_bytes(base.read_bytes())
    ours.write_bytes(base.read_bytes())
    return ours, theirs


def test_the_other_writers_rows_survive(two_writers):
    """182c2da -> 42222a0: a backfill copied its file back and destroyed 8,751
    rows four collector runs had pushed while it worked."""
    ours, theirs = two_writers

    conn = schema.connect(ours)
    _signal(conn, "Ours Inc", "Ours Inc opens a hub")
    conn.commit()
    conn.close()

    conn = schema.connect(theirs)
    _signal(conn, "Theirs Ltd", "Theirs Ltd cuts 200 roles")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    companies = {r[0] for r in conn.execute("SELECT company FROM signals")}
    assert companies == {"Shared Co", "Ours Inc", "Theirs Ltd"}, (
        "a merge that drops either side is the bug this file exists to prevent")


def test_a_humans_identity_cache_survives(two_writers):
    """9991861 -> e1bfb03: a laptop commit added 3,604 employer_identity rows and
    the next backfill took the table to zero. No concurrency group can gate a
    human, so the merge is the only thing that can."""
    ours, theirs = two_writers

    conn = schema.connect(theirs)
    conn.execute(
        "INSERT INTO employer_identity (company_key, company, resolved, resolved_at)"
        " VALUES ('nhs trust', 'NHS Trust', 1, '2026-07-28T23:22:49')")
    conn.commit()
    conn.close()

    conn = schema.connect(ours)
    conn.execute(
        "INSERT INTO employer_identity (company_key, company, resolved, resolved_at)"
        " VALUES ('ours inc', 'Ours Inc', 1, '2026-07-28T22:00:00')")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    keys = {r[0] for r in conn.execute("SELECT company_key FROM employer_identity")}
    assert keys == {"nhs trust", "ours inc"}


def test_the_later_identity_resolution_wins(two_writers):
    """employer_identity is a cache written with INSERT OR REPLACE, so a later
    resolution is a correction and must not be reverted by an older one."""
    ours, theirs = two_writers

    for path, hq, when in ((theirs, "London", "2026-07-01T00:00:00"),
                           (ours, "Leeds", "2026-07-28T00:00:00")):
        conn = schema.connect(path)
        conn.execute(
            "INSERT INTO employer_identity (company_key, company, hq_city, resolved,"
            " resolved_at) VALUES ('shared co', 'Shared Co', ?, 1, ?)", (hq, when))
        conn.commit()
        conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    assert conn.execute(
        "SELECT hq_city FROM employer_identity WHERE company_key = 'shared co'"
    ).fetchone()[0] == "Leeds"

    # And the other way round: merging the older file in must not undo it.
    merge_db.merge(theirs, ours)
    conn = sqlite3.connect(ours)
    assert conn.execute(
        "SELECT hq_city FROM employer_identity WHERE company_key = 'shared co'"
    ).fetchone()[0] == "Leeds"


def test_a_retraction_is_not_resurrected(two_writers):
    """dedupe.py carries the scar: a withdrawn record coming back is how the WWT
    row returned. A merge must never undo a retraction, in either direction."""
    ours, theirs = two_writers

    conn = schema.connect(ours)
    conn.execute("UPDATE signals SET is_current = 0, notes = 'retracted'")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    assert conn.execute(
        "SELECT is_current FROM signals WHERE company = 'Shared Co'").fetchone()[0] == 0


def test_a_revision_supersedes_the_row_it_replaces(two_writers):
    """store.revise() appends a revision and clears the old one. After a merge
    the pair has to still read that way, with the pointer between them intact
    under the destination's row_id numbering."""
    ours, theirs = two_writers

    conn = schema.connect(ours)
    row = conn.execute("SELECT * FROM signals WHERE company = 'Shared Co'").fetchone()
    signal = validate.Signal(**{k: row[k] for k in row.keys()
                                if k in validate.Signal.__dataclass_fields__})
    conn.execute("UPDATE signals SET is_current = 0 WHERE row_id = ?", (row["row_id"],))
    conn.execute(
        """
        INSERT INTO signals (signal_id, revision, is_current, supersedes_row_id,
                             headline, summary, talent_readthrough, company,
                             company_key, pillar, signal_direction, confidence,
                             source_url, source_name, published_date, captured_at,
                             as_of, content_hash, collector, notes)
        VALUES (?, 2, 1, ?, 'Shared Co raises a round (corrected)', 'summary',
                'readthrough', ?, ?, ?, 'neutral', 'reported', ?, 'Test', ?,
                '2026-07-02T00:00:00', '2026-07-02T00:00:00', ?, 'test', 'corrected')
        """,
        (signal.signal_id, row["row_id"], signal.company, signal.company_key,
         signal.pillar, signal.source_url, signal.published_date, signal.content_hash),
    )
    conn.commit()
    conn.close()

    # Give the destination an unrelated row first, so its row_id numbering
    # differs from ours and a naive copy of supersedes_row_id would point wrong.
    conn = schema.connect(theirs)
    _signal(conn, "Theirs Ltd", "Theirs Ltd cuts 200 roles")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = schema.connect(theirs)
    rows = conn.execute(
        "SELECT row_id, revision, is_current, supersedes_row_id FROM signals"
        " WHERE company = 'Shared Co' ORDER BY revision").fetchall()
    assert [r["revision"] for r in rows] == [1, 2]
    assert [r["is_current"] for r in rows] == [0, 1], "only the newest revision is current"
    assert rows[1]["supersedes_row_id"] == rows[0]["row_id"], (
        "the revision points at the row it replaced, in THIS database's numbering")


def test_seen_urls_keeps_the_earlier_sighting(two_writers):
    """seen_urls is what stops the next run re-fetching and re-paying the LLM for
    a story. Union it, and keep the first time we actually saw each URL."""
    ours, theirs = two_writers

    conn = schema.connect(ours)
    store.mark_seen(conn, "https://example.com/a", "test", "stored")
    conn.execute("UPDATE seen_urls SET first_seen = '2026-07-01T00:00:00'")
    store.mark_seen(conn, "https://example.com/only-ours", "test", "stored")
    conn.commit()
    conn.close()

    conn = schema.connect(theirs)
    store.mark_seen(conn, "https://example.com/a", "test", "stored")
    conn.execute("UPDATE seen_urls SET first_seen = '2026-07-05T00:00:00'")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    seen = dict(conn.execute("SELECT url, first_seen FROM seen_urls"))
    assert "https://example.com/only-ours" in seen
    assert seen["https://example.com/a"] == "2026-07-01T00:00:00"


def test_merging_the_same_database_twice_changes_nothing(two_writers):
    """The workflow loop can run the merge up to five times against a moving
    main. Attempts 2..5 must be no-ops rather than duplicating the run's rows."""
    ours, theirs = two_writers

    conn = schema.connect(ours)
    _signal(conn, "Ours Inc", "Ours Inc opens a hub")
    conn.commit()
    conn.close()

    first = merge_db.merge(ours, theirs)
    second = merge_db.merge(ours, theirs)

    assert first["signals_inserted"] == 1
    assert second["signals_inserted"] == 0
    assert first["signals_total"] == second["signals_total"]


def test_a_missing_database_fails_loudly(tmp_path):
    """Data-changing jobs fail loudly. A merge that quietly does nothing would
    let the workflow commit an unmerged file and call it a success."""
    with pytest.raises(SystemExit):
        merge_db.merge(tmp_path / "does-not-exist.db", tmp_path / "into.db")
