"""Put back the rows the reset-and-copy commits destroyed.

CLAUDE.md says the repository IS this system's memory. Between 2026-07-28 and
2026-07-29 it stopped being that. Seven workflows ended a rejected push with
`git reset --hard origin/main` followed by a copy of their own database over the
top, and five commits each silently dropped whatever the previous one had added:
9,572 signal rows and the whole 3,604-row employer_identity cache, without one
red run. merge_db.py is why it cannot happen again; this is the repair.

Nothing here is reconstructed. Every destroyed row was COMMITTED before the next
job overwrote it, so the rows are still in this repository — sitting in blobs
that no branch tip points at any more. `git show <sha>:data/talent_intel.db`
hands each one back whole, with every column, the true `collector`, the real
revision numbers and the identity cache. So the repair is to walk the file's own
history and merge each version forward with merge_db.py, which is the same
merge, and the same tests, the workflows now use.

That matters more than it sounds. The obvious alternative — rebuilding the rows
from the public /query endpoint — publishes only 35 of the table's 48 columns
and no `collector` at all, and `collector` is not decoration: correct_form_d and
correct_sec_pillar sweep by it. Inferring it from the source URL looked fine and
was not. It would have labelled 3,910 `sec_execcomp` rows `sec_edgar`, because
both live on sec.gov, and handed every one of them to a corrector that rewrites
pillars for a collector they do not belong to.

The live site is still used, for the one thing it is authoritative about: which
rows OUGHT to exist. It kept everything, because publishing happened before the
commit was lost. So it is the check on the result rather than the source of it,
and it names any row history cannot supply.

Usage:
    python restore_lost_rows.py --dry-run
    python restore_lost_rows.py
    python restore_lost_rows.py --skip-live      # no network; no cross-check
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import merge_db
from pipeline import schema

DB_IN_REPO = "data/talent_intel.db"
# The second committed file, since the 2026-08-20 split. Revisions OLDER than
# the split do not have it — their seen_urls, source_links and
# employer_identity are inside the product file — and that is handled rather
# than special-cased: schema.connect() moves any such table into the cache file
# as it opens the version, so both eras arrive at the merge in the same shape.
CACHE_IN_REPO = "data/talent_intel_cache.db"
QUERY_URL = "https://asktherecruiter.com/blog/wp-json/talent/v1/query"
# ModSecurity on the WP host rejects python-requests' default agent.
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
PER_PAGE = 200


def _git(*args: str) -> str:
    return subprocess.run(("git", *args), capture_output=True, check=True,
                          text=True).stdout


def historical_versions() -> list[str]:
    """Every commit that touched the database, oldest first.

    Oldest first so later versions win any collision, which is what the caches
    mean: employer_identity keeps the later resolved_at, and a revision appended
    after a row was written must not be undone by the version before it.
    """
    shas = _git("log", "--format=%H", "--", DB_IN_REPO, CACHE_IN_REPO).split()
    return list(reversed(shas))


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "signals": conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
        "current": conn.execute(
            "SELECT COUNT(*) FROM signals WHERE is_current = 1").fetchone()[0],
        "employer_identity": conn.execute(
            "SELECT COUNT(*) FROM employer_identity").fetchone()[0],
        "seen_urls": conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0],
    }


def replay_history(db_path: Path, shas: list[str], *, dry_run: bool) -> dict:
    """Merge every past version of the database forward into `db_path`.

    On a dry run this works on a scratch copy, so the report is a real result
    rather than a guess about one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        target = db_path
        if dry_run:
            target = tmp / "dry-run.db"
            target.write_bytes(db_path.read_bytes())

        conn = schema.connect(target)
        before = _counts(conn)
        conn.close()

        recovered = 0
        for index, sha in enumerate(shas, 1):
            version = tmp / f"{sha}.db"
            version_cache = schema.cache_path_for(version)
            with version.open("wb") as handle:
                subprocess.run(("git", "show", f"{sha}:{DB_IN_REPO}"),
                               stdout=handle, check=True)
            # Post-split revisions carry their own cache file. Pre-split ones
            # do not, and must NOT get an empty one written here: opening the
            # version through connect() below moves the cache tables out of the
            # product file, which is where they still are at that revision.
            with version_cache.open("wb") as handle:
                if subprocess.run(("git", "show", f"{sha}:{CACHE_IN_REPO}"),
                                  stdout=handle,
                                  stderr=subprocess.DEVNULL).returncode != 0:
                    handle.close()
                    version_cache.unlink()
            # Normalise the version before merging it: creates the cache file
            # when the revision predates the split, and moves any shadowing
            # copy out of main so the merge reads the real table and not an
            # empty one that happens to have the same name.
            schema.connect(version).close()
            report = merge_db.merge(version, target)
            version.unlink()
            version_cache.unlink(missing_ok=True)
            if report["signals_inserted"] or report["employer_identity_added"]:
                print(f"  [{index}/{len(shas)}] {sha[:9]}: "
                      f"+{report['signals_inserted']} signals, "
                      f"+{report['employer_identity_added']} identity, "
                      f"+{report['seen_urls_added']} seen_urls")
            recovered += report["signals_inserted"]

        conn = schema.connect(target)
        after = _counts(conn)
        # Collected before the scratch copy is discarded, so a dry run can be
        # cross-checked against the live site exactly like a real one.
        held = {row[0] for row in conn.execute("SELECT signal_id FROM signals")}
        conn.close()

    return {"signals_recovered": recovered, "before": before, "after": after,
            "held": held}


