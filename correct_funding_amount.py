#!/usr/bin/env python3
"""Re-derive `funding_amount_usd` from the string the publisher wrote.

    python3 correct_funding_amount.py            # DRY RUN — the default here
    python3 correct_funding_amount.py --apply    # writes

Queue it, never dispatch it (CLAUDE.md, "Never dispatch a database writer
directly"):

    gh workflow run drain-writers.yml -f enqueue=correct-funding-amount.yml \
      -f inputs_json='{"dry_run":"false"}' \
      -f reason='re-derive funding_amount_usd after the multiplier fixes'

WHAT IS WRONG
-------------
`funding_amount` is the source's own wording and never changes.
`funding_amount_usd` is what `vocab.parse_funding_usd` made of it AT THE MOMENT
THE ROW WAS WRITTEN, and that function keeps getting better: 2026-07-29 added
the hyphenated multiplier and the stated-dollar rule, 2026-07-30 added `milyon`,
`mi`, the dot-as-thousands reading and `_MIN_PLAUSIBLE_USD`.

Every one of those improvements leaves the rows collected before it holding a
figure the parser would no longer produce. Measured against `main` on
2026-07-30: **12 of 3,254 live rows with a funding string (0.37%)** disagree with
the current parser, all 12 published, and every one of the 12 is stored as a
two- or three-digit dollar amount for a round of millions. The stored total
moves $133,405,633,262 -> $133,745,781,597.

This is not a list of twelve row ids. THE WHOLE COLUMN IS RE-DERIVED, because
the defect is not those rows — it is that a pure function was improved after its
output was stored, which will happen again the next time somebody adds a scale
word. The next vocabulary fix should be followed by one queued run of this, not
by a fourteenth bespoke correction workflow.

TWO SHAPES OF CORRECTION, AND THE SECOND ONE IS THE POINT
---------------------------------------------------------
Some strings now parse to a correct figure (`USD 53 millones` -> 53,000,000).
Others now REFUSE (`25 millioner kroner`, `US$ 544 mi`, `$1`), and a row whose
amount refuses must end with **no funding_amount_usd at all**. The page states
that an amount it cannot read is left out rather than converted at a rate nobody
published, and a stale wrong number sitting where the parser now says "I will
not guess" is exactly the falsehood that promise exists to prevent.

That second shape is why `/enrich` grew `tit_clearable_columns()`: its ordinary
rule is that an absent or empty field NEVER erases a stored value, so a clear
has to be asked for by name, in `{"clear": ["funding_amount_usd"]}`.

WHY IN PLACE ON THE SITE AND A REVISION LOCALLY
------------------------------------------------
Locally: `store.revise()`, so the original survives at `is_current = 0` and
"what did the money chart say on 2026-07-29" stays answerable. Nothing is
overwritten. This is the rule, not a preference.

On the site: `/enrich`, which UPDATEs the live row. `funding_amount_usd` is NOT
an input to `content_hash` — md5(company_key|pillar|published_date|normalised
headline|source_name) — so the corrected revision carries the SAME hash as the
row it replaces, and `tit_insert_signal()` in includes/db.php refuses any hash
it has already seen at ANY revision. A withdraw-then-republish, which is what
correct_sec_pillar.py and correct_company_key.py do, would therefore take these
rows OFF the live page and get 'retracted' back when it tried to put them there
again: twelve real records silently deleted, reported as duplicates. Same
reasoning as correct_city_country.py, checked against the plugin rather than
assumed.

`enrich_published()` cannot do this job on its own. It carries a new VALUE
happily — that path already exists — but it can never carry an absent one, by
design, so the five rows whose only true answer is "no figure" have no route
through it. This script is that route.

Nor can `schema.backfill_funding_usd()`, which every `connect()` already runs. It
fills the column only `WHERE funding_amount_usd IS NULL`, so a parser
improvement reaches a row that never had a figure automatically — and reaches
none of these twelve, every one of which holds a WRONG figure rather than no
figure. That is not an oversight in it: filling a NULL invents nothing and needs
no revision, while replacing a stored value is a correction and owes one. The
same asymmetry is what makes a cleared row STAY cleared: the backfill re-examines
it every run and asks the identical function, which is the function that refused
it in the first place.

WHY IT REFUSES RATHER THAN RUNNING LONG
----------------------------------------
Re-deriving a whole column from a function means a bug in that function is a
bug in every row. Two ceilings guard it, both with a `--force` a person has to
type after reading the printed table: the share of rows that would move at all,
and the share that would be CLEARED. The second matters more — a parser that
started refusing everything would empty the money charts silently, and clearing
is the one direction `/enrich` cannot undo by re-running.

Quarantined rows are skipped, matching `publish.enrich_published()`: this is a
path that moves the headline money total, and a flagged row must not reach it by
the back door while publish() is carefully not sending it by the front.

Bounded, idempotent, and safe to interrupt. The worklist is DERIVED from what is
stored, so a row already corrected is not a target and a second run reports
nothing to do; the site update is an UPDATE to the value it already holds; and
the remote step happens BEFORE the local revision, so a run killed between them
retries both rather than leaving the page wrong with nothing left to find it.
"""

