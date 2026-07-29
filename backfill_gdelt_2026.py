#!/usr/bin/env python3
"""One-time 2026 catch-up: world news via GDELT DOC 2.0, Jan 1 to now.

Why this exists at all: **Google News RSS has no archive.** It serves a recent
window and nothing else, so January to June 2026 is unrecoverable through it.
GDELT DOC 2.0 takes explicit `startdatetime`/`enddatetime` and holds years, and
it returns the publisher's own URL rather than an aggregator redirect, so a
historical row still carries a receipt. It is the only route to 2026 news, and
`collectors/gdelt.py:34` hardcoded a rolling 3-day window, so the capability
had never been used.

Everything goes through the SAME pipeline as the daily collector — prefilter,
gate, read-through, validate, store, publish — so every guard applies. Nothing
is written directly.

    python backfill_gdelt_2026.py --start 2026-01-01 --end 2026-01-31
    python backfill_gdelt_2026.py --start 2026-01-01 --end 2026-01-31 --dry-run

COST. This is news, so unlike the SEC backfill it is not free of noise and the
two-stage design in pipeline/classify.py is what makes it affordable:

    fetch (free) -> title de-dup (free) -> prefilter (free)
                 -> one-word gate (~1/40th) -> full read-through (the money)

Run ONE month, read the report at the end, and only then decide about the next.
`--max-readthroughs` is the hard stop; the run ends cleanly and publishes what
it earned rather than failing, because a half-month of real rows is worth
keeping.

WINDOWING. DOC 2.0 caps a response at 250 articles and has NO pagination — no
offset, no cursor — so a query that hits the cap is silently truncated to the
most recent 250 and the rest of that window is gone. Hence one day per window,
and hence the truncation count in the report: if it is ever non-zero, the
window has to get smaller, not the query broader.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, datetime, timedelta

import source_registry as registry
from collectors import gdelt
from pipeline import classify, prefilter, publish, schema, store, validate

# One day. See WINDOWING above.
WINDOW_HOURS = 24


def iter_windows(start: date, end: date):
    """Half-open [lo, hi) day windows as GDELT stamps."""
    lo = datetime(start.year, start.month, start.day)
    stop = datetime(end.year, end.month, end.day) + timedelta(days=1)
    while lo < stop:
        hi = lo + timedelta(hours=WINDOW_HOURS)
        yield lo, min(hi, stop)
        lo = hi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-readthroughs", type=int, default=1200,
                    help="hard stop on FULL classifications for the whole run")
    ap.add_argument("--fetch-only", action="store_true",
                    help="fetch and prefilter, call no model, store nothing — "
                         "proves the collector without spending anything")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = min(date.fromisoformat(args.end), date.today())
    queries = list(registry.GDELT_QUERIES)

    gdelt.reset_stats()
    conn = schema.connect()

    # Carried across every window so one wire item is paid for once for the
    # whole month, not once per day it was re-syndicated.
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    fetched = candidates = 0
    stored = duplicates = rejected = skipped = errors = 0
    windows = empty_windows = 0
    filtered = Counter()
    outlet_countries: Counter = Counter()
    stored_countries: Counter = Counter()
    stopped_early = ""

    print(f"[gdelt] {len(queries)} queries x {(end - start).days + 1} day-windows")

    for lo, hi in iter_windows(start, end):
        windows += 1
        items = gdelt.collect(queries, startdatetime=lo, enddatetime=hi,
                              seen_urls=seen_urls, seen_titles=seen_titles)
        fetched += len(items)
        if not items:
            empty_windows += 1

        kept = []
        for item in items:
            ok, reason = prefilter.passes(item.get("raw_text", ""))
            if ok:
                kept.append(item)
            else:
                filtered[reason.split("(")[0].strip()] += 1
        candidates += len(kept)
        print(f"\n[{lo:%Y-%m-%d}] {len(items)} new articles, "
              f"{len(kept)} past the free filter")

        for item in kept:
            url = item["source_url"]
            if store.already_seen(conn, url):
                skipped += 1
                continue
            if args.fetch_only:
                outlet_countries[item.get("source_country") or "?"] += 1
                print(f"  WOULD GATE   {item['headline'][:70]}")
                continue
            if classify.STATS["full_calls"] >= args.max_readthroughs:
                stopped_early = (f"--max-readthroughs ({args.max_readthroughs}) "
                                 f"reached at {lo:%Y-%m-%d}")
                break

            try:
                classified = classify.classify(item)
            except classify.CreditsExhausted:
                # Publish what this run already earned, then stop cleanly.
                stopped_early = "OpenRouter credits exhausted"
                break
            except classify.AuthFailed as exc:
                print(f"\nSTOPPING: {exc}", file=sys.stderr)
                return 1
            except classify.BudgetDeferred as exc:
                # The per-run ceiling in classify.py, not a busy provider.
                stopped_early = f"read-through cap: {exc}"
                break
            except classify.Throttled:
                # Historical news is not going anywhere: leave it unseen and a
                # re-dispatch of the same window picks it up.
                errors += 1
                continue
            except classify.ClassifyError:
                errors += 1
                continue

            if classified is None:
                rejected += 1
                if not args.dry_run:
                    store.mark_seen(conn, url, gdelt.COLLECTOR, "rejected")
                continue
            try:
                # conn: without it identity.enrich() inside build_signal is a
                # no-op, so the row lands with no ticker, type or HQ. See
                # the note at the same call in run_collect.py.
                signal = validate.build_signal(classified, item, gdelt.COLLECTOR,
                                               conn=conn)
            except validate.Rejected:
                rejected += 1
                if not args.dry_run:
                    store.mark_seen(conn, url, gdelt.COLLECTOR, "rejected")
                continue

            outlet_countries[item.get("source_country") or "?"] += 1
            if args.dry_run:
                stored += 1
                stored_countries[signal.country or "-"] += 1
                print(f"  WOULD STORE  [{signal.country or '--'}] {signal.headline[:64]}")
                continue

            outcome = store.store(conn, signal)
            store.mark_seen(conn, url, gdelt.COLLECTOR, outcome)
            if outcome == "stored":
                stored += 1
                stored_countries[signal.country or "-"] += 1
                print(f"  STORED  [{signal.country or '--'}] {signal.headline[:64]}")
            else:
                duplicates += 1

        conn.commit()
        if stopped_early:
            print(f"\nSTOPPING EARLY: {stopped_early}", file=sys.stderr)
            break

    g = gdelt.STATS
    print(f"\nBACKFILL {args.start}..{args.end}")
    print(f"  windows            {windows} ({empty_windows} empty)")
    print(f"  queries sent       {g['queries']}  "
          f"(throttled out {g['throttled_out']}, rejected {g['rejected_queries']}, "
          f"truncated at the 250 cap {g['truncated']})")
    print(f"  articles           {g['articles']} raw -> {fetched} after de-dup "
          f"(same URL {g['duplicate_url']}, syndicated copies {g['syndicated']})")
    print(f"  free filter        {fetched} -> {candidates} candidates")
    for reason, count in filtered.most_common():
        print(f"      {count:5d}  {reason}")
    print(f"  gate calls         {classify.STATS['gate_calls']} "
          f"({classify.STATS['gate_rejects']} rejected there, cost avoided)")
    print(f"  read-throughs      {classify.STATS['full_calls']}")
    print(f"  stored={stored} duplicate={duplicates} rejected={rejected} "
          f"already-seen={skipped} transient-errors={errors}")
    if stored_countries:
        print(f"  countries stored   {len([c for c in stored_countries if c != '-'])}: "
              + ", ".join(f"{c}={n}" for c, n in stored_countries.most_common()))
    if outlet_countries:
        print(f"  outlet countries   {len(outlet_countries)}: "
              + ", ".join(f"{c}={n}" for c, n in outlet_countries.most_common(12)))
    if stopped_early:
        print(f"  STOPPED EARLY      {stopped_early}")

    if not (args.dry_run or args.fetch_only):
        publish.publish(conn)

    # FAIL LOUD. A month of world news cannot be empty. If every window came
    # back with nothing, the FETCH is broken — a rejected query, a throttling
    # lockout, a changed endpoint — and the run must not exit green on it. The
    # first SEC backfill dispatch exited 0 after five silent 403s and looked
    # exactly like a successful run that found nothing (2026-07-28).
    if windows and empty_windows == windows:
        print("\nSTOPPING: every window returned zero articles. A historical "
              "month of world news cannot be empty, so the GDELT fetch itself "
              "is failing — check the QUERY REJECTED and throttle counts "
              "above.", file=sys.stderr)
        return 1
    if g["rejected_queries"] and not g["articles"]:
        print("\nSTOPPING: GDELT rejected every query.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
