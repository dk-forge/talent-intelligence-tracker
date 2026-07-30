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

    python backfill_gdelt_2026.py --plan-cost      # what each pace costs; no requests
    python backfill_gdelt_2026.py --start 2026-01-01 --end 2026-01-31
    python backfill_gdelt_2026.py --start 2026-01-01 --end 2026-01-31 --dry-run

PACE IS THE OWNER'S DECISION AND IT IS NOT ARMED. Cost scales with slices, so the
cron would BE the budget; there is no cron. `--plan-cost` prints the table to
decide from, `tests/test_backfill_pace.py` refuses to let a schedule appear
unnoticed, and the cursor advances per RUN rather than per date — which is the
sibling's ~$3.80/day-for-six-days mistake, and the reason that test asserts the
property rather than the symptom.

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
import math
import sys
from collections import Counter
from datetime import date, datetime, timedelta

import backfill_slices
import source_registry as registry
from collectors import gdelt
from pipeline import classify, prefilter, publish, schema, store, validate

# One day. See WINDOWING above.
WINDOW_HOURS = 24

WORKFLOW = "backfill-gdelt-2026.yml"

#: Days of news per slice. MEASURED, not guessed: run 30423752001 held the
#: writer lock from 04:59 to 10:49 UTC on 2026-07-29 — 350 minutes on a single
#: month — and had still not finished when its timeout cancelled it, so a
#: day-window costs at least 11 minutes here. The collector's own arithmetic
#: agrees: 9 queries at MIN_PAUSE = 12s is 108 seconds of pacing a day before
#: anything is fetched, and a throttled day walks the 12/24/36s retry ladder to
#: roughly 12 minutes of pure waiting.
#:
#: Four days is therefore ~45-50 minutes of collection, which fits inside
#: SLICE_BUDGET_MINUTES with room for the slowest observed day. A whole month
#: in one run is what caused the incident; four days is the largest slice that
#: is still comfortably a short run.
SLICE_DAYS = 4


# --------------------------------------------------------------------------
# COST, PER WINDOW, SO THE PACE IS A DECISION AND NOT A DISCOVERY
# --------------------------------------------------------------------------
#
# This walker is the biggest coverage lever available and the only one in its
# session that touches the budget. 51 of the 81 gold-set misses are
# `outside_our_history`: the news collectors first ran on 2026-07-27 and
# `national_press` on 2026-07-29, against a gold window of 2026-07-01..28. Nothing
# is broken; we simply did not exist yet. Walking history is the only fix.
#
# But cost scales with SLICES, so the CRON IS THE BUDGET. That makes the pace an
# owner decision rather than an implementation detail, and a decision needs a
# number beside it. `--plan-cost` prints these:
#
#   * a gate call is ~$0.00003 (141 tokens in / 35 out, measured)
#   * a full read-through is ~$0.00128 (3,100 in / 400 out, measured)
#   * so cost per slice ~= candidates x 0.00003 + reads x 0.00128, and the read
#     term is ~40x the gate term. What bounds a slice is therefore the READ
#     ceiling and nothing else.
#
# The default read ceiling was 1200, which is ~$1.54 a slice: a year of history at
# four days a slice is 92 slices and up to ~$142. That is not a pace the owner can
# choose from, it is a number spend.py would have to stop, and a ceiling only
# spend.py can stop is a ceiling that reads as a plan. So the default is sized
# against the product's own monthly allowance instead — see DEFAULT_MAX_READTHROUGHS
# — and the CLI still takes any value for a session somebody is watching.

#: What this walker may spend in a month, all-in. A slice of the ~$5/month
#: product budget, chosen so a year of history can be closed without the walker
#: ever being the reason a collect run finds the allowance gone. It is a SIZING
#: input, not an enforcement point: enforcement is spend.py, which runs first on
#: every job and hard-stops at 90% of the allowance, and the OpenRouter key's own
#: cap behind that.
MONTHLY_WALKER_BUDGET_USD = 1.50

#: Measured per-item prices. Both from the ledger's own accounting, not a rate
#: card: see docs/TECHLOG.md "cost levers, second pass".
GATE_USD_PER_ITEM = 0.00003
READ_USD_PER_ITEM = 0.00128

#: Gate survivors screened per read bought. The measured pipeline is far kinder
#: than this — the last real run screened ~1,050 and bought 60 — but the
#: derivation below has to be pessimistic or the ceiling it produces is one the
#: budget cannot actually pay for, which is the whole failure mode being avoided.
CANDIDATES_PER_READ = 4

