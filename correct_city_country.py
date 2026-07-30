#!/usr/bin/env python3
"""Re-issue the rows whose stored country contradicts the city gazetteer.

    python3 correct_city_country.py            # DRY RUN — the default here
    python3 correct_city_country.py --apply    # writes

Queue it, never dispatch it (CLAUDE.md, "Never dispatch a database writer
directly"):

    gh workflow run drain-writers.yml -f enqueue=correct-city-country.yml \
      -f inputs_json='{"dry_run":"false"}' \
      -f reason='file the two Toronto rows under Canada'

WHAT IS WRONG
-------------
`vocab._CITY_ALIASES` mapped "toronto" to the UNITED STATES until it was
corrected, so `validate.build_signal` — which takes the country from the city
table and lets it outrank anything else, deliberately, because the table is
curated and the model's country string is not — wrote `country = 'US'` onto
every Toronto row collected before the fix. Two live rows carry it:

    Aptose Biosciences Inc.  8-K Item 5.02, published 2026-03-23
    Celestica Inc.           8-K Item 5.02, published 2026-03-24

Both are Toronto-headquartered filers whose rows are on the live site right now,
filed under the US country filter, and Celestica's is in the US state facet's
denominator. tests/test_city_gazetteer.py names them as the one accepted
disagreement between the table and the data, and says a backfill is the owner's
call. This is that backfill.

The same table decides `hq_country` (build_signal reads the HQ city through it
too), so both rows also say the employer is headquartered in Toronto, US. That
moves in the same pass, for exactly the same reason.

WHY THE WORKLIST IS DERIVED AND THEN CHECKED AGAINST A SHAPE
------------------------------------------------------------
Targets are found by asking the CURRENT vocabulary about every live row, the way
correct_company_key.py finds stale employer keys: a hand-typed list of two
row_ids would go stale the next time somebody corrects a city, and would not
have found these two in the first place.

But a derived worklist that grows silently is how a correction pass turns into
re-filing history. So every target must match ACCEPTED_SHAPES — city Toronto,
stored country US, corrected country CA — and anything else stops the run and
gets named. A vocabulary edit that contradicts a third city is a decision for a
person, not a row this script may quietly move.

WHY A REVISION LOCALLY AND AN IN-PLACE CORRECTION ON THE SITE
-------------------------------------------------------------
Locally: `store.revise()`, so the original survives at `is_current = 0` and
"what did we publish on 2026-07-28" stays answerable. Nothing is overwritten.

On the site: `/correct`, which UPDATEs the live row's fields. That is not a
second opinion about the revision rule, it is the only door that exists.
`country` is not an input to `content_hash` — md5(company_key|pillar|
published_date|normalised_headline) — so the corrected revision carries the SAME
hash as the row it replaces, and `tit_insert_signal()` refuses any hash it has
already seen at ANY revision. A withdraw-then-republish, which is what
correct_sec_pillar.py and correct_company_key.py do, would therefore take these
rows off the site and get 'retracted' back when it tried to put them there
again: two real records silently deleted from a live page, reported as a
duplicate. Checked in includes/db.php, not assumed.

    THE SITE CANNOT ACCEPT THIS YET. tit_correctable_columns() in
    includes/api.php allows exactly signal_direction and talent_readthrough, so
    the geography fields are dropped server-side and the response says
    skipped_no_fields. This script REFUSES to revise anything locally when that
    happens — a corrected database in front of an uncorrected page is a
    divergence nobody would notice — and prints what has to change:

        tit_correctable_columns() must return 'city', 'region', 'country'
        as well, then bump the plugin version and deploy.

`hq_country` needs none of that: it is already in `tit_enrichable_columns()`
(looked up, never claimed by a source), so the existing `enrich.yml` pass
carries the corrected value to the site on its own once it is stored here.

Bounded, idempotent, and safe to interrupt. The worklist is derived from what is
stored, so a row already revised is not a target; the site correction is an
UPDATE to the value it already holds on a second run; and the remote step
happens BEFORE the local revision, so a run killed between them retries both
rather than leaving the page wrong with nothing left to find it.
"""

from __future__ import annotations

import argparse
import dataclasses
import sqlite3
import sys

import requests

from pipeline import publish, schema, store, validate, vocab