from __future__ import annotations

import argparse
import dataclasses
import sqlite3
import sys

import requests

from pipeline import publish, schema, store, validate, vocab

#: The one column this pass may move. Named as a constant so the payload, the
#: clear list and the refusal messages cannot drift apart.
COLUMN = "funding_amount_usd"

#: Below this many rows a percentage is noise, so the share ceilings are not
#: applied at all. A test database with three rows must not be refused for
#: moving 33% of them.
MIN_ROWS = 200

#: Ceiling on the share of rows carrying a funding string that this pass may
#: touch. Measured 2026-07-30 against main: 12 of 3,254, or 0.37%. Five percent
#: is an order of magnitude above that. Exceeding it means the parser changed in
#: a way nobody described, and that is a person's call, not a run's.
MAX_SHARE = 0.05

#: Ceiling on the share of rows currently HOLDING a figure that would be
#: cleared. Deliberately tighter than MAX_SHARE. Measured: 5 of 3,196, or 0.16%.
#: A parser that starts refusing strings it used to read would empty the money
#: charts one queued run at a time, and unlike a wrong value a cleared one is
#: not restored by simply running the enrich pass again.
MAX_CLEAR_SHARE = 0.01

NOTE = ("funding_amount_usd re-derived: the stored figure was what "
        "vocab.parse_funding_usd made of this row's own funding_amount string "
        "before the multiplier and currency rules were corrected")

CLEAR_NOTE = ("funding_amount_usd removed: the parser no longer reads a figure "
              "from this row's funding_amount string, and an amount we cannot "
              "read is left out rather than guessed at")


class Unsafe(RuntimeError):
    """The derivation would move more of the column than a person has agreed
    to. Never raised for one row: it is always a statement about the parser."""


def current_rows(conn) -> list[dict]:
    """Every live row carrying a funding string, which is the whole population.

    `funding_amount` is the input; a row without one has nothing to re-derive
    from, and a row whose funding_amount_usd was somehow set without a string
    is not this pass's to invent an answer for.
    """
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 "
        "  AND funding_amount IS NOT NULL AND funding_amount <> '' "
        "ORDER BY row_id")]


def _stored_usd(row: dict):
    """The stored figure as an int, or None. SQLite will hand back '' from a
    text-typed write, and '' is not a number and not a figure."""
    value = row.get(COLUMN)
    if value is None or value == "":
        return None
    return int(value)


def rederivation(row: dict):
    """(stored, what the parser says today), or None if they already agree.

    The single place the comparison is made. Everything else — the worklist, the
    printed table, the payload, the tests — reads this, so there is no second
    opinion about what "disagrees" means.
    """
    stored = _stored_usd(row)
    parsed = vocab.parse_funding_usd(row.get("funding_amount") or "")
    if parsed == stored:
        return None
    return stored, parsed


def targets(rows: list[dict], *, force: bool = False) -> list[tuple[dict, tuple]]:
    """Live rows whose stored figure disagrees with the current parser.

    Derived, never listed. A hand-typed set of row ids would be stale the next
    time the multiplier vocabulary widens, which is the exact event this script
    exists to follow.
    """
    found = [(row, change) for row in rows if (change := rederivation(row))]
    if not found or len(rows) < MIN_ROWS:
        return found

    share = len(found) / len(rows)
    if share > MAX_SHARE and not force:
        raise Unsafe(
            f"{len(found)} of {len(rows)} rows ({share:.1%}) disagree with "
            f"vocab.parse_funding_usd, over the {MAX_SHARE:.0%} ceiling. "
            f"0.37% was measured. Read pipeline/vocab.py before re-running, and "
            f"pass --force only if the parser really did change that much.")

    holding = sum(1 for row in rows if _stored_usd(row) is not None)
    clearing = sum(1 for _row, (stored, parsed) in found
                   if parsed is None and stored is not None)
    if holding >= MIN_ROWS and clearing / holding > MAX_CLEAR_SHARE and not force:
        raise Unsafe(
            f"{clearing} of the {holding} rows holding a figure "
            f"({clearing / holding:.1%}) would be CLEARED, over the "
            f"{MAX_CLEAR_SHARE:.0%} ceiling. 0.16% was measured. A parser that "
            f"has started refusing strings it used to read empties the money "
            f"charts, and a cleared row is not restored by re-running enrich. "
            f"Read pipeline/vocab.py, then --force if it is right.")
    return found


