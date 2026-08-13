#!/usr/bin/env python3
"""Re-issue the published 8-K filings whose headline carries a mangled filer name.

    python3 correct_sec_filer_name.py --dry-run      # counts only, writes nothing
    python3 correct_sec_filer_name.py                # apply

collectors/sec_edgar.py writes its own headline, because an 8-K is dense legal
prose with no headline in it:

    {filer name} 8-K filing (Item 5.02): officer or director change

The filer name came out of EDGAR's `display_names`, and the rule that cut
EDGAR's own ticker and CIK groups off it was wrong in two opposite directions.
It hunted for a single parenthesised ticker-shaped token anywhere in the
string, so it never matched a ticker LIST (the comma in "(BBBY, BBBY-WT)"
stopped it) and it did match a company's own parenthetical. 127 published
headlines are wrong today, in two shapes:

    BED BATH & BEYOND, INC.  (BBBY, BBBY-WT) 8-K filing ...   126 rows
    Jerash Holdings , Inc. 8-K filing ...                       1 row

collectors/sec_edgar._company_and_cik now reads the string's structure instead,
but that governs only filings collected AFTER it. This governs the rows already
on the site.

**Only the DISPLAY string is wrong.** `company`, `company_key`, `cik` and
`ticker` are not built from this value — `company` is what the model read out
of the filing itself — so no join key, employer page or dedup key was ever
built on the mangled name. Checked across all 127: not one stored company
carries a leftover ticker, and the single eaten-parenthetical row stores the
company correctly as "Jerash Holdings (US), Inc.". So this is a headline
correction and nothing more.

**Why a revision and not a correction in place.** headline is an input to
content_hash — md5(company_key|pillar|published_date|normalised_headline), see
pipeline/validate.content_hash(). Rewriting it moves the row's fingerprint, so
/correct is both the wrong door (it writes signal_direction and
talent_readthrough, nothing else) and the wrong shape: an in-place change would
leave a row whose stored hash no longer matches its own contents, and the next
collection of that filing would hash to the new value, find no match, and
publish it a second time. So each row is re-issued exactly as
correct_sec_pillar.py re-issues one, and for exactly the same reason:

    store.revise()   locally: the old row survives at is_current = 0 and a new
                     revision is appended with the same signal_id, the new
                     content_hash and published_at NULL
    /retract         on the site: the published row goes to is_current = 0
    publish()        sends every published_at IS NULL row

**Where the corrected name comes from, and where it refuses to guess.** Two
groups, and the difference matters:

- **Recoverable from the stored string.** For the 126 ticker-list rows nothing
  was deleted; EDGAR's block is still sitting there. The clean name is the
  stored one with that block cut off, and it is cut off by calling
  `sec_edgar._TICKER_GROUP` rather than re-implementing it, so the collector
  and this script can never drift.
- **NOT recoverable from the stored string.** The eaten-parenthetical rows lost
  characters. This script will not invent them. It restores such a row only
  when the stored `company` column PROVES the original: applying the old broken
  rule to that company must reproduce the mangled headline name exactly. For
  Jerash, "Jerash Holdings (US), Inc." minus "(US)" is "Jerash Holdings ,
  Inc.", which is what is stored, so the name is proven rather than guessed.
  Any row that fails that proof is listed and left alone for a human.

Nothing here refetches a filing or calls a model, so it costs nothing. The
corrected row carries the model's own words for everything; the headline gets
the filer's real name, and content_hash and materiality are recomputed from the
stored columns exactly as build_signal computes them.

Idempotent, and safe to interrupt: the worklist is derived from what is stored,
so a row already revised is not revised again, and publishing was resumable to
begin with.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sqlite3
import sys
from collections import Counter

import requests

import retract
from collectors import sec_edgar
from correct_sec_pillar import _is_near, _remember_near
from pipeline import publish, schema, store, validate

COLLECTOR = "sec_edgar"

# The headline collectors/sec_edgar.collect() writes, minus the filer name.
SUFFIX = " 8-K filing (Item 5.02): officer or director change"

NOTE = ("filer name corrected: EDGAR's ticker block was left in the headline, or "
        "part of the company's own name was cut out of it")

REASON = ("republished with the filer's own name: the headline carried EDGAR's "
          "exchange-ticker block, or had part of the company's real name removed")

DUPLICATE_REASON = ("the same employer, date and event as a record already "
                    "published under the corrected filer name")

# The old rule, kept HERE and nowhere else, because proving what a stored
# mangled name came from means replaying exactly the transformation that
# mangled it. It is dead in the collector and must never come back there.
_OLD_RULE = re.compile(r"\s*\((?:[A-Z0-9.\-]{1,10})\)\s*")

# What is stored is not always what the collector wrote. build_signal takes
# `classified.get("headline") or raw.get("headline")`, so where the model
# echoed the collector's headline back it sometimes collapsed EDGAR's two-space
# delimiter to one — four live rows read "CareCloud, Inc. (CCLD, CCLDO) 8-K
# filing ...". The collector's own expression will not match those, and widening
# IT to one space is exactly the mistake this whole change is undoing: at one
# space "ACUITY INC. (DE)" and "Western Asset Diversified Income Fund (WDI)"
# become indistinguishable from a ticker.
#
# A comma is what makes the collapsed case safe to match. A ticker LIST is not
# a shape a company name ends in — no filer is called "Something (CCLD, CCLDO)"
# — so this asks for two or more ticker-shaped tokens and accepts one space.
# A single collapsed token stays unprovable and is listed for a human.
_COLLAPSED_TICKER_LIST = re.compile(
    r"\s+\([A-Z0-9][A-Z0-9.\-]{0,9}(?:,\s*[A-Z0-9][A-Z0-9.\-]{0,9})+\)\s*$")

# A pass that re-issues most of the source is a broken worklist, not a broken
# table. The measured share is 127 of 3,808, about 3%.
MAX_SHARE = 0.15
MIN_ROWS = 50


class Unsafe(RuntimeError):
    """The worklist is so large that the likeliest explanation is a broken
    rule, not a broken table."""


class Unprovable(RuntimeError):
    """The stored row does not prove what the filer's name was."""


