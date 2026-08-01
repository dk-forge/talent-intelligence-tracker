#!/usr/bin/env python3
"""One-time 2026 catch-up: SEC Form D private placements, Jan 1 to now.

The funding pillar is the thinnest thing on the page: 1,979 records, 17 of
them carrying a money figure, because `collectors/sec_form_d.py` only ever
looked back five days and the first 2026 sweep was 8-K leadership only. Form D
is the filing every US private placement must make, and the amount sold is a
structured XML field — a fact read off a legal filing, not a number a model
produced. That makes a historical sweep the cheapest large win available.

Everything goes through the SAME pipeline as the daily collector — gate,
read-through, validate, store, publish — so every guard applies. Nothing is
written directly. The issuer filters (pooled-investment industries, the
investment-vehicle name patterns, MIN_RAISED) are the collector's own
constants, imported rather than restated, so the backfill and the daily run
cannot drift apart on what counts as an employer raising money.

Usage:
    python backfill_form_d_2026.py --start 2026-01-01 --end 2026-01-31
    python backfill_form_d_2026.py --start 2026-01-01 --end 2026-01-31 --dry-run

Chunk by month from the workflow: a whole-year sweep in one job would brush
the 6-hour Actions ceiling; a month is comfortably under it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

import requests

import backfill_slices
from collectors import sec_edgar, sec_form_d
from pipeline import gate_ledger, publish, schema, store, validate
from pipeline import classify

WORKFLOW = "backfill-funding-2026.yml"

#: Days of filings per slice. MEASURED: run 30377226199 did 2026-01-01..01-31
#: in 12.7 minutes of job time — about 0.4 minutes a day, because the issuer
#: filters drop most Form D volume before anything is classified. Four weeks is
#: therefore roughly 12 minutes, an order of magnitude inside
#: SLICE_BUDGET_MINUTES.
#:
#: This one has never run over an hour. It is sliced anyway because it declared
#: `timeout-minutes: 350` like the others, so nothing but the size of the
#: window a human happened to type stood between it and a five-hour lock hold —
#: and "it has not happened yet" is not a bound.
SLICE_DAYS = 28

# Weekly windows keep each query far below the EFTS result-window ceiling: a
# 2026 month is ~850 Form D filings matching the collector's query, so a week
# is ~200.
WINDOW_DAYS = 7

# `sec_form_d.search(page=N)` asks EFTS for offset N*10, but EFTS answers with
# up to 100 hits per request. Advancing one page at a time would therefore
# re-request the same records ten times over. The stride is derived from what
# came back rather than assumed, so a change in EFTS's page size costs a
# little overlap (which the per-window `seen` set absorbs) instead of silently
# skipping filings.
MAX_REQUESTS_PER_WINDOW = 60


def iter_windows(start: date, end: date):
    lo = start
    while lo <= end:
        hi = min(lo + timedelta(days=WINDOW_DAYS - 1), end)
        yield lo.isoformat(), hi.isoformat()
        lo = hi + timedelta(days=1)


def collect_window(conn, startdt: str, enddt: str) -> tuple[list[dict], int, int]:
    """All qualifying Form D filings in one window, paginated.

    Returns (items, raw_hits, already_seen). `raw_hits` is what the SEC search
    itself returned, BEFORE any issuer filtering — the fail-loud check reads
    that, because "no filings matched" and "no issuers survived the fund
    filter" are different failures and only the first one means the search is
    broken.

    A fetch failure skips the single filing, never the window.
    """
    out: list[dict] = []
    seen: set[str] = set()
    raw_hits = skipped = 0

    page = 0
    for _request in range(MAX_REQUESTS_PER_WINDOW):
        try:
            hits = sec_form_d.search(startdt=startdt, enddt=enddt, page=page)
        except Exception as exc:  # noqa: BLE001 - one window must not kill the run
            print(f"  window {startdt}..{enddt} page {page}: search failed: {exc}",
                  file=sys.stderr)
            break
        if not hits:
            break
        raw_hits += len(hits)
        page += max(1, len(hits) // 10)

        for hit in hits:
            url = sec_edgar.document_url(hit)
            if not url or url in seen:
                continue
            seen.add(url)

            # Cheapest check first: a re-dispatched month must not re-fetch
            # thousands of XML documents it already classified.
            if store.already_seen(conn, url):
                skipped += 1
                continue

            try:
                xml = requests.get(
                    url, headers={"User-Agent": sec_form_d.USER_AGENT}, timeout=30).text
            except requests.RequestException:
                continue

            industry = sec_form_d._tag(xml, "industryGroupType")
            if industry.lower() in sec_form_d.EXCLUDED_INDUSTRIES:
                continue

            raised = sec_form_d._money(sec_form_d._tag(xml, "totalAmountSold"))
            if not raised or raised < sec_form_d.MIN_RAISED:
                continue

            company = sec_form_d._tag(xml, "entityName")
            if not company:
                continue
            if sec_form_d.EXCLUDED_NAME_PATTERNS.search(company):
                # An investment vehicle raising capital employs nobody; only an
                # operating company's raise implies hiring.
                continue

            city = sec_form_d._tag(xml, "city").title()
            # Read exactly as the daily collector reads it: the two-character
            # code decides US-versus-foreign, and the country comes off the
            # description beside it. Storing "United States" unconditionally is
            # the bug both paths shipped.
            state_code = sec_form_d._tag(xml, "stateOrCountry").upper()
            place = sec_form_d._tag(xml, "stateOrCountryDescription").title()
            in_us = state_code in sec_form_d.US_STATE_CODES
            money = sec_form_d._humanise(raised)

            # Identical wording to the daily collector's: the classifier reads
            # only raw_text, and validate.assert_figures_are_sourced compares
            # any figure it returns against exactly this string.
            headline = f"{company} raised {money} in a private placement"
            body = (
                f"{company} filed a Form D with the SEC reporting {money} "
                f"({raised:,} dollars) sold in a private securities offering. "
                f"Industry: {industry}. Location: {city}, {place or state_code}. "
                f"Form D filings are required for exempt offerings and are the "
                f"public record of private fundraising."
            )

            out.append({
                "raw_text": f"{headline}\n\n{body}",
                "headline": headline,
                "source_url": url,
                "source_name": "SEC EDGAR (Form D)",
                "discovery_url": url,
                "published_date": (hit.get("_source") or {}).get("file_date"),
                "country": "United States" if in_us else sec_form_d._country_name(place),
                "state": state_code if in_us else "",
                "city": city,
                "funding_amount": money,
                "query": f"form D backfill {startdt}",
                "collector": sec_form_d.COLLECTOR,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    return out, raw_hits, skipped


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
    windows = empty_search_windows = total_hits = 0
    stopped_early = ""
    # The last window this run FINISHED, which is what the cursor is derived
    # from: a run that stops on its budget resumes on the exact next day.
    done_through = None

    for lo, hi in iter_windows(start, end):
        if budget.expired():
            stopped_early = budget.reason()
            print(f"\nSTOPPING EARLY: {stopped_early}", file=sys.stderr)
            break
        windows += 1
        items, raw_hits, window_skipped = collect_window(conn, lo, hi)
        total_hits += raw_hits
        skipped += window_skipped
        if raw_hits == 0:
            empty_search_windows += 1
        print(f"\n[{lo}..{hi}] {raw_hits} Form D search results, "
              f"{window_skipped} already seen, {len(items)} qualifying issuers")

        for item in items:
            url = item["source_url"]
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
                    store.mark_seen(conn, url, sec_form_d.COLLECTOR, "rejected")
                continue
            try:
                # conn: without it identity.enrich() inside build_signal is a
                # no-op, so the row lands with no ticker, type or HQ. See
                # the note at the same call in run_collect.py.
                signal = validate.build_signal(classified, item, sec_form_d.COLLECTOR,
                                               conn=conn)
            except validate.Rejected:
                rejected += 1
                gate_ledger.outcome(item, "validate_reject")
                if not args.dry_run:
                    store.mark_seen(conn, url, sec_form_d.COLLECTOR, "rejected")
                continue
            if args.dry_run:
                stored += 1
                gate_ledger.outcome(item, "would_store")
                print(f"  WOULD STORE  {signal.headline[:70]}")
                continue
            outcome = store.store(conn, signal)
            gate_ledger.outcome(item, outcome)
            store.mark_seen(conn, url, sec_form_d.COLLECTOR, outcome)
            if outcome == "stored":
                stored += 1
                print(f"  STORED  {signal.headline[:70]}")
            else:
                duplicates += 1
        conn.commit()
        done_through = hi

    print(f"\nFORM D BACKFILL {start}..{end}: stored={stored} "
          f"duplicate={duplicates} rejected={rejected} already-seen={skipped} "
          f"transient-errors={errors} windows={windows} "
          f"filings-found={total_hits} empty-search-windows={empty_search_windows}")
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

    # The slice ticket, emitted BEFORE the fail-loud check: work this run
    # finished survives however the run ends. A run that finished nothing
    # emits an unmoved cursor, which `backfill_slices record` goes red on
    # rather than requeueing into a loop.
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

    # FAIL LOUD. A historical month ALWAYS contains Form D filings — thousands
    # of them — so every window's SEARCH coming back empty means the search is
    # broken, not that the month was quiet. The leadership backfill's first
    # dispatch exited 0 after five silent SEC 403s and looked exactly like a
    # successful run that found nothing (2026-07-28). Note this tests raw
    # hits, not stored rows: a month where the fund filters happened to drop
    # everything is implausible but not evidence of breakage, whereas zero
    # filings from the SEC is only ever breakage.
    if windows and empty_search_windows == windows:
        print("\nSTOPPING: every window returned zero Form D filings. A "
              "historical month cannot be empty, so the SEC search itself is "
              "failing (check the User-Agent and the errors above).",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