_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def corrected_signal(row: dict, parsed) -> validate.Signal:
    """The stored row with its dollar figure re-derived and nothing else moved.

    content_hash is asserted UNCHANGED rather than recomputed. funding_amount_usd
    is not an input to it, and if that ever stops being true this whole pass is
    the wrong shape: a moved fingerprint needs withdraw-and-republish, and the
    in-place site update below would leave the live row disagreeing with its own
    hash.
    """
    signal = validate.Signal(**{name: row[name] for name in _FIELDS})
    setattr(signal, COLUMN, parsed)

    rehashed = validate.content_hash(
        signal.company_key, signal.pillar, signal.published_date,
        signal.headline, signal.source_name)
    if rehashed != row["content_hash"]:
        raise Unsafe(
            f"correcting {row['company']} would move its content_hash "
            f"({row['content_hash']} -> {rehashed}), so it cannot be corrected "
            f"in place on the site. That means content_hash now reads "
            f"{COLUMN}, and this whole pass needs rewriting as a "
            f"withdraw-and-republish.")
    return signal


def push_amount(row: dict, parsed, *, session=None) -> dict:
    """Update the live row's figure, or explicitly clear it.

    /enrich is an UPDATE keyed on (content_hash, is_current), which is what
    makes it idempotent here: a second run sends the value the row already
    holds and the server reports it as unchanged rather than as an error.

    The two branches are NOT symmetrical, and that asymmetry is the plugin's
    deliberate guarantee. A present value is written; an absent one is ignored,
    so a blank can never erase a figure by accident. Erasing has to be asked for
    by name, in `clear`, and only for tit_clearable_columns().
    """
    site, key = publish._config()
    payload = {"content_hash": row["content_hash"]}
    if parsed is None:
        payload["clear"] = [COLUMN]
    else:
        payload[COLUMN] = parsed

    poster = session or requests
    resp = poster.post(
        f"{site}/wp-json/talent/v1/enrich",
        json={"rows": [payload]},
        headers={"X-Talent-API-Key": key, "User-Agent": publish.USER_AGENT,
                 "Content-Type": "application/json"},
        timeout=publish.TIMEOUT,
    )
    if resp.status_code >= 400:
        raise publish.PublishError(f"{resp.status_code}: {resp.text[:300]}")
    result = resp.json() or {}
    if result.get("errors"):
        # 'not clearable: funding_amount_usd' is the shape that matters here: it
        # means the deployed plugin predates tit_clearable_columns(), so the
        # rows that must lose their figure cannot. That is the whole pass being
        # impossible, not one row failing, and it must not be counted and
        # skipped past.
        raise publish.PublishError(f"/enrich reported {result['errors']}")
    return result


def reissue(conn, row: dict, change: tuple, *, push=push_amount) -> dict:
    """Correct the site, then append the revision. In that order, on purpose.

    A row is a target while its LIVE revision holds the stale figure, so the
    local revision is the only record that the site was corrected. Written
    first, a run killed between the two steps leaves the page wrong with nothing
    left in the database to find it. Written second, the worst a kill costs is
    one repeated UPDATE of a value the site already holds.
    """
    _stored, parsed = change
    result = {}
    if row["published_at"]:
        result = push(row, parsed)

    store.revise(conn, row["signal_id"], corrected_signal(row, parsed),
                 CLEAR_NOTE if parsed is None else NOTE)

    # The site's live row now holds this revision's figure, so the revision is
    # published. Left NULL it would be offered to publish() every run, come back
    # 'duplicate' on a hash the site has already seen, and be marked published
    # anyway — the same outcome after a pointless round trip that reads like a
    # lost row in the log.
    if row["published_at"]:
        conn.execute(
            "UPDATE signals SET published_at = ? WHERE signal_id = ? AND is_current = 1",
            (row["published_at"], row["signal_id"]))
    conn.commit()
    return result