def filer_name(headline: str) -> str | None:
    """The name the headline currently states, or None if it is not one of
    the collector's own headlines (the model rewrote 605 of them, and those
    are its reading of the document, not this parser's output)."""
    if not headline or not headline.endswith(SUFFIX):
        return None
    return headline[:-len(SUFFIX)]


def corrected_name(row: dict) -> str:
    """The filer's real name, proven from what is stored. Raises Unprovable
    rather than guessing."""
    stated = filer_name(row["headline"])
    if stated is None:
        raise Unprovable("not one of the collector's own headlines")

    # Group 1: EDGAR's block is still in the string. Cut it with the
    # collector's own expression, so the two can never drift, then with the
    # collapsed-delimiter variant above.
    for rule in (sec_edgar._TICKER_GROUP, _COLLAPSED_TICKER_LIST):
        cut = rule.sub("", stated).strip()
        if cut != stated:
            return cut

    # Group 2: characters were deleted. The stored company must prove them.
    company = (row["company"] or "").strip()
    if company and _OLD_RULE.sub(" ", company).strip() == stated.strip():
        return company
    raise Unprovable(
        f"the stored company {company!r} does not reproduce the stored headline "
        f"name {stated!r} under the old rule, so the real name is not proven")


def is_mangled(row: dict) -> bool:
    """A live row whose headline states something that is not the filer's name."""
    stated = filer_name(row["headline"])
    if stated is None:
        return False
    return bool(sec_edgar._TICKER_GROUP.search(stated)
                or _COLLAPSED_TICKER_LIST.search(stated)
                or re.search(r"\s+,", stated))


def current_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE collector = ? AND is_current = 1 ORDER BY row_id",
        (COLLECTOR,))]


def targets(rows: list[dict], *, force: bool = False) -> tuple[list[dict], list[tuple]]:
    """(rows to re-issue, rows whose real name is not proven)."""
    out, unprovable = [], []
    for row in rows:
        if not is_mangled(row):
            continue
        try:
            corrected_name(row)
        except Unprovable as exc:
            unprovable.append((row, str(exc)))
            continue
        out.append(row)

    share = (len(out) + len(unprovable)) / len(rows) if rows else 0
    if len(rows) >= MIN_ROWS and share > MAX_SHARE and not force:
        raise Unsafe(
            f"{len(out) + len(unprovable)} of {len(rows)} live {COLLECTOR} rows "
            f"({share:.0%}) look mangled. Expected about 3%. Check "
            f"collectors/sec_edgar._company_and_cik before re-running, and pass "
            f"--force only if the number is genuinely right.")
    return out, unprovable


