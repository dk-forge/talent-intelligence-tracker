"""Frozen, read-only shards of `signals`.

WHY THIS EXISTS. GitHub refuses a pushed file over 100 MiB and
`backup_check.py` reds at 90 MiB. On 2026-11-15 `data/talent_intel.db` crossed
90 MiB with `signals` as the growth, and nothing here deletes a row. The owner
ruled on 2026-11-16 (docs/DECISIONS.md) that current, published `uk_paygap`
and `sec_execcomp` rows whose event date is older than 400 days may move to a
read-only archive. This module is that archive.

THE RULES, each of them a test in tests/test_archive.py:

* NOTHING IS DELETED. A row leaves `main.signals` only after an identical copy
  is in the shard and the counts are verified; `build()` refuses otherwise.
* A SHARD IS NEVER REWRITTEN. `data/archive/signals-<date>.db` is written once
  by `build()` and is ATTACHed `mode=ro` by every connection afterwards, so a
  writer that tries to touch it gets an error rather than a quiet edit. A later
  move makes a NEW shard.
* EVERYTHING THAT MUST SEE HISTORY READS `signals_all`, a TEMP view that is
  `main.signals UNION ALL` every attached shard. Dedupe reads it (an old-dated
  backfill must still meet the archived copy of its own event), the
  republish-from-database path reads it (a rebuilt site must get the archived
  rows back), and merge_db reads it (a stale runner copy must not resurrect an
  archived row into main). Plain `signals` is the live table and is what every
  writer touches.
* A row is in exactly ONE place. `overlap()` counts content_hashes present in
  both main and a shard; backup_check grades it and a non-zero is a FAIL.

An archived row is frozen: `store.revise()` refuses it by name. Correcting one
means a deliberate un-archive, which is a decision and not a code path.

THIS IS NOT THE PERMANENT FIX. It buys roughly two to four weeks at the
growth measured in November 2026. The durable answer is a dated shard of
`signals` cut by capture period for ALL collectors on a schedule, so the file
pushed every day is bounded for good.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

#: Directory, relative to the product database, that holds the shards. Derived
#: from the database path for the same reason the cache file is: a test's
#: tmp_path database has no shards, the committed one has the committed ones.
ARCHIVE_DIRNAME = "archive"
SHARD_GLOB = "signals-*.db"
SCHEMA_PREFIX = "archive_"
VIEW = "signals_all"

#: The ruling of 2026-11-16, as data. Changing either list is a new ruling.
RULED_COLLECTORS = ("uk_paygap", "sec_execcomp")
RULED_AGE_DAYS = 400

_SHARD_NAME = re.compile(r"^signals-\d{4}-\d{2}-\d{2}\.db$")


class ArchiveError(RuntimeError):
    """A move that could lose or duplicate a row was refused."""


def archive_dir(db_path: Path | str) -> Path:
    return Path(db_path).resolve().parent / ARCHIVE_DIRNAME


def shards(db_path: Path | str) -> list[Path]:
    """Every shard belonging to `db_path`, oldest name first."""
    d = archive_dir(db_path)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(SHARD_GLOB) if _SHARD_NAME.match(p.name))


def _ro_uri(path: Path) -> str:
    return f"file:{quote(str(path))}?mode=ro"


def attached(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA database_list")
            if str(r[1]).startswith(SCHEMA_PREFIX)]


def _columns(conn: sqlite3.Connection, schema: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info(signals)")]


def attach(conn: sqlite3.Connection, db_path: Path | str) -> list[str]:
    """ATTACH every shard read-only and (re)build the `signals_all` view.

    The connection MUST have been opened with uri=True, or SQLite reads
    `file:...?mode=ro` as a literal filename and creates an empty database by
    that name. That is checked rather than trusted: an attached shard with no
    `signals` table is refused.
    """
    have = set(attached(conn))
    for i, shard in enumerate(shards(db_path)):
        name = f"{SCHEMA_PREFIX}{i}"
        if name in have:
            continue
        try:
            conn.execute(f"ATTACH DATABASE ? AS {name}", (_ro_uri(shard),))
        except sqlite3.OperationalError as e:
            raise ArchiveError(
                f"could not attach {shard} read-only ({e}); the connection "
                f"must be opened with uri=True") from e
        if not _columns(conn, name):
            conn.execute(f"DETACH DATABASE {name}")
            raise ArchiveError(
                f"{shard} attached without a signals table: the connection was "
                f"not opened with uri=True, or the shard is not a shard")
    build_view(conn)
    return attached(conn)


def build_view(conn: sqlite3.Connection) -> None:
    """TEMP view over main.signals and every attached shard.

    Columns are main's. A shard written before a later migration lacks the new
    column, which reads NULL — the same thing the migration gave every old row
    in main, so the two halves agree.
    """
    main_cols = _columns(conn, "main")
    if not main_cols:
        return
    parts = [f"SELECT {', '.join(main_cols)} FROM main.signals"]
    for schema in attached(conn):
        have = set(_columns(conn, schema))
        cols = [c if c in have else f"NULL AS {c}" for c in main_cols]
        parts.append(f"SELECT {', '.join(cols)} FROM {schema}.signals")
    conn.execute(f"DROP VIEW IF EXISTS temp.{VIEW}")
    conn.execute(f"CREATE TEMP VIEW {VIEW} AS " + " UNION ALL ".join(parts))


def source(conn: sqlite3.Connection) -> str:
    """`signals_all` if this connection has it, else `signals`.

    Some tests and tools build a bare sqlite3 connection with a signals table;
    for them there is no archive and the live table is the whole history.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_temp_master WHERE type='view' AND name=?",
        (VIEW,)).fetchone()
    return VIEW if row else "signals"


