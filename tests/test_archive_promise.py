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


def test_ops_status_never_swallows_an_unreadable_ledger():
    """UNKNOWN gets a line, not a `pass`.

    The promise block used to end `except sqlite3.OperationalError: pass`, so
    an unreadable ledger removed the verdict LINE from [2c] entirely — which
    on a glance is indistinguishable from a section with nothing to report.
    Absence of a signal is not a pass.
    """
    text = (ROOT / "ops_status.py").read_text()
    block = text.split("archive_recheck_overdue", 1)[1][:2600]
    assert "except sqlite3.OperationalError:\n        pass" not in block, (
        "ops_status.py is swallowing an unreadable source-link ledger again. "
        "An unreadable ledger is UNKNOWN and must print a verdict and append "
        "a problem, never vanish.")
    assert "UNKNOWN" in block


def test_ops_status_says_unknown_out_loud_when_the_ledger_will_not_read(
        tmp_path, capsys, monkeypatch):
    """Prove the UNKNOWN branch by mutation, not by reading the source.

    A text assertion that the word UNKNOWN appears would pass over a branch
    that never runs. So this hands [2c] a database whose source_links table is
    gone and checks that the section SAYS so and RAISES a problem — the two
    things `pass` did not do.
    """
    import ops_status

    db = tmp_path / "blind.db"
    connection = schema.connect(db)
    _add_signal(connection, "https://ex.test/x", "national_press", _iso(30))
    # One checked link, so [2c] gets past its "nothing measured yet" early
    # return and actually reaches the promise verdict.
    connection.execute(
        "INSERT INTO source_links (source_url, http_status, final_url, "
        "final_domain, state, checked_at, checks, updated_at) "
        "VALUES (?, 200, ?, 'ex.test', 'live', ?, 1, ?)",
        ("https://ex.test/x", "https://ex.test/x", _iso(0), _iso(0)))
    connection.commit()
    connection.close()

    # MUTATION: the one read the promise verdict rests on cannot be made. A
    # dropped table is the cheapest faithful stand-in for the half-fetched
    # cache file and the mid-migration column this branch exists for.
    def blind(*a, **k):
        raise sqlite3.OperationalError("no such table: source_links")

    # _report_link_rot imports the module inside the function, so patch the
    # module itself rather than a name bound on ops_status.
    monkeypatch.setattr(source_links, "archive_recheck_overdue", blind)
    conn = schema.connect_ro(db)
    try:
        problems = ops_status._report_link_rot(conn)
    finally:
        conn.close()

    printed = capsys.readouterr().out
    assert "UNKNOWN" in printed, (
        "[2c] printed no verdict for an unreadable ledger. A missing line "
        "reads as a section with nothing to report.")
    assert any("could NOT be checked" in p for p in problems), (
        "an unreadable ledger left ops_status.py exiting 0. A check that "
        "could not run has not reported that the promise is kept.")


# --- the promise has a keeper between sessions -------------------------------

def test_the_promise_check_is_asserted_by_the_job_that_owes_it():
    """ops_status.py is a session's dashboard, not enforcement.

    Every invocation of it in .github/workflows/ is `|| true` — correctly, it
    exits 2 for a dozen unrelated reasons. So the reality half of the promise
    needs its own assertion in the archiver's own workflow, or a promise that
    breaks between two human sessions is nobody's red run.
    """
    wf = (ROOT / ".github" / "workflows" / "archive-sources.yml").read_text()
    assert "--check-promise" in wf, (
        "archive-sources.yml no longer asserts the re-check promise. The "
        "listing pages print that sentence on every scheduled run; if this "
        "step goes, nothing between two sessions checks that it is true.")
    # It must not be neutered the way ops_status.py had to be.
    for line in wf.splitlines():
        if "--check-promise" in line:
            assert "|| true" not in line and "continue-on-error" not in line, (
                "the promise assertion is present but its exit code is "
                "discarded, which is the state this step exists to end.")


def test_check_promise_passes_fails_and_answers_unknown(tmp_path, monkeypatch):
    """Prove all three verdicts by mutation, on a real database.

    A checker that has only ever seen one branch is an untested checker.
    """
    import archive_sources

    scope = ["national_press"]
    monkeypatch.setattr(source_links, "scheduled_archive_scope",
                        lambda *a, **k: scope)

    db = tmp_path / "promise.db"
    connection = schema.connect(db)
    # A kept promise: stored long ago, probed just now.
    _add_signal(connection, "https://ex.test/ok", "national_press", _iso(30))
    source_links.record_archive(connection, "https://ex.test/ok",
                                state="pending", attempts=1, probes=1)
    connection.commit()
    connection.close()
    assert archive_sources.check_promise(db) == 0, (
        "a ledger with nothing overdue must PASS")

    # MUTATION 1: age that URL's last round past the window. Nothing else
    # changes, and the verdict must flip.
    connection = schema.connect(db)
    connection.execute(
        "UPDATE source_links SET updated_at = ? WHERE source_url = ?",
        (_iso(source_links.RECHECK_PROMISE_DAYS + 2), "https://ex.test/ok"))
    connection.commit()
    connection.close()
    assert archive_sources.check_promise(db) == 1, (
        "an in-scope URL last attempted outside the promise window must FAIL. "
        "If this passes, the pages can print a re-check nobody is making and "
        "no run anywhere goes red.")

    # MUTATION 2: an unreadable scope is UNKNOWN, never a pass. This is the
    # branch that matters most — a check that cannot run must not report the
    # promise kept.
    monkeypatch.setattr(source_links, "scheduled_archive_scope",
                        lambda *a, **k: [])
    assert archive_sources.check_promise(db) == 3

    # MUTATION 3: a ledger that is NOT THERE is UNKNOWN, not a crash and above
    # all not a pass. This is the branch that caught its own author: with
    # schema.connect() the missing file was CREATED, opened empty, found
    # nothing overdue and reported the promise kept. Reading zero overdue rows
    # because the ledger was never fetched looks exactly like a kept promise,
    # so the check opens read-only and lets the refusal be the answer.
    monkeypatch.setattr(source_links, "scheduled_archive_scope",
                        lambda *a, **k: scope)
    missing = tmp_path / "nested" / "gone.db"
    assert archive_sources.check_promise(missing) == 3
    assert not missing.exists(), (
        "--check-promise created the ledger it could not find, which is how "
        "an empty read becomes a confident PASS")


def test_check_promise_makes_no_request_and_writes_nothing(tmp_path,
                                                           monkeypatch):
    """It is free and read-only, which is why it can run on every pass."""
    import archive_sources

    monkeypatch.setattr(source_links, "scheduled_archive_scope",
                        lambda *a, **k: ["national_press"])
    db = tmp_path / "promise.db"
    connection = schema.connect(db)
    _add_signal(connection, "https://ex.test/ok", "national_press", _iso(1))
    connection.commit()
    connection.close()

    def explode(*a, **k):                                     # pragma: no cover
        raise AssertionError("--check-promise made a network request")

    monkeypatch.setattr(archive_sources, "_get", explode, raising=False)
    before = db.stat().st_mtime_ns
    assert archive_sources.check_promise(db) == 0
    assert db.stat().st_mtime_ns == before, (
        "--check-promise wrote to the ledger it is only supposed to read")


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