def fetch_live_ids() -> set[str]:
    """The signal_ids the live site is currently showing.

    Offset pagination here has no unique tiebreaker in its ORDER BY, so one walk
    can skip a row when two share a sort key. Re-walking under a different sort
    moves the ties; the union converges.
    """
    def get(**params):
        url = f"{QUERY_URL}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.loads(response.read())
            except Exception as error:  # noqa: BLE001 - retried, then raised
                if attempt == 3:
                    raise
                print(f"  retry {attempt + 1}: {error}")
                time.sleep(3 * (attempt + 1))

    total = get(per_page=1)["total"]
    ids: set[str] = set()
    for sort in ("notable", "newest", "oldest", "employer", "place", "evidence"):
        page = 1
        while (page - 1) * PER_PAGE < total:
            batch = get(per_page=PER_PAGE, page=page, sort=sort).get("rows") or []
            if not batch:
                break
            ids.update(row["signal_id"] for row in batch)
            page += 1
        print(f"  live sort={sort}: {len(ids)} of {total}")
        if len(ids) >= total:
            break
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="replay onto a scratch copy and report; write nothing")
    parser.add_argument("--skip-live", action="store_true",
                        help="skip the cross-check against the published site")
    parser.add_argument("--db", type=Path, default=Path(DB_IN_REPO))
    args = parser.parse_args(argv)

    shas = historical_versions()
    if not shas:
        print("::error::no history for the database; nothing to replay")
        return 1
    print(f"replaying {len(shas)} committed versions of {DB_IN_REPO}")

    result = replay_history(args.db, shas, dry_run=args.dry_run)
    before, after = result["before"], result["after"]
    for key in before:
        print(f"{key}: {before[key]} -> {after[key]} (+{after[key] - before[key]})")

    if not args.skip_live:
        print("cross-checking against the published site")
        live = fetch_live_ids()
        gap = live - result["held"]
        print(f"live rows: {len(live)}")
        print(f"published but still not held after the replay: {len(gap)}")
        if gap:
            # Named, not swallowed. A row the site shows and history cannot
            # supply was published by a run whose database never reached a
            # commit at all, and only the endpoint has it.
            print("  " + ", ".join(sorted(gap)[:10])
                  + (" ..." if len(gap) > 10 else ""))

    if args.dry_run:
        print("(dry run - nothing written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
