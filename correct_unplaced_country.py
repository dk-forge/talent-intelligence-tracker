#!/usr/bin/env python3
"""Give a country to the rows written before pipeline/place_fallback.py.

    python3 correct_unplaced_country.py            # DRY RUN, the default
    python3 correct_unplaced_country.py --apply    # writes

Queue it, never dispatch it (CLAUDE.md, "Never dispatch a database writer
directly"):

    gh workflow run drain-writers.yml -f enqueue=correct-unplaced-country.yml \
      -f inputs_json='{"dry_run":"false"}' \
      -f reason='place the rows #311 measured'

WHAT AND WHY
------------
PR #311 made build_signal place an unplaced row only where two independent
signals agree and nothing on the row disagrees (place_fallback.decide). It
measured the rows published 2026-11-02..09: 449 with no country, 58 the rule
would place. Those rows were written before the rule and stay blank until a
writer re-issues them. This is that writer.

THE SAME DECISION, NOT A SECOND ONE. Every target is chosen by calling
place_fallback.decide with the inputs build_signal gives it (the headline, its
detected language, the source URL, the row's hq_country). One signal alone
never places a row, and any disagreement leaves it blank; the dry run names
each blank row's reason so a reader can see the refusal held.

THE WINDOW IS THE MEASURED ONE by default. Older unplaced rows exist in far
larger numbers; widening the pass is a separate, reviewed run (--since).

HOW IT WRITES: correct_city_country's path, unchanged. /correct updates the
live row's country and region in place (country is not an input to
content_hash, asserted), then store.revise() appends the revision locally, so
the original survives at is_current = 0. Idempotent: a placed row is no longer
a target.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import requests

from correct_city_country import (  # noqa: F401  (re-exported for tests)
    PluginTooOld,
    Unsafe,
    corrected_signal,
    push_place,
)
from correct_city_country import reissue as _reissue
from pipeline import language, place_fallback, publish, schema, validate

#: The window PR #311 measured.
SINCE = "2026-11-02"
UNTIL = "2026-11-09"

#: A ceiling, not a share. 58 measured; twice that means something moved.
MAX_ROWS = 120

NOTE = ("country backfilled by place_fallback (PR #311): two independent "
        "signals agree and nothing on the row disagrees")


def current_rows(conn, since: str = SINCE, until: str = UNTIL) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 "
        "AND (country IS NULL OR country = '') "
        "AND published_date BETWEEN ? AND ? ORDER BY row_id", (since, until))]


def headline_language(headline: str | None) -> str | None:
    return language.detect(headline or "")[0]


def evidence(row: dict) -> dict:
    """The four inputs decide reads, as ISO2 codes, for the dry run."""
    named = place_fallback.headline_countries(row.get("headline") or "")
    return {
        "publisher": place_fallback.publisher_country(row.get("source_url") or ""),
        "language": place_fallback.language_country(headline_language(row.get("headline"))),
        "headline": ",".join(sorted(named)) or None,
        "hq": (row.get("hq_country") or "").strip().upper() or None,
    }


def why_blank(ev: dict) -> str:
    if ev["headline"] and "," in ev["headline"]:
        return "headline names more than one country"
    present = {v for v in ev.values() if v}
    if not present:
        return "no signal"
    if len(present) > 1:
        return "signals disagree"
    if sum(1 for v in ev.values() if v) == 1:
        return "one signal only, not corroborated"
    # Two signals naming the same country that decide still refuses: the
    # language and headline agree only with no publisher, or hq alone with lang.
    return "signals agree but not in a pairing the rule accepts"


def worklist(rows: list[dict]):
    """(placed: [(row, fixed, evidence)], left: [(row, reason, evidence)])."""
    placed, left = [], []
    for row in rows:
        ev = evidence(row)
        country = place_fallback.decide(
            source_url=row.get("source_url") or "", headline=row.get("headline") or "",
            language=headline_language(row.get("headline")),
            hq_country=row.get("hq_country"))
        if country:
            fixed = {"country": country}
            if not row.get("region"):
                region = validate._region_for_country(country)
                if region:
                    fixed["region"] = region
            placed.append((row, fixed, ev))
        else:
            left.append((row, why_blank(ev), ev))
    if len(placed) > MAX_ROWS:
        raise Unsafe(f"{len(placed)} rows would be placed, over the {MAX_ROWS}-row "
                     f"ceiling (58 were measured). Read pipeline/place_fallback.py "
                     f"before re-running.")
    return placed, left


def reissue(conn, row: dict, fixed: dict, *, push=push_place) -> dict:
    return _reissue(conn, row, fixed, push=push, note=NOTE)


def _ev(ev: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in ev.items() if v) or "none"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write")
    parser.add_argument("--since", default=SINCE)
    parser.add_argument("--until", default=UNTIL)
    args = parser.parse_args(argv)

    conn = schema.connect()
    rows = current_rows(conn, args.since, args.until)
    print(f"{len(rows)} current rows with no country, published "
          f"{args.since}..{args.until}")
    try:
        placed, left = worklist(rows)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    print(f"\nPLACED ({len(placed)}): row_id | company | country | signals | source")
    for row, fixed, ev in placed:
        print(f"  {row['row_id']} | {row['company'][:40]} | {fixed['country']!r} | "
              f"{_ev(ev)} | {row['source_url'][:80]}")

    reasons: dict[str, int] = {}
    for _row, reason, _ev_ in left:
        reasons[reason] = reasons.get(reason, 0) + 1
    print(f"\nLEFT BLANK ({len(left)}):")
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {reason}")
    for row, reason, ev in left:
        print(f"  {row['row_id']} | {row['company'][:40]} | {reason} | {_ev(ev)}")

    if not args.apply:
        print("\ndry run: nothing written. Add --apply to write.")
        return 0

    failures = 0
    for row, fixed, _ev_ in placed:
        try:
            reissue(conn, row, fixed)
            print(f"  placed {row['row_id']} {row['company'][:40]} -> {fixed['country']}")
        except PluginTooOld as exc:
            print(f"\nSTOPPING: {exc}", file=sys.stderr)
            return 2
        except (publish.PublishError, requests.RequestException, Unsafe) as exc:
            failures += 1
            print(f"  FAILED {row['row_id']} {row['company']}: {exc}", file=sys.stderr)
    print(f"\nplaced {len(placed) - failures} of {len(placed)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
