"""A run has to write down what it cost.

`classify.STATS` has counted tokens and carried OpenRouter's own cost figure
since the gate was added. It was printed at the end of every run and then lost
when the process exited, so the only place spend existed was a month-end total
and "cost per stored row" could not be plotted at all. These pin the three
properties that make the record trustworthy:

  * it lands on the health row the run already files, so it merges and reaches
    ops_status with no new plumbing;
  * a run that called no model records NULL rather than zero, because a free
    run and a run whose accounting went missing must not look the same;
  * it survives a database that predates the columns, in both directions — the
    migration adds them, and a writer looking at an unmigrated file still files
    its health row instead of failing over a cost figure.
"""

from __future__ import annotations

import sqlite3

import pytest

import ops_status
from pipeline import classify, schema, store


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "cost.db")
    yield connection
    connection.close()


@pytest.fixture
def stats():
    """classify.STATS is module state, so it is saved and put back.

    Deliberately not a stubbed module (see CLAUDE.md): a fake in sys.modules
    outlives the test and shadows the real thing for everything loaded after it.
    """
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


def latest(connection) -> sqlite3.Row:
    return connection.execute(
        "SELECT * FROM source_health ORDER BY run_at DESC LIMIT 1").fetchone()


USAGE = {
    "model": "deepseek/deepseek-chat",
    "gate_model": "google/gemini-2.5-flash-lite",
    "prompt_tokens": 216_000,
    "cached_tokens": 131_000,
    "completion_tokens": 24_000,
    "cost_usd": 0.0768,
    "reads_bought": 60,
    "rows_from_reads": 34,
}


# --- what is persisted -------------------------------------------------------

def test_the_health_row_carries_what_the_run_cost(conn):
    store.report_health(conn, "google_news", status="ok", items_found=220,
                        items_stored=34, detail="20 dup", usage=USAGE)
    row = latest(conn)
    for column, value in USAGE.items():
        assert row[column] == value, column


def test_a_run_that_called_no_model_records_null_not_zero(conn):
    """A structured source spends nothing. Writing 0.0 would make a genuinely
    free run indistinguishable from a run whose accounting went missing."""
    store.report_health(conn, "sec_form_d_bulk", status="ok", items_found=1690,
                        items_stored=0, usage=None)
    row = latest(conn)
    for column in store.USAGE_COLUMNS:
        assert row[column] is None, column


def test_the_zero_found_downgrade_still_applies_with_usage(conn):
    """The oldest rule in this ledger must not be lost to a new argument."""
    store.report_health(conn, "google_news", status="ok", items_found=0,
                        usage=USAGE)
    row = latest(conn)
    assert row["status"] == "degraded"
    assert row["cost_usd"] == USAGE["cost_usd"]


# --- the ratio ---------------------------------------------------------------

def test_reads_to_rows_is_computed_in_one_place():
    assert store.reads_to_rows_pct(60, 34) == 56
    assert store.reads_to_rows_pct(10, 10) == 100
    assert store.reads_to_rows_pct(10, 0) == 0
    # No reads bought is not a 0% ratio, it is no ratio at all: a run that
    # closed everything deterministically wasted nothing.
    assert store.reads_to_rows_pct(0, 0) is None
    assert store.reads_to_rows_pct(None, None) is None


def test_the_run_log_and_ops_status_share_that_function():
    """Two format strings computing one percentage is two percentages."""
    import inspect

    import run_collect

    assert "store.reads_to_rows_pct" in inspect.getsource(run_collect.run)
    assert "reads_to_rows_pct" in inspect.getsource(ops_status._report_run_cost)


# --- the snapshot ------------------------------------------------------------

def test_no_model_call_means_no_snapshot(stats):
    stats.update({"gate_calls": 0, "full_calls": 0})
    assert classify.usage_snapshot() is None


def test_a_gate_only_run_still_reports_its_cost(stats):
    """Every candidate rejected at the gate is still money spent."""
    stats.update({"gate_calls": 120, "gate_rejects": 120, "full_calls": 0,
                  "prompt_tokens": 9_000, "cached_tokens": 0,
                  "completion_tokens": 120, "usd": 0.0004, "read_stored": 0})
    snapshot = classify.usage_snapshot()
    assert snapshot is not None
    assert snapshot["reads_bought"] == 0
    assert snapshot["cost_usd"] == 0.0004


