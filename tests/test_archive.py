"""The read-only archive shard (pipeline/archive.py), owner ruling 2026-11-16.

Four promises, each with a test that fails if it is taken away:
  1. nothing is deleted: every moved row is in the shard, union count unchanged;
  2. the shard is attached READ-ONLY: a write to it raises;
  3. dedupe still sees archived rows, so an old-dated backfill does not store
     a second copy of an event the archive already holds;
  4. the republish-from-database path still sends archived rows.
Plus the two places a moved row could come back or read as lost: merge_db and
backup_check.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

import backup_check
import merge_db
from pipeline import archive, dedupe, publish, schema, store, validate

AS_OF = "2026-11-16"


def _row(conn, company, headline, *, collector, published, pillar="compensation",
         published_at="2025-01-01 00:00:00"):
    chash = validate.content_hash(company.lower(), pillar, published, headline)
    conn.execute(
        """
        INSERT INTO signals (signal_id, headline, summary, talent_readthrough,
                             company, company_key, pillar, signal_direction,
                             confidence, source_url, source_name, published_date,
                             captured_at, as_of, content_hash, collector,
                             published_at)
        VALUES (?, ?, 'summary', 'readthrough', ?, ?, ?, 'neutral', 'verified',
                ?, 'Test', ?, '2026-07-01T00:00:00', '2026-07-01T00:00:00', ?,
                ?, ?)
        """,
        (chash, headline, company, company.lower(), pillar,
         f"https://example.com/{chash}", published, chash, collector,
         published_at))
    return chash


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "talent_intel.db"
    conn = schema.connect(path)
    hashes = {
        "old_paygap": _row(conn, "Acme Ltd", "Acme gender pay gap 2019",
                           collector="uk_paygap", published="2019-04-04"),
        "old_execcomp": _row(conn, "Beta Inc", "Beta CEO pay 2023",
                             collector="sec_execcomp", published="2023-03-01"),
        # Each of these must STAY: wrong collector, too recent, unpublished.
        "old_news": _row(conn, "Gamma", "Gamma hires", collector="google_news",
                         published="2019-04-04"),
        "recent_paygap": _row(conn, "Delta Ltd", "Delta pay gap 2026",
                              collector="uk_paygap", published="2026-04-04"),
        "unpublished": _row(conn, "Eps Ltd", "Eps pay gap 2019",
                            collector="uk_paygap", published="2019-04-04",
                            published_at=None),
    }
    conn.commit()
    conn.close()
    return path, hashes


def _moved(db):
    path, hashes = db
    result = archive.build(path, AS_OF, dry_run=False)
    return path, hashes, result


def test_dry_run_moves_nothing(db):
    path, _ = db
    result = archive.build(path, AS_OF)
    assert result["dry_run"] is True
    assert result["rows"] == 2
    assert result["by_collector"] == {"sec_execcomp": 1, "uk_paygap": 1}
    assert archive.shards(path) == []
    conn = schema.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 5


def test_nothing_is_deleted(db):
    path, hashes, result = _moved(db)
    assert result["rows"] == 2
    conn = schema.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM main.signals").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM signals_all").fetchone()[0] == 5
    held = {r[0] for r in conn.execute("SELECT content_hash FROM signals_all")}
    assert held == set(hashes.values())
    assert archive.overlap(conn) == 0
    # Only the ruled rows moved.
    live = {r[0] for r in conn.execute("SELECT content_hash FROM main.signals")}
    assert live == {hashes["old_news"], hashes["recent_paygap"],
                    hashes["unpublished"]}


def test_a_shard_is_never_rewritten(db):
    path, _, _ = _moved(db)
    with pytest.raises(archive.ArchiveError):
        # Re-plant a ruled row and try again on the same date.
        conn = sqlite3.connect(path)
        _row(conn, "Zed Ltd", "Zed pay gap 2018", collector="uk_paygap",
             published="2018-04-04")
        conn.commit()
        conn.close()
        archive.build(path, AS_OF, dry_run=False)


def test_the_shard_is_attached_read_only(db):
    path, _, _ = _moved(db)
    for conn in (schema.connect(path), schema.connect_ro(path)):
        names = archive.attached(conn)
        assert names == ["archive_0"]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE archive_0.signals SET headline = 'x'")


def test_a_plain_connection_still_cannot_write_the_shard(tmp_path):
    """Whatever this SQLite build does with a URI on a connection opened
    WITHOUT uri=True, the shard must never become writable.

    Builds differ: with SQLITE_USE_URI the URI is honoured and the shard
    attaches read-only; without it SQLite would read the URI as a filename,
    and attach() must refuse rather than hand back an empty writable file.
    Both outcomes are safe; a writable shard is the only failure.
    """
    path = tmp_path / "talent_intel.db"
    conn = schema.connect(path)
    _row(conn, "Acme Ltd", "Acme gender pay gap 2019", collector="uk_paygap",
         published="2019-04-04")
    conn.commit()
    conn.close()
    archive.build(path, AS_OF, dry_run=False)
    plain = sqlite3.connect(path)
    try:
        archive.attach(plain, path)
    except archive.ArchiveError as e:
        assert "uri=True" in str(e)
        assert not list(tmp_path.glob("file:*")), "a refusal left a stray file"
        return
    assert plain.execute(
        "SELECT COUNT(*) FROM archive_0.signals").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        plain.execute("UPDATE archive_0.signals SET headline = 'x'")


def test_attach_refuses_a_shard_with_no_signals_table(tmp_path):
    """The guard in attach() that turns a mis-opened ATTACH into a loud error."""
    path = tmp_path / "talent_intel.db"
    schema.connect(path).close()
    bogus = archive.archive_dir(path) / "signals-2026-01-01.db"
    bogus.parent.mkdir()
    sqlite3.connect(bogus).close()  # a valid, empty database: not a shard
    with pytest.raises(archive.ArchiveError, match="signals table"):
        schema.connect(path)


def test_dedupe_still_sees_archived_rows(db):
    """An old-dated backfill must meet the archived copy of its own event."""
    path, hashes, _ = _moved(db)
    conn = schema.connect(path)
    assert dedupe.exact_duplicate(conn, hashes["old_paygap"]) == "duplicate"
    same_event = SimpleNamespace(
        content_hash="new-hash", company_key="acme ltd", pillar="compensation",
        published_date="2019-04-05", headline="Acme gender pay gap 2019",
        funding_amount_usd=None)
    assert dedupe.fuzzy_duplicate(conn, same_event)
    assert store.duplicate_verdict(conn, same_event) == "duplicate"


def test_dedupe_would_miss_them_without_the_view(db):
    """Mutation proof: the same question against main alone says 'new'."""
    path, hashes, _ = _moved(db)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert dedupe.exact_duplicate(conn, hashes["old_paygap"]) is None


def test_republish_from_database_sends_archived_rows(db):
    path, hashes, _ = _moved(db)
    conn = schema.connect(path)
    conn.execute("UPDATE signals SET published_at = NULL")  # RECOVERY.md step
    plain = {r["content_hash"] for r in publish.unpublished(conn)}
    full = {r["content_hash"]
            for r in publish.unpublished(conn, include_archived=True)}
    assert hashes["old_paygap"] not in plain
    assert full == set(hashes.values())


def test_an_archived_row_cannot_be_revised(db):
    path, hashes, _ = _moved(db)
    conn = schema.connect(path)
    with pytest.raises(ValueError, match="archive"):
        store.revise(conn, hashes["old_paygap"], None, "note")


def test_merge_does_not_resurrect_an_archived_row(db, tmp_path):
    """A runner whose copy predates the move must not put the row back."""
    path, hashes = db
    stale = tmp_path / "runner" / "ours.db"
    stale.parent.mkdir()
    stale.write_bytes(path.read_bytes())
    schema.cache_path_for(stale).write_bytes(
        schema.cache_path_for(path).read_bytes())
    archive.build(path, AS_OF, dry_run=False)

    merge_db.merge(stale, path)
    conn = schema.connect(path)
    assert archive.overlap(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM main.signals").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM signals_all").fetchone()[0] == 5


def test_backup_check_counts_the_archive_and_grades_overlap():
    base = dict(integrity="ok", signals_columns=list(publish.FIELDS),
                size_bytes=10, tables={t: 5 for t in backup_check.CORE_TABLES},
                prior={"tables": {"signals": 5}, "checked_at": "x"})
    ok = backup_check.evaluate(**base, archive_overlap=0)
    bad = backup_check.evaluate(**base, archive_overlap=2)
    none = backup_check.evaluate(**base)
    status = lambda r: {c["check"]: c["status"] for c in r["checks"]}
    assert status(ok)["archive_disjoint"] == backup_check.PASS
    assert status(bad)["archive_disjoint"] == backup_check.FAIL
    assert bad["verdict"] == backup_check.FAIL
    assert "archive_disjoint" not in status(none)


def test_backup_check_overlap_reader(db):
    path, hashes, _ = _moved(db)
    shard = str(archive.shards(path)[0])
    assert backup_check.shard_overlap(str(path), shard) == 0
    conn = sqlite3.connect(path)
    conn.execute("ATTACH DATABASE ? AS s", (shard,))
    conn.execute("INSERT INTO main.signals SELECT * FROM s.signals")
    conn.commit()
    conn.close()
    assert backup_check.shard_overlap(str(path), shard) == 2
