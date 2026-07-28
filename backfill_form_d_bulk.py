#!/usr/bin/env python3
"""Backfill the funding pillar from SEC's quarterly Form D DATA SETS.

The search-based sweep (`backfill_form_d_2026.py`) works, but it pays a model
to read filings whose fields SEC already publishes as columns, and its
full-text query only matched about a sixth of the quarter's filings. This
walks the same filings from the bulk data set instead:

    zero LLM spend        the fields are columns, so the record is derived
    ~2,000 rows/quarter   against ~30 a month from the search path
    exact dollar amounts  an integer off the filing, not a rounded phrase
    back to 2008Q1        if the owner ever wants the history

Records still go through `validate.build_signal` -> `store` -> `publish`, so
every credibility guard applies unchanged: source_url is the filing's own
EDGAR page, the figure must appear in the source text, and confidence is
capped by the source rather than asserted.

Usage:
    python backfill_form_d_bulk.py --quarters 2026q1
    python backfill_form_d_bulk.py --quarters 2026q1,2026q2 --dry-run
    python backfill_form_d_bulk.py --list          # what SEC publishes today
"""

from __future__ import annotations

import argparse
import sys

from collectors import sec_form_d_bulk as bulk
from pipeline import publish, schema, store, validate


def run_quarter(conn, quarter: str, *, dry_run: bool) -> dict:
    print(f"\n=== {quarter} ===")
    items = bulk.collect(quarter)
    counts = {"found": len(items), "stored": 0, "duplicate": 0,
              "rejected": 0, "skipped": 0}
    print(f"{len(items)} qualifying issuer raises in the data set")

    for item in items:
        url = item["source_url"]
        if store.already_seen(conn, url):
            counts["skipped"] += 1
            continue
        try:
            signal = validate.build_signal(bulk.as_classified(item), item, bulk.COLLECTOR)
        except validate.Rejected as exc:
            counts["rejected"] += 1
            print(f"  REJECT  {item['headline'][:66]}\n          {exc}")
            if not dry_run:
                store.mark_seen(conn, url, bulk.COLLECTOR, "rejected")
            continue
        if dry_run:
            counts["stored"] += 1
            continue
        outcome = store.store(conn, signal)
        store.mark_seen(conn, url, bulk.COLLECTOR, outcome)
        if outcome == "stored":
            counts["stored"] += 1
        else:
            counts["duplicate"] += 1
        if counts["stored"] and counts["stored"] % 250 == 0:
            conn.commit()
            print(f"  ... {counts['stored']} stored")

    if not dry_run:
        conn.commit()
    print(f"{quarter}: stored={counts['stored']} duplicate={counts['duplicate']} "
          f"rejected={counts['rejected']} already-seen={counts['skipped']} "
          f"found={counts['found']}")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quarters", help="comma-separated, e.g. 2026q1,2026q2")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print the quarters SEC currently publishes and exit")
    args = ap.parse_args()

    if args.list:
        for label in sorted(bulk.dataset_urls(), reverse=True):
            print(label)
        return 0
    if not args.quarters:
        ap.error("--quarters is required (or --list)")

    quarters = [q.strip().lower() for q in args.quarters.split(",") if q.strip()]
    conn = schema.connect()
    totals = {"found": 0, "stored": 0, "duplicate": 0, "rejected": 0, "skipped": 0}
    empty: list[str] = []

    for quarter in quarters:
        try:
            counts = run_quarter(conn, quarter, dry_run=args.dry_run)
        except bulk.DatasetError as exc:
            print(f"\nSTOPPING: {exc}", file=sys.stderr)
            return 1
        for key, value in counts.items():
            totals[key] += value
        if counts["found"] == 0:
            empty.append(quarter)

    print(f"\nFORM D BULK BACKFILL {','.join(quarters)}: "
          f"found={totals['found']} stored={totals['stored']} "
          f"duplicate={totals['duplicate']} rejected={totals['rejected']} "
          f"already-seen={totals['skipped']}")

    if not args.dry_run:
        store.report_health(
            conn, bulk.COLLECTOR, status="ok",
            items_found=totals["found"], items_stored=totals["stored"],
            detail=f"bulk data set backfill: {','.join(quarters)}")
        conn.commit()
        result = publish.publish(conn)
        print(f"published: {result}")
        publish.publish_health(conn)
        conn.commit()

    # FAIL LOUD. A quarter of Form D holds ~15,700 submissions and ~2,000
    # qualifying raises, so zero is never the market being quiet — it is the
    # archive not downloading, the columns being renamed, or SEC throttling the
    # request. The search backfill's ancestor exited 0 on five silent 403s and
    # looked exactly like a successful run (2026-07-28); this is the same trap
    # one layer up.
    if empty:
        print(f"\nSTOPPING: no qualifying filings in {', '.join(empty)}. A "
              f"quarter of Form D cannot be empty, so the data set itself did "
              f"not parse (check the archive layout and the User-Agent).",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
