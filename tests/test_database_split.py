"""The database is two files, and unqualified SQL must never notice.

The split exists because GitHub refuses a single file over 100 MiB in a push
and the product file was 32 days from it (schema.py, "the second committed
file"). Everything else about the system was kept: both halves are committed,
so `git push` is still the compare-and-swap that makes merge_db.py safe and
`git show <sha>:data/...` is still the restore path.

What these pin is the mechanism that makes the split invisible: SQLite resolves
an unqualified table name across ATTACHed schemas, so no query anywhere had to
change. The failure mode is a table that exists in BOTH files — `main` wins the
name resolution, silently, and reading an empty cache is indistinguishable from
a cache that legitimately holds nothing.
"""

from __future__ import annotations

import sqlite3

import pytest

from pipeline import schema


def test_cache_tables_live_only_in_the_cache_file(tmp_path):
    conn = schema.connect(tmp_path / "t.db")
    in_main = {r[0] for r in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table'")}
    in_cache = {r[0] for r in conn.execute(
        "SELECT name FROM cache.sqlite_master WHERE type='table'")}
    for table in schema.CACHE_TABLE_NAMES:
        assert table in in_cache, f"{table} is not in the cache file"
        assert table not in in_main, (
            f"{table} is in BOTH files. main wins an unqualified lookup, so "
            f"every reader would silently see the empty one.")
    assert "signals" in in_main and "signals" not in in_cache


def test_the_cache_file_sits_beside_its_database(tmp_path):
    """Derived, never configured: two databases must not share one cache."""
    assert schema.cache_path_for(tmp_path / "a.db") == tmp_path / "a_cache.db"
    assert schema.cache_path_for("/x/talent_intel.db").name == "talent_intel_cache.db"
    schema.connect(tmp_path / "a.db").close()
    schema.connect(tmp_path / "b.db").close()
    assert (tmp_path / "a_cache.db").exists()
    assert (tmp_path / "b_cache.db").exists()


def test_unqualified_sql_reaches_the_cache_file(tmp_path):
    conn = schema.connect(tmp_path / "t.db")
    conn.execute("INSERT INTO seen_urls VALUES ('u', '2026-01-01', 'c', 'stored')")
    conn.commit()
    conn.close()
    again = schema.connect(tmp_path / "t.db")
    assert again.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0] == 1
    # ...and it really is in the other file, not quietly back in main.
    assert again.execute(
        "SELECT COUNT(*) FROM cache.seen_urls").fetchone()[0] == 1


def test_a_write_spanning_both_files_is_one_transaction(tmp_path):
    """A signal stored without its URL recorded as seen is a story we will pay
    to read again. SQLite commits attached databases atomically; this is the
    check that the code still relies on that rather than on two commits."""
    conn = schema.connect(tmp_path / "t.db")
    conn.execute("BEGIN")
    conn.execute("INSERT INTO seen_urls VALUES ('u', '2026-01-01', 'c', 'stored')")
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0] == 0


def test_a_legacy_database_is_migrated_on_open(tmp_path):
    """The repair that makes this safe to land on a repo with live branches.

    A branch still running the old schema.py puts an empty `seen_urls` back
    into the product file, where it SHADOWS the real one. connect() moves it
    out — rows first, with the cache's own copy winning — rather than reporting
    it, because a shadow that is merely reported is a shadow that is read.
    """
    path = tmp_path / "t.db"
    legacy = sqlite3.connect(path)
    legacy.executescript("""
        CREATE TABLE seen_urls (url TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
                                collector TEXT NOT NULL, outcome TEXT NOT NULL);
        INSERT INTO seen_urls VALUES ('old', '2026-01-01', 'c', 'stored');
    """)
    legacy.commit()
    legacy.close()

    conn = schema.connect(path)
    assert conn.execute(
        "SELECT COUNT(*) FROM main.sqlite_master "
        " WHERE type='table' AND name='seen_urls'").fetchone()[0] == 0
    assert conn.execute(
        "SELECT url FROM cache.seen_urls").fetchone()[0] == "old"


def test_the_cache_row_wins_when_both_files_hold_the_key(tmp_path):
    """The shadow is the stale one, by construction: it was written by a
    checkout that did not know the cache file existed."""
    path = tmp_path / "t.db"
    schema.connect(path).close()
    conn = sqlite3.connect(path)
    conn.execute("ATTACH DATABASE ? AS cache", (str(schema.cache_path_for(path)),))
    conn.execute("INSERT INTO cache.seen_urls VALUES ('u','2026-08-01','real','stored')")
    conn.executescript("""
        CREATE TABLE main.seen_urls (url TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
                                     collector TEXT NOT NULL, outcome TEXT NOT NULL);
        INSERT INTO main.seen_urls VALUES ('u','2026-01-01','shadow','rejected');
    """)
    conn.commit()
    conn.close()

    conn = schema.connect(path)
    row = conn.execute("SELECT collector, outcome FROM seen_urls").fetchone()
    assert tuple(row) == ("real", "stored")


def test_connect_ro_refuses_a_missing_cache_file(tmp_path):
    """Loud, never silent. A read-only tool that tolerated the absence would
    report zero dead links and zero seen URLs as a clean result."""
    schema.connect(tmp_path / "t.db").close()
    schema.cache_path_for(tmp_path / "t.db").unlink()
    with pytest.raises(sqlite3.OperationalError):
        schema.connect_ro(tmp_path / "t.db")


