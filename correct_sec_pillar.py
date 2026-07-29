#!/usr/bin/env python3
"""Move the already-published Item 5.02 filings into the leadership pillar.

    python3 correct_sec_pillar.py --dry-run      # counts only, writes nothing
    python3 correct_sec_pillar.py                # apply

An 8-K Item 5.02 is "Departure of Directors or Certain Officers", and
collectors/sec_edgar.py only fetches filings carrying it — it writes that
headline itself. The pillar was still put to the model, and it filed 573 of
them elsewhere, 568 under rewards_comp, because a 5.02(e) filing spends most of
its words on the incoming officer's pay package. Every one of those records is
true and published, and none of them is reachable by anyone browsing leadership
changes: 18% of that pillar's primary source, held and unfindable.

validate.forced_pillar now settles it at ingestion, but that governs only rows
collected AFTER it. This governs the rows already on the site, and it decides
which rows those are by CALLING forced_pillar rather than re-implementing it,
so the two can never drift.

**Why a revision and not a correction in place.** pillar is an input to
content_hash — md5(company_key|pillar|published_date|normalised_headline), see
pipeline/validate.content_hash(). Rewriting it moves the row's fingerprint, so
/correct is both the wrong door (it writes signal_direction and
talent_readthrough, nothing else) and the wrong shape: an in-place pillar
change would leave a row whose stored hash no longer matches its own contents,
and the next collection of that filing would hash to the new value, find no
match, and publish it a second time. So each row is re-issued the way the repo
already re-issues a record:

    store.revise()   locally: the old row survives at is_current = 0 and a new
                     revision is appended with the same signal_id, the new
                     content_hash and published_at NULL
    /retract         on the site: the published row goes to is_current = 0
    publish()        sends every published_at IS NULL row, so the new revision
                     lands as an ordinary insert

Nothing here refetches a filing or calls a model. The corrected row carries the
model's own words for everything except the pillar, which the document decided,
and materiality, which is recomputed from the stored columns exactly as
build_signal computes it — a corrected row and a freshly collected one say the
same thing.

A handful of rows cannot be re-issued at all, because the corrected pillar
moves their fingerprint onto one another live row already holds. Those are the
same record by the definition content_hash exists to state, so they are
WITHDRAWN rather than republished. See split_duplicates.

Idempotent, and safe to interrupt: each phase derives its own worklist from
what is stored, so a row already revised is not revised again, a retraction
already sent is not sent again (the withdrawn revision is marked), and
publishing was resumable to begin with.
"""

from __future__ import annotations

import argparse
import dataclasses
import sqlite3
import sys
from collections import Counter
from datetime import date

import requests

import retract
from pipeline import publish, schema, store, validate

COLLECTOR = "sec_edgar"
PILLAR = "leadership_change"

# Written onto the new revision, and the marker every later phase resumes from.
NOTE = ("pillar corrected: an 8-K Item 5.02 is an officer or director change by "
        "the filing's own definition, and was classified as another pillar")

REASON = ("republished under the leadership pillar: an 8-K Item 5.02 is an "
          "officer or director change by the filing's own definition, and this "
          "record was filed under another pillar")

DUPLICATE_REASON = ("the same employer, date and event as a record already "
                    "published under the leadership pillar: two 8-K filings on "
                    "one day are one entry here, and this is the second")

# A pass that re-issues most of the source is not a correction pass, it is
# forced_pillar matching something it should not. The measured share is 16%.
# Below MIN_ROWS a share says nothing — three rows out of four is 75% and no
# evidence of anything — so the guard waits until there is a population to
# judge rather than blocking a small table or a test fixture.
MAX_SHARE = 0.35
MIN_ROWS = 50


class Unsafe(RuntimeError):
    """The worklist is so large that the likeliest explanation is a broken
    rule, not a broken table."""


def current_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE collector = ? AND is_current = 1 ORDER BY row_id",
        (COLLECTOR,))]


def targets(rows: list[dict], *, force: bool = False) -> list[dict]:
    """Live rows whose stored pillar disagrees with the document."""
    out = [r for r in rows
           if validate.forced_pillar(COLLECTOR, r["headline"]) not in (None, r["pillar"])]
    share = len(out) / len(rows) if rows else 0
    if len(rows) >= MIN_ROWS and share > MAX_SHARE and not force:
        raise Unsafe(
            f"{len(out)} of {len(rows)} live {COLLECTOR} rows ({share:.0%}) would be "
            f"re-issued. Expected ~16%. Check validate.forced_pillar before "
            f"re-running, and pass --force only if the number is genuinely right.")
    return out


