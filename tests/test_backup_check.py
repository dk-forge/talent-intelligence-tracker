"""The backup verifier, and the ways it could quietly stop verifying.

Most of these are not about whether the checker computes a tidy verdict. They
are about the fact that a guard which has only ever been seen to pass is not a
guard: every FAIL branch below is seeded deliberately and asserted to fire,
because the failure this job exists for — a database that silently shrank under
a racing push — produced no red run anywhere in this repository for five
commits in July.

There is no network, no model and no key in any of this, and nothing here
touches the real database: every fixture is a small SQLite file built in a
temporary directory.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import backup_check

ROOT = Path(__file__).resolve().parent.parent


# --- fixtures ---------------------------------------------------------------

def _reading(tables: dict, *, integrity: str = "ok", when: str | None = None,
             size: int = 1024) -> dict:
    return {
        "checked_at": when or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "commit": "0" * 40, "blob": "1" * 40, "bytes": size,
        "integrity": integrity, "tables": dict(tables), "verdict": "PASS",
    }


HEALTHY = {t: 100 for t in backup_check.CORE_TABLES}


def _columns() -> list[str]:
    """Every column the republish path sends, plus the local publish marker."""
    from pipeline import publish
    return list(publish.FIELDS) + ["row_id", "published_at"]


def _grade(tables=None, *, prior=None, integrity="ok", size=1024,
           columns=None, now=None) -> dict:
    return backup_check.evaluate(
        integrity=integrity,
        tables=HEALTHY if tables is None else tables,
        signals_columns=_columns() if columns is None else columns,
        size_bytes=size, prior=prior, now=now)


def _named(report: dict, check: str) -> dict:
    return next(c for c in report["checks"] if c["check"] == check)


# --- the happy path, so the positive controls below mean something ----------

def test_a_healthy_reading_passes():
    report = _grade(prior=_reading(HEALTHY))
    assert report["verdict"] == backup_check.PASS, report


# --- POSITIVE CONTROLS: each of these must FAIL --------------------------

def test_a_shrunken_table_fails():
    """The July incident, in one assertion.

    A count that went down is never routine here: nothing in this repository
    deletes a row. If this test ever needs a tolerance, the thing to check is
    what started deleting rows, not what the tolerance should be.
    """
    before = dict(HEALTHY)
    after = dict(HEALTHY, signals=HEALTHY["signals"] - 1)
    report = _grade(after, prior=_reading(before))
    assert report["verdict"] == backup_check.FAIL
    assert _named(report, "no_shrink")["status"] == backup_check.FAIL
    assert "signals 100 -> 99" in _named(report, "no_shrink")["detail"]


def test_a_table_that_disappeared_fails():
    after = {t: n for t, n in HEALTHY.items() if t != "source_links"}
    report = _grade(after, prior=_reading(HEALTHY))
    assert report["verdict"] == backup_check.FAIL
    # It fails as a missing CORE table AND as a table that vanished since the
    # last run. Both, deliberately: the second catches a non-core table going
    # missing, which the first cannot see.
    assert _named(report, "core_tables")["status"] == backup_check.FAIL
    assert _named(report, "no_shrink")["status"] == backup_check.FAIL


def test_an_emptied_core_table_fails_even_with_no_history():
    """An empty database with an empty ledger must not read as a clean start.

    This is the shape the guard would take if its expectations came only from
    its own history: nothing to compare against, therefore nothing wrong.
    """
    report = _grade(dict(HEALTHY, signals=0), prior=None)
    assert report["verdict"] == backup_check.FAIL
    assert "empty" in _named(report, "core_tables")["detail"]


def test_a_corrupt_database_fails():
    report = _grade(integrity="*** in database main *** row 4 missing from index",
                    prior=_reading(HEALTHY))
    assert report["verdict"] == backup_check.FAIL
    assert _named(report, "integrity")["status"] == backup_check.FAIL


def test_a_schema_the_site_cannot_be_rebuilt_from_fails():
    from pipeline import publish
    dropped = [c for c in _columns() if c != publish.FIELDS[3]]
    report = _grade(prior=_reading(HEALTHY), columns=dropped)
    assert report["verdict"] == backup_check.FAIL
    assert publish.FIELDS[3] in _named(report, "republishable")["detail"]


def test_a_file_too_big_to_push_fails_before_the_push_does():
    report = _grade(prior=_reading(HEALTHY),
                    size=backup_check.SIZE_ACTION_BYTES + 1)
    assert report["verdict"] == backup_check.FAIL
    assert _named(report, "push_size")["status"] == backup_check.FAIL


def test_the_size_ceiling_leaves_room_to_act():
    """A ceiling set at the limit is an alarm that rings as the door closes."""
    assert backup_check.SIZE_ACTION_BYTES < backup_check.GITHUB_FILE_LIMIT_BYTES
    headroom = backup_check.GITHUB_FILE_LIMIT_BYTES - backup_check.SIZE_ACTION_BYTES
    # Measured growth over 2026-08-05..20 is about 650 KB/day, so this is
    # roughly two weeks of warning at the rate the file is actually growing.
    assert headroom > 14 * 650_000


# --- UNKNOWN is a third state, and never a pass -----------------------------

def test_the_first_run_is_a_baseline_and_not_a_pass():
    report = _grade(prior=None)
    assert report["verdict"] == backup_check.UNKNOWN
    assert _named(report, "no_shrink")["status"] == backup_check.UNKNOWN


def test_a_failure_outranks_an_unknown():
    """Something definitely broken is the answer, whatever else went unread."""
    report = _grade(dict(HEALTHY, signals=0), prior=None, columns=[])
    assert report["verdict"] == backup_check.FAIL


def test_a_baseline_reading_is_still_a_valid_comparison_base():
    """Otherwise the shrink check never starts: every run is the baseline.

    The obvious filter — compare only against the last PASS — has exactly this
    bug, because a baseline run cannot be a PASS by construction.
    """
    baseline = dict(_reading(HEALTHY), verdict=backup_check.UNKNOWN)
    assert backup_check.previous({"checks": [baseline]}) == baseline


def test_a_reading_that_did_not_open_cleanly_is_not_a_comparison_base():
    broken = dict(_reading(HEALTHY, integrity="malformed"), verdict="FAIL")
    assert backup_check.previous({"checks": [broken]}) is None


def test_a_missing_ledger_is_not_an_error(tmp_path):
    assert backup_check.load_ledger(str(tmp_path / "nope.json"))["checks"] == []


def test_an_unreadable_ledger_is_unknown_rather_than_an_empty_one(tmp_path):
    path = tmp_path / "backup_check.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(backup_check.Unavailable):
        backup_check.load_ledger(str(path))


def test_a_checkout_with_no_history_reports_unknown_and_not_a_pass(tmp_path, monkeypatch):
    """Exit 3, not 0. "I could not look" must never read as "it is fine"."""
    monkeypatch.setattr(backup_check, "HERE", str(tmp_path))
    monkeypatch.setattr(backup_check, "LEDGER", str(tmp_path / "ledger.json"))
    code = backup_check.main(["--ledger", str(tmp_path / "ledger.json")])
    assert code == 3


# --- the extraction itself --------------------------------------------------

def test_the_extraction_refuses_to_write_over_the_tracked_database():
    """The 2026-07-28 rule, enforced where it would be broken.

    A restore that copies a file over the live database destroyed 9,572 rows
    once already. Nothing in this verifier may write anywhere git is looking,
    and the one function that writes a file is the place to say so.
    """
    with pytest.raises(ValueError):
        backup_check.extract("HEAD", str(ROOT / backup_check.DB_IN_REPO))


def test_the_verifier_reads_the_committed_blob_and_not_the_working_copy():
    """The working copy is what a session has been editing. The blob is the
    backup. Reading the wrong one is how a check certifies a file that was
    never pushed."""
    source = (ROOT / "backup_check.py").read_text(encoding="utf-8")
    assert "cat-file" in source
    body = source.split("def run(")[1]
    assert "committed_blob" in body and "extract(" in body


def test_it_reads_a_real_sqlite_file_read_only(tmp_path):
    """Not a mock. The real failure is a file that will not open, so the read
    path has to be exercised against an actual database."""
    path = tmp_path / "small.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE signals (row_id INTEGER PRIMARY KEY, headline TEXT)")
    conn.execute("INSERT INTO signals (headline) VALUES ('x')")
    conn.commit()
    conn.close()
    integrity, read = backup_check.read_counts(str(path))
    assert integrity == "ok"
    assert read["tables"] == {"signals": 1}
    assert "headline" in read["signals_columns"]


def test_the_end_to_end_run_on_this_repository_reads_the_real_database(tmp_path):
    """The whole path, on the real committed blob, in a real checkout.

    Slow enough to notice and cheap enough to keep: this is the only assertion
    that the 80 MiB file in this repository actually opens.
    """
    ledger = tmp_path / "ledger.json"
    record = backup_check.run(ledger_path=str(ledger))
    assert record["integrity"] == "ok"
    assert record["tables"]["signals"] > 20_000
    assert record["verdict"] in (backup_check.PASS, backup_check.UNKNOWN)


# --- wiring: a number nobody reads is not a guard --------------------------

def test_ops_status_reports_the_backup():
    source = (ROOT / "ops_status.py").read_text(encoding="utf-8")
    assert "_report_backup" in source
    assert "[6] BACKUP" in source
    assert "problems += _report_backup()" in source


def test_the_weekly_job_exists_and_is_scheduled():
    import yaml
    path = ROOT / ".github/workflows/backup-check.yml"
    doc = yaml.safe_load(path.read_text())
    triggers = doc.get("on") or doc.get(True) or {}
    crons = [e["cron"] for e in (triggers.get("schedule") or [])]
    assert crons, "the backup check is not scheduled, so it verifies nothing"
    assert "backup_check.py --write" in path.read_text(), (
        "the job must RECORD its reading, or there is no baseline next week")


def test_the_weekly_job_is_not_mistaken_for_a_database_writer():
    """It commits a ledger, never a row, so it must stay out of the writer
    lock — and out of the test that assigns that lock, which keys on the
    database filename appearing in the workflow file."""
    text = (ROOT / ".github/workflows/backup-check.yml").read_text()
    assert "talent_intel.db" not in text


def test_the_recovery_document_exists_and_names_what_is_not_covered():
    doc = (ROOT / "docs/RECOVERY.md").read_text(encoding="utf-8")
    assert "What this backup does NOT cover" in doc
    for gap in ("wp_posts", "uploads", "subscriber"):
        assert gap in doc, f"RECOVERY.md does not name {gap} as a gap"


def test_the_ledger_that_ships_is_the_shape_the_checker_writes():
    ledger = backup_check.load_ledger()
    assert ledger["checks"], "the shipped ledger has no readings"
    latest = ledger["checks"][-1]
    for key in ("checked_at", "commit", "blob", "bytes", "integrity",
                "tables", "verdict"):
        assert key in latest


def test_a_truncated_file_grades_as_a_failed_backup_and_not_a_traceback(tmp_path):
    """A push that died halfway is the likeliest way this file goes bad.

    sqlite3 answers that with a raised exception, and an exception ends the run
    before the ledger is written, which loses the baseline as well as the week.
    It has to come back as a verdict.
    """
    good = tmp_path / "good.db"
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE signals (row_id INTEGER PRIMARY KEY, headline TEXT)")
    conn.executemany("INSERT INTO signals (headline) VALUES (?)",
                     [(f"row {i}",) for i in range(2000)])
    conn.commit()
    conn.close()

    truncated = tmp_path / "truncated.db"
    truncated.write_bytes(good.read_bytes()[: good.stat().st_size // 2])

    integrity, read = backup_check.read_counts(str(truncated))
    assert integrity != "ok"
    report = backup_check.evaluate(
        integrity=integrity, tables=read["tables"],
        signals_columns=read["signals_columns"], size_bytes=1024, prior=None)
    assert report["verdict"] == backup_check.FAIL
