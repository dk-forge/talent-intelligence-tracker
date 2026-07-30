#!/usr/bin/env python3
"""Does ranking the read budget actually change the mix? Measure, do not assume.

    python3 -m analysis.ranking.measure --stored           # offline, no network
    python3 -m analysis.ranking.measure --live --feeds 120  # real candidate set

MEASUREMENT, NEVER COLLECTION. Nothing here writes a row, marks a URL seen,
calls a model or costs a cent. `--live` makes read-only HTTP requests to public
RSS feeds through the collector's own path; `--stored` makes none at all.

THE LIMITATION, FIRST, BECAUSE IT DECIDES HOW TO READ EVERYTHING BELOW
---------------------------------------------------------------------
**No real candidate set was ever captured, so none can be replayed.** `raw_text`
is not persisted, and a candidate that was gate-rejected or budget-deferred
leaves a bare URL in `seen_urls` with no text and no reason. The rejection audit
hit the same wall and printed a zero rather than an estimate; the same honesty
applies here. So there are two populations available and neither is the run that
motivated the change:

  --stored  the rows we already hold, in `captured_at` order. Real text, real
            publishers, real arrival order. But they are the rows that STORED, so
            this measures which of the eventual winners a capped run would have
            reached — not the yield on the ones it rejected. And the
            "country holds nothing" signal is circular on this population by
            construction (every row's own country holds at least that row), so
            it is excluded here and breadth is reported instead.

  --live    a real fetch through `national_press`, prefiltered exactly as a run
            prefilters. This is a genuine candidate set: the same items, in the
            same order, that a run would have handed the gate. It costs nothing
            but politeness, and it is the number to trust.

WHAT IS REPORTED, AND WHY THESE THREE
-------------------------------------
The defect being addressed is concentration, measured: 15,140 of 15,711 current
rows are US or GB, 57 of ~200 countries hold any row at all, and it is not a feed
problem — the editions are swept and 141 countries were reached by one real
`national_press` run. So:

  us_gb_share       of the first N read. The concentration itself.
  countries         distinct countries in the first N. Breadth bought.
  empty_countries   first-N candidates from countries holding zero rows. The
                    thing ranking is FOR. Only meaningful on --live.

If these do not move, say so and revert the ordering rather than keeping a change
that only feels better.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import candidate_rank, classify, prefilter, schema  # noqa: E402

NEWS_COLLECTORS = ("google_news", "gdelt", "national_press")


def _mix(items: list[dict], context: candidate_rank.Context, cut: int) -> dict:
    head = items[:cut]
    countries = [candidate_rank.candidate_country(i) for i in head]
    named = [c for c in countries if c]
    return {
        "n": len(head),
        "us_gb": sum(1 for c in named if c in ("US", "GB")),
        "countries": len(set(named)),
        "empty": sum(1 for c in named if context.country_rows(c) == 0),
        "thin": sum(1 for c in named if context.country_rows(c) is not None
                    and 0 < context.country_rows(c) < candidate_rank.COUNTRY_THIN_ROWS),
        "no_hint": len(head) - len(named),
    }


def _report(label: str, items: list[dict], context: candidate_rank.Context,
            cuts: list[int], *, circular_empty: bool) -> None:
    print(f"\n{label}: {len(items)} candidates")
    if not items:
        return
    ordered = candidate_rank.rank(items, context)
    assert sorted(map(id, ordered)) == sorted(map(id, items)), \
        "rank() must be a permutation"

    header = f"{'cap':>6}  {'order':<8} {'US/GB':>12}  {'countries':>9}"
    if not circular_empty:
        header += f"  {'zero-row':>8}"
    header += f"  {'thin':>5}  {'no hint':>7}"
    print(header)
    for cut in cuts:
        rows = []
        for name, population in (("arrival", items), ("ranked", ordered)):
            m = _mix(population, context, cut)
            share = (100.0 * m["us_gb"] / m["n"]) if m["n"] else 0.0
            line = (f"{cut:>6}  {name:<8} {m['us_gb']:>5} ({share:>4.1f}%)  "
                    f"{m['countries']:>9}")
            if not circular_empty:
                line += f"  {m['empty']:>8}"
            line += f"  {m['thin']:>5}  {m['no_hint']:>7}"
            rows.append(line)
        for line in rows:
            print(line)
    if circular_empty:
        print("  (the zero-row column is omitted: on stored rows every row's own "
              "country holds at least that row, so the signal is circular here)")


def stored_population(conn) -> list[dict]:
    """Rows we hold, reconstructed into candidate shape, in arrival order.

    `source_country` comes from the catalogue row matching the stored
    `source_name`, which is precisely what the collector puts on a live item, so
    the country hint is the real one rather than a back-derivation from the
    answer.
    """
    import csv

    from source_registry import CATALOGUE_CSV

    country_by_name = {}
    with CATALOGUE_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            if name:
                country_by_name.setdefault(name, (row.get("country") or "").strip())

    placeholders = ", ".join("?" for _ in NEWS_COLLECTORS)
    rows = conn.execute(
        f"""SELECT headline, summary, source_name, collector
              FROM signals
             WHERE is_current = 1 AND collector IN ({placeholders})
             ORDER BY captured_at ASC, row_id ASC""", NEWS_COLLECTORS).fetchall()
    out = []
    for row in rows:
        out.append({
            "raw_text": f"{row['headline']}\n\n{row['summary'] or ''}".strip(),
            "headline": row["headline"],
            "source_name": row["source_name"],
            "collector": row["collector"],
            "source_country": country_by_name.get(row["source_name"] or "", ""),
        })
    return out


def live_population(feeds: int) -> list[dict]:
    """A real candidate set: fetch, then the same free prefilter a run applies.

    Feeds are taken one per country in turn before any country contributes twice,
    for the same reason `run_collect.fair_share` exists — a head slice of the
    catalogue would be 43 US feeds and tell us nothing about breadth.
    """
    from collectors import national_press

    catalogue = national_press.load_feeds()
    buckets: dict[str, list] = {}
    for feed in catalogue:
        buckets.setdefault(feed.country or "?", []).append(feed)
    chosen, depth = [], 0
    while len(chosen) < feeds:
        added = False
        for bucket in buckets.values():
            if depth < len(bucket):
                chosen.append(bucket[depth])
                added = True
                if len(chosen) >= feeds:
                    break
        if not added:
            break
        depth += 1

    print(f"fetching {len(chosen)} feeds across {len({f.country for f in chosen})} "
          f"countries (read-only, no model, nothing written)")
    items = national_press.collect(feeds=chosen, dry_run=True)
    kept = [i for i in items if prefilter.passes(i.get("raw_text", ""))[0]]
    print(f"{len(items)} items -> {len(kept)} past the free prefilter "
          f"(the population the gate would see)")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stored", action="store_true",
                    help="measure on rows already held. No network.")
    ap.add_argument("--live", action="store_true",
                    help="fetch a real candidate set through national_press")
    ap.add_argument("--feeds", type=int, default=120,
                    help="feeds to sample for --live (one per country in turn)")
    ap.add_argument("--caps", default="",
                    help="comma-separated read caps to report at (default: the "
                         "historically binding 60 and the current cap)")
    args = ap.parse_args(argv)
    if not (args.stored or args.live):
        args.stored = True

    caps = ([int(c) for c in args.caps.split(",") if c.strip()]
            or sorted({60, classify.READTHROUGH_CAP}))

    conn = schema.connect()
    try:
        context = candidate_rank.Context.for_conn(conn)
        held = sum(context.rows_by_country.values())
        print(f"context: {len(context.rows_by_country)} countries hold "
              f"{held:,} rows; {len(context.known_employers):,} employers known")
        print(f"read caps reported: {', '.join(map(str, caps))}")

        if args.stored:
            _report("STORED news rows, arrival order", stored_population(conn),
                    context, caps, circular_empty=True)
        if args.live:
            _report("LIVE candidate set", live_population(args.feeds),
                    context, caps, circular_empty=False)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