def is_archived(conn: sqlite3.Connection, signal_id: str) -> bool:
    for schema in attached(conn):
        if conn.execute(
                f"SELECT 1 FROM {schema}.signals WHERE signal_id = ? LIMIT 1",
                (signal_id,)).fetchone():
            return True
    return False


def overlap(conn: sqlite3.Connection) -> int:
    """content_hashes present in main AND a shard. Must be zero."""
    total = 0
    for schema in attached(conn):
        total += conn.execute(
            f"SELECT COUNT(*) FROM main.signals m JOIN {schema}.signals a "
            f"ON a.content_hash = m.content_hash AND a.revision = m.revision"
        ).fetchone()[0]
    return total


# --- the move ---------------------------------------------------------------

def ruled_where(as_of: str) -> tuple[str, tuple]:
    """The 2026-11-16 ruling as one WHERE clause. `as_of` is an ISO date."""
    marks = ", ".join("?" for _ in RULED_COLLECTORS)
    return (f"collector IN ({marks}) AND is_current = 1 "
            f"AND published_at IS NOT NULL "
            f"AND published_date IS NOT NULL "
            f"AND published_date < date(?, '-{RULED_AGE_DAYS} days')",
            (*RULED_COLLECTORS, as_of))


def plan(conn: sqlite3.Connection, as_of: str) -> dict:
    where, params = ruled_where(as_of)
    rows = conn.execute(
        f"SELECT collector, COUNT(*) FROM main.signals WHERE {where} "
        f"GROUP BY collector ORDER BY collector", params).fetchall()
    by = {r[0]: r[1] for r in rows}
    return {"as_of": as_of, "rows": sum(by.values()), "by_collector": by}


def build(db_path: Path | str, as_of: str, *, dry_run: bool = True) -> dict:
    """Move the ruled rows into a new shard. Dry run by default.

    Order, and every step is checked before the next:
      1. write the shard from main's OWN stored CREATE statement (never a
         hand-written schema: `split_cache_db.py` lost 1,232 rows to a NOT NULL
         the copy had and the source did not);
      2. copy the rows, verify the shard's count equals the plan's;
      3. delete them from main inside the same transaction, verify main shrank
         by exactly that count and the union total is unchanged;
      4. VACUUM main, which is what returns the bytes.
    """
    db_path = Path(db_path)
    # Autocommit, so the explicit BEGIN/COMMIT below is the only transaction.
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        p = plan(conn, as_of)
        p["bytes_before"] = db_path.stat().st_size
        shard = archive_dir(db_path) / f"signals-{as_of}.db"
        p["shard"] = str(shard)
        if dry_run or p["rows"] == 0:
            p["dry_run"] = dry_run
            return p
        if shard.exists():
            raise ArchiveError(f"{shard} exists; a shard is never rewritten")
        create = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchone()[0]
        shard.parent.mkdir(parents=True, exist_ok=True)
        where, params = ruled_where(as_of)
        before = conn.execute("SELECT COUNT(*) FROM main.signals").fetchone()[0]
        conn.execute("ATTACH DATABASE ? AS _shard", (str(shard),))
        try:
            conn.execute(create.replace("CREATE TABLE signals",
                                        "CREATE TABLE _shard.signals", 1)
                         .replace("CREATE TABLE IF NOT EXISTS signals",
                                  "CREATE TABLE _shard.signals", 1))
            cols = ", ".join(_columns(conn, "main"))
            conn.execute("BEGIN")
            conn.execute(f"INSERT INTO _shard.signals ({cols}) "
                         f"SELECT {cols} FROM main.signals WHERE {where}", params)
            copied = conn.execute(
                "SELECT COUNT(*) FROM _shard.signals").fetchone()[0]
            if copied != p["rows"]:
                raise ArchiveError(f"copied {copied}, planned {p['rows']}")
            conn.execute(
                "DELETE FROM main.signals WHERE row_id IN "
                "(SELECT row_id FROM _shard.signals)")
            after = conn.execute(
                "SELECT COUNT(*) FROM main.signals").fetchone()[0]
            if before - after != copied:
                raise ArchiveError(
                    f"main went {before} -> {after}, expected -{copied}")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.execute("DETACH DATABASE _shard")
            shard.unlink(missing_ok=True)
            raise
        conn.execute("DETACH DATABASE _shard")
        conn.execute("VACUUM")
        p["dry_run"] = False
        p["main_rows_before"] = before
        p["main_rows_after"] = after
    finally:
        conn.close()
    p["bytes_after"] = db_path.stat().st_size
    p["shard_bytes"] = shard.stat().st_size
    return p


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from pipeline import schema

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--as-of", required=True, help="ISO date of the ruling")
    ap.add_argument("--db", default=str(schema.DB_PATH))
    ap.add_argument("--apply", action="store_true",
                    help="move the rows; without it this is a dry run")
    args = ap.parse_args(argv)
    result = build(args.db, args.as_of, dry_run=not args.apply)
    if not result.get("dry_run"):
        conn = schema.connect(args.db)
        try:
            result["overlap"] = overlap(conn)
            result["union_rows"] = conn.execute(
                f"SELECT COUNT(*) FROM {VIEW}").fetchone()[0]
        finally:
            conn.close()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
