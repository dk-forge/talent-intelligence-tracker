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

import backfill_slices
from collectors import sec_edgar
from pipeline import classify, gate_ledger, publish, schema, store, validate

WORKFLOW = "backfill-2026.yml"

#: Days of filings per slice. MEASURED, not guessed: the seven month-long runs
#: on 2026-07-28/29 took 137, 145, 159, 184, 185, 188 and 215 minutes of job
#: time for roughly thirty days each — 4.6 to 7.2 minutes a day. Every one of
#: them held the single writer lock for its whole duration, and the drainer
#: calls a hold past two hours starvation.
#:
#: A week is therefore 32 to 50 minutes, which fits inside
#: SLICE_BUDGET_MINUTES even at the worst rate observed. It is also exactly
#: WINDOW_DAYS, so a slice is one EFTS window and the two cannot drift into a
#: slice that splits a window in half.
SLICE_DAYS = 7

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


@gate_ledger.around_run(WORKFLOW)
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slice", action="store_true",
                    help="do ONE bounded slice of --start..--end, resuming from "
                         "the committed cursor, then stop")
    ap.add_argument("--slice-days", type=int, default=SLICE_DAYS,
                    help=f"days per slice (default {SLICE_DAYS}; see the constant)")
    ap.add_argument("--budget-minutes", type=float,
                    default=backfill_slices.SLICE_BUDGET_MINUTES,
                    help="stop at the next window boundary after this long")
    ap.add_argument("--emit-next", help="write the slice ticket here, for "
                                        "backfill_slices.py record")
    ap.add_argument("--state", help="slice state file (default data/backfill_state.json)")
    args = ap.parse_args()
    # The decorator could only guess from kwargs, and this one comes from argv.
    # A rehearsal must not leave an uncommitted shard for a real run to push.
    gate_ledger.set_dry_run(args.dry_run)
    requested_start = date.fromisoformat(args.start)
    requested_end = min(date.fromisoformat(args.end), date.today())

    job = None
    if args.slice:
        job, window = backfill_slices.open_slice(
            workflow=WORKFLOW, unit="days",
            start=requested_start.isoformat(), end=requested_end.isoformat(),
            slice_size=args.slice_days, state_path=args.state,
            inputs={"dry_run": "false"})
        if window is None:
            print(f"{backfill_slices.job_id(WORKFLOW, args.start, args.end)} is "
                  "already complete — nothing to do.")
            return 0
        start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        print(f"SLICE {start}..{end} of {requested_start}..{requested_end} "
              f"(slice {job['slices'] + 1}, budget {args.budget_minutes:g} min)")
    else:
        start, end = requested_start, requested_end

    budget = backfill_slices.Budget(args.budget_minutes)
    conn = schema.connect()
    stored = duplicates = rejected = skipped = errors = 0
    windows = fetch_failures = 0
    stopped_early = ""
    # The last window this run FINISHED. The cursor is derived from it, so a
    # run that stops on its budget resumes on the exact next day.
    done_through = None

    for lo, hi in iter_windows(start, end):
        if budget.expired():
            stopped_early = budget.reason()
            print(f"\nSTOPPING EARLY: {stopped_early}", file=sys.stderr)
            break
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
                gate_ledger.outcome(item, "deferred")
                continue
            except classify.ClassifyError:
                errors += 1
                gate_ledger.outcome(item, "error")
                continue

            if classified is None:
                rejected += 1
                # A gate NO already closed its own line as `gate_reject` and
                # `outcome()` refuses to overwrite it — the two rejections
                # arrive here identically, and telling them apart is the whole
                # point of the ledger.
                gate_ledger.outcome(item, "model_reject")
                if not args.dry_run:
                    store.mark_seen(conn, url, sec_edgar.COLLECTOR, "rejected")
                continue
            try:
                # conn: without it identity.enrich() inside build_signal is a
                # no-op, so the row lands with no ticker, type or HQ. See
                # the note at the same call in run_collect.py.
                signal = validate.build_signal(classified, item, sec_edgar.COLLECTOR,
                                               conn=conn)
            except validate.Rejected:
                rejected += 1
                gate_ledger.outcome(item, "validate_reject")
                if not args.dry_run:
                    store.mark_seen(conn, url, sec_edgar.COLLECTOR, "rejected")
                continue
            if args.dry_run:
                stored += 1
                gate_ledger.outcome(item, "would_store")
                print(f"  WOULD STORE  {signal.headline[:70]}")
                continue
            outcome = store.store(conn, signal)
            gate_ledger.outcome(item, outcome)
            store.mark_seen(conn, url, sec_edgar.COLLECTOR, outcome)
            if outcome == "stored":
                stored += 1
                print(f"  STORED  {signal.headline[:70]}")
            else:
                duplicates += 1
        conn.commit()
        done_through = hi

    print(f"\nBACKFILL {start}..{end}: stored={stored} "
          f"duplicate={duplicates} rejected={rejected} already-seen={skipped} "
          f"transient-errors={errors} windows={windows} empty-windows={fetch_failures}")
    # Publishing is a SEPARATE gate from collecting, and a slice must survive
    # it failing. This is not hypothetical: the first live sliced run
    # (30481065108) collected its quarter and then died inside
    # `publish.publish` because the publish guardrails held eight open
    # findings — so the ticket was never emitted, the cursor never moved, and
    # the chain stopped with nothing recorded. The rows are real either way.
    blocked = ""
    if not args.dry_run:
        try:
            publish.publish(conn)
        except publish.PublishError as exc:
            blocked = f"publish refused: {exc}"
            print(f"\nPUBLISH FAILED: {exc}", file=sys.stderr)

    # The slice ticket, emitted BEFORE the fail-loud check below: a run that
    # did four weeks and then hit a broken search has still done four weeks,
    # and slicing exists so that finished work survives however the run ends. A
    # run that finished nothing emits an unmoved cursor, which
    # `backfill_slices record` refuses to requeue and goes red on.
    if args.slice and args.emit_next and not args.dry_run:
        cursor = (backfill_slices.advance(done_through, "days")
                  if done_through else job["cursor"])
        backfill_slices.emit(args.emit_next, backfill_slices.slice_ticket(
            job, start.isoformat(), end.isoformat(), next_cursor=cursor,
            totals={"stored": stored, "duplicates": duplicates,
                    "rejected": rejected, "windows": windows},
            stopped_early=stopped_early, halt=blocked))
        print(f"  next cursor {cursor}")
    if blocked:
        return 1

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