#: (stored city, stored country) -> the country the gazetteer says today.
#:
#: The precise shape this pass is allowed to touch. Anything else the derivation
#: finds is refused and named: a second city contradicting the table is either a
#: new defect or a new decision, and neither is this script's to make.
ACCEPTED_SHAPES = {("Toronto", "US"): "CA"}

#: A ceiling, not a share. Measured 2026-07-29 against the committed database:
#: 2 of 15,711 live rows, and 5 rows carry the HQ half. Twenty-five is an order
#: of magnitude above that and still small enough that every affected row is
#: printed and read by a person before anything moves.
MAX_ROWS = 25

NOTE = ("country corrected: the city gazetteer placed Toronto in the United "
        "States when this row was written, so the record was filed under the "
        "wrong country")

#: The fields this pass may move, and the only ones it does. `city` is already
#: right on every target — it is the country derived FROM the city that was
#: wrong — and `region` is checked rather than assumed because the table carries
#: it alongside the country code.
PLACE_FIELDS = ("country", "region", "state", "hq_country")

#: What the live site can be told about a sourced location, and where that is
#: decided. Kept as a constant so the refusal below can name the exact function.
SITE_ALLOWLIST = "tit_correctable_columns() in includes/api.php"

#: Of PLACE_FIELDS, the ones that are the SOURCE's claim about where the roles
#: are, and therefore go through /correct. hq_country is the employer's
#: headquarters — looked up, not claimed — and is already enrichable, so
#: publish.enrich_published() carries it with no plugin change at all.
CORRECTABLE_ON_SITE = ("city", "region", "country")


class Unsafe(RuntimeError):
    """The derivation found something outside the shape this pass may touch."""


class PluginTooOld(RuntimeError):
    """The live site dropped the geography fields, so its allowlist has not been
    extended yet. Refusing beats a corrected database behind a wrong page."""


def current_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 ORDER BY row_id")]


def place_correction(row: dict) -> dict | None:
    """The geography this row would be given today, or None if nothing moves.

    Mirrors validate.build_signal: the city table decides the country and the
    region, the state facet exists only inside the US, and the HQ city is read
    through the same table. Anything the table cannot read is left alone — an
    unreadable city is a vocabulary question, not a country to invent.

    The TRIGGER is the sourced country disagreeing with the city, and only that.
    A row whose job location is already right but whose hq_country is not is a
    different and much larger job — hq_country is enrichable, so the identity and
    enrich passes own it — and pulling it in here would turn a two-row correction
    into an open-ended one. Those rows are counted and named by hq_only_rows().
    """
    hit = vocab.normalize_city(row.get("city") or "")
    if not hit or hit[0] != (row.get("city") or ""):
        return None

    city, region, country = hit
    if country == row.get("country"):
        return None

    fixed: dict = {"country": country}
    if region != row.get("region"):
        fixed["region"] = region

    # The state facet is meaningful only inside the US, so a row leaving the US
    # must not keep one. build_signal only ever sets it when country == 'US'.
    state = vocab.state_for_city(city) if country == "US" else None
    if state != row.get("state"):
        fixed["state"] = state

    # The employer's headquarters, from the same table and wrong for the same
    # reason. Only when the HQ city is one the table can read: where hq_city is
    # empty, hq_country came from the model's country string instead and this
    # pass has nothing to say about it.
    hq_hit = vocab.normalize_city(row.get("hq_city") or "")
    if hq_hit and hq_hit[0] == (row.get("hq_city") or ""):
        if hq_hit[2] != row.get("hq_country"):
            fixed["hq_country"] = hq_hit[2]

    return fixed


def hq_only_rows(rows: list[dict]) -> list[dict]:
    """Rows whose HQ country contradicts the table but whose job location does
    not. Reported, never touched: see place_correction."""
    out = []
    for row in rows:
        if place_correction(row):
            continue
        hit = vocab.normalize_city(row.get("hq_city") or "")
        if hit and hit[0] == (row.get("hq_city") or "") and hit[2] != row.get("hq_country"):
            out.append(row)
    return out


