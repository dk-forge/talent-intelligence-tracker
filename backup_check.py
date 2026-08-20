#!/usr/bin/env python3
"""Is the committed database still a usable backup?

    python3 backup_check.py            # check and print, offline, no write
    python3 backup_check.py --write    # ...and append to the committed ledger
    python3 backup_check.py --json     # machine-readable, for ops_status

WHAT THIS IS FOR. This repository's answer to "what happens when the host
disappears" is that the database is committed: `data/talent_intel.db` is in git,
every collect run pushes it back, and `git show <sha>:data/talent_intel.db`
hands any past version back whole. That is a real backup and it is also, until
something exercises it, a hypothesis. An unexercised backup is exactly the kind
of protection that is discovered to be worthless on the one day it is needed.

So this opens THE COMMITTED BLOB, not the working copy. The working copy is
what a session has been editing; the blob is what a stranger with nothing but
this repository would actually get. They are not the same file and only one of
them is the backup.

Four questions, and each one is a way the backup dies quietly:

  1. does the committed blob open at all, and does `PRAGMA integrity_check`
     pass — a truncated or half-written 82MB file pushed by a runner that died
     mid-commit looks exactly like a healthy one in `git log`;
  2. is every table still there, and is any core table empty — the 2026-07-28
     reset-and-copy commits destroyed 9,572 signal rows and the entire
     employer_identity cache across five commits without a single red run;
  3. did any table SHRINK since the last recorded run — nothing in this
     repository deletes a row (revisions are appended, `is_current` is flipped),
     so a smaller count is never routine and is always the signature of one
     job's push overwriting another's;
  4. can the restored file still feed the republish path — every column
     `pipeline.publish.FIELDS` sends has to exist in the restored schema, or
     the backup restores a database the site cannot be rebuilt from.

And one that is about the backup MECHANISM rather than its contents: GitHub
refuses a single file over 100 MiB outright. A backup whose next push is
rejected is not a backup, and the failure arrives as a red push in an unrelated
collect run at 22:00 UTC, which is the worst possible place to learn it.

Since 2026-08-20 the backup is TWO committed files — `data/talent_intel.db` and
`data/talent_intel_cache.db` — and all five questions are asked of the pair.
Both are extracted, both must open, their table counts are unioned, and the
size question is asked of the larger one, because the limit is per file.

EXIT CODES
    0  PASS   — the committed blob is a usable backup
    2  FAIL   — it is not, or it is shrinking, or the push is about to be
                refused. A human is needed.
    3  UNKNOWN — the check could not be performed (no git, no blob, an import
                that is not there). Never a pass. FAIL wins over UNKNOWN: if
                something is definitely wrong, that is the answer, whatever
                else could not be read.

Costs nothing. No model, no network, no key, stdlib only apart from the
optional `pipeline.publish` import in check 4, whose absence is UNKNOWN rather
than a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_IN_REPO = "data/talent_intel.db"

# THE BACKUP IS TWO FILES NOW. The database was split on 2026-08-20 because the
# per-file push limit below was 32 days away (schema.py, "the second committed
# file"). Both halves are committed, both are restored together, and this check
# grades the PAIR: an integrity_check that passes on the product file while the
# cache file is truncated is not a backup that restores. Every table count from
# both files goes into one dict, so the split itself does not read as tables
# vanishing, and `push_size` grades the LARGER of the two, because the limit is
# per file and it is the bigger half that hits the wall first.
CACHE_IN_REPO = "data/talent_intel_cache.db"
DB_FILES = (DB_IN_REPO, CACHE_IN_REPO)
LEDGER = os.path.join(HERE, "data", "backup_check.json")

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

# Tables whose absence or emptiness is a failure on its own, without any
# history to compare against. Hard-coded ON PURPOSE: a check that derives its
# expectations only from the last run reads an empty ledger as "nothing to
# compare, all clear", and the first run after the ledger is lost would bless
# an empty database. `funding_corroborations` is deliberately NOT here — it is
# legitimately 0 rows today, and a floor it cannot meet would be a permanent
# red that teaches the next session to stop reading this exit code.
CORE_TABLES = (
    "signals",
    "seen_urls",
    "source_links",
    "employer_identity",
    "source_health",
    "publish_guardrails",
)

# GitHub rejects a push containing a file over 100 MiB. Not a policy we can
# raise, not something a paid plan changes, and it applies to the push and to a
# single FILE — not to the repository. That last word is why the answer on
# 2026-08-20 was to split the database rather than to take it out of git: two
# committed files of 57 and 18 MiB are as safe to push as one of 75 would not
# be, and every guarantee that depends on the database being in git survives.
#
# The check still fires at 90 MiB, now measured on the larger half. At the
# post-split growth of ~358 KB/day that is roughly 120 days between the alarm
# and the wall, and the alarm is what stops this being discovered as a failed
# push inside a 22:00 collect run.
#
# WHEN THIS FIRES AGAIN, the answer is NOT another VACUUM: the file was
# vacuumed at the split and has no free pages to reclaim. `signals` is the
# growth now, it is the product rather than a cache, and nothing here deletes
# a row. The next move is to close the file and start a new one — a dated,
# frozen shard that is never rewritten, which bounds the live file for good
# instead of buying another four months. See docs/RECOVERY.md.
GITHUB_FILE_LIMIT_BYTES = 100 * 1024 * 1024
SIZE_ACTION_BYTES = 90 * 1024 * 1024

# A year of weekly runs, plus room for the dispatched ones.
KEEP_CHECKS = 80


# --- reading the committed blob --------------------------------------------

def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", HERE, *args],
        check=True, capture_output=True, text=True).stdout.strip()


def committed_blob(ref: str = "HEAD",
                   db_in_repo: str = DB_IN_REPO) -> tuple[str, str, int]:
    """(commit sha, blob sha, size in bytes) of one database file at `ref`.

    Raises `Unavailable` when this cannot be answered, which is UNKNOWN and not
    a failure: a checkout with no git directory has not proved the backup is
    broken, only that it could not look.
    """
    try:
        commit = _git("rev-parse", ref)
        blob = _git("rev-parse", f"{ref}:{db_in_repo}")
        size = int(_git("cat-file", "-s", blob))
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
        raise Unavailable(f"could not read {db_in_repo} out of git ({e})") from e
    return commit, blob, size


class Unavailable(RuntimeError):
    """The check could not be performed. UNKNOWN, never a pass."""


def extract(blob: str, path: str) -> None:
    """Write the blob to `path`. Never over the working database.

    `git cat-file` to a NEW file, deliberately, and never `git checkout` of the
    tracked path: this repository has already paid once for a job that restored
    a database by copying a file over the live one (2026-07-28, 9,572 rows).
    Nothing here may write anywhere git is looking.
    """
    real = os.path.realpath(path)
    tracked = {os.path.realpath(os.path.join(HERE, f)) for f in DB_FILES}
    if real in tracked:
        raise ValueError("refusing to extract over the tracked database")
    with open(path, "wb") as fh:
        subprocess.run(["git", "-C", HERE, "cat-file", "-p", blob],
                       check=True, stdout=fh)


def read_counts(path: str) -> tuple[str, dict]:
    """(integrity verdict, {table: rows}) from a database opened READ ONLY.

    A file too damaged to read is a FAILED backup, not an error to crash on: a
    truncated push is the single most likely way this file goes bad, and
    sqlite3 answers that with a raised `DatabaseError` rather than a verdict
    string. It is turned into one here so it grades like any other integrity
    result instead of ending the run in a traceback with no ledger entry.
    """
    try:
        return _read_counts(path)
    except sqlite3.DatabaseError as e:
        return f"unreadable: {e}", {"tables": {}, "signals_columns": []}


def _read_counts(path: str) -> tuple[str, dict]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
                  for n in names}
        signals_columns = [r[1] for r in conn.execute("PRAGMA table_info(signals)")]
    finally:
        conn.close()
    return integrity, {"tables": counts, "signals_columns": signals_columns}


# --- the ledger -------------------------------------------------------------

def load_ledger(path: str = LEDGER) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"version": 1, "checks": []}
    except (json.JSONDecodeError, OSError) as e:
        raise Unavailable(f"the ledger at {path} could not be read ({e})") from e
    if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
        raise Unavailable(f"the ledger at {path} is not the shape this writes")
    return data


def previous(ledger: dict) -> dict | None:
    """The newest reading whose counts can be trusted as a comparison base.

    NOT "the newest PASS". A baseline run is UNKNOWN because it has nothing to
    compare against, and if that disqualified it from being compared against in
    turn, the shrink check could never start: every run would be the baseline
    for ever. What disqualifies a reading is a database that did not open
    cleanly, whose counts mean nothing.
    """
    checks = [c for c in ledger.get("checks", [])
              if c.get("integrity") == "ok" and c.get("tables")]
    return checks[-1] if checks else None


def write_ledger(ledger: dict, path: str = LEDGER) -> None:
    ledger["checks"] = ledger["checks"][-KEEP_CHECKS:]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True)
        fh.write("\n")


# --- the verdict ------------------------------------------------------------

def publishable(columns: list[str]) -> tuple[str, str]:
    """Does the restored schema still carry every column the site is fed from?

    Imported rather than restated: a list copied into this file would drift
    from `pipeline.publish.FIELDS` the first time a column was added, and would
    then quietly certify a backup that cannot rebuild the site. When the import
    is unavailable (the module needs `requests`), that is UNKNOWN.
    """
    try:
        from pipeline import publish
    except Exception as e:                                    # noqa: BLE001
        return UNKNOWN, f"pipeline.publish could not be imported ({e})"
    missing = [f for f in publish.FIELDS if f not in columns]
    if missing:
        return FAIL, ("the restored schema is missing column(s) the republish "
                      f"path sends: {', '.join(missing)}")
    return PASS, f"all {len(publish.FIELDS)} republished column(s) present"


def evaluate(*, integrity: str, tables: dict, signals_columns: list[str],
             size_bytes: int, prior: dict | None, now: datetime | None = None) -> dict:
    """Grade one reading. No git and no files: the inputs are all arguments,
    which is what lets the tests seed a shrunken or corrupt reading."""
    checks: list[dict] = []

    checks.append({
        "check": "integrity",
        "status": PASS if integrity == "ok" else FAIL,
        "detail": f"PRAGMA integrity_check: {integrity}",
    })

    absent = [t for t in CORE_TABLES if t not in tables]
    empty = [t for t in CORE_TABLES if tables.get(t) == 0]
    if absent:
        checks.append({"check": "core_tables", "status": FAIL,
                       "detail": f"core table(s) absent: {', '.join(absent)}"})
    elif empty:
        checks.append({"check": "core_tables", "status": FAIL,
                       "detail": f"core table(s) empty: {', '.join(empty)}"})
    else:
        checks.append({"check": "core_tables", "status": PASS,
                       "detail": f"{len(CORE_TABLES)} core table(s) present and populated"})

    if prior is None:
        checks.append({"check": "no_shrink", "status": UNKNOWN,
                       "detail": "no previous reading to compare against — "
                                 "this run is the baseline, not a pass"})
    else:
        before = prior.get("tables") or {}
        # Zero tolerance, and it is not strictness for its own sake: nothing in
        # this repository deletes a row. Corrections append a revision, a
        # retraction flips a flag. So a count that went down did not go down
        # because of anything anyone meant to do.
        shrunk = [(t, before[t], tables.get(t))
                  for t in sorted(before)
                  if t in tables and tables[t] < before[t]]
        vanished = [t for t in sorted(before) if t not in tables]
        if vanished:
            checks.append({"check": "no_shrink", "status": FAIL,
                           "detail": "table(s) present last run and gone now: "
                                     + ", ".join(vanished)})
        elif shrunk:
            checks.append({
                "check": "no_shrink", "status": FAIL,
                "detail": "table(s) SHRANK since " + str(prior.get("checked_at"))
                          + ": " + ", ".join(f"{t} {was} -> {now}"
                                             for t, was, now in shrunk),
            })
        else:
            checks.append({"check": "no_shrink", "status": PASS,
                           "detail": "no table is smaller than it was on "
                                     + str(prior.get("checked_at"))})

    status, detail = publishable(signals_columns)
    checks.append({"check": "republishable", "status": status, "detail": detail})

    headroom = GITHUB_FILE_LIMIT_BYTES - size_bytes
    growth = _growth_per_day(prior, size_bytes, now)
    runway = ""
    if growth and growth > 0:
        runway = f", about {int(headroom / growth)} day(s) of headroom at " \
                 f"{growth / 1024:.0f} KB/day"
    checks.append({
        "check": "push_size",
        "status": FAIL if size_bytes >= SIZE_ACTION_BYTES else PASS,
        "detail": f"{size_bytes / 1048576:.1f} MiB of GitHub's 100 MiB "
                  f"per-file push limit{runway}",
    })

    if any(c["status"] == FAIL for c in checks):
        verdict = FAIL
    elif any(c["status"] == UNKNOWN for c in checks):
        verdict = UNKNOWN
    else:
        verdict = PASS
    return {"verdict": verdict, "checks": checks}


# Two readings less than this far apart cannot measure a daily growth rate.
# The first dispatched run sat 30 minutes after the seeded one, divided one
# collect run's worth of new rows by half an hour, and reported "3099 KB/day,
# about 7 days of headroom" on a file with five weeks in it. A rate is a
# measurement over a window, and a window that short is noise wearing a unit.
MIN_GROWTH_WINDOW_DAYS = 3.0


def _growth_per_day(prior: dict | None, size_bytes: int,
                    now: datetime | None = None) -> float | None:
    if not prior or not prior.get("bytes") or not prior.get("checked_at"):
        return None
    try:
        then = datetime.fromisoformat(str(prior["checked_at"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    days = ((now or datetime.now(timezone.utc)) - then).total_seconds() / 86400
    if days < MIN_GROWTH_WINDOW_DAYS:
        return None
    return (size_bytes - int(prior["bytes"])) / days


# --- one run ----------------------------------------------------------------

def run(*, ref: str = "HEAD", ledger_path: str = LEDGER) -> dict:
    """Extract, read, grade. Returns the record this run would append.

    BOTH committed files, graded as one backup. A restore needs the pair: the
    product file holds `signals`, the cache file holds `seen_urls`,
    `source_links` and `employer_identity`, and a republish that gets one
    without the other is not a restore. So a missing or unreadable cache file
    is a failure of the backup and not a reason to grade the half that opened.
    """
    ledger = load_ledger(ledger_path)
    prior = previous(ledger)

    blobs = {}
    for name in DB_FILES:
        commit, blob, size = committed_blob(ref, name)
        blobs[name] = {"commit": commit, "blob": blob, "bytes": size}

    tables: dict[str, int] = {}
    integrities: dict[str, str] = {}
    signals_columns: list[str] = []

    with tempfile.TemporaryDirectory(prefix="tit-backup-check-") as tmp:
        for name, meta in blobs.items():
            path = os.path.join(tmp, os.path.basename(name))
            extract(meta["blob"], path)
            on_disk = os.path.getsize(path)
            if on_disk != meta["bytes"]:
                raise Unavailable(
                    f"the extracted {name} is {on_disk} bytes and git says the "
                    f"blob is {meta['bytes']}; the extraction, not the backup, "
                    f"is what failed")
            integrity, read = read_counts(path)
            integrities[name] = integrity
            # The two files hold disjoint tables, so this is a union and never
            # an overwrite. If that ever stops being true the split has been
            # half-undone, and a shadowed table is exactly what the no_shrink
            # check is there to catch.
            tables.update(read["tables"])
            if read["signals_columns"]:
                signals_columns = read["signals_columns"]

    # One verdict for the pair. "ok" only when BOTH said ok; otherwise the
    # failing file is named, because "not ok" without a filename sends a human
    # to the wrong 80 MiB file at 2am.
    bad = [f"{name}: {verdict}" for name, verdict in integrities.items()
           if verdict != "ok"]
    integrity = "ok" if not bad else "; ".join(bad)

    # The push limit is PER FILE, so the binding number is the larger half.
    biggest = max(DB_FILES, key=lambda n: blobs[n]["bytes"])
    size = blobs[biggest]["bytes"]

    graded = evaluate(integrity=integrity, tables=tables,
                      signals_columns=signals_columns,
                      size_bytes=size, prior=prior)

    return {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": blobs[DB_IN_REPO]["commit"],
        "blob": blobs[DB_IN_REPO]["blob"],
        "bytes": size,
        "largest_file": biggest,
        "files": {name: {"blob": meta["blob"], "bytes": meta["bytes"],
                         "integrity": integrities[name]}
                  for name, meta in blobs.items()},
        "integrity": integrity,
        "tables": tables,
        "verdict": graded["verdict"],
        "checks": graded["checks"],
    }


def summary_line(record: dict) -> str:
    rows = (record.get("tables") or {}).get("signals")
    return (f"{record['verdict']} — the committed database restores, "
            f"{rows} signal row(s), {record['bytes'] / 1048576:.1f} MiB, "
            f"checked {record.get('checked_at')}")


def load_latest(path: str = LEDGER) -> dict | None:
    """The newest recorded reading, for ops_status. None when there is none."""
    try:
        ledger = load_ledger(path)
    except Unavailable:
        return None
    checks = ledger.get("checks") or []
    return checks[-1] if checks else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true",
                        help="append this reading to data/backup_check.json")
    parser.add_argument("--json", action="store_true",
                        help="print the record as JSON and nothing else")
    parser.add_argument("--ref", default="HEAD",
                        help="which committed revision to verify (default HEAD)")
    parser.add_argument("--ledger", default=LEDGER)
    args = parser.parse_args(argv)

    try:
        record = run(ref=args.ref, ledger_path=args.ledger)
    except Unavailable as e:
        # UNKNOWN, and it says so in the same words everywhere else in this
        # repository does. It is NOT a pass and it is NOT a failure of the
        # backup.
        print(f"UNKNOWN — the backup could not be checked: {e}")
        print("         This is not a pass. Nothing here says the backup is "
              "broken; it says nothing looked.")
        return 3

    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        print("=" * 64)
        print("BACKUP CHECK — can this repository rebuild the database?")
        print("=" * 64)
        print(f"    commit {record['commit'][:12]}")
        for name, meta in sorted((record.get("files") or {}).items()):
            flag = "  <- largest" if name == record.get("largest_file") else ""
            print(f"      {name:<32} blob {meta['blob'][:12]}  "
                  f"{meta['bytes'] / 1048576:5.1f} MiB{flag}")
        for check in record["checks"]:
            print(f"    {check['status']:<7} {check['check']}: {check['detail']}")
        print("    rows: " + ", ".join(f"{t}={n}" for t, n in
                                       sorted(record["tables"].items())))

    if args.write:
        ledger = load_ledger(args.ledger)
        ledger["checks"].append(record)
        write_ledger(ledger, args.ledger)
        if not args.json:
            print(f"    recorded in {args.ledger}")

    if record["verdict"] == FAIL:
        if not args.json:
            print("\n    Read docs/RECOVERY.md. A shrinking or unreadable "
                  "committed database is the one failure that looks like "
                  "protection right up until it is needed.")
        return 2
    if record["verdict"] == UNKNOWN:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
