#!/usr/bin/env python3
"""Split the committed database into its product half and its cache half. Once.

    python3 split_cache_db.py --check     # what it would do, writes nothing
    python3 split_cache_db.py --apply     # do it, then VACUUM both files

WHY. GitHub refuses any single file over 100 MiB in a push. On 2026-08-20
`data/talent_intel.db` was 78.8 MiB and growing 676 KB/day measured over the
preceding fortnight, which is 32 days from a repository that stops accepting
commits — and `backup_check.py` reds at 90 MiB about a fortnight before that.

WHY NOT THE OBVIOUS ANSWERS. The limit is per FILE, not per repository, and
that word is what decides this:

  * Release assets (what the sibling layoff tracker does for its weekly export)
    would take the database out of git, and in THIS repository git is not
    storage. `git push` is the compare-and-swap that makes merge_db.py safe
    against two runners and a laptop; a release asset has no such thing, and
    two concurrent uploads silently clobber. That failure has already been paid
    for once here — 9,572 signal rows, 2026-07-28/29 — and it is the reason
    merge_db.py exists at all. It would also break `git show <sha>:data/...`,
    which is backup_check.py, restore_lost_rows.py and docs/RECOVERY.md.
  * Git LFS keeps the file addressable but moves the bytes to a quota. This
    repository checks the database out in more than twenty workflow runs a day;
    at ~80 MiB a checkout that is over a gigabyte a day against a 1 GB/month
    free allowance, after which checkouts fail rather than degrade.
  * VACUUM buys nothing: measured on 2026-08-20 the file had a freelist of
    ZERO pages. There is no slack in it to reclaim.
  * Pruning old rows buys nothing either, and for a reason worth writing down:
    the whole database is 24 days old. Nothing in it is old enough to prune,
    and nothing here deletes a signal anyway.

So the file is SPLIT and both halves stay committed. Everything that depends on
the database being in git keeps working, and each half is far enough from the
ceiling to stop being the thing that breaks next month.

WHAT MOVES, and the rule is "a cache or a ledger, never the product":

    seen_urls          15.2 MiB with its autoindex, 31% of daily growth
    source_links        3.7 MiB with its indexes,   14% of daily growth
    employer_identity   0.8 MiB,                     2% of daily growth

`signals` stays, with `source_health` and `publish_guardrails`.

MEASURED RESULT (2026-08-20, on the committed blob):

    before   talent_intel.db        78.8 MiB   676 KB/day   32 days of headroom
    after    talent_intel.db        56.7 MiB   358 KB/day  ~127 days
             talent_intel_cache.db  18.1 MiB   318 KB/day  ~285 days

This is NOT a permanent fix and must not be described as one. `signals` is
still growing and is still the product, so the file walks at the ceiling again
in about four months. What this buys is the time to do the durable thing, which
is to close the file and start a dated frozen shard. See docs/RECOVERY.md.

AFTER THIS RUNS the split is self-maintaining: schema.connect() ATTACHes the
cache file, creates it when it is absent, and moves any shadowing copy back out
of the product file (schema._move_legacy_cache_tables). Nothing needs to run
this script twice, and running it again on an already-split database is a
no-op that says so.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from pipeline import schema


def _tables_in_main(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table'")}


def inspect(db_path: Path) -> dict:
    """What is where, without changing anything."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        in_main = _tables_in_main(conn)
        pending = [t for t in schema.CACHE_TABLE_NAMES if t in in_main]
        rows = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in pending}
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        conn.close()
    return {"pending": pending, "rows": rows, "freelist": free,
            "bytes": db_path.stat().st_size}


def apply(db_path: Path) -> dict:
    """Move the cache tables out, then VACUUM both files.

    The move itself is schema._move_legacy_cache_tables, called by every
    connect(). It is deliberately NOT reimplemented here: one definition of
    what moves and how, so a repeat of this script and an ordinary collect run
    cannot disagree.

    The VACUUM is what this script adds, and it is the whole reason it exists
    as a separate step. DROP TABLE returns 15 MiB of pages to the freelist and
    leaves the file exactly as large on disk, so without a rewrite the split
    would move the tables and save nothing at all. A VACUUM inside connect()
    would rewrite an 80 MiB file on every open, which is why it is here and run
    once by a human instead.
    """
    before = db_path.stat().st_size
    conn = schema.connect(db_path)          # attaches, creates, and moves
    cache_path = schema.cache_path_for(db_path)
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
              for t in schema.CACHE_TABLE_NAMES}
    signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()

    # VACUUM cannot run inside a transaction and needs its own connection per
    # file: `VACUUM` only ever rewrites `main`.
    for path in (db_path, cache_path):
        vac = sqlite3.connect(path)
        try:
            vac.execute("VACUUM")
        finally:
            vac.close()

    # Prove both halves still open cleanly before anyone commits them. A split
    # that produced a corrupt file and reported its new size would be a worse
    # outcome than the ceiling it was avoiding.
    integrity = {}
    for path in (db_path, cache_path):
        chk = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity[path.name] = chk.execute(
                "PRAGMA integrity_check").fetchone()[0]
        finally:
            chk.close()

    return {"before": before,
            "after": db_path.stat().st_size,
            "cache": cache_path.stat().st_size,
            "counts": counts, "signals": signals, "integrity": integrity}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=schema.DB_PATH)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"no database at {args.db}")
        return 2

    state = inspect(args.db)
    print(f"{args.db}  {state['bytes'] / 1048576:.1f} MiB  "
          f"freelist {state['freelist']} page(s)")
    # Two things can be outstanding and they are independent. connect() moves
    # the tables on its own, so an ordinary test run or a session's ops_status
    # can leave the database SPLIT but not yet VACUUMed — and in that state the
    # dropped pages are still on the freelist and the file is still 78.8 MiB,
    # which is the whole problem, untouched. Reporting "already split" and
    # exiting 0 there would be the most expensive kind of true statement.
    for table in state["pending"]:
        print(f"  move  {table:<20} {state['rows'][table]:>8,} row(s)")
    if not state["pending"]:
        print("  tables: already split — connect() has moved them.")
    if state["freelist"]:
        print(f"  vacuum: {state['freelist']:,} free page(s) "
              f"({state['freelist'] * 4096 / 1048576:.1f} MiB) to reclaim")
    if not state["pending"] and not state["freelist"]:
        print("\nnothing to do: split and vacuumed.")
        return 0

    if args.check:
        print("\n--check: nothing written. Re-run with --apply.")
        return 0

    result = apply(args.db)
    bad = [f"{n}: {v}" for n, v in result["integrity"].items() if v != "ok"]
    print(f"\n  signals kept       {result['signals']:>10,}")
    for table, n in result["counts"].items():
        print(f"  {table:<18} {n:>10,}")
    print(f"\n  {args.db.name:<26} {result['before'] / 1048576:6.1f} MiB"
          f"  ->  {result['after'] / 1048576:6.1f} MiB")
    print(f"  {schema.cache_path_for(args.db).name:<26} "
          f"{'':>6}      ->  {result['cache'] / 1048576:6.1f} MiB")
    if bad:
        print("\nINTEGRITY FAILED: " + "; ".join(bad))
        print("Do NOT commit this. Restore with: git checkout -- data/")
        return 2
    print("\n  integrity_check ok on both files.")
    print("  Commit BOTH files in one commit, or a checkout gets half a "
          "database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
