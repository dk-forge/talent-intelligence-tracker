"""A run must be honest about what happened.

The failure this guards against is the quiet one: the collector fetches fine,
every candidate is dropped by a broken guard or a dead API key, and the run
reports `ok` with zero rows. That is how you discover in month three that
something died in month one.
"""

import sqlite3

import pytest

from pipeline import store


@pytest.fixture
def conn(tmp_path):
    from pipeline import schema
    connection = schema.connect(tmp_path / "t.db")
    yield connection
    connection.close()


def latest(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM source_health ORDER BY run_at DESC LIMIT 1").fetchone()


def test_zero_found_is_degraded(conn):
    store.report_health(conn, "google_news", status="ok", items_found=0, items_stored=0)
    assert latest(conn)["status"] == "degraded"


def test_zero_found_explains_itself(conn):
    store.report_health(conn, "google_news", status="ok", items_found=0)
    assert "zero items" in latest(conn)["detail"]


def test_a_normal_run_stays_ok(conn):
    store.report_health(conn, "google_news", status="ok", items_found=20, items_stored=6,
                        detail="14 dup, 0 rejected")
    assert latest(conn)["status"] == "ok"


def test_explicit_degraded_is_not_overridden(conn):
    store.report_health(conn, "google_news", status="degraded", items_found=20, items_stored=3,
                        detail="every candidate rejected")
    assert latest(conn)["status"] == "degraded"


def test_ops_status_flags_a_degraded_collector(conn, capsys):
    """ops_status must surface it, or the ledger is just a diary nobody reads."""
    import ops_status

    store.report_health(conn, "google_news", status="degraded", items_found=40, items_stored=0,
                        detail="every candidate rejected")
    problems = ops_status._report_health(conn)

    assert any("degraded" in p for p in problems)