def targets(rows: list[dict]) -> list[tuple[dict, dict]]:
    """Live rows the gazetteer contradicts, shape-checked and bounded.

    Raises Unsafe rather than returning a longer list, because every way this
    could find more rows than expected is a reason for a person to look: a
    vocabulary edit nobody mentioned, a collector writing a country the table
    disagrees with, or this function drifting from build_signal.
    """
    found = [(row, fix) for row in rows if (fix := place_correction(row))]

    unexpected = [(row, fix) for row, fix in found
                  if ACCEPTED_SHAPES.get((row.get("city"), row.get("country")))
                  != fix["country"]]
    if unexpected:
        shapes = sorted({
            f"{row.get('city')!r} {row.get('country')!r} -> {fix['country']!r}"
            for row, fix in unexpected})
        raise Unsafe(
            f"{len(unexpected)} row(s) disagree with the city table in a shape "
            f"this pass does not cover: {', '.join(shapes)}. Expected only "
            f"{sorted(f'{c!r} {a!r} -> {b!r}' for (c, a), b in ACCEPTED_SHAPES.items())}. "
            f"Decide what the right country is, add the shape to "
            f"ACCEPTED_SHAPES, and run the dry run again.")

    if len(found) > MAX_ROWS:
        raise Unsafe(
            f"{len(found)} rows would be re-filed, over the {MAX_ROWS}-row "
            f"ceiling. Two were measured. Something has changed in "
            f"pipeline/vocab.py — read it before re-running.")
    return found


_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def corrected_signal(row: dict, fixed: dict) -> validate.Signal:
    """The stored row with its geography corrected and nothing else touched.

    content_hash is asserted UNCHANGED rather than recomputed. None of these
    columns is an input to it, and if that ever stops being true this pass is
    the wrong shape entirely: a moved fingerprint needs the withdraw-and-
    republish path, and the in-place site correction below would leave the live
    row disagreeing with its own hash.
    """
    signal = validate.Signal(**{name: row[name] for name in _FIELDS})
    for field, value in fixed.items():
        setattr(signal, field, value)

    rehashed = validate.content_hash(
        signal.company_key, signal.pillar, signal.published_date,
        signal.headline, signal.source_name)
    if rehashed != row["content_hash"]:
        raise Unsafe(
            f"correcting {row['company']} would move its content_hash "
            f"({row['content_hash']} -> {rehashed}), so it cannot be corrected "
            f"in place on the site. That means content_hash now reads one of "
            f"{PLACE_FIELDS}, and this whole pass needs rewriting as a "
            f"withdraw-and-republish.")
    return signal


def push_place(row: dict, fixed: dict, *, session=None) -> dict:
    """Correct the live row's geography in place, or refuse.

    /correct is an UPDATE keyed on (content_hash, collector, is_current), which
    is what makes it safe here and idempotent: a second run sends the values the
    row already holds and the server reports it as unchanged.

    `skipped_no_fields` is the server saying its allowlist dropped everything we
    sent. That is not a failure to correct one row, it is the whole pass being
    impossible against the deployed plugin, so it raises rather than counting.
    """
    site, key = publish._config()
    payload = {"content_hash": row["content_hash"]}
    for field in CORRECTABLE_ON_SITE:
        value = fixed.get(field, row.get(field))
        if value:
            payload[field] = value

    poster = session or requests
    resp = poster.post(
        f"{site}/wp-json/talent/v1/correct",
        json={"collector": row["collector"], "rows": [payload]},
        headers={"X-Talent-API-Key": key, "User-Agent": publish.USER_AGENT,
                 "Content-Type": "application/json"},
        timeout=publish.TIMEOUT,
    )
    if resp.status_code >= 400:
        raise publish.PublishError(f"{resp.status_code}: {resp.text[:300]}")
    result = resp.json() or {}
    if result.get("errors"):
        raise publish.PublishError(f"/correct reported {result['errors']}")
    if int(result.get("skipped_no_fields") or 0):
        raise PluginTooOld(
            f"the live site dropped every field this correction sends, so "
            f"{SITE_ALLOWLIST} still allows only signal_direction and "
            f"talent_readthrough. Add {', '.join(repr(c) for c in CORRECTABLE_ON_SITE)} "
            f"to it, bump the plugin version, deploy, then run this again. "
            f"Nothing has been written locally.")
    return result


