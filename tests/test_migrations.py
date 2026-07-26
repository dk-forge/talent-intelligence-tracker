"""The database is committed to the repo, so it outlives every schema change.

CREATE TABLE IF NOT EXISTS does nothing to a table that already exists. Adding
a column and an index that references it broke `schema.connect()` on any
existing checkout — including the one in the repo — until _migrate ran first.
"""

import sqlite3

from pipeline import schema


def test_migration_adds_columns_to_a_pre_existing_table(tmp_path):
    """Simulate a database created before hq_city/hq_country existed."""
    db = tmp_path / "old.db"
    old_tables = schema.TABLES.replace("    hq_city           TEXT,\n", "")
    old_tables = old_tables.replace("    hq_country        TEXT,\n", "")

    raw = sqlite3.connect(db)
    raw.executescript(old_tables)
    raw.close()

    before = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(signals)")}
    assert "hq_country" not in before

    conn = schema.connect(db)
    after = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    assert {"hq_city", "hq_country"} <= after
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "twice.db"
    schema.connect(db).close()
    conn = schema.connect(db)  # would raise "duplicate column" if not guarded
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
    conn.close()


def test_existing_rows_survive_a_migration(tmp_path):
    """A migration must never be a rebuild — history is the whole point."""
    db = tmp_path / "rows.db"
    old_tables = schema.TABLES.replace("    hq_city           TEXT,\n", "")
    old_tables = old_tables.replace("    hq_country        TEXT,\n", "")

    raw = sqlite3.connect(db)
    raw.executescript(old_tables)
    raw.execute(
        "INSERT INTO signals (signal_id, headline, summary, talent_readthrough,"
        " company, company_key, pillar, signal_direction, confidence, source_url,"
        " source_name, captured_at, as_of, content_hash, collector)"
        " VALUES ('x','h','s','t','Acme','acme','company_development','hiring',"
        " 'reported','https://e.com','E','2026-01-01','2026-01-01','hash','c')"
    )
    raw.commit()
    raw.close()

    conn = schema.connect(db)
    row = conn.execute("SELECT company, hq_country FROM signals").fetchone()
    assert row["company"] == "Acme"
    assert row["hq_country"] is None
    conn.close()


def test_every_migration_column_exists_in_the_create_statement():
    """A column added by ALTER but missing from TABLES would exist on old
    databases and vanish on fresh ones."""
    for table, column, _decl in schema.MIGRATIONS:
        assert column in schema.TABLES, f"{table}.{column} missing from CREATE"