#: The all-in price of one read: the read itself plus the gate calls it took to
#: find. Deriving the ceiling from READ_USD_PER_ITEM alone overshot the budget by
#: 9% — small, and exactly the kind of arithmetic that makes a stated ceiling
#: quietly untrue.
USD_PER_READ_ALL_IN = READ_USD_PER_ITEM + CANDIDATES_PER_READ * GATE_USD_PER_ITEM

#: Read-throughs one slice may buy. DERIVED from the budget above at one slice a
#: day (30 slices a month), so changing the budget changes this and the two can
#: never disagree, and it is derived from the ALL-IN price of a read rather than
#: from the read alone. Printed by --plan-cost: about $0.05 a slice, $1.50 a
#: month at one slice a day, and a year of 2026 history in 92 slices for about
#: $4.60 all in — one tracker's monthly budget, spent once, for everything from
#: 1 January to the day collection started.
DEFAULT_MAX_READTHROUGHS = max(
    1, int(MONTHLY_WALKER_BUDGET_USD / 30 / USD_PER_READ_ALL_IN))


def window_cost(*, candidates: int, reads: int) -> dict:
    """What one slice costs at the measured prices. Pure; no network, no model."""
    gate = candidates * GATE_USD_PER_ITEM
    read = reads * READ_USD_PER_ITEM
    return {"gate_usd": gate, "read_usd": read, "usd": gate + read}


def year_projection(*, windows_per_run: int = SLICE_DAYS,
                    reads_per_run: int | None = None,
                    candidates_per_read: int = CANDIDATES_PER_READ) -> dict:
    """Closing a year of history: how many slices, how long, how much.

    `candidates_per_read` is the gate-survivors-per-read ratio, and it is
    deliberately pessimistic — see CANDIDATES_PER_READ. It barely moves the total,
    because a gate call is a fortieth of a read, but it is carried through rather
    than dropped: an "about" in a budget is how a ceiling stops being one.
    """
    reads = reads_per_run if reads_per_run is not None else DEFAULT_MAX_READTHROUGHS
    slices = math.ceil(366 / max(windows_per_run, 1))
    per_slice = window_cost(candidates=reads * candidates_per_read, reads=reads)
    return {
        "slices": slices,
        "reads_per_slice": reads,
        "usd_per_slice": per_slice["usd"],
        "usd_total": per_slice["usd"] * slices,
        "days_at_one_slice_per_day": slices,
        "days_at_two_slices_per_day": math.ceil(slices / 2),
        "days_at_four_slices_per_day": math.ceil(slices / 4),
    }