def test_the_snapshot_reports_the_providers_own_figure(stats):
    stats.update({"gate_calls": 200, "full_calls": 60, "prompt_tokens": 216_000,
                  "cached_tokens": 131_000, "completion_tokens": 24_000,
                  "usd": 0.07684321, "read_stored": 34})
    snapshot = classify.usage_snapshot()
    assert snapshot["prompt_tokens"] == 216_000
    assert snapshot["cached_tokens"] == 131_000
    assert snapshot["completion_tokens"] == 24_000
    assert snapshot["reads_bought"] == 60
    assert snapshot["rows_from_reads"] == 34
    # Six decimals, because one gate call costs about $0.000004 and anything
    # coarser records a real charge as free.
    assert snapshot["cost_usd"] == 0.076843


def test_the_snapshot_names_the_models_that_were_actually_configured(stats):
    stats.update({"gate_calls": 1, "full_calls": 1})
    snapshot = classify.usage_snapshot()
    assert snapshot["model"] == classify.MODEL
    assert snapshot["gate_model"] == classify.GATE_MODEL


def test_every_snapshot_key_is_a_column(conn, stats):
    """A key the table does not have would be silently dropped."""
    stats.update({"gate_calls": 1, "full_calls": 1})
    columns = {r[1] for r in conn.execute("PRAGMA table_info(source_health)")}
    assert set(classify.usage_snapshot()) <= columns


def test_the_run_records_it(stats):
    """run_collect must actually pass the snapshot, or none of this fires."""
    import inspect

    import run_collect

    src = inspect.getsource(run_collect.run)
    assert "usage=classify.usage_snapshot()" in src


def test_every_stop_path_records_the_usage(stats):
    """A 402 or a rotated key can arrive after money has already been spent.

    This used to count occurrences of the snapshot call, and broke the day the
    stop paths were folded into one helper — a refactor that made the property
    MORE true, not less. So it asserts the property instead: every early
    `return` inside the candidate loop goes through `_stop_run`, and
    `_stop_run` is what records the usage. There are four such sites since the
    read-late split (401 and 402, from extraction and from interpretation);
    adding a fifth that forgets is the mistake this catches.
    """
    import inspect
    import re

    import run_collect

    src = inspect.getsource(run_collect.run)
    stop = src.split("def _stop_run", 1)[1].split("\n    print(f\"\\n[", 1)[0]
    assert "usage=classify.usage_snapshot()" in stop
    assert 'status="error"' in stop

    # The candidate loop only. Everything after it is the run's own summary
    # and its ordinary exit codes, which are not stop paths.
    body = src.split("for item in kept:", 1)[1].split('f"\\n[{collector}] found=', 1)[0]
    stray = re.findall(r"^\s+return (?!_stop_run)\S.*$", body, re.M)
    assert not stray, (
        "a stop path inside the candidate loop bypasses _stop_run, so the "
        f"run's spend would go unrecorded: {stray}")
    assert body.count("return _stop_run(") >= 4


# --- old databases -----------------------------------------------------------

def _unmigrated(path) -> sqlite3.Connection:
    """A source_health table as it was before cost accounting existed."""
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """CREATE TABLE source_health (
               collector TEXT NOT NULL, run_at TEXT NOT NULL,
               status TEXT NOT NULL, items_found INTEGER NOT NULL DEFAULT 0,
               items_stored INTEGER NOT NULL DEFAULT 0, detail TEXT,
               PRIMARY KEY (collector, run_at))""")
    raw.commit()
    return raw


def test_the_migration_adds_the_columns_to_a_pre_existing_ledger(tmp_path):
    db = tmp_path / "old.db"
    _unmigrated(db).close()

    connection = schema.connect(db)
    try:
        assert store.health_has_cost_columns(connection)
    finally:
        connection.close()


def test_health_still_lands_on_a_database_without_the_columns(tmp_path):
    """The database is committed and other jobs run against it, so a writer can
    be looking at a file the migration has not reached. A cost figure is the
    last thing that may cost a run its health row."""
    db = tmp_path / "old.db"
    raw = _unmigrated(db)
    try:
        assert not store.health_has_cost_columns(raw)
        store.report_health(raw, "google_news", status="ok", items_found=220,
                            items_stored=34, usage=USAGE)
        row = latest(raw)
        assert row["items_stored"] == 34
        assert set(row.keys()) == {"collector", "run_at", "status",
                                   "items_found", "items_stored", "detail"}
    finally:
        raw.close()


def test_rows_written_before_the_columns_existed_survive(tmp_path):
    db = tmp_path / "old.db"
    raw = _unmigrated(db)
    store.report_health(raw, "gdelt", status="ok", items_found=120, items_stored=3)
    raw.commit()
    raw.close()

    connection = schema.connect(db)
    try:
        row = latest(connection)
        assert row["items_found"] == 120
        # NULL, and read as "never measured" rather than as free.
        assert row["cost_usd"] is None
    finally:
        connection.close()