def reissue(conn, row: dict, fixed: dict, *, push=push_place) -> dict:
    """Correct the site, then append the revision. In that order, on purpose.

    A row is a target while its LIVE revision carries the wrong country, so the
    local revision is the only record that the site was corrected. Written
    first, a run killed between the two steps would leave the page wrong with
    nothing left in the database to find it. Written second, the worst a kill
    costs is one repeated UPDATE of a value the site already holds.
    """
    result = {}
    if row["published_at"]:
        result = push(row, fixed)

    store.revise(conn, row["signal_id"], corrected_signal(row, fixed), NOTE)

    # The site's live row now holds this revision's geography, so the revision
    # is published. Left NULL it would be offered to publish() every run, come
    # back 'duplicate' on a hash the site has already seen, and be marked
    # published anyway — the same outcome after a pointless round trip that
    # reads like a lost row in the log.
    if row["published_at"]:
        conn.execute(
            "UPDATE signals SET published_at = ? WHERE signal_id = ? AND is_current = 1",
            (row["published_at"], row["signal_id"]))
    conn.commit()
    return result


def _describe(row: dict, fixed: dict) -> str:
    moves = "\n".join(
        f"                {field:<11} {row.get(field)!r} -> {fixed[field]!r}"
        for field in PLACE_FIELDS if field in fixed)
    live = "on the live site" if row["published_at"] else "never published"
    return (f"  [{row['collector']}] {row['company']}  (row {row['row_id']}, "
            f"{live})\n"
            f"                {row['headline'][:78]}\n"
            f"                {row['source_url']}\n"
            f"{moves}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Dry run is the DEFAULT here, unlike the older correction scripts. This one
    # edits the geography of rows that are live on the site, and the two numbers
    # it moves are the country filter and the state facet: cheap to read, not
    # cheap to get wrong.
    parser.add_argument("--apply", action="store_true",
                        help="write. Without this, nothing is written anywhere.")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicit no-op; the default already writes nothing")
    args = parser.parse_args(argv)

    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply contradict each other")

    conn = schema.connect()
    rows = current_rows(conn)
    print(f"{len(rows)} live rows")

    try:
        found = targets(rows)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    if not found:
        print("\nNothing to correct: every stored city agrees with the table.")
        return 0

    published = sum(1 for row, _ in found if row["published_at"])
    print(f"\n  rows to re-issue                {len(found):>5}")
    print(f"  of those, live on the site      {published:>5}   "
          f"(each needs its geography corrected in place first)")
    print()
    for row, fixed in found:
        print(_describe(row, fixed))
        print()

    # Named in the dry run rather than discovered on the real one. Both halves
    # of this are things a reader of the output needs before saying yes.
    if published:
        print(f"  The site correction goes through /correct, whose allowlist is")
        print(f"  {SITE_ALLOWLIST}. If it still allows only signal_direction and")
        print(f"  talent_readthrough, a real run refuses and writes nothing.")
    hq = sum(1 for _row, fixed in found if "hq_country" in fixed)
    if hq:
        print(f"\n  {hq} row(s) also move hq_country. That column is already")
        print("  enrichable, so enrich.yml carries it to the site by itself.")

    # Named, not corrected. Their job location is right, so they are not this
    # pass's rows; saying nothing about them would leave a reader of this output
    # thinking the gazetteer defect is now entirely gone.
    untouched = hq_only_rows(rows)
    if untouched:
        print(f"\n  {len(untouched)} further row(s) carry an hq_country the table "
              f"contradicts")
        print("  while their job location is already right. OUT OF SCOPE here:")
        for row in untouched[:6]:
            print(f"      {row['company'][:40]:<40} hq {row['hq_city']} "
                  f"{row['hq_country']!r}")
        if len(untouched) > 6:
            print(f"      ... and {len(untouched) - 6} more")
        print("  hq_country is enrichable and pipeline/identity.py owns it.")

    if not args.apply:
        print("\ndry run: nothing written. Add --apply to write.")
        return 0

    failures = 0
    print(f"\ncorrecting and re-issuing {len(found)} rows ...")
    for row, fixed in found:
        try:
            reissue(conn, row, fixed)
            print(f"  re-issued {row['company'][:44]:<44} "
                  f"{row.get('country')!r} -> {fixed['country']!r}")
        except PluginTooOld as exc:
            # Not one row's problem. Every remaining row would fail the same
            # way, and continuing would spend the rest of the run proving it.
            print(f"\nSTOPPING: {exc}", file=sys.stderr)
            return 2
        except (publish.PublishError, requests.RequestException, Unsafe) as exc:
            failures += 1
            print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)

    if failures:
        print(f"\n{failures} row(s) were not corrected and are still filed under "
              f"the wrong country. The next run finds them again.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
