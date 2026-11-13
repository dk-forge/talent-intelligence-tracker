#!/usr/bin/env python3
"""Take the money off the published rows whose figure is not a raise.

    python3 correct_not_capital_raised.py --dry-run   # counts only, writes nothing
    python3 correct_not_capital_raised.py             # apply

WHAT THIS IS FOR. "Masimo to be Acquired by Danaher for $180.00 Per Share" was
published carrying funding_amount "$9.9 billion" — the price Danaher is paying
FOR Masimo, read off Masimo's own 8-K exhibit 99.1, counted as money Masimo
raised. It was retracted by hand on 2026-07-29 because a human read the filing.

`pipeline/validate.not_capital_raised` now refuses that figure at ingestion,
but that governs only rows collected AFTER it. This governs the rows already on
the site, and it decides which rows those are by CALLING not_capital_raised
rather than re-testing anything, so the two can never drift.

Eight live rows qualify, all from `sec_edgar`, all of them an 8-K about an
officer change that mentions money the record does not report — $1.53bn of
stated "raises" between them:

    Compass, Inc.       $1,000.0M   a real convertible note issuance, disclosed
                                    in a filing about an officer change. The
                                    money is real; this record is not about it.
    Yorkville Int'l     $230M       blank-check trust
    Irenic Acquisition  $220M       blank-check trust; a SPAC employs nobody
    Laird Superfood     $50M        and four more below $50M

**Why the published row is WITHDRAWN and not corrected in place.** The site has
no route that can blank a column. `/correct` writes two columns and neither is
a money one; `/enrich` ignores an empty value on purpose, so that a blank can
never erase a known figure (includes/api.php says both, deliberately). And the
corrected revision cannot be republished either: `tit_insert_signal` dedups on
content_hash across EVERY revision, and content_hash is
md5(company_key|pillar|published_date|normalised_headline) — no funding column
is an input — so the corrected row hashes to the value the site already holds
and comes back 'retracted'. That is asserted below rather than assumed.

So the two consistent end states are "the site keeps a $1bn figure the filing
does not support" and "the site holds nothing for this record". The second one
is the only one this product can carry, and the corrections page says why in
public. Seven of the eight rows are 'routine' or 'medium' once the money is
gone, so what the page loses is an officer-change row it mostly did not show.

Locally the record SURVIVES and is corrected: `store.revise()` appends a
revision with the funding columns cleared and materiality recomputed, and the
old row stays at is_current = 0 with its figure intact, so "what did we publish
on date D" is still answerable. The new revision inherits its predecessor's
`published_at`, which is what keeps the bookkeeping honest: a row the site
never held stays unpublished and publishes normally, and a row whose published
copy has just been withdrawn is not offered again to be told 'retracted' and
then marked published.

Nothing here refetches a filing or calls a model. Idempotent and safe to
interrupt: a corrected row carries no funding_amount, so it is no longer a
target, and /retract on an already-withdrawn record reports zero rows rather
than failing.
"""

from __future__ import annotations

import argparse
import dataclasses
import sqlite3
import sys
from collections import Counter

import requests

import retract
from pipeline import publish, schema, store, validate

# Written onto the new revision.
NOTE = "funding removed: the figure is not capital this employer raised"

# What the public corrections page shows. It has to say what was wrong with the
# record, not what was wrong with our code.
REASON = ("the record carried a funding figure the source does not support: "
          "money named in the filing that this record does not report, or a "
          "deal price rather than capital raised. Withdrawn rather than "
          "corrected because the published row cannot have a figure removed "
          "in place")

# A pass that condemns a large share of the funding rows is a broken rule, not
# a broken table. The measured share is 8 of 3,064 (0.3%), so this leaves two
# orders of magnitude of headroom and still catches a rule that has started
# matching everything. Below MIN_ROWS a share says nothing.
MAX_SHARE = 0.05
MIN_ROWS = 100


class Unsafe(RuntimeError):
    """The worklist is so large that the likeliest explanation is a broken
    rule, not a broken table."""


def funded_rows(conn) -> list[dict]:
    """Every live row carrying a funding figure, whatever collected it.

    Not scoped to sec_edgar. The defect is a class — a number that is real and
    is not a raise — and it can arrive from any source; scoping the sweep to
    the collector that produced today's eight rows would close one door.
    """
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 "
        "   AND funding_amount IS NOT NULL AND funding_amount <> '' "
        " ORDER BY row_id")]


def targets(rows: list[dict], *, force: bool = False) -> list[tuple[dict, str]]:
    """Live funding rows the pipeline would no longer store the money on.

    The document body is not kept, so this reads the record's own headline and
    summary. Both arms of not_capital_raised work from those: the officer-change
    arm needs only the collector and the headline, and the price arm caught the
    Masimo row on its stored summary ("total consideration of $9.9 billion")
    with no document at all.
    """
    out = []
    for row in rows:
        why = validate.not_capital_raised(
            row["funding_amount"], collector=row["collector"],
            headline=row["headline"], summary=row["summary"] or "")
        if why:
            out.append((row, why))

    share = len(out) / len(rows) if rows else 0
    if len(rows) >= MIN_ROWS and share > MAX_SHARE and not force:
        raise Unsafe(
            f"{len(out)} of {len(rows)} live funding rows ({share:.1%}) would "
            f"lose their figure. Expected ~0.3%. Check "
            f"validate.not_capital_raised before re-running, and pass --force "
            f"only if the number is genuinely right.")
    return out


