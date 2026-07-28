#!/usr/bin/env python3
"""One-time 2026 catch-up: SEC 8-K Item 5.02 leadership changes, Jan 1 to now.

Owner-approved spend, 2026-07-28: an estimated $7-12 one-time, on top of the
monthly allowance, to give the page a year of verified leadership depth on day
one. Scope is DELIBERATELY 5.02-only: leadership changes age well (a March CEO
appointment is still a fact a recruiter wants on the company page), while
historical Form D rows are high-volume, low value-per-row, and accumulate
forward-only from the daily runs instead.

Everything goes through the SAME pipeline as the daily collector - gate,
read-through, validate, store, publish - so every guard applies. Nothing is
written directly.

Usage:
    python backfill_sec_2026.py --start 2026-01-01 --end 2026-01-31
    python backfill_sec_2026.py --start 2026-01-01 --end 2026-01-31 --dry-run

Chunk by month from the workflow: a whole-year sweep in one job would brush
the 6-hour Actions ceiling; a month is comfortably under it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

from collectors import sec_edgar
from pipeline import classify, publish, schema, store, validate

# The item code is the highest-precision phrase available: it appears whenever
# the event is an officer or director change. The daily collector's extra
# appointment phrases are recall boosters inside a 7-day window; over a
# 7-month sweep they only re-find filings "item 5.02" already matched.
PHRASE = "item 5.02"

# EFTS pages are 10 hits; its result window is capped at 10,000 per query.
# Weekly windows keep each query far below that (a busy week is ~500 8-Ks
# mentioning 5.02).
WINDOW_DAYS = 7
MAX_PAGES_PER_WINDOW = 120


def iter_windows(start: date, end: date):
    lo = start
    while lo <= end:
        hi = min(lo + timedelta(days=WINDOW_DAYS - 1), end)
        yield lo.isoformat(), hi.isoformat()
        lo = hi + timedelta(days=1)


def collect_window(startdt: str, enddt: str) -> list[dict]:
    """All 5.02 hits in one window, paginated, as daily-collector-shaped raw
    dicts. Fetch failures skip the single filing, never the window."""
    out, seen = [], set()
    for page in range(MAX_PAGES_PER_WINDOW):
        try:
            hits = sec_edgar.search(PHRASE, startdt=startdt, enddt=enddt, page=page)
        except Exception as exc:  # noqa: BLE001 - one window must not kill the run
            print(f"  window {startdt}..{enddt} page {page}: search failed: {exc}",
                  file=sys.stderr)
            break
        if not hits:
            break
        for hit in hits:
            url = sec_edgar.document_url(hit)
            if not url or url in seen:
                continue
            seen.add(url)
            company, cik = sec_edgar._company_and_cik(hit)
            src = hit.get("_source") or {}
            try:
                body = sec_edgar.fetch_text(url)
            except Exception:  # noqa: BLE001
                continue
            if not body:
                continue
            headline = f"{company} 8-K filing (Item 5.02): officer or director change"
            out.append({
                "raw_text": f"{headline}\n\n{body}",
                "headline": headline,
                "source_url": url,
                "source_name": "SEC EDGAR",
                "discovery_url": url,
                "published_date": src.get("file_date"),
                "country": "United States",
                "cik": cik,      # join key to the sibling tracker
                "query": f"{PHRASE} backfill {startdt}",
                "collector": sec_edgar.COLLECTOR,
                "fetched_at": datetime.utcnow().isoformat(timespec="seconds"),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    start = date.fromisoformat(args.start)
    end = min(date.fromisoformat(args.end), date.today())

    conn = schema.connect()
    stored = duplicates = rejected = skipped = errors = 0
    windows = fetch_failures = 0

    for lo, hi in iter_windows(start, end):
        windows += 1
        items = collect_window(lo, hi)
        if not items:
            fetch_failures += 1
        print(f"\n[{lo}..{hi}] {len(items)} filings fetched")
        for item in items:
            url = item["source_url"]
            if store.already_seen(conn, url):
                skipped += 1
                continue
            try:
                classified = classify.classify(item)
            except classify.CreditsExhausted:
                # Publish what this run already earned, then stop cleanly.
                print("\nSTOPPING: OpenRouter credits exhausted", file=sys.stderr)
                conn.commit()
                if not args.dry_run:
                    publish.publish(conn)
                return 1
            except classify.AuthFailed as exc:
                print(f"\nSTOPPING: {exc}", file=sys.stderr)
                return 1
            except classify.Throttled:
                # Historical filings are not going anywhere: leave unseen and
                # a re-dispatch of the same window picks them up.
                errors += 1
                continue
            except classify.ClassifyError:
                errors += 1
                continue

            if classified is None:
                rejected += 1
                if not args.dry_run:
                    store.mark_seen(conn, url, sec_edgar.COLLECTOR, "rejected")
                continue
            try:
                signal = validate.build_signal(classified, item, sec_edgar.COLLECTOR)
            except validate.Rejected:
                rejected += 1
                if not args.dry_run:
                    store.mark_seen(conn, url, sec_edgar.COLLECTOR, "rejected")
                continue
            if args.dry_run:
                stored += 1
                print(f"  WOULD STORE  {signal.headline[:70]}")
                continue
            outcome = store.store(conn, signal)
            store.mark_seen(conn, url, sec_edgar.COLLECTOR, outcome)
            if outcome == "stored":
                stored += 1
                print(f"  STORED  {signal.headline[:70]}")
            else:
                duplicates += 1
        conn.commit()

    print(f"\nBACKFILL {args.start}..{args.end}: stored={stored} "
          f"duplicate={duplicates} rejected={rejected} already-seen={skipped} "
          f"transient-errors={errors} windows={windows} empty-windows={fetch_failures}")
    if not args.dry_run:
        publish.publish(conn)

    # FAIL LOUD. A historical month always contains 8-K 5.02 filings, so every
    # window coming back empty means the SEARCH is broken, not that the month
    # was quiet. The first dispatch exited 0 after five silent SEC 403s and
    # looked exactly like a successful run that found nothing (2026-07-28).
    if windows and fetch_failures == windows:
        print("\nSTOPPING: every window returned zero filings. A historical "
              "month cannot be empty, so the SEC search itself is failing "
              "(check the User-Agent and the 403s above).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
