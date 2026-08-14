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
from pipeline import guardrails, schema, store, validate


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


def _finding(conn, subject, state="open", reviewed_at=None, last_seen="2026-07-29T00:00:00"):
    conn.execute(
        "INSERT INTO publish_guardrails (check_name, subject, label, detail, "
        "  value, state, first_seen, last_seen, seen, reviewed_at, review_note) "
        "VALUES ('amount', ?, 'X', 'd', 1.0, ?, '2026-07-28T00:00:00', ?, 1, ?, ?)",
        (subject, state, last_seen, reviewed_at,
         "checked the filing" if reviewed_at else None))


def test_a_runs_new_guardrail_findings_are_not_lost_by_the_merge(two_writers):
    """A run that trips a guardrail then loses its push would otherwise throw
    the findings away and publish clean on the next attempt."""
    ours, theirs = two_writers

    conn = schema.connect(ours)
    _finding(conn, "from-the-run")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    assert conn.execute(
        "SELECT COUNT(*) FROM publish_guardrails WHERE subject = 'from-the-run'"
    ).fetchone()[0] == 1


def test_a_humans_acceptance_beats_a_later_automatic_write(two_writers):
    """The owner accepts a real mega-raise from a laptop while a run is in
    flight. The run's copy still says 'open' and is written LATER. If the newer
    write won, the acceptance would evaporate and every publish would block
    again over a figure somebody had already checked."""
    ours, theirs = two_writers

    conn = schema.connect(theirs)
    _finding(conn, "changxin", state="accepted",
             reviewed_at="2026-07-29T09:00:00", last_seen="2026-07-29T09:00:00")
    conn.commit()
    conn.close()

    conn = schema.connect(ours)
    _finding(conn, "changxin", state="open", last_seen="2026-07-29T18:00:00")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    row = conn.execute(
        "SELECT state, review_note FROM publish_guardrails "
        " WHERE subject = 'changxin'").fetchone()
    assert row[0] == "accepted"
    assert row[1] == "checked the filing"


def test_an_unreviewed_disagreement_resolves_to_open(two_writers):
    """This table decides whether a figure goes out, so a merge it cannot
    resolve has to fail loud rather than quietly clear the queue."""
    ours, theirs = two_writers

    conn = schema.connect(theirs)
    _finding(conn, "row", state="resolved")
    conn.commit()
    conn.close()

    conn = schema.connect(ours)
    _finding(conn, "row", state="open", last_seen="2026-07-29T18:00:00")
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    assert conn.execute(
        "SELECT state FROM publish_guardrails WHERE subject = 'row'"
    ).fetchone()[0] == "open"


def test_a_publication_survives_the_merge(two_writers):
    """The one that was really broken, measured 2026-08-14 on run 31780939430.

    publish() sends a row and stamps published_at. The commit step then resets
    to origin/main and merges, and _merge_signals skips every
    (content_hash, revision) the destination already holds - so the stamp was
    discarded on any row collected in an EARLIER run than the one that
    published it. Ten such rows sat at published_at IS NULL on main while the
    live site already held every one of them.

    It is not cosmetic. enrich_published() only ever offers rows with
    published_at IS NOT NULL, so a derived column learned later - archive_url,
    hq_city, funding_stage - could never reach them; and guardrails reads the
    same marker to decide whether a flagged figure is LIVE (retract it) or
    PENDING (withholding is the whole fix), so a wrong number in public would
    be filed as one that had never left the building.
    """
    ours, theirs = two_writers
    chash = validate.content_hash(
        "shared co", "company_development", "2026-07-01",
        "Shared Co raises a round")

    conn = schema.connect(ours)
    conn.execute("UPDATE signals SET published_at = '2026-08-14T08:04:45' "
                 " WHERE content_hash = ?", (chash,))
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    assert conn.execute(
        "SELECT published_at FROM signals WHERE content_hash = ?", (chash,)
    ).fetchone()[0] == "2026-08-14T08:04:45", (
        "a row this run published must not come back unpublished; the site "
        "already holds it and nothing downstream can tell")


