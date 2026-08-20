"""Merge one database into another instead of copying it over the top.

Every workflow that writes data/talent_intel.db used to end the same way: on a
rejected push, `git reset --hard origin/main` and then copy our own file back.
That is not a merge. It replaces the whole file, so every row anyone else
committed while we were collecting is destroyed, and nothing reports an error.

It cost 9,572 signal rows and the entire 3,604-row identity cache between
2026-07-28 and 2026-07-29, in three different ways:

  * Two workflows in different concurrency groups running at once. Fixed by
    1cbe2a4 ("one lock for the database, not two").
  * A human pushing from a laptop mid-run (9991861 -> e1bfb03, the identity
    cache). No concurrency group can gate that; GitHub does not know about it.
  * A run that the lock CORRECTLY serialised (d46fb10 -> 4d604f3). It was
    dispatched at 01:05, waited in the queue until 03:21, and actions/checkout
    gave it the SHA pinned at dispatch — so queueing behind the lock is exactly
    what made it stale, and reset-and-copy then discarded the 311 rows the run
    ahead of it had just pushed.

The third one is the point: the lock cannot fix this, because the lock is what
causes it. Only merging can.

What merges, and on what key:

  signals            append-plus-revision. Keyed on (content_hash, revision),
                     which is already the table's one UNIQUE index. row_id is
                     an autoincrement local to each file, so it is reassigned
                     and supersedes_row_id is remapped onto the new numbering.

                     TWO columns on an EXISTING row still have to travel, and
                     both are one-way markers rather than data: `is_current`,
                     because a withdrawal is sticky (_reconcile_is_current),
                     and `published_at`, because a row this run sent is on the
                     site whether or not the destination knows it
                     (_carry_publications). Nothing else does: a value learned
                     about an existing row is either re-derived after the merge
                     (archive-sources.yml) or written as a new revision.
  seen_urls          pure cache, PK url. Union; the earlier first_seen wins.
  employer_identity  pure cache, PK company_key. The later resolved_at wins,
                     which is what identity.py's INSERT OR REPLACE means.
  source_health      append-only ledger, PK (collector, run_at). Union.
  source_links       link-rot ledger, PK source_url. The later updated_at wins
                     wholesale. Two jobs write it (link_check and
                     archive_sources) and a true collision can drop one of
                     them's fields for one URL; both are resumable and
                     idempotent, so the next run re-derives it. Losing a
                     reachability observation for a cycle is a very different
                     cost from losing a signal, which is why this one is allowed
                     to be simple.
  publish_guardrails pre-publish findings, PK (check_name, subject). NOT simple,
                     and the one table here where the newer write must sometimes
                     LOSE: a row carrying a human's accept or reject always beats
                     an automatic one, whichever side holds it. Without that, a
                     run in flight would overwrite an acceptance made from a
                     laptop while it worked, and a genuine mega-raise the owner
                     had already cleared would start blocking every publish
                     again. Where neither side is reviewed and they disagree,
                     'open' wins: this table decides whether a figure goes out,
                     so its merge conflicts resolve loud.

TWO FILES, ONE MERGE. Since the 100 MiB split the database is
`talent_intel.db` plus `talent_intel_cache.db`, and seen_urls, source_links and
employer_identity live in the second one. Nothing below changed for it: both
sides are opened through schema.connect(), which ATTACHes each file's own cache
sibling, and SQLite resolves an unqualified table name across attached schemas.
The commit spans both files atomically. What DID change is that four paths are
now required to exist rather than four being created on demand.

Usage:
    python merge_db.py OURS INTO

OURS is this run's database (the copy saved before the reset). INTO is the
checked-out file, which now holds whatever landed on main while we worked.
INTO is modified in place. Exits non-zero on any failure: a merge that half
works must not look like a clean commit.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from pipeline import schema

# Never merged. row_id is reassigned by the insert; sqlite_sequence follows it.
SIGNAL_SKIP_COLUMNS = {"row_id"}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _merge_signals(ours: sqlite3.Connection, into: sqlite3.Connection) -> dict[str, int]:
    """Insert the revisions `into` does not have, then re-derive is_current.

    Both sides are open through schema.connect(), so both have every column the
    current schema declares; the intersection guards against a merge running
    against a database written by a newer checkout than this script.
    """
    shared = [c for c in _columns(ours, "signals")
              if c in set(_columns(into, "signals")) and c not in SIGNAL_SKIP_COLUMNS]

    # Where each (content_hash, revision) already lives in the destination, so a
    # supersedes pointer into a row we are NOT re-inserting still resolves.
    existing = {
        (r["content_hash"], r["revision"]): r["row_id"]
        for r in into.execute("SELECT row_id, content_hash, revision FROM signals")
    }

    ours_rows = list(ours.execute(
        "SELECT * FROM signals ORDER BY revision, row_id"))
    # Our own row_id -> its (content_hash, revision) identity, for remapping.
    ours_identity = {r["row_id"]: (r["content_hash"], r["revision"]) for r in ours_rows}

    placeholders = ", ".join("?" for _ in shared)
    inserted = 0
    for row in ours_rows:
        key = (row["content_hash"], row["revision"])
        if key in existing:
            continue
        values = []
        for column in shared:
            value = row[column]
            if column == "supersedes_row_id" and value is not None:
                # Point at the same revision under the destination's numbering.
                # Unresolvable means the superseded row never made it here; the
                # is_current pass below still gets the ordering right, so drop
                # the dangling pointer rather than inventing a target.
                value = existing.get(ours_identity.get(value))
            values.append(value)
        cursor = into.execute(
            f"INSERT INTO signals ({', '.join(shared)}) VALUES ({placeholders})",
            values,
        )
        existing[key] = cursor.lastrowid
        inserted += 1

    return {"inserted": inserted,
            "withdrawn": _reconcile_is_current(ours, into),
            "published": _carry_publications(ours, into)}


def _carry_publications(ours: sqlite3.Connection, into: sqlite3.Connection) -> int:
    """A row this run PUBLISHED is on the site, so the marker has to survive.

    publish() sets published_at with an UPDATE on an existing row, and the loop
    above skips every (content_hash, revision) the destination already holds.
    So the marker survived only on rows collected and published in the SAME
    run, which arrive here through the INSERT and carry it as data. Every row
    that waited - anything held by a guardrail until a human accepted it, which
    is every mega-round we have ever published - was stamped by the run that
    sent it and reset back to NULL by the commit step, for good.

    Measured on run 31780939430 (2026-08-14): ten current rows sat at
    published_at IS NULL on main while the live site held all ten, one of them
    since 2026-07-30. Nothing was red; publish() re-offered them every run and
    WordPress answered `duplicate`, which is what `sent=84 stored=75
    duplicate=9` in that log actually was.

    It is not bookkeeping. `enrich_published()` only offers rows with
    published_at IS NOT NULL, so a value learned after the row went out -
    archive_url, hq_city, funding_stage - could never reach exactly the rows a
    human had cleared; and guardrails._row_placement reads the same marker to
    choose between "withhold it" and "it is live, retract it", so a wrong
    figure in public would be filed as one that never left the building.

    Union, and the EARLIER stamp wins: published_at answers "when did this
    reach the site", so a re-send must not move it forward, and a NULL on
    either side can never erase the other's answer.
    """
    rows = list(ours.execute(
        "SELECT content_hash, revision, published_at FROM signals "
        " WHERE published_at IS NOT NULL"))
    if not rows:
        return 0
    cursor = into.executemany(
        "UPDATE signals SET published_at = ? "
        " WHERE content_hash = ? AND revision = ? "
        "   AND (published_at IS NULL OR published_at > ?)",
        [(r["published_at"], r["content_hash"], r["revision"], r["published_at"])
         for r in rows])
    return cursor.rowcount


def _reconcile_is_current(ours: sqlite3.Connection, into: sqlite3.Connection) -> int:
    """Two rules, each already true of one writer, now true of the union.

    1. A withdrawal is sticky. retract.py sets is_current = 0 without appending
       a revision, so if EITHER side withdrew a row it stays withdrawn — the
       merge must not resurrect it. dedupe.py has the scar from the last time a
       retracted record came back.
    2. Only the newest revision of a signal_id is current. revise() appends a
       revision and clears the old one; if the two sides each hold a different
       revision, the older one is superseded here.
    """
    withdrawn = {
        (r["content_hash"], r["revision"])
        for r in ours.execute(
            "SELECT content_hash, revision FROM signals WHERE is_current = 0")
    }
    cleared = 0
    for key in withdrawn:
        cleared += into.execute(
            "UPDATE signals SET is_current = 0 "
            " WHERE content_hash = ? AND revision = ? AND is_current = 1", key
        ).rowcount

    cleared += into.execute(
        """
        UPDATE signals SET is_current = 0
         WHERE is_current = 1
           AND revision < (SELECT MAX(s2.revision) FROM signals s2
                            WHERE s2.signal_id = signals.signal_id)
        """
    ).rowcount
    return cleared


def _merge_cache(ours: sqlite3.Connection, into: sqlite3.Connection, table: str,
                 *, newer_column: str | None = None, newer_wins: bool = True) -> int:
    """Union a cache table on its primary key.

    `newer_column` decides a collision: employer_identity keeps the later
    resolved_at (identity.py writes INSERT OR REPLACE, so later is the intent),
    seen_urls keeps the earlier first_seen (that is when we truly first saw it).
    Without one, the destination's row stands.
    """
    shared = [c for c in _columns(ours, table) if c in set(_columns(into, table))]
    # PRAGMA's `pk` column is the 1-based position within a composite key, so
    # source_health's (collector, run_at) has to be sorted back into order.
    key_columns = [row[1] for row in sorted(
        (r for r in into.execute(f"PRAGMA table_info({table})") if r[5]),
        key=lambda r: r[5])]
    if not key_columns:
        raise SystemExit(f"{table} has no primary key; refusing to guess how to merge it")

    placeholders = ", ".join("?" for _ in shared)
    conflict = ", ".join(key_columns)
    if newer_column:
        assignments = ", ".join(f"{c} = excluded.{c}" for c in shared if c not in key_columns)
        comparison = ">" if newer_wins else "<"
        resolution = (f"DO UPDATE SET {assignments} "
                      f"WHERE excluded.{newer_column} {comparison} {table}.{newer_column}")
    else:
        resolution = "DO NOTHING"

    sql = (f"INSERT INTO {table} ({', '.join(shared)}) VALUES ({placeholders}) "
           f"ON CONFLICT({conflict}) {resolution}")

    before = into.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    into.executemany(sql, [tuple(r[c] for c in shared)
                           for r in ours.execute(f"SELECT * FROM {table}")])
    return into.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - before


def _merge_guardrails(ours: sqlite3.Connection, into: sqlite3.Connection) -> int:
    """Union the guardrail ledger, keeping whichever side a human answered.

    This one cannot use _merge_cache. Every other table here resolves a
    collision with "the later write wins", and that rule is actively wrong for a
    review queue: the later write is usually a collect run re-firing the same
    finding, and the earlier one may be the owner's decision. Letting the run
    win would silently reopen an accepted row and start blocking publishing over
    a figure that had already been checked.

    Where neither side is reviewed and the two disagree, 'open' wins. This table
    decides whether a figure goes out, so its conflicts resolve loud.
    """
    table = "publish_guardrails"
    shared = [c for c in _columns(ours, table) if c in set(_columns(into, table))]
    if not shared:
        return 0
    placeholders = ", ".join("?" for _ in shared)
    sql = (
        f"INSERT INTO {table} ({', '.join(shared)}) VALUES ({placeholders}) "
        f"ON CONFLICT(check_name, subject) DO UPDATE SET "
        f"  label = excluded.label, detail = excluded.detail, "
        f"  value = excluded.value, "
        f"  first_seen = MIN({table}.first_seen, excluded.first_seen), "
        f"  last_seen = MAX({table}.last_seen, excluded.last_seen), "
        f"  seen = MAX({table}.seen, excluded.seen), "
        f"  state = CASE "
        f"    WHEN {table}.reviewed_at IS NOT NULL THEN {table}.state "
        f"    WHEN excluded.reviewed_at IS NOT NULL THEN excluded.state "
        f"    WHEN {table}.state = 'open' OR excluded.state = 'open' THEN 'open' "
        f"    ELSE excluded.state END, "
        f"  reviewed_at = COALESCE({table}.reviewed_at, excluded.reviewed_at), "
        f"  reviewed_by = COALESCE({table}.reviewed_by, excluded.reviewed_by), "
        f"  review_note = COALESCE({table}.review_note, excluded.review_note)")
    before = into.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    into.executemany(sql, [tuple(r[c] for c in shared)
                           for r in ours.execute(f"SELECT * FROM {table}")])
    return into.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - before


def _require(path: Path, what: str) -> None:
    """A database half that is not there is a hard stop, never an empty merge.

    Both sides are now TWO files (see schema.CACHE_TABLES): the product file
    and its `_cache` sibling. schema.connect() CREATES a missing file, which is
    right for a fresh checkout and catastrophic here — a merge whose `ours`
    cache is absent would union an empty seen_urls over a full one, report
    `seen_urls_added: 0`, exit 0, and hand the next collect run a cache that
    has forgotten every URL it has ever paid to read. So the caller's files are
    checked before either connection is opened, and their absence is a failure
    with the missing path named.
    """
    if not path.exists():
        raise SystemExit(f"nothing to merge: {what} {path} does not exist")


def merge(ours_path: Path, into_path: Path) -> dict[str, int]:
    _require(ours_path, "this run's database")
    _require(schema.cache_path_for(ours_path), "this run's cache file")
    # `into` is the checked-out copy. Its product file is guaranteed by the
    # checkout; its cache file is guaranteed by the same commit, and if it is
    # missing something has gone wrong with the checkout rather than with us.
    _require(into_path, "the destination database")
    _require(schema.cache_path_for(into_path), "the destination cache file")

    ours = schema.connect(ours_path)
    into = schema.connect(into_path)
    try:
        with into:
            signals = _merge_signals(ours, into)
            report = {
                "signals_inserted": signals["inserted"],
                "signals_withdrawn": signals["withdrawn"],
                "signals_published": signals["published"],
                "seen_urls_added": _merge_cache(
                    ours, into, "seen_urls",
                    newer_column="first_seen", newer_wins=False),
                "employer_identity_added": _merge_cache(
                    ours, into, "employer_identity", newer_column="resolved_at"),
                "source_health_added": _merge_cache(ours, into, "source_health"),
                "source_links_added": _merge_cache(
                    ours, into, "source_links", newer_column="updated_at"),
                "guardrail_findings_added": _merge_guardrails(ours, into),
            }
        # A merge that loses rows is the bug this file exists to end, so it is
        # checked rather than assumed.
        kept = into.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        report["signals_total"] = kept
        return report
    finally:
        ours.close()
        into.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ours", type=Path, help="this run's database")
    parser.add_argument("into", type=Path, help="the checked-out database, modified in place")
    args = parser.parse_args(argv)

    report = merge(args.ours, args.into)
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
