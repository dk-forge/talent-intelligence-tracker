#!/usr/bin/env python3
"""How many stored records the deterministic city scanner would newly place.

    python3 measure_city_placement.py              # the number, and the cities
    python3 measure_city_placement.py --examples 20
    python3 measure_city_placement.py --db path/to/talent_intel.db

READ-ONLY, always: the connection is opened `mode=ro` and there is no write
path in this file at all. A backfill is a separate, queued job for the owner to
run and read; this only says whether one is worth running.

WHY THE NUMBER IS SMALLER THAN IT LOOKS LIKE IT SHOULD BE, and read this
before quoting it: **`raw_text` is not persisted.** The pipeline reads a
candidate's full headline + teaser, classifies it, and stores the RESULT. What
survives in `signals` is `headline`, `summary` and `talent_readthrough`. So the
sentence a story really did carry — "the Vilnius-based company will scale
across Europe" — is usually gone, and this measurement can only read the three
columns that remain:

  headline    the source's own line, and the only one of the three that is
              not prose somebody else wrote. The defensible number.
  summary     a restatement of the source. Close to the source, but written by
              the model, so a place appearing ONLY here is a place we cannot
              prove the source stated. Reported separately for that reason.
  readthrough the model's own sentence about consequences. Reported last and
              never counted as sourced: it is the least defensible of the
              three and exists here only to size what re-fetching would buy.

The consequence for the backfill: the honest ceiling on placing history from
what is already stored is the headline number. Placing the rest means
re-fetching the article, which is bandwidth rather than budget — no model call
is involved, because this scanner is free.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from pipeline import cheap_extract

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "talent_intel.db"

# The three readable columns, narrowest-evidence first. Each pass adds one
# column to the one before it, so the counts nest and the increments are what
# each extra column is worth.
#
# `sourced` is whether a backfill may STORE what the pass found. The
# read-through pass is measured and must never be stored: the sentences it
# matches read "the Houston-based food and beverage giant" and "a real estate
# firm based in San Francisco", and those are the model's own knowledge of
# where Sysco and Prologis are, not anything the 8-K said. That is precisely
# the inference this product may not make, and it is why the pass is printed
# with a refusal beside it rather than added to the total.
PASSES = (
    ("headline", ("headline",), True),
    ("headline + summary", ("headline", "summary"), True),
    ("headline + summary + read-through",
     ("headline", "summary", "talent_readthrough"), False),
)


def rows(db: Path):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT row_id, city, country, collector, headline, summary, "
            "       talent_readthrough, source_url "
            "FROM signals WHERE is_current = 1"
        ).fetchall()
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--examples", type=int, default=12,
                    help="how many newly placed rows to print in full")
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 2

    all_rows = rows(args.db)
    placed = [r for r in all_rows if (r["city"] or "").strip()]
    unplaced = [r for r in all_rows if not (r["city"] or "").strip()]

    print(f"current rows          {len(all_rows):>6}")
    print(f"  with a city         {len(placed):>6}  "
          f"({100 * len(placed) / len(all_rows):.1f}%, "
          f"{len({r['city'] for r in placed})} distinct cities)")
    print(f"  with no city        {len(unplaced):>6}  "
          f"({100 * len(unplaced) / len(all_rows):.1f}%)")
    print()

    seen: set[int] = set()
    examples: list[tuple] = []
    storable: list[tuple] = []
    for label, columns, sourced in PASSES:
        hits: list[tuple] = []
        for r in unplaced:
            hit = cheap_extract.stated_city(*[r[c] or "" for c in columns])
            if hit:
                hits.append((r, hit))
        cities = Counter(hit[0] for _r, hit in hits)
        new_rows = [(r, hit) for r, hit in hits if r["row_id"] not in seen]
        seen |= {r["row_id"] for r, _h in hits}
        examples.extend(new_rows)
        if sourced:
            storable = hits

        print(f"[{label}]" + ("" if sourced else "   NOT SOURCED — never store this"))
        print(f"  would newly place   {len(hits):>6} rows"
              f"   (+{len(new_rows)} over the pass above)")
        print(f"  distinct cities     {len(cities):>6}")
        by_collector = Counter(r["collector"] for r, _h in hits)
        if cities:
            print("  cities: " + ", ".join(
                f"{c} ({n})" for c, n in cities.most_common()))
            print("  collectors: " + ", ".join(
                f"{c} ({n})" for c, n in by_collector.most_common()))
        print()

    known = {r["city"] for r in placed}
    added = {hit[0] for _r, hit in storable} - known
    print(f"THE BACKFILL NUMBER: {len(storable)} rows, from sourced text only")
    print(f"cities that would be new to the database: {len(added)}"
          + (("  " + ", ".join(sorted(added))) if added else ""))
    print()

    # Precision, against the only second opinion available: the rows a model
    # already placed. A disagreement is either the scanner reading a place the
    # model missed, or the scanner being wrong — and either is worth a look
    # before a backfill runs.
    agree = disagree = silent = 0
    conflicts = []
    for r in placed:
        hit = cheap_extract.stated_city(r["headline"] or "", r["summary"] or "")
        if not hit:
            silent += 1
        elif hit[0] == r["city"]:
            agree += 1
        else:
            disagree += 1
            conflicts.append((r["city"], hit[0], r["headline"]))
    print(f"[against the {len(placed)} rows a model already placed]")
    print(f"  same city           {agree:>6}")
    print(f"  different city      {disagree:>6}")
    print(f"  scanner says nothing{silent:>6}   (declining is the design)")
    for stored, scanned, headline in conflicts[:10]:
        print(f"    stored {stored!r} vs scanned {scanned!r}: {headline[:90]}")
    print()

    if args.examples and examples:
        print(f"--- {min(args.examples, len(examples))} of "
              f"{len(examples)} newly placed rows, in full ---")
        for r, hit in examples[: args.examples]:
            print(f"\n  {hit[0]} ({hit[2]})   [{r['collector']}]")
            print(f"  headline: {r['headline']}")
            print(f"  summary : {(r['summary'] or '')[:220]}")
            print(f"  source  : {r['source_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
