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
    """Storing little because the provider was busy is not a quiet news day.

    Asserted on run_outcome() rather than on the text of run(): the two
    questions "is the page shallower" and "does a human need to fix something"
    were one variable until 2026-08-03 and are now separate, so the behaviour is
    testable directly instead of by reading a boolean expression.
    """
    import inspect

    import run_collect

    src = inspect.getsource(run_collect.run)
    assert "mostly_throttled" in src
    degraded, failed = run_collect.run_outcome(
        observed=10, everything_rejected=False, mostly_throttled=True,
        running_degraded=False)
    assert degraded is True and failed is True


def test_a_diff_shaped_collector_is_judged_on_what_it_read():
    """`items_found` is what health is measured on, and for a collector whose
    output is a DIFF the emitted-row count is the wrong quantity: a day when
    sixty job boards were read and none moved materially is a healthy day.
    Counting rows instead marks it degraded every day, until the health page is
    worth nothing. Reading NOTHING is still degraded — that is the real
    breakage."""
    import inspect

    import run_collect
    from collectors import ats_boards

    src = inspect.getsource(run_collect.run)
    assert 'getattr(module, "LAST_RUN", None)' in src
    assert "items_found=observed" in src
    # Reading nothing is the real breakage, and it is red as well as degraded.
    assert run_collect.run_outcome(
        observed=0, everything_rejected=False, mostly_throttled=False,
        running_degraded=False) == (True, True)
    # The collector's side of the contract.
    assert "read" in ats_boards.LAST_RUN


def test_every_other_collector_is_unaffected_by_that():
    """A source with no LAST_RUN must still be judged on what it fetched."""
    import inspect

    import run_collect
    from collectors import google_news

    assert not hasattr(google_news, "LAST_RUN")
    src = inspect.getsource(run_collect.run)
    assert "observed = found if observed is None else observed" in src


def test_already_seen_candidates_are_not_counted_as_rejections():
    """A weekend is not a broken classifier.

    sec_edgar and sec_form_d read a fixed-size window of the most recent SEC
    filings. SEC publishes nothing on Saturday or Sunday, so both weekend runs
    re-read Friday's filings and skip every one as already seen. No guard runs,
    no verdict is reached, nothing is rejected — and the run still reported
    "every candidate rejected" (next to its own "0 rejected") and exited
    non-zero, so `collect` went red both weekend days while the Friday run had
    stored 1 and 3 rows perfectly normally.

    The test therefore has to reach a guard before it can claim they all failed.
    """
    import inspect

    import run_collect

    src = inspect.getsource(run_collect.run)
    assert "everything_rejected = (len(kept) > skipped" in src, (
        "everything_rejected must exclude already-seen skips; comparing against "
        "0 makes an all-already-seen run look like a wholesale rejection")


def test_a_genuine_wholesale_rejection_still_degrades():
    """The guard this protects must keep firing for the case it exists for."""
    import inspect

    import run_collect

    src = inspect.getsource(run_collect.run)
    # Still requires nothing stored AND nothing duplicate, so a run whose
    # candidates DID reach the guards and were all rejected is unchanged.
    assert "and stored == 0 and duplicates == 0" in src
    assert run_collect.run_outcome(
        observed=10, everything_rejected=True, mostly_throttled=False,
        running_degraded=False) == (True, True)


def test_already_seen_is_visible_on_the_health_row():
    """"0 dup, 0 rejected, 0 deferred" named no cause for three zeroes.

    The already-seen count lived only in the step log, so the health page (and
    ops_status, which prints detail[:70]) showed a collector that looked broken
    and gave a reader nothing to go on.
    """
    import inspect

    import run_collect

    src = inspect.getsource(run_collect.run)
    assert "already seen" in src


def test_the_already_seen_counter_is_not_rebound_later_in_the_run():
    """`skipped` held the already-seen count and was then reused as a local for
    a second-pass statistic. Nothing read it after that point, so it was
    harmless — right up until the health verdict started depending on it."""
    import inspect

    import run_collect

    src = inspect.getsource(run_collect.run)
    verdict = src.index("everything_rejected = (len(kept) > skipped")
    assert 'skipped = classify.STATS["read_skipped_strong"]' not in src, (
        "the second-pass statistic must not rebind `skipped`")
    assert src.count("skipped += 1") == 1
    assert verdict > src.index("skipped += 1")
