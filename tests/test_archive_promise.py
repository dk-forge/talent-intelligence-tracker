"""The reader-facing archive re-check promise, and what keeps it true.

Every publisher-sourced listing row without a Wayback snapshot now prints:

    No archive snapshot yet. We re-check weekly; next check by <date>.

That sentence is a commitment. This file pins the chain that keeps it one:

  1. the shipped plugin file (data/archive_promise.json) matches a FRESH
     derivation from the real schedule, so a cron or scope edit that nobody
     regenerated for is a red test rather than a quiet lie;
  2. the schedule has the CAPACITY to sweep the whole in-scope unarchived
     queue inside the promised window, measured against the committed database;
  3. archive_recheck_overdue() — the check ops_status.py [2c] goes red on —
     actually catches a URL the promise has been broken for, and does not cry
     wolf over new rows, archived rows or out-of-scope collectors;
  4. the sentence is composed in exactly ONE place (tit_archive_pending_note in
     the plugin main file); dashboard.js prints the server's copy verbatim, so
     the two paints cannot drift and the date has one derivation.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import schema, source_links

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
SHIPPED = PLUGIN / "data" / "archive_promise.json"


@pytest.fixture(scope="module")
def shipped():
    assert SHIPPED.exists(), (
        "wordpress-plugin/.../data/archive_promise.json is missing. The listing "
        "pages render the pending-archive sentence from it; run "
        "build_archive_promise.py.")
    return json.loads(SHIPPED.read_text())


def test_shipped_promise_matches_the_schedule(shipped):
    """Run build_archive_promise.py if this fails — never hand-edit the JSON."""
    fresh = source_links.archive_promise(ROOT)
    for key in ("recheck_days", "cadence_hours", "collectors"):
        assert shipped.get(key) == fresh[key], (
            f"data/archive_promise.json disagrees with the schedule on {key!r} "
            f"(shipped {shipped.get(key)!r}, derived {fresh[key]!r}). The page "
            f"is promising a cadence the workflows do not run. Regenerate: "
            f"python3 build_archive_promise.py")


def test_the_promise_days_have_one_definition(shipped):
    assert shipped["recheck_days"] == source_links.RECHECK_PROMISE_DAYS


def test_the_schedule_can_keep_the_promise(shipped):
    """Capacity, against the committed database rather than against hope."""
    cadence = source_links.scheduled_archive_cadence_hours(ROOT)
    assert cadence, (
        "The archive slot is missing from schedule-link-hygiene.yml while the "
        "plugin ships a re-check promise. Either re-arm the slot or remove the "
        "pending-state copy; a promise nothing runs is worse than silence.")
    runs = (source_links.RECHECK_PROMISE_DAYS * 24) // cadence
    capacity = runs * source_links.scheduled_archive_limit(ROOT)
    conn = schema.connect(ROOT / "data" / "talent_intel.db")
    try:
        cover = source_links.archive_coverage(conn, shipped["collectors"])
    finally:
        conn.close()
    queue = cover["capture_queue"] + cover["never_probed"]
    assert queue <= capacity, (
        f"{queue} in-scope URLs await a snapshot but the schedule can only "
        f"examine {capacity} in {source_links.RECHECK_PROMISE_DAYS} days. The "
        f"live pages are promising a re-check the cadence cannot deliver. Fix "
        f"the schedule or the backlog; do NOT widen the promise to fit.")


# --- the overdue check itself ------------------------------------------------

def _add_signal(conn, url, collector, captured_at):
    conn.execute(
        """INSERT INTO signals (signal_id, headline, summary,
               talent_readthrough, company, company_key, pillar,
               signal_direction, confidence, source_url, source_name,
               captured_at, as_of, content_hash, collector)
           VALUES (?, 'h', 's', 't', 'ACME', 'acme', 'company_development',
                   'hiring', 'reported', ?, 'Outlet', ?, ?, ?, ?)""",
        (url, url, captured_at, captured_at, "hash-" + url, collector))


def _iso(days_ago):
    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "promise.db")
    yield connection
    connection.close()


def test_overdue_catches_a_broken_promise_and_only_that(conn):
    scope = ["national_press"]
    days = source_links.RECHECK_PROMISE_DAYS

    # Stored long ago, pending, last archive round OLDER than the promise.
    _add_signal(conn, "https://ex.test/broken", "national_press", _iso(30))
    source_links.record_archive(conn, "https://ex.test/broken", state="pending",
                                attempts=1, probes=1)
    conn.execute("UPDATE source_links SET updated_at = ? WHERE source_url = ?",
                 (_iso(days + 2), "https://ex.test/broken"))

    # Stored long ago, NEVER probed at all: also a broken promise.
    _add_signal(conn, "https://ex.test/never", "national_press", _iso(days + 3))

    # Kept promises, none of which may appear:
    _add_signal(conn, "https://ex.test/fresh", "national_press", _iso(0))
    _add_signal(conn, "https://ex.test/recent", "national_press", _iso(30))
    source_links.record_archive(conn, "https://ex.test/recent", state="pending",
                                attempts=1, probes=1)  # updated_at = now
    _add_signal(conn, "https://ex.test/done", "national_press", _iso(30))
    source_links.record_archive(conn, "https://ex.test/done", state="archived",
                                archive_url="https://web.archive.org/web/1/x",
                                attempts=1, probes=1)
    # Out of scope: EDGAR keeps its own filings; no promise is printed there.
    _add_signal(conn, "https://sec.test/old", "sec_edgar", _iso(60))
    conn.commit()

    overdue = source_links.archive_recheck_overdue(conn, scope, days=days)
    urls = sorted(r["source_url"] for r in overdue)
    assert urls == ["https://ex.test/broken", "https://ex.test/never"], urls


def test_ops_status_enforces_the_promise():
    """The check must be wired to the surface a session actually reads."""
    text = (ROOT / "ops_status.py").read_text()
    assert "archive_recheck_overdue" in text


# --- the rendered sentence has one composer ----------------------------------

def test_the_sentence_is_composed_once():
    """dashboard.js must PRINT the note, never rebuild it.

    The pending sentence carries a date. Two composers is two clocks, and the
    repaint disagreeing with the first paint about a promised date is the exact
    drift the data-archive-note attribute exists to prevent.
    """
    opener = "No archive snapshot yet. We re-check "
    composers = []
    for path in PLUGIN.rglob("*"):
        if path.suffix in (".php", ".js") and opener in path.read_text():
            composers.append(path.name)
    assert composers == ["talent-intelligence-tracker.php"], (
        f"the pending-archive sentence is written in {composers}; it must be "
        f"composed only by tit_archive_pending_note() and carried to "
        f"dashboard.js on the data-archive-note attribute.")


def test_the_note_never_renders_without_the_promise_file():
    """Null promise file = no note, everywhere. An invented date is worse."""
    php = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert "if (!$p || !in_array((string) $collector, $p['collectors'], true)) return '';" in php