def print_cost_plan() -> None:
    """The table the owner needs to choose a cadence. Calls nothing."""
    print("HISTORICAL WALKER — what a pace costs, at measured per-item prices")
    print(f"  gate  ${GATE_USD_PER_ITEM:.5f}/item      "
          f"read  ${READ_USD_PER_ITEM:.5f}/item")
    print(f"  slice = {SLICE_DAYS} day-windows, ceiling "
          f"{DEFAULT_MAX_READTHROUGHS} read-throughs "
          f"(derived from ${MONTHLY_WALKER_BUDGET_USD:.2f}/month at 1 slice/day)")
    print()
    print(f"  {'pace':<22} {'wall clock':>12} {'$/month':>9} {'$ total':>9}")
    year = year_projection()
    for label, per_day in (("1 slice/day", 1), ("2 slices/day", 2),
                           ("4 slices/day", 4)):
        days = math.ceil(year["slices"] / per_day)
        monthly = year["usd_per_slice"] * per_day * 30
        print(f"  {label:<22} {days:>9} days {monthly:>8.2f} "
              f"{year['usd_total']:>9.2f}")
    print()
    print(f"  A year of 2026 history is {year['slices']} slices and "
          f"${year['usd_total']:.2f} at ANY pace — the pace only decides how long "
          f"it takes and how much lands in one month.")
    print("  It is NOT armed. Arming means a cron in "
          ".github/workflows/backfill-gdelt-2026.yml,")
    print("  which is a spend decision. Queue a slice by hand instead:")
    print("    gh workflow run drain-writers.yml -f enqueue=backfill-gdelt-2026.yml \\")
    print("         -f inputs_json='{\"start\":\"2026-01-01\",\"end\":\"2026-07-26\","
          "\"slice\":\"true\"}' \\")
    print("         -f reason='history walk, slice 1'")


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
    # Not `required`: --plan-cost answers a question about the pace and
    # needs no window to answer it.
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-readthroughs", type=int,
                    default=DEFAULT_MAX_READTHROUGHS,
                    help=f"hard stop on FULL classifications for the whole run "
                         f"(default {DEFAULT_MAX_READTHROUGHS}, DERIVED from "
                         f"${MONTHLY_WALKER_BUDGET_USD:.2f}/month at one slice a "
                         f"day; was 1200, which is ~$1.54 a slice and ~$142 for a "
                         f"year of history). See --plan-cost.")
    ap.add_argument("--plan-cost", action="store_true",
                    help="print what each pace costs and exit. Fetches nothing, "
                         "calls nothing, writes nothing.")
    ap.add_argument("--fetch-only", action="store_true",
                    help="fetch and prefilter, call no model, store nothing — "
                         "proves the collector without spending anything")
    ap.add_argument("--slice", action="store_true",
                    help="do ONE bounded slice of --start..--end, resuming from "
                         "the committed cursor, then stop. Without this the run "
                         "does the whole window in one go, which is what held "
                         "the writer lock for 350 minutes on 2026-07-29.")
    ap.add_argument("--slice-days", type=int, default=SLICE_DAYS,
                    help=f"days per slice (default {SLICE_DAYS}; see the constant)")
    ap.add_argument("--budget-minutes", type=float,
                    default=backfill_slices.SLICE_BUDGET_MINUTES,
                    help="stop at the next day boundary after this long")
    ap.add_argument("--emit-next", help="write the slice ticket here, for "
                                        "backfill_slices.py record")
    ap.add_argument("--state", help="slice state file (default data/backfill_state.json)")
    args = ap.parse_args()

    if args.plan_cost:
        print_cost_plan()
        return 0

    requested_start = date.fromisoformat(args.start)
    requested_end = min(date.fromisoformat(args.end), date.today())

    job = window = None
    if args.slice:
        job, window = backfill_slices.open_slice(
            workflow=WORKFLOW, unit="days",
            start=requested_start.isoformat(), end=requested_end.isoformat(),
            slice_size=args.slice_days, state_path=args.state,
            inputs={"dry_run": "false", "fetch_only": "false",
                    "max_readthroughs": str(args.max_readthroughs)})
        if window is None:
            print(f"[gdelt] {backfill_slices.job_id(WORKFLOW, args.start, args.end)} "
                  "is already complete — nothing to do.")
            return 0
        start, end = (date.fromisoformat(window[0]), date.fromisoformat(window[1]))
        print(f"[gdelt] SLICE {start}..{end} of {requested_start}..{requested_end} "
              f"(slice {job['slices'] + 1}, budget {args.budget_minutes:g} min)")
    else:
        start, end = requested_start, requested_end

    budget = backfill_slices.Budget(args.budget_minutes)
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
    # The last day-window this run FINISHED. The cursor is derived from it, so
    # a run that stops on its budget half way through a slice resumes on the
    # exact next day rather than repeating or skipping one.
    done_through: date | None = None

    print(f"[gdelt] {len(queries)} queries x {(end - start).days + 1} day-windows")

    for lo, hi in iter_windows(start, end):
        if budget.expired():
            stopped_early = budget.reason()
            break
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
        done_through = lo.date()

    g = gdelt.STATS
    print(f"\nBACKFILL {start}..{end}")
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

    # Publishing is a SEPARATE gate from collecting, and a slice must survive
    # it failing. This is not hypothetical: the first live sliced run
    # (30481065108) collected its quarter and then died inside
    # `publish.publish` because the publish guardrails held eight open
    # findings — so the ticket was never emitted, the cursor never moved, and
    # the chain stopped with nothing recorded. The rows are real either way.
    blocked = ""
    if not (args.dry_run or args.fetch_only):
        try:
            publish.publish(conn)
        except publish.PublishError as exc:
            blocked = f"publish refused: {exc}"
            print(f"\nPUBLISH FAILED: {exc}", file=sys.stderr)

    # The slice ticket. Emitted BEFORE the fail-loud checks below on purpose: a
    # run that collected four days and then hit a broken fetch has still done
    # four days, and the whole point of slicing is that finished work is never
    # thrown away by however the run ends. A run that finished NOTHING emits a
    # cursor that has not moved, which `backfill_slices record` refuses to
    # requeue and goes red on — so a broken chain stops itself rather than
    # spinning.
    #
    # Nothing is emitted for a dry or fetch-only run: those store nothing, so a
    # chain of them would advance the cursor over days it never collected.
    if args.slice and args.emit_next and not (args.dry_run or args.fetch_only):
        cursor = (backfill_slices.advance(done_through.isoformat(), "days")
                  if done_through else job["cursor"])
        backfill_slices.emit(args.emit_next, backfill_slices.slice_ticket(
            job, start.isoformat(), end.isoformat(),
            next_cursor=cursor,
            totals={"stored": stored, "duplicates": duplicates,
                    "rejected": rejected, "windows": windows},
            stopped_early=stopped_early, halt=blocked))
        print(f"  next cursor        {cursor}")
    if blocked:
        return 1

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
