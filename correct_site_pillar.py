#!/usr/bin/env python3
"""Re-file the already-published office openings under the location pillar.

    python3 correct_site_pillar.py --dry-run     # counts and diff, writes nothing
    python3 correct_site_pillar.py               # apply

Commit 71d17c2 gave SCHEMA_HINT its four pillar definitions and added the
site_event column, so a story that says an employer opened an office now lands
in how_we_work — "How we work & location strategy" — carrying site_event
'opened'. That governs rows collected AFTER it. This governs the rows collected
while SCHEMA_HINT defined no pillar at all, which is where the damage is.

Measured on 2026-07-29, over the 132 live rows the news collectors hold:

    5   already in how_we_work, site_event never set
    4   in company_development, which is money and corporate events
    1   already correct (Infiligence), and left alone

and "4Life Opens New Office in Mexico" is in the table TWICE, once under each
pillar. Not a hash collision and not two articles: BOTH ROWS CARRY THE SAME
source_url and seen_urls holds exactly one entry for it. pillar is an input to
content_hash AND the key dedupe.fuzzy_duplicate groups on, so one article read
under two pillars produces two fingerprints and walks through both dedup
layers. Correcting the pillar is therefore also what RESOLVES the duplicate:
the two rows collapse onto one hash and the second is withdrawn rather than
re-issued, by the same split_duplicates the SEC pass uses.

**Nothing here calls a model.** A headline that says "opens new office" decides
its own pillar, which is the argument validate.forced_pillar already makes for
Item 5.02 filings, so this EXTENDS that mechanism rather than adding a second
one: validate.forced_site_event answers "does this headline plainly say a site
opened", forced_pillar returns how_we_work when it does, build_signal applies
both at ingestion, and this pass decides which stored rows are wrong by CALLING
them. The two can never drift, because there is only one rule.

What "plainly" is allowed to mean is deliberately narrow, and lives in
prefilter.site_opening_term next to the vocabulary it is built from. A planned
opening, an expansion and a closure all return None and stay the model's to
read — "to open in 2028" is `announced`, not `opened`, and one word carries the
whole difference. So this pass does NOT touch "$7B firm expands Cary office
space" or "OpenAI expands AI workforce in Dublin", and does not second-guess a
site_event the model already set.

**Why a revision and not a correction in place.** pillar is an input to
content_hash — md5(company_key|pillar|published_date|normalised_headline), see
pipeline/validate.content_hash(). Rewriting it moves the row's fingerprint, so
an in-place change would leave a row whose stored hash no longer matches its
own contents, and the next collection of that story would hash to the new
value, find no match, and publish it a second time. Each row is re-issued the
way the repo already re-issues a record:

    store.revise()   locally: the old row survives at is_current = 0 and a new
                     revision is appended with the same signal_id, the new
                     content_hash and published_at NULL
    /retract         on the site: the published row goes to is_current = 0
    publish()        sends every published_at IS NULL row, so the new revision
                     lands as an ordinary insert

site_event is NOT a hash input, so the rows that only need the column filled
could have been corrected in place. They go through revise() anyway: one path
for one pass is what lets the workflow rebase on a race the way
correct-sec-pillar does, instead of needing correct-form-d's red-and-rerun.

Idempotent, and safe to interrupt: each phase derives its own worklist from
what is stored, so a row already revised is not revised again, a retraction
already sent is not sent again, and publishing was resumable to begin with.
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

PILLAR = "how_we_work"
SITE_EVENT = "opened"

# The collectors whose headline is a journalist's sentence, so the only ones
# whose pillar a headline is allowed to settle. Taken from validate rather than
# restated, so the worklist and the rule cannot disagree about who is in scope.
COLLECTORS = validate.SITE_HEADLINE_COLLECTORS

# Written onto the new revision, and the marker every later phase resumes from.
NOTE = ("re-filed under how we work & location strategy: the headline states "
        "that the employer opened a place of work, which is a location signal "
        "rather than a corporate event")

REASON = ("republished under how we work & location strategy: the headline "
          "states that the employer opened a place of work, and this record "
          "was filed under another pillar")

DUPLICATE_REASON = ("the same article as a record already published under how "
                    "we work & location strategy: one story was read under two "
                    "pillars and stored twice, and this is the second copy")

# A pass that re-issues most of the news collectors is not a correction pass,
# it is site_opening_term matching something it should not. The measured share
# is 7% (9 of 132). Below MIN_ROWS a share says nothing — three rows out of
# four is 75% and no evidence of anything — so the guard waits until there is a
# population to judge rather than blocking a small table or a test fixture.
MAX_SHARE = 0.20
MIN_ROWS = 50


class Unsafe(RuntimeError):
    """The worklist is so large that the likeliest explanation is a broken
    rule, not a broken table."""


def current_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    placeholders = ", ".join("?" for _ in COLLECTORS)
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM signals WHERE collector IN ({placeholders}) "
        "AND is_current = 1 ORDER BY row_id", tuple(sorted(COLLECTORS)))]


def corrections(row: dict) -> tuple[str, str | None]:
    """(pillar, site_event) as build_signal would set them for this row today.

    Both come from validate, and both keep build_signal's asymmetry exactly:
    the pillar is FORCED, because a headline that says a site opened settles
    which pillar it belongs to; site_event only FILLS A BLANK, because a model
    that named a site event read the body, and the body outranks a headline on
    "expanded or opened" every time.
    """
    collector, headline = row["collector"], row["headline"]
    pillar = validate.forced_pillar(collector, headline) or row["pillar"]
    site_event = row["site_event"] or validate.forced_site_event(collector, headline)
    return pillar, site_event


def targets(rows: list[dict], *, force: bool = False) -> list[dict]:
    """Live rows whose stored pillar or site_event disagrees with the headline."""
    out = [r for r in rows if corrections(r) != (r["pillar"], r["site_event"])]
    share = len(out) / len(rows) if rows else 0
    if len(rows) >= MIN_ROWS and share > MAX_SHARE and not force:
        raise Unsafe(
            f"{len(out)} of {len(rows)} live news rows ({share:.0%}) would be "
            f"re-issued. Expected ~7%. Check prefilter.site_opening_term before "
            f"re-running, and pass --force only if the number is genuinely right.")
    return out


_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def corrected_signal(row: dict) -> validate.Signal:
    """The stored row as build_signal would have built it with the pillar and
    the site event fixed.

    Everything the model said is carried across untouched. Two further values
    are recomputed rather than copied, because both are DERIVED from the pillar
    and a copy would leave the row disagreeing with itself:

      content_hash  the pillar is one of its four inputs
      materiality   recomputed exactly as build_signal computes it, from the
                    stored columns
    """
    signal = validate.Signal(**{name: row[name] for name in _FIELDS})
    signal.pillar, signal.site_event = corrections(row)
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

    This is what resolves the 4Life pair. Correcting the pillar moves the
    fingerprint, and here it moves the company_development copy onto the hash
    the how_we_work copy already holds — same employer, same date, same
    headline, same URL. Under the corrected pillar those two are one record by
    the definition content_hash exists to state, and store.store() would have
    called the second a duplicate at collection time had the pillar been right
    then.

    So the second is withdrawn rather than re-issued. Publishing it is not an
    option even in principle: the site's unique key is (content_hash, revision)
    and every insert it makes is revision 1, so the second row would not error,
    it would silently never land.

    The site's SECOND guard is mirrored here for the same reason. Beyond the
    hash, tit_insert_signal() refuses a row whose employer, pillar, direction
    and published date (within 14 days) match a live one, and /bulk reports
    that as an ordinary 'duplicate'. publish() counts duplicates, it cannot
    name them, and it marks every row the server accepted as published — so a
    row refused there would be withdrawn on the site, replaced by nothing, and
    recorded here as published. Applying the rule here withdraws it honestly,
    with a reason, instead.

    Order matters and is row_id ascending, from current_rows: the copy that has
    been live longest is the one kept.
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
    predecessor is still live puts the same story on the page twice under two
    pillars — which is the exact state the 4Life pair is already in. The local
    revision comes second because it is the ONLY record that the withdrawal
    happened: a row is a target while its live revision disagrees with its
    headline, so a run killed between the two steps simply retries both, and
    /retract on an already-withdrawn record reports zero rows rather than
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


def _describe(row: dict) -> str:
    """One row's change, as the diff a dry run has to show before anything is
    written."""
    pillar, site_event = corrections(row)
    parts = []
    if pillar != row["pillar"]:
        parts.append(f"{row['pillar']} -> {pillar}")
    if site_event != row["site_event"]:
        parts.append(f"site_event {row['site_event'] or '-'} -> {site_event}")
    return ", ".join(parts)


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
    print(f"{len(rows)} live rows across {len(COLLECTORS)} news collectors")

    try:
        to_move = targets(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    if args.limit:
        to_move = to_move[:args.limit]
    to_move, dupes = split_duplicates(conn, to_move)

    repillared = [r for r in to_move if corrections(r)[0] != r["pillar"]]
    by_pillar = Counter(r["pillar"] for r in repillared)
    materiality = Counter(corrected_signal(r).materiality for r in to_move)

    print(f"\n  to re-issue                {len(to_move):>5}")
    print(f"    of which change pillar   {len(repillared):>5}")
    for pillar, n in by_pillar.most_common():
        print(f"      from {pillar:<20}{n:>5}")
    print(f"    site_event only          {len(to_move) - len(repillared):>5}")
    print(f"  to withdraw as duplicates  {len(dupes):>5}   "
          f"(the corrected pillar makes them a record already live)")
    print(f"  already correct            {len(rows) - len(to_move) - len(dupes):>5}")
    print(f"  published, so needing a retraction first  "
          f"{sum(1 for r in to_move if r['published_at']):>5}")
    print("  materiality after: " + ", ".join(
        f"{k} {v}" for k, v in materiality.most_common()))

    # The diff, in full. This pass is small enough to read every row of, and a
    # correction nobody looked at before it ran is how the wrong rule ships.
    for row in to_move:
        print(f"\n  [{_describe(row)}]"
              f"\n    {row['company']}  ({row['collector']}, {row['published_date']})"
              f"\n    {row['headline'][:96]}"
              f"\n    {row['source_url']}")
    for row in dupes:
        print(f"\n  [withdraw as duplicate]"
              f"\n    {row['company']}  ({row['collector']}, {row['published_date']})"
              f"\n    {row['headline'][:96]}"
              f"\n    {row['source_url']}")

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