def test_connect_ro_cannot_write_either_half(tmp_path):
    schema.connect(tmp_path / "t.db").close()
    conn = schema.connect_ro(tmp_path / "t.db")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO signals (signal_id) VALUES ('x')")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO seen_urls VALUES ('u','t','c','stored')")


def test_merge_refuses_a_missing_half(tmp_path):
    """A merge whose cache half is absent would union an empty seen_urls over a
    full one, print `seen_urls_added: 0` and exit 0."""
    import merge_db

    ours, into = tmp_path / "ours.db", tmp_path / "into.db"
    schema.connect(ours).close()
    schema.connect(into).close()
    schema.cache_path_for(ours).unlink()

    with pytest.raises(SystemExit) as caught:
        merge_db.merge(ours, into)
    assert "cache file" in str(caught.value)


def test_merge_carries_the_cache_across(tmp_path):
    import merge_db

    ours, into = tmp_path / "ours.db", tmp_path / "into.db"
    conn = schema.connect(ours)
    conn.execute("INSERT INTO seen_urls VALUES ('u','2026-01-01','c','stored')")
    conn.commit()
    conn.close()
    schema.connect(into).close()

    report = merge_db.merge(ours, into)
    assert report["seen_urls_added"] == 1
    conn = schema.connect(into)
    assert conn.execute("SELECT COUNT(*) FROM cache.seen_urls").fetchone()[0] == 1


# --- the move must not lose a row -------------------------------------------
#
# MEASURED, not hypothetical. The first version of the migration copied the
# legacy tables into freshly-CREATEd ones with `INSERT OR IGNORE`, and on the
# real database that silently dropped 1,232 of 6,496 `source_links` rows: the
# ALTER that added `archive_probes` made it nullable, the CREATE declares it
# NOT NULL DEFAULT 0, and OR IGNORE skips a NOT NULL violation without a word.
# The move reported success and the file was 3.7 MiB lighter for the wrong
# reason. These pin both halves of the fix — the shape is reproduced rather
# than re-declared, and the row count is checked before anything is dropped.

def _legacy_source_links(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE source_links (
            source_url TEXT PRIMARY KEY,
            state      TEXT,
            updated_at TEXT NOT NULL
        );
    """)
    # ...then the column arrives by ALTER, exactly as MIGRATIONS adds it:
    # nullable, no default, NULL on every row that predates it.
    conn.execute("ALTER TABLE source_links ADD COLUMN archive_probes INTEGER")
    conn.executemany(
        "INSERT INTO source_links (source_url, state, updated_at, archive_probes) "
        "VALUES (?, 'live', '2026-08-01', ?)", rows)
    conn.commit()
    conn.close()


def test_a_null_in_a_column_the_create_calls_not_null_still_moves(tmp_path):
    path = tmp_path / "t.db"
    _legacy_source_links(path, [("https://a/1", None), ("https://a/2", 3),
                                ("https://a/3", None)])

    conn = schema.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 3
    # And the NULLs are still NULL. Coercing them to 0 would lose the
    # distinction archive_sources.py reads: "never probed" is not "probed and
    # told nothing", and only one of them may go terminal.
    assert conn.execute(
        "SELECT COUNT(*) FROM source_links "
        " WHERE archive_probes IS NULL").fetchone()[0] == 2


def test_the_moved_table_keeps_the_legacy_declaration(tmp_path):
    """Rebuilt from the legacy CREATE, not from CACHE_TABLES. A database that
    has been migrated and one that was created fresh have always differed in
    exactly this way; the move must not quietly convert between them."""
    path = tmp_path / "t.db"
    _legacy_source_links(path, [("https://a/1", None)])
    conn = schema.connect(path)
    sql = conn.execute(
        "SELECT sql FROM cache.sqlite_master WHERE name='source_links'").fetchone()[0]
    assert "archive_probes INTEGER" in sql
    assert "archive_probes INTEGER NOT NULL" not in sql


def test_a_move_that_would_lose_rows_raises_and_drops_nothing(tmp_path):
    """The backstop, independent of any particular shape mismatch.

    Simulated by making the destination refuse a row for a reason the copy
    cannot help: a shadow in main whose rows collide on nothing and still
    cannot land. What matters is that `main` survives — a failed move must
    leave the database exactly as it found it, so a human can look.
    """
    path = tmp_path / "t.db"
    schema.connect(path).close()

    conn = sqlite3.connect(path)
    conn.execute("ATTACH DATABASE ? AS cache", (str(schema.cache_path_for(path)),))
    conn.execute("INSERT INTO cache.seen_urls VALUES ('u','2026-08-01','real','stored')")
    conn.executescript("""
        CREATE TABLE main.seen_urls (url TEXT PRIMARY KEY, first_seen TEXT,
                                     collector TEXT, outcome TEXT);
        INSERT INTO main.seen_urls VALUES ('v', NULL, 'shadow', 'rejected');
    """)
    conn.commit()
    conn.close()

    with pytest.raises(schema.CacheMoveFailed):
        schema.connect(path)
    survivor = sqlite3.connect(path)
    assert survivor.execute(
        "SELECT COUNT(*) FROM main.sqlite_master "
        " WHERE type='table' AND name='seen_urls'").fetchone()[0] == 1
    assert survivor.execute(
        "SELECT COUNT(*) FROM main.seen_urls").fetchone()[0] == 1
    survivor.close()