def test_a_merge_carries_the_cost_columns(tmp_path):
    """merge_db unions the health ledger on (collector, run_at). A new column
    missing from that union would be dropped on every collect run, because the
    workflow merges before it commits."""
    import merge_db

    ours, theirs = tmp_path / "ours.db", tmp_path / "theirs.db"
    mine = schema.connect(ours)
    store.report_health(mine, "google_news", status="ok", items_found=220,
                        items_stored=34, usage=USAGE)
    mine.commit()
    mine.close()
    schema.connect(theirs).close()

    merge_db.merge(ours, theirs)

    connection = schema.connect(theirs)
    try:
        assert latest(connection)["cost_usd"] == USAGE["cost_usd"]
    finally:
        connection.close()


# --- what a session sees -----------------------------------------------------

def _record(connection, collector: str, run_at: str, **usage) -> None:
    """Write a health row at a chosen timestamp. report_health stamps `now`, so
    the run_at is corrected afterwards rather than faked through the clock."""
    store.report_health(connection, collector, status="ok", items_found=220,
                        items_stored=usage.pop("items_stored", 34),
                        usage={**USAGE, **usage})
    connection.execute(
        "UPDATE source_health SET run_at = ? WHERE run_at = "
        "(SELECT MAX(run_at) FROM source_health)", (run_at,))
    connection.commit()


def _recent(days_ago: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_ops_status_shows_the_cost_and_the_ratio(conn, capsys):
    _record(conn, "google_news", _recent(0.2))
    problems = ops_status._report_run_cost(conn)
    out = capsys.readouterr().out

    assert "$0.0768" in out
    assert "60 reads -> 34 rows (56%)" in out
    assert "deepseek/deepseek-chat read-through" in out
    assert "per stored row" in out
    assert problems == []


def test_ops_status_reports_the_cache_share(conn, capsys):
    """Whether prefix caching fired is the difference between the measured bill
    and twice it, and it is only knowable from what the provider reported."""
    _record(conn, "google_news", _recent(0.2))
    ops_status._report_run_cost(conn)
    assert "60% served from cache" in capsys.readouterr().out


def test_ops_status_says_so_when_nothing_has_recorded_yet(conn, capsys):
    store.report_health(conn, "sec_form_d_bulk", status="ok", items_found=9)
    assert ops_status._report_run_cost(conn) == []
    assert "No run has recorded a cost yet" in capsys.readouterr().out


def test_ops_status_survives_a_ledger_without_the_columns(tmp_path, capsys):
    raw = _unmigrated(tmp_path / "old.db")
    try:
        store.report_health(raw, "google_news", status="ok", items_found=220,
                            items_stored=34)
        assert ops_status._report_run_cost(raw) == []
        assert "predates per-run cost accounting" in capsys.readouterr().out
    finally:
        raw.close()


def test_drift_past_the_allowance_needs_a_human(conn, capsys):
    """The whole reason to persist this: a week's drift is visible in a day."""
    allowance = ops_status._monthly_allowance()
    assert allowance, "spend.py's monthly allowance could not be read"
    # Three runs, because one dispatched backfill must not be able to raise
    # this alarm on its own.
    for n, day in enumerate((0.2, 1.2, 2.2)):
        _record(conn, f"c{n}", _recent(day), cost_usd=allowance)

    problems = ops_status._report_run_cost(conn)
    assert any("project" in p for p in problems), problems
    assert "allowance (spend.py)" in capsys.readouterr().out


def test_two_runs_alone_do_not_raise_the_alarm(conn):
    allowance = ops_status._monthly_allowance()
    for n, day in enumerate((0.2, 1.2)):
        _record(conn, f"c{n}", _recent(day), cost_usd=allowance * 4)
    assert ops_status._report_run_cost(conn) == []


def test_the_allowance_is_read_from_spend_py_not_copied(tmp_path):
    """A duplicated budget is a budget that goes stale silently. Read by parsing
    rather than importing, because spend.py imports requests and ops_status
    promises stdlib only."""
    import ast

    source = (ops_status.ROOT / "spend.py").read_text()
    expected = next(
        float(ast.literal_eval(node.value))
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "MONTHLY_ALLOWANCE_USD"
                for t in node.targets))
    assert ops_status._monthly_allowance() == expected
    assert "MONTHLY_ALLOWANCE_USD" not in ops_status.__dict__


def test_ops_status_keeps_its_no_dependency_promise():
    """It is the first thing every session runs, so it must work in a checkout
    with nothing installed."""
    import ast

    tree = ast.parse((ops_status.ROOT / "ops_status.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "requests" not in imported
    assert "spend" not in imported
