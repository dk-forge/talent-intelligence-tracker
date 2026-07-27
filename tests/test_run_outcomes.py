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


def test_a_busy_provider_is_deferred_not_rejected():
    """A 429 is the upstream provider being busy, not a verdict on the story.

    Treating it as one threw five real candidates away in a single dry run,
    OpenAI tripling its Dublin headcount among them, and printed them as
    REJECT, which reads exactly like the model declining them.
    """
    import inspect

    import run_collect
    from pipeline import classify

    assert 429 in classify.TRANSIENT_STATUS
    assert issubclass(classify.Throttled, RuntimeError)
    # Throttled must not inherit from ClassifyError, or the reject branch
    # would swallow it before the defer branch is ever reached.
    assert not issubclass(classify.Throttled, classify.ClassifyError)

    src = inspect.getsource(run_collect.run)
    defer = src.index("except classify.Throttled")
    reject = src.index("except classify.ClassifyError")
    assert defer < reject, "the Throttled handler must come first"
    # The candidate has to stay unseen so a later run retries it.
    assert "throttled += 1" in src


def test_a_mostly_throttled_run_reports_degraded():
    """Storing little because the provider was busy is not a quiet news day."""
    import inspect

    import run_collect

    src = inspect.getsource(run_collect.run)
    assert "mostly_throttled" in src
    assert "or mostly_throttled" in src
