#!/usr/bin/env python3
"""Re-issue the rows whose stored `company_key` is not the key we would compute today.

    python3 correct_company_key.py --dry-run      # every change listed, nothing written
    python3 correct_company_key.py                # apply

WHAT IS WRONG
-------------
`company_key` is the only employer identity this product has. It is a
normalised name, computed once at ingestion and then stored, so every later
change to `pipeline.vocab.company_key` leaves the rows behind it spelled the
old way. Three changes have done that, and this pass is the backward half of
all three:

  1. THE LEGAL-SUFFIX STRIP USED `\\b`, AND A HYPHEN IS A WORD BOUNDARY, so
     `\\bco\\b` matched the "co" inside "co-operative" and CO-OPERATIVE GROUP
     LIMITED was stored as `-operative group`. Six real employers, 30 rows.
     One of them was found by a reader looking at a URL.
  2. THREE EMPLOYERS ARE RECORDED TWICE under keys that differ only in
     punctuation, because the filer spells them two ways: EDGAR's company index
     writes PERMA FIX where the 8-K cover page writes Perma-Fix, and the GOV.UK
     pay-gap service holds one NHS trust under two employer ids, once with "&"
     and once with "and". Both spellings claim the same profile URL, which
     includes/company.php refuses to serve rather than show half an employer's
     history. `vocab.EMPLOYER_KEY_ALIASES` merges them; this moves the rows.
     6 rows.
  3. `lp` AND `pbc` WERE ADDED TO THE SUFFIX LIST after some rows were stored,
     so `crossamerica partners lp` and `peace coffee pbc` still carry a suffix
     every later row of theirs would have lost. 2 rows.

Nobody wrote (3) down. It is here because the worklist is DERIVED — every live
row whose stored key differs from `vocab.company_key(row.company)` — rather
than typed from the list of employers somebody already knew about. The same
derivation is why the next change to that function needs no new script.

WHY IT MATTERS
--------------
`company_key` is the first input to `validate.content_hash()`, the fingerprint
every collector dedupes against. A row stored under a key the pipeline no
longer produces cannot match its own history: the next signal about
CO-OPERATIVE GROUP hashes to the new key, finds nothing, and lands as a second
record for the same employer. It also decides the employer's public URL, since
includes/company.php derives the slug from the key.

WHY A REVISION AND NOT A CORRECTION IN PLACE
--------------------------------------------
Exactly the reasoning in correct_sec_pillar.py, one input along. Rewriting the
key moves the row's fingerprint, so an in-place edit would leave a row whose
stored hash no longer matches its own contents, and the next collection of that
document would hash to the new value, find no match, and publish it twice. So
each row is re-issued the way this repo already re-issues a record:

    store.revise()   locally: the old row survives at is_current = 0 and a new
                     revision is appended with the same signal_id, the new
                     content_hash and published_at NULL
    /retract         on the site: the published row goes to is_current = 0
    publish()        sends every published_at IS NULL row

Nothing here refetches a document or calls a model. TWO values change and no
others: `company_key`, and the `content_hash` computed from it. `materiality`
is deliberately NOT recomputed the way the pillar pass recomputes it —
`compute_materiality` does not read the key, so recomputing it could only
introduce a difference, never remove one.

THE THREE URLS THAT MOVE
------------------------
Of the eleven employers here, three are over the profile publishing threshold
and therefore in the company sitemap, and their slug changes with their key:

    /company/operative-group/            -> /company/co-operative-group/
    /company/the-midcounties-operative/  -> /company/the-midcounties-co-operative/
    /company/central-england-operative/  -> /company/central-england-co-operative/

The old three must not become 404s. They do not, and not because of anything
in this file: plugin 1.47.0 resolves a slug that only a SUPERSEDED revision
claims to that signal's current key and 301s there, which is a property of
revisions rather than a redirect list, so it covers this pass and every later
one. DEPLOY THAT BEFORE RUNNING THIS. Between the two there is a window in
which those URLs 404.

A handful of rows may not be re-issuable at all, because the corrected key
moves their fingerprint onto one another live row already holds. Those are the
same record by the definition content_hash exists to state, so they are
WITHDRAWN rather than republished. See split_duplicates.

Idempotent, and safe to interrupt: the worklist is derived from what is stored,
so a row already revised is not a target, a retraction already sent is not sent
again, and publishing was resumable to begin with.
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
from pipeline import publish, schema, store, validate, vocab

# Written onto the new revision, and the marker a reader of the corrections log
# sees. Deliberately says what changed and not which of the three defects
# caused it: the row cannot tell, and guessing would be a fact we invented.
NOTE = ("employer key corrected: the stored company_key was not the key this "
        "name normalises to, so the record could not dedupe against its own "
        "history")

REASON = ("republished under the employer's corrected key: the stored "
          "company_key was not the key this employer's name normalises to")

DUPLICATE_REASON = ("the same employer, date and event as a record already "
                    "published under the corrected employer key: the two "
                    "spellings were one employer and this is the second copy")

# A pass that re-keys a meaningful share of the corpus is not a correction
# pass, it is somebody having broken company_key. Measured here: 38 of 15,650
# live rows, 0.24%. Five percent is two decimal orders above that and still
# nowhere near a real edit to the suffix vocabulary, which would be the one
# legitimate way to exceed it — and that edit deserves a human saying --force
# out loud before 800 rows are withdrawn from the site and put back.
MAX_SHARE = 0.05
MIN_ROWS = 50


class Unsafe(RuntimeError):
    """The worklist is so large that the likeliest explanation is a broken
    company_key, not a broken table."""


def current_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 ORDER BY row_id")]


def targets(rows: list[dict], *, force: bool = False) -> list[dict]:
    """Live rows whose stored key is not what their name normalises to now.

    Calling vocab.company_key is the whole design. A list of the eleven
    employers somebody knows about would have missed two of them, and would go
    stale the next time that function is touched.
    """
    out = [r for r in rows if vocab.company_key(r["company"]) != r["company_key"]]
    share = len(out) / len(rows) if rows else 0
    if len(rows) >= MIN_ROWS and share > MAX_SHARE and not force:
        raise Unsafe(
            f"{len(out)} of {len(rows)} live rows ({share:.1%}) would be re-keyed. "
            f"Expected well under {MAX_SHARE:.0%}. Check pipeline/vocab.py "
            f"company_key before re-running, and pass --force only if the "
            f"number is genuinely right.")
    return out


_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def corrected_signal(row: dict) -> validate.Signal:
    """The stored row with its key fixed, as build_signal would have built it.

    Everything the model said is carried across untouched. content_hash is
    recomputed rather than copied because company_key is its first input and a
    copy would leave the row disagreeing with itself.
    """
    signal = validate.Signal(**{name: row[name] for name in _FIELDS})
    signal.company_key = vocab.company_key(signal.company)
    signal.content_hash = validate.content_hash(
        signal.company_key, signal.pillar, signal.published_date,
        signal.headline, signal.source_name)
    return signal


def split_duplicates(conn, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the worklist into rows to re-issue and rows the correction makes
    duplicates of a record that is already live.

    Mirrors correct_sec_pillar.split_duplicates, for the same two reasons and
    against the same two server-side guards. Merging two spellings of one
    employer makes a collision more likely here than it is there, because the
    two halves are by definition the same employer:

      * the exact hash. The site's unique key is (content_hash, revision) and
        every insert it makes is revision 1, so a second row carrying a live
        row's hash would not error, it would silently never land.
      * the near-duplicate guard. Beyond the hash, tit_insert_signal() refuses
        a row whose employer, pillar, direction and published date (within 14
        days) match a live one, and /bulk reports that as an ordinary
        'duplicate'. publish() counts duplicates, it cannot name them, and it
        marks every row the server accepted as published — so a row refused
        there would be withdrawn on the site, replaced by nothing, and recorded
        here as published.

    Withdrawing them says so honestly, with a reason, instead.
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


def key_moves(rows: list[dict]) -> dict[str, str]:
    """old key -> new key, for the employers in the worklist."""
    return {r["company_key"]: vocab.company_key(r["company"]) for r in rows}


def carry_identity_cache(conn, moves: dict[str, str]) -> int:
    """Copy each moved employer's resolved identity onto its new key.

    `employer_identity` is a pure cache keyed on company_key, holding the CIK,
    ticker, HQ and employer type that pipeline.identity resolved from Wikidata
    and SEC — including the NEGATIVE results, which are two thirds of it. A
    re-key orphans that entry, and the next enrichment pass would pay for the
    same lookups again.

    NOTHING IS DELETED HERE, and that is the rule for this whole pass rather
    than an oversight. An entry under a key no row uses costs a row in a cache;
    dropping it is the only irreversible thing this script could do, and a cache
    is the last place to spend irreversibility. For the same reason an existing
    entry on the new key WINS: it was resolved for the key that survives.
    """
    carried = 0
    for old, new in moves.items():
        if old == new:
            continue
        row = conn.execute(
            "SELECT * FROM employer_identity WHERE company_key = ?", (old,)).fetchone()
        if row is None:
            continue
        if conn.execute("SELECT 1 FROM employer_identity WHERE company_key = ?",
                        (new,)).fetchone():
            continue
        data = dict(row)
        data["company_key"] = new
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO employer_identity ({columns}) VALUES ({placeholders})",
                     tuple(data.values()))
        carried += 1
    return carried


def reissue(conn, row: dict, *, withdraw) -> None:
    """Take one row off the site, then append its corrected revision.

    That order, and one row at a time, is what keeps this interruptible. The
    withdrawal comes first because a replacement published while its
    predecessor is still live puts the same document on the page twice under
    two employers. The local revision comes second because it is the ONLY
    record that the withdrawal happened: a row is a target while its live
    revision carries the wrong key, so a run killed between the two steps
    simply retries both, and /retract on an already-withdrawn record reports
    zero rows rather than failing.

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
    print(f"{len(rows)} live rows")

    try:
        to_move = targets(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    if args.limit:
        to_move = to_move[:args.limit]
    to_move, dupes = split_duplicates(conn, to_move)

    moves = key_moves(to_move + dupes)
    by_collector = Counter(r["collector"] for r in to_move)

    print(f"\n  employers re-keyed              {len(moves):>5}")
    print(f"  rows to re-issue                {len(to_move):>5}")
    for collector, n in by_collector.most_common():
        print(f"    from {collector:<25}{n:>5}")
    print(f"  rows to withdraw as duplicates  {len(dupes):>5}   "
          f"(the corrected key makes them a record already live)")
    print(f"  published, so needing a retraction first  "
          f"{sum(1 for r in to_move if r['published_at']):>5}")

    # Every employer, every time. Eleven lines a reader can check by eye beats
    # a sample of five, and if this ever prints hundreds the guard above has
    # already refused the run.
    counts = Counter(r["company_key"] for r in to_move + dupes)
    print()
    for old, new in sorted(moves.items()):
        n = counts[old]
        print(f"  [{n:>2} row{' ' if n == 1 else 's'}] {old!r}\n             -> {new!r}")
    for row in dupes:
        print(f"\n  [withdraw] {row['company']}\n             {row['source_url']}")

    if args.dry_run:
        waiting = len(publish.unpublished(conn))
        if waiting:
            # publish() sends every unpublished row, not only this pass's. That
            # is deliberate — a stored row the site has never seen is a row
            # nobody can read — but it means the count below is not a
            # prediction about this correction, so it says whose rows they are.
            print(f"\n  {waiting} stored row(s) are unpublished before this pass "
                  f"even starts (an interrupted correction, or a collect run "
                  f"whose publish leg did not land). A real run sends those too:")
            for row in publish.unpublished(conn)[:5]:
                print(f"      {row['collector']:<16} {row['company'][:44]}")
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
                # One row's failure is one row. It keeps its old key, stays
                # live, and the next run picks it up again.
                failures += 1
                print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)
            if n % 50 == 0:
                print(f"  {n}/{len(to_move)}", flush=True)

    carried = carry_identity_cache(conn, moves)
    conn.commit()
    print(f"\nidentity cache carried onto {carried} new key(s)")

    # Every row that reached a local revision is off the site and needs its
    # replacement, so this runs even after a failure above: leaving them
    # withdrawn and unpublished is the one outcome worse than either.
    waiting = len(publish.unpublished(conn))
    print(f"publishing {waiting} unpublished rows ...")
    result = publish.publish(conn)
    print(f"  sent {result['sent']}, stored {result['stored']}, "
          f"duplicate {result['duplicate']}, errors {len(result['errors'])}")
    for err in result["errors"][:10]:
        print(f"  ERROR {err}", file=sys.stderr)

    return 1 if (failures or result["errors"]) else 0


if __name__ == "__main__":
    sys.exit(main())