_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def corrected_signal(row: dict) -> validate.Signal:
    """The stored row as build_signal would have built it without the money.

    Everything else the model said is carried across untouched. Three columns go
    and one is recomputed:

      funding_amount / _usd   the figure and its parse
      funding_stage           it goes with them: the page's "Funding raised"
                              tile counts a row carrying EITHER, so a stage left
                              behind keeps the row in the funding count with no
                              figure to show for it
      materiality             funding of $10M or more is one of its inputs, so a
                              copy would leave the row disagreeing with itself
    """
    signal = validate.Signal(**{name: row[name] for name in _FIELDS})
    signal.funding_amount = None
    signal.funding_amount_usd = None
    signal.funding_stage = None
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


def hash_is_stable(row: dict, signal: validate.Signal) -> bool:
    """The correction must not move the fingerprint.

    Checked rather than assumed, because the whole withdraw-and-do-not-republish
    shape rests on it. If a funding column ever became an input to content_hash
    this pass would have to re-issue rows the way correct_sec_pillar.py does,
    and it should stop and say so rather than silently withdraw them.
    """
    return signal.content_hash == row["content_hash"] == validate.content_hash(
        signal.company_key, signal.pillar, signal.published_date,
        signal.headline, row["source_name"])


def correct(conn, row: dict, reason: str, *, withdraw) -> None:
    """Take one row off the site, then append its corrected revision.

    The withdrawal comes FIRST because the local revision is the only record
    that it happened: a row is a target while its live revision still carries
    the figure, so a run killed between the two steps simply retries both.

    The revision then inherits the predecessor's `published_at`. For a row that
    was on the site that keeps it out of publish()'s worklist, where it would
    otherwise be sent once, answered 'retracted' by the server, and marked
    published — leaving the local table claiming a publication the site does not
    have. For a row that was never sent it stays NULL and publishes normally.
    """
    if row["published_at"]:
        withdraw(row["signal_id"], REASON)
    store.revise(conn, row["signal_id"], corrected_signal(row), f"{NOTE}: {reason}")
    conn.execute(
        "UPDATE signals SET published_at = ? WHERE signal_id = ? AND is_current = 1",
        (row["published_at"], row["signal_id"]))
    conn.commit()


def _money(value) -> str:
    value = int(value or 0)
    if value >= 1_000_000_000:
        return f"${value / 1e9:,.2f}bn"
    if value >= 1_000_000:
        return f"${value / 1e6:,.1f}M"
    return f"${value:,}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    parser.add_argument("--limit", type=int,
                        help="stop after N rows (for a first pass)")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share of rows would change")
    args = parser.parse_args()

    conn = schema.connect()
    rows = funded_rows(conn)
    print(f"{len(rows)} live rows carry a funding figure")

    try:
        to_correct = targets(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    if args.limit:
        to_correct = to_correct[:args.limit]

    moved = [(row, why) for row, why in to_correct
             if not hash_is_stable(row, corrected_signal(row))]
    if moved:
        print(f"REFUSING: {len(moved)} row(s) would change content_hash. A "
              f"funding column has become an input to the fingerprint, so these "
              f"have to be re-issued rather than withdrawn — see "
              f"correct_sec_pillar.py for that shape.", file=sys.stderr)
        return 2

    total = sum(int(row["funding_amount_usd"] or 0) for row, _ in to_correct)
    published = sum(1 for row, _ in to_correct if row["published_at"])
    by_collector = Counter(row["collector"] for row, _ in to_correct)
    after = Counter(corrected_signal(row).materiality for row, _ in to_correct)

    print(f"\n  to correct               {len(to_correct):>5}   "
          f"({_money(total)} of stated raises leaves the money total)")
    for collector, n in by_collector.most_common():
        print(f"    {collector:<22}{n:>5}")
    print(f"  published, so needing a withdrawal first  {published:>5}")
    print(f"  already correct          {len(rows) - len(to_correct):>5}")
    print("  materiality after: " + ", ".join(
        f"{k} {v}" for k, v in after.most_common()))

    for row, why in to_correct:
        print(f"\n  [{_money(row['funding_amount_usd'])}] {row['company']}"
              f"\n            {row['headline'][:92]}"
              f"\n            {why}")

    if args.dry_run:
        waiting = len(publish.unpublished(conn))
        if waiting:
            print(f"\n  {waiting} row(s) are unpublished; a real run publishes them.")
        print("\ndry run: nothing written")
        return 0

    failures = 0
    if to_correct:
        print(f"\nwithdrawing and correcting {len(to_correct)} rows ...")
        for row, why in to_correct:
            try:
                correct(conn, row, why, withdraw=retract.retract_remote)
            except (publish.PublishError, requests.RequestException) as exc:
                # One row's failure is one row. It keeps its figure, stays live,
                # and the next run picks it up again.
                failures += 1
                print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)

    # Any row that was never on the site still needs sending, and this is also
    # where the pre-publish guardrails run over the corrected table.
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