def test_the_merge_never_unpublishes_a_row(two_writers):
    """The other direction, and the reason this is a UNION and not a copy.

    Another writer published the row while we worked. Our copy still says NULL
    because we never sent it. Carrying our NULL across would be the same defect
    with the sides swapped."""
    ours, theirs = two_writers
    chash = validate.content_hash(
        "shared co", "company_development", "2026-07-01",
        "Shared Co raises a round")

    conn = schema.connect(theirs)
    conn.execute("UPDATE signals SET published_at = '2026-08-14T06:00:00' "
                 " WHERE content_hash = ?", (chash,))
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    assert conn.execute(
        "SELECT published_at FROM signals WHERE content_hash = ?", (chash,)
    ).fetchone()[0] == "2026-08-14T06:00:00"


def test_the_earlier_publication_wins(two_writers):
    """Both sides sent it. published_at answers "when did this reach the site",
    so the earlier stamp is the true one and a re-send must not move it
    forward."""
    ours, theirs = two_writers
    chash = validate.content_hash(
        "shared co", "company_development", "2026-07-01",
        "Shared Co raises a round")

    for path, stamp in ((theirs, "2026-08-14T06:00:00"),
                        (ours, "2026-08-14T08:04:45")):
        conn = schema.connect(path)
        conn.execute("UPDATE signals SET published_at = ? WHERE content_hash = ?",
                     (stamp, chash))
        conn.commit()
        conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    assert conn.execute(
        "SELECT published_at FROM signals WHERE content_hash = ?", (chash,)
    ).fetchone()[0] == "2026-08-14T06:00:00"


def test_a_withdrawn_row_is_not_republished_by_the_marker(two_writers):
    """Rule 1 of _reconcile_is_current outranks this one. A retraction flips
    is_current on the row the marker belongs to, and carrying a publication
    stamp must not be a reason to look at a withdrawn row again."""
    ours, theirs = two_writers
    chash = validate.content_hash(
        "shared co", "company_development", "2026-07-01",
        "Shared Co raises a round")

    conn = schema.connect(ours)
    conn.execute("UPDATE signals SET published_at = '2026-08-14T08:04:45', "
                 " is_current = 0 WHERE content_hash = ?", (chash,))
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = sqlite3.connect(theirs)
    row = conn.execute(
        "SELECT is_current, published_at FROM signals WHERE content_hash = ?",
        (chash,)).fetchone()
    assert row[0] == 0, "a withdrawal is sticky"
    assert row[1] == "2026-08-14T08:04:45"


def test_a_live_figure_is_not_filed_as_one_that_never_left(two_writers):
    """What the lost marker costs the guardrails, end to end.

    guardrails decides between two very different corrections by reading
    published_at: a PENDING row is fixed by withholding it, a LIVE one can only
    be fixed by `retract.py` and stays red until somebody does. Drop the marker
    on a row the site already holds and a wrong number in public is filed as
    "decided, never publishing, no clock" - the one bucket that never asks
    anybody for anything.
    """
    ours, theirs = two_writers
    chash = validate.content_hash(
        "shared co", "company_development", "2026-07-01",
        "Shared Co raises a round")

    conn = schema.connect(ours)
    conn.execute("UPDATE signals SET published_at = '2026-08-14T08:04:45' "
                 " WHERE content_hash = ?", (chash,))
    conn.commit()
    conn.close()

    merge_db.merge(ours, theirs)

    conn = schema.connect(theirs)
    conn.execute(
        "INSERT INTO publish_guardrails (check_name, subject, label, value, "
        "  state, first_seen, last_seen, seen, reviewed_at, reviewed_by, "
        "  review_note) "
        "VALUES ('amount', ?, 'Shared Co', 5e9, 'rejected', "
        "        '2026-08-14T09:00:00', '2026-08-14T09:00:00', 1, "
        "        '2026-08-14T09:30:00', 'owner', 'not a round')", (chash,))
    conn.commit()

    report = guardrails.quarantine(conn, write=False)
    conn.close()

    assert [r["subject"] for r in report["live"]] == [chash], (
        "a rejected figure that is ALREADY on the site needs a retraction, and "
        "saying so depends entirely on published_at surviving the merge")
    assert report["withheld"] == [], (
        "withholding is not available to a published figure")


def test_a_missing_database_fails_loudly(tmp_path):
    """Data-changing jobs fail loudly. A merge that quietly does nothing would
    let the workflow commit an unmerged file and call it a success."""
    with pytest.raises(SystemExit):
        merge_db.merge(tmp_path / "does-not-exist.db", tmp_path / "into.db")