_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def corrected_signal(row: dict) -> validate.Signal:
    """The stored row as build_signal would have built it with the pillar fixed.

    Everything the model said is carried across untouched. Two values are
    recomputed rather than copied, because both are DERIVED from the pillar and
    a copy would leave the row disagreeing with itself:

      content_hash  the pillar is one of its four inputs
      materiality   a bare officer change with no city is 'routine', which is
                    what its 2,480 correctly-filed peers already carry
    """
    signal = validate.Signal(**{name: row[name] for name in _FIELDS})
    signal.pillar = validate.forced_pillar(COLLECTOR, signal.headline) or signal.pillar
    signal.content_hash = validate.content_hash(
        signal.company_key, signal.pillar, signal.published_date,
        signal.headline, signal.source_name)
    signal.materiality = validate.compute_materiality(
        headcount=signal.headcount,
        funding_usd=signal.funding_amount_usd,
        ticker=signal.ticker,
        cik=signal.cik,
        pillar=signal.pillar,
        headline=signal.headline,
        city=signal.city,
    )
    return signal


def split_duplicates(conn, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the worklist into rows to re-issue and rows the correction makes
    duplicates of a record that is already live.

    Correcting the pillar moves the fingerprint, and it can move it onto one
    another row already holds — measured once here, Cadence Design Systems,
    two separate 8-K accessions filed the same day, both carrying the
    collector's identical boilerplate headline. Under the corrected pillar
    those two are one record by the definition content_hash exists to state,
    and store.store() would have called the second a duplicate at collection
    time had the pillar been right then.

    So they are withdrawn rather than re-issued. Publishing them is not an
    option even in principle: the site's unique key is (content_hash, revision)
    and every insert it makes is revision 1, so the second row would not error,
    it would silently never land.

    The site's SECOND guard is mirrored here for the same reason. Beyond the
    hash, tit_insert_signal() refuses a row whose employer, pillar, direction
    and published date (within 14 days) match a live one, and /bulk reports
    that as an ordinary 'duplicate'. publish() counts duplicates, it cannot
    name them, and it marks every row the server accepted as published — so a
    row refused there would be withdrawn on the site, replaced by nothing, and
    recorded here as published. Two rows in the live table do this. Applying
    the rule here withdraws them honestly, with a reason, instead.
    """
    live = [dict(r) for r in conn.execute(
        "SELECT signal_id, content_hash, company_key, pillar, signal_direction, "
        "       published_date FROM signals WHERE is_current = 1")]
    moving = {r["signal_id"] for r in rows}
    live = [r for r in live if r["signal_id"] not in moving]
    held = {r["content_hash"]: r["signal_id"] for r in live}
    near: dict[tuple, list[date]] = {}
    for row in live:
        _remember_near(near, row)

    reissue, duplicate = [], []
    for row in rows:
        fixed = corrected_signal(row)
        if held.get(fixed.content_hash) or _is_near(near, dataclasses.asdict(fixed)):
            duplicate.append(row)
            continue
        held[fixed.content_hash] = row["signal_id"]
        _remember_near(near, dataclasses.asdict(fixed))
        reissue.append(row)
    return reissue, duplicate


# The site's window, from tit_insert_signal(). Matching it is the whole point.
NEAR_DAYS = 14


def _near_key(row: dict):
    """The employer, pillar and direction the site compares on, or None where
    it would not run the check at all."""
    if not all(row.get(k) for k in
               ("company_key", "pillar", "signal_direction", "published_date")):
        return None
    return (row["company_key"], row["pillar"], row["signal_direction"])


def _remember_near(near: dict, row: dict) -> None:
    key = _near_key(row)
    if key:
        near.setdefault(key, []).append(date.fromisoformat(row["published_date"]))


def _is_near(near: dict, row: dict) -> bool:
    key = _near_key(row)
    if not key:
        return False
    day = date.fromisoformat(row["published_date"])
    return any(abs((other - day).days) <= NEAR_DAYS for other in near.get(key, ()))


def reissue(conn, row: dict, *, withdraw) -> None:
    """Take one row off the site, then append its corrected revision.

    That order, and one row at a time, is what keeps this interruptible. The
    withdrawal comes first because a replacement published while its
    predecessor is still live puts the same filing on the page twice under two
    pillars. The local revision comes second because it is the ONLY record that
    the withdrawal happened: a row is a target while its live revision has the
    wrong pillar, so a run killed between the two steps simply retries both,
    and /retract on an already-withdrawn record reports zero rows rather than
    failing. Once the revision exists the row is no longer a target and cannot
    be withdrawn a second time, which is the one thing that would matter — both
    revisions share a signal_id, and /retract works on signal_id.

    Committed per row, so a run killed at any point leaves whole rows behind
    and publish() finds exactly the ones that got this far.
    """
    if row["published_at"]:
        withdraw(row["signal_id"], REASON)
    store.revise(conn, row["signal_id"], corrected_signal(row), NOTE)
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    parser.add_argument("--limit", type=int, help="stop after N rows (for a first pass)")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share of rows would move")
    args = parser.parse_args()

    conn = schema.connect()
    rows = current_rows(conn)
    print(f"{len(rows)} live {COLLECTOR} rows")

    try:
        to_move = targets(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    if args.limit:
        to_move = to_move[:args.limit]
    to_move, dupes = split_duplicates(conn, to_move)

    by_pillar = Counter(r["pillar"] for r in to_move)
    materiality = Counter(corrected_signal(r).materiality for r in to_move)

    print(f"\n  to re-issue as {PILLAR}   {len(to_move):>5}")
    for pillar, n in by_pillar.most_common():
        print(f"    from {pillar:<22}{n:>5}")
    print(f"  to withdraw as duplicates{len(dupes):>5}   "
          f"(the corrected pillar makes them a record already live)")
    print(f"  already correct          {len(rows) - len(to_move) - len(dupes):>5}")
    print(f"  published, so needing a retraction first  "
          f"{sum(1 for r in to_move if r['published_at']):>5}")
    print("  materiality after: " + ", ".join(
        f"{k} {v}" for k, v in materiality.most_common()))

    for row in dupes:
        print(f"\n  [withdraw] {row['company']}\n             {row['source_url']}")
    for row in to_move[:5]:
        print(f"\n  [{row['pillar']} -> {PILLAR}] {row['company']}"
              f"\n            {row['headline'][:96]}")

    if args.dry_run:
        waiting = len(publish.unpublished(conn))
        if waiting:
            print(f"\n  {waiting} rows are already revised and unpublished, from an "
                  f"interrupted run; a real run publishes them.")
        print("\ndry run: nothing written")
        return 0

    failures = 0
    if dupes:
        print(f"\nwithdrawing {len(dupes)} duplicates ...")
        for row in dupes:
            try:
                retract.retract_remote(row["signal_id"], DUPLICATE_REASON)
                retract.retract_local(conn, row["signal_id"], DUPLICATE_REASON)
            except (publish.PublishError, requests.RequestException) as exc:
                failures += 1
                print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)

    if to_move:
        print(f"\nwithdrawing and re-issuing {len(to_move)} rows ...")
        for n, row in enumerate(to_move, 1):
            try:
                reissue(conn, row, withdraw=retract.retract_remote)
            except (publish.PublishError, requests.RequestException) as exc:
                # One row's failure is one row. It keeps its old pillar, stays
                # live, and the next run picks it up again.
                failures += 1
                print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)
            if n % 50 == 0:
                print(f"  {n}/{len(to_move)}", flush=True)

    # Every row that reached a local revision is off the site and needs its
    # replacement, so this runs even after a failure above: leaving them
    # withdrawn and unpublished is the one outcome worse than either.
    waiting = len(publish.unpublished(conn))
    print(f"\npublishing {waiting} unpublished rows ...")
    result = publish.publish(conn)
    print(f"  sent {result['sent']}, stored {result['stored']}, "
          f"duplicate {result['duplicate']}, errors {len(result['errors'])}")
    for err in result["errors"][:10]:
        print(f"  ERROR {err}", file=sys.stderr)

    return 1 if (failures or result["errors"]) else 0


if __name__ == "__main__":
    sys.exit(main())