_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def corrected_signal(row: dict) -> validate.Signal:
    """The stored row as build_signal would have built it with the name fixed.

    Everything the model said is carried across untouched. Three values are
    recomputed rather than copied, because all three are DERIVED from the
    headline and a copy would leave the row disagreeing with itself:

      content_hash  the normalised headline is one of its four inputs
      materiality   compute_materiality reads the headline
      pillar        validate.forced_pillar reads the headline, and the item
                    code it looks for is still in the corrected one
    """
    signal = validate.Signal(**{name: row[name] for name in _FIELDS})
    signal.headline = corrected_name(row) + SUFFIX
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
    """Split the worklist into rows to re-issue and rows the correction turns
    into a record that is already live.

    Same shape and same reasons as correct_sec_pillar.split_duplicates: the new
    fingerprint can land on one another row already holds, the site's unique key
    is (content_hash, revision) and every insert it makes is revision 1, so a
    colliding row would not error, it would silently never land. The site's
    SECOND guard, tit_insert_signal()'s employer/pillar/direction/14-day check,
    is mirrored here through the same helpers for the same reason: publish()
    counts duplicates but cannot name them, so a row refused there would be
    withdrawn on the site, replaced by nothing, and recorded here as published.

    A filer that changed its registered name between two filings is the case
    that reaches this — the corrected headlines converge where the mangled ones
    differed.
    """
    live = [dict(r) for r in conn.execute(
        "SELECT signal_id, content_hash, company_key, pillar, signal_direction, "
        "       published_date FROM signals WHERE is_current = 1")]
    moving = {r["signal_id"] for r in rows}
    live = [r for r in live if r["signal_id"] not in moving]
    held = {r["content_hash"]: r["signal_id"] for r in live}
    near: dict[tuple, list] = {}
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


def reissue(conn, row: dict, *, withdraw) -> None:
    """Take one row off the site, then append its corrected revision.

    That order, and one row at a time, is what keeps this interruptible; see
    correct_sec_pillar.reissue for the full argument. Committed per row, so a
    run killed at any point leaves whole rows behind and publish() finds
    exactly the ones that got this far.
    """
    if row["published_at"]:
        withdraw(row["signal_id"], REASON)
    store.revise(conn, row["signal_id"], corrected_signal(row), NOTE)
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    parser.add_argument("--limit", type=int, help="stop after N rows")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share of rows would move")
    args = parser.parse_args()

    conn = schema.connect()
    rows = current_rows(conn)
    print(f"{len(rows)} live {COLLECTOR} rows")

    try:
        to_move, unprovable = targets(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    if args.limit:
        to_move = to_move[:args.limit]
    to_move, dupes = split_duplicates(conn, to_move)

    def shape(row):
        stated = filer_name(row["headline"])
        if sec_edgar._TICKER_GROUP.search(stated):
            return "ticker block left in"
        if _COLLAPSED_TICKER_LIST.search(stated):
            return "ticker block, space collapsed"
        return "parenthetical eaten"

    shapes = Counter(shape(r) for r in to_move)

    print(f"\n  to re-issue               {len(to_move):>5}")
    for shape, n in shapes.most_common():
        print(f"    {shape:<24}{n:>5}")
    print(f"  to withdraw as duplicates {len(dupes):>5}")
    print(f"  name not proven, left alone {len(unprovable):>3}")
    print(f"  already correct           "
          f"{len(rows) - len(to_move) - len(dupes) - len(unprovable):>5}")
    print(f"  published, so needing a retraction first  "
          f"{sum(1 for r in to_move if r['published_at']):>5}")

    for row, why in unprovable:
        print(f"\n  [SKIP] row {row['row_id']} {row['company']}\n         {why}")
    for row in dupes:
        print(f"\n  [withdraw] {row['company']}\n             {row['source_url']}")
    for row in to_move[:8]:
        print(f"\n  [fix] {filer_name(row['headline'])}"
              f"\n     -> {corrected_name(row)}")

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
                # One row's failure is one row. It keeps its old headline,
                # stays live, and the next run picks it up again.
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