def _money(value) -> str:
    return "(none)" if value is None else f"{int(value):,}"


def _describe(row: dict, change: tuple) -> str:
    stored, parsed = change
    live = "on the live site" if row["published_at"] else "never published"
    verb = "CLEARED" if parsed is None else "re-derived"
    return (f"  [{row['collector']}] {row['company']}  (row {row['row_id']}, "
            f"{live})\n"
            f"                {(row['headline'] or '')[:78]}\n"
            f"                {row['source_url']}\n"
            f"                {row['funding_amount']!r}\n"
            f"                {verb:<10} {_money(stored):>18} -> "
            f"{_money(parsed)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Dry run is the DEFAULT, matching correct_city_country.py. This edits a
    # figure that is summed into the headline money total on a live page:
    # cheap to read, not cheap to get wrong.
    parser.add_argument("--apply", action="store_true",
                        help="write. Without this, nothing is written anywhere.")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicit no-op; the default already writes nothing")
    parser.add_argument("--limit", type=int,
                        help="stop after N rows (for a first pass)")
    parser.add_argument("--force", action="store_true",
                        help="proceed past the share ceilings, having read the table")
    args = parser.parse_args(argv)

    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply contradict each other")

    conn = schema.connect()
    rows = current_rows(conn)
    holding = sum(1 for row in rows if _stored_usd(row) is not None)
    print(f"{len(rows)} live rows carry a funding string; {holding} hold a "
          f"dollar figure")

    try:
        found = targets(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    if not found:
        print(f"\nNothing to correct: every stored {COLUMN} is what "
              f"vocab.parse_funding_usd makes of its own string today.")
        return 0

    # A quarantined row must not have a figure pushed to it, for the same reason
    # publish.enrich_published() filters them: this is a path that moves the
    # headline money total. Held back rather than dropped from the report —
    # saying nothing would read as "the column is now entirely re-derived".
    try:
        guard = publish._guard(conn, dry_run=not args.apply)
    except publish.PublishError as exc:
        # An aggregate finding means the published set does not add up, and the
        # house rule is that nothing is sent while that is true. Re-deriving the
        # money column is precisely the wrong thing to do on top of it.
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    quarantined = guard["quarantined"]
    held = [(row, ch) for row, ch in found
            if row["published_at"] and row["content_hash"] in quarantined]
    held_hashes = {row["content_hash"] for row, _ch in held}
    found = [(row, ch) for row, ch in found
             if row["content_hash"] not in held_hashes]

    if args.limit:
        found = found[:args.limit]

    cleared = sum(1 for _row, (_s, parsed) in found if parsed is None)
    published = sum(1 for row, _ in found if row["published_at"])
    delta = sum((parsed or 0) - (stored or 0) for _row, (stored, parsed) in found)
    print(f"\n  rows to re-derive               {len(found):>5}")
    print(f"  of those, CLEARED to no figure  {cleared:>5}   "
          f"(the parser will not guess, so neither does the page)")
    print(f"  of those, live on the site      {published:>5}   "
          f"(each is updated in place through /enrich first)")
    print(f"  net change to the money total   {delta:>+21,} USD")
    print()
    for row, change in found:
        print(_describe(row, change))
        print()

    if held:
        print(f"  {len(held)} row(s) are QUARANTINED by guardrails.py and are not "
              f"touched.")
        print("  Answer them first:  python3 guardrails.py\n")

    if cleared:
        print(f"  Clearing goes through /enrich's `clear` list, allowed by")
        print(f"  tit_clearable_columns() in includes/api.php. A deployed plugin")
        print(f"  without it answers 'not clearable' and this run fails loudly.")

    if not args.apply:
        print("\ndry run: nothing written. Add --apply to write.")
        return 0

    failures = 0
    print(f"\ncorrecting and re-issuing {len(found)} rows ...")
    for row, change in found:
        stored, parsed = change
        try:
            reissue(conn, row, change)
            print(f"  re-issued {row['company'][:40]:<40} "
                  f"{_money(stored):>18} -> {_money(parsed)}")
        except (publish.PublishError, requests.RequestException, Unsafe) as exc:
            failures += 1
            print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)

    if failures:
        print(f"\n{failures} row(s) still hold a figure the parser disagrees "
              f"with. The next run finds them again.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
