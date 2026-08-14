#!/usr/bin/env python3
"""Historical walker for Google News, one day-window at a time.

WHY THIS EXISTS, AND WHAT IT CORRECTS
-------------------------------------
This repo has said in three places — CLAUDE.md's sibling notes, the header of
`backfill_gdelt_2026.py`, and `.github/workflows/backfill-gdelt-2026.yml` —
that **"Google News RSS has no archive; it serves a recent window and nothing
else."** That is the stated reason GDELT exists. It is FALSE, and it was
measured false on 2026-07-30:

    query                                       items   pubDate span
    (no operator)                                 100   2026-03-11..07-30
    when:7d                                        50   2026-07-23..07-30
    after:2026-01-01 before:2026-02-01            100   2026-01-02..01-30
    after:2026-01-05 before:2026-01-06             16   2026-01-05..01-06
    after:2025-03-01 before:2025-04-01             92   2025-03-03..04-01
    after:2021-03-01 before:2021-04-01             36   2021-03-01..04-01
    after:2016-03-01 before:2016-04-01             12   2016-03-01..03-29

`after:` and `before:` are honoured, the returned pubDates fall inside the
window, and the archive reaches at least to 2016. The 51 non-English editions
are reachable the same way, which matters because `registry.GDELT_QUERIES` is
English-only by design, so GDELT structurally cannot walk the history of the
markets the recall worklist says hold nothing (AU, CA, JP, GB, IN, BR, CN, DE,
SA, SG, AE, AR, CH, CO all at 0%; non-US funding at 2.3%).

THE 100-ITEM CAP, AND WHY THE WINDOW IS ONE DAY
-----------------------------------------------
Google News RSS returns at most **100 items per query** and offers no
pagination. A month window hits that cap and is silently truncated. Slicing is
what recovers the rest, and the recovery was measured rather than assumed —
January 2026, one leadership query, en:US:

    one 31-day query        100 unique articles   (at the cap)
    31 one-day queries      170 unique articles
    in daily, not month      70
    in month, not daily       0     <- the month's set is a strict SUBSET

So a day window loses nothing a month window held and finds 70% more. Busiest
single day in that month returned 22 items against the 100 cap — 4.5x headroom,
which is why one day is enough and half-days are not needed. `RESULT_CAP` and
the truncation counter in the report are the guard: if a single query ever
comes back at the cap, the window is too wide and the report says so.

WHAT IT COSTS, WHICH IS THE PART THAT DECIDES THE SHAPE
-------------------------------------------------------
Measured on three real historical days (2026-01-14, 02-11, 03-18), full
52-edition sweep, 156 requests each, ~2.6 min of fetching:

    fetched (URL-deduped)      643 / 679 / 666
    past the free prefilter    401 / 444 / 417        mean ~421
    reaching the gate          94% of those           mean ~395
    closed deterministically   ~6% ($0, cheap_extract)
    already seen                 0 of 444             <- virgin history

At the ledger's measured prices (gate $0.00003, read $0.00128) and the ledger's
measured gate survival (~155 of ~1,050 screened, 15%), **one day of history
swept in full costs about $0.088**, so a year of 2026 is about **$32**.

The GDELT walker closes a year for $4.51. Thirty-two dollars against a ~$5/month
product budget is not a pace anybody can choose, so a full-breadth sweep of
2026 through Google News is REFUSED here. What is built instead is a walker
that rations the *gate*, because the arithmetic says it must:

    gate alone, year of 2026, full sweep = 366 x 395 x $0.00003 = $4.34

Merely LOOKING at a year of Google News across 52 editions costs as much as
GDELT's entire year including its reads. A read ceiling on its own — the shape
`backfill_gdelt_2026.py` uses — cannot fix that, and worse, it would stall this
chain on its first day: at ~59 reads demanded per day of history and a
budget-derived ceiling in the tens, the ceiling binds inside window one, the
run finishes no window, `done_through` never moves, and `backfill_slices.record`
correctly refuses to requeue a cursor that did not advance.

SO THE BUDGET BUYS A RATION PER DAY, AND RANKING DECIDES WHO GETS IT
--------------------------------------------------------------------
`DAILY_GATE_RATION` is DERIVED from `MONTHLY_WALKER_BUDGET_USD` (see below),
never typed. Each day window ranks its candidates with `pipeline.candidate_rank`
— free, no model, no network — and gates the top N. The rest are left
**unmarked**, which is the property that makes this honest: the walk is a
SAMPLE of history and says so, and a second walk of the same range skips
everything the first one stored (`store.already_seen`, free) and spends its
ration on the next-best candidates. Coverage converges by repetition at a pace
the owner sets, instead of demanding $32 up front.

That is the one material difference from the GDELT walker and it is deliberate.
There, the ceiling is a STOP. Here it is a RATION, because the corpus is denser
than the budget and a stop would be a stall.

    python backfill_gnews_2026.py --plan-cost     # the table; fetches nothing
    python backfill_gnews_2026.py --start 2026-01-01 --end 2026-01-04 --fetch-only
    python backfill_gnews_2026.py --start 2026-01-01 --end 2026-01-31 --dry-run

Everything goes through the SAME pipeline as the daily collector — prefilter,
resolve, precheck, cheap_extract, gate, read-through, validate, store, publish
— so every guard applies and nothing is written directly. Google News is an
aggregator, so `resolve_source_url` runs before anything is judged and the
publisher's own URL is what gets stored; an unresolved item keeps the outlet
homepage and `validate.precheck` rejects it as a bare domain rather than
crediting the aggregator.

IT IS NOT ARMED, and arming it is a spend decision that belongs to the owner.
`tests/test_backfill_pace.py` refuses to let a cron appear unnoticed and pins
the cursor property: progress advances per RUN, never per date. That pairing is
the sibling's `edgar-history-sweep` mistake — a date ordinal against an hourly
cron, ~$3.80 a day of re-extraction for six days out of runs that were all
green.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import Counter
from datetime import date, timedelta

import backfill_slices
import source_registry as registry
from collectors import google_news
from pipeline import (candidate_rank, cheap_extract, classify, dedupe,
                      gate_ledger, prefilter, publish, schema, store,
                      validate)

COLLECTOR = google_news.COLLECTOR
WORKFLOW = "backfill-gnews-2026.yml"

#: Google News RSS returns at most this many items for one query and offers no
#: pagination — no offset, no cursor, no continuation token. A query that comes
#: back at exactly this number was TRUNCATED and the rest of its window is
#: unreachable at that width. Measured 2026-07-30; the report counts truncated
#: queries for exactly this reason.
RESULT_CAP = 100

#: One day. See "THE 100-ITEM CAP" above: a month query truncates to 100 while
#: 31 day queries return 170 of which the month's 100 are a strict subset, and
#: the busiest single day of that month returned 22 against the cap.
WINDOW_DAYS = 1

#: Days of history per slice, sized by WALL CLOCK and not by money — the ration
#: below is what bounds the money. Measured per day of history: 2.6 min to fetch
#: 156 queries at a 0.4s pace, 1.9 min to resolve ~440 aggregator redirects at
#: 0.26s each on a keep-alive session, plus the model time for the ration. Four
#: days is ~40 minutes, inside backfill_slices.SLICE_BUDGET_MINUTES (50) with
#: room for the slowest observed day, and the Budget object is the actual
#: promise rather than this estimate.
SLICE_DAYS = 4


# --------------------------------------------------------------------------
# COST — measured per item, rationed per day, derived from a monthly budget
# --------------------------------------------------------------------------

#: What this walker may spend in a month, all-in. Deliberately BELOW the GDELT
#: walker's $1.50: the two share one ~$5/month product budget and GDELT's chain
#: is the one already dispatched, so a second walker helping itself to an equal
#: share would put $3 of $5 into backfills. A SIZING input, not an enforcement
#: point — enforcement is spend.py at 90% of the monthly allowance and the
#: OpenRouter key's own hard cap behind it.
MONTHLY_WALKER_BUDGET_USD = 1.00

#: Measured per-item prices, from the ledger's own accounting rather than a rate
#: card. See docs/TECHLOG.md "cost levers, second pass": gate 141 tokens in / 35
#: out, read-through 3,100 in / 400 out.
GATE_USD_PER_ITEM = 0.00003
READ_USD_PER_ITEM = 0.00128

#: Fraction of gated candidates that survive the gate and buy a read. From the
#: same ledger entry: a real run screened ~1,050 and had 155 survivors (60 read,
#: 95 budget-deferred). This is the ONE number in the model taken from the daily
#: collector rather than measured on historical windows, because measuring it
#: needs the API key and therefore real spend; it is named here so a future
#: session can correct it in one place.
GATE_SURVIVAL = 0.15

#: The all-in price of gating ONE candidate: the gate call itself plus the
#: expected fraction of a read it buys. Rationing the gate rather than the read
#: is what makes this the right unit — under a read-only ceiling the gate cost
#: is unbounded, which is how a $1/month walker quietly becomes a $4.34/year
#: one before it reads a single article.
USD_PER_GATED_CANDIDATE = GATE_USD_PER_ITEM + GATE_SURVIVAL * READ_USD_PER_ITEM

#: Candidates one day-window of a full 52-edition sweep puts past the free
#: prefilter and the free reducers. MEASURED on 2026-01-14, 2026-02-11 and
#: 2026-03-18: 401 / 444 / 417 past the prefilter, of which 94% survived
#: resolve, already-seen, precheck, the funding-duplicate check and the
#: deterministic extractor. Used only to state what the ration is a fraction OF;
#: nothing is bounded by it.
MEASURED_CANDIDATES_PER_DAY = 395

#: Candidates gated per day of history. DERIVED from the budget at one slice a
#: day, so changing the budget changes this and the two can never disagree.
#: Printed by --plan-cost together with what fraction of a day it actually
#: reads, because a ration that does not say how much it is leaving behind is a
#: coverage claim rather than a budget.
DAILY_GATE_RATION_STATIC = max(
    1, int(MONTHLY_WALKER_BUDGET_USD / 30 / SLICE_DAYS / USD_PER_GATED_CANDIDATE))

#: The same figure under its old name, for --plan-cost and for the sizing
#: arithmetic above — both of which are about a WHOLE month and have no clock
#: in them. A RUN uses live_ration() instead.
DAILY_GATE_RATION = DAILY_GATE_RATION_STATIC


def live_ration() -> tuple[int, str]:
    """The ration this run may actually buy, and the sentence that says why.

    The static figure above assumes a fresh month and one slice a day. Neither
    is true late in a month somebody has already spent, and a walker that
    keeps taking a whole month's ration on the 28th is how catch-up work ends
    up starving the collectors — which is exactly what August did.

    So the ration comes from `budget.walker_ration`: this walker's share of
    what is LEFT in the discretionary pot, spread over the days actually left.
    It gets smaller as the month tightens and reaches 0 only when the pot is
    gone, which is a skip and not a failure. Returns `(units, disclosure)`;
    0 units means buy nothing, say so, exit zero.
    """
    import budget

    return budget.walker_ration(
        monthly_walker_budget_usd=MONTHLY_WALKER_BUDGET_USD,
        usd_per_unit=USD_PER_GATED_CANDIDATE,
        per_slice_days=SLICE_DAYS)


#: A per-run backstop on FULL read-throughs, in case a window's gate survival
#: runs far above the measured 15%. It is NOT the mechanism — the ration is —
#: and it is sized well above the expectation so that in ordinary running it
#: never binds and never stalls a window.
DEFAULT_MAX_READTHROUGHS = max(
    1, int(DAILY_GATE_RATION * SLICE_DAYS * GATE_SURVIVAL * 3))


def window_cost(*, gated: int, reads: int | None = None) -> dict:
    """What gating `gated` candidates costs at the measured prices.

    Pure: no network, no model, no clock. `reads` defaults to the measured gate
    survival, which is the whole point of pricing the GATE rather than the read
    — a gate call is never free of the read it implies.
    """
    if reads is None:
        reads = gated * GATE_SURVIVAL
    gate = gated * GATE_USD_PER_ITEM
    read = reads * READ_USD_PER_ITEM
    return {"gate_usd": gate, "read_usd": read, "usd": gate + read,
            "reads": reads}


def year_projection(*, ration: int = DAILY_GATE_RATION,
                    days_per_slice: int = SLICE_DAYS, days: int = 366) -> dict:
    """Closing a year of history at a given ration: slices, days, dollars."""
    slices = math.ceil(days / max(days_per_slice, 1))
    per_slice = window_cost(gated=ration * days_per_slice)
    depth = ration / MEASURED_CANDIDATES_PER_DAY
    return {
        "slices": slices,
        "ration": ration,
        "usd_per_slice": per_slice["usd"],
        "usd_total": per_slice["usd"] * slices,
        "reads_total": per_slice["reads"] * slices,
        "read_depth": depth,
    }


def print_cost_plan() -> None:
    """The table the owner needs to choose a ration and a cadence. Calls nothing."""
    full = window_cost(gated=MEASURED_CANDIDATES_PER_DAY)
    print("GOOGLE NEWS HISTORICAL WALKER — measured prices, measured density")
    print(f"  gate  ${GATE_USD_PER_ITEM:.5f}/item   "
          f"read  ${READ_USD_PER_ITEM:.5f}/item   "
          f"gate survival {GATE_SURVIVAL:.0%}")
    print(f"  one day of history, 52 editions x 3 queries: "
          f"~{MEASURED_CANDIDATES_PER_DAY} candidates reach the gate")
    print()
    print("  THE REFUSAL, WITH ITS NUMBER")
    print(f"    sweeping a day IN FULL        ${full['usd']:.4f}  "
          f"(gate ${full['gate_usd']:.4f} + read ${full['read_usd']:.4f})")
    print(f"    a year of 2026 in full        ${full['usd'] * 366:>7.2f}")
    print(f"    ... of which the GATE alone   "
          f"${MEASURED_CANDIDATES_PER_DAY * 366 * GATE_USD_PER_ITEM:>7.2f}   "
          "<- as much as GDELT's whole year")
    print("    GDELT's equivalent            $   4.51")
    print("    So a full-breadth sweep is NOT built. The gate is rationed.")
    print()
    print(f"  slice = {SLICE_DAYS} day-windows, ration {DAILY_GATE_RATION} "
          f"candidates gated per day "
          f"(derived from ${MONTHLY_WALKER_BUDGET_USD:.2f}/month at 1 slice/day)")
    print(f"  that reads {DAILY_GATE_RATION / MEASURED_CANDIDATES_PER_DAY:.1%} "
          "of a day, RANKED by country need — the rest stay unmarked, so a "
          "second walk of the same")
    print("  range costs the same again and buys entirely different rows.")
    print()
    print(f"  {'pace':<20} {'wall clock':>13} {'$/month':>9} {'$ 2026':>8} "
          f"{'reads':>7}")
    year = year_projection()
    for label, per_day in (("1 slice/day", 1), ("2 slices/day", 2),
                           ("4 slices/day", 4)):
        days = math.ceil(year["slices"] / per_day)
        monthly = year["usd_per_slice"] * per_day * 30
        print(f"  {label:<20} {days:>8} days {monthly:>8.2f} "
              f"{year['usd_total']:>8.2f} {year['reads_total']:>7.0f}")
    print()
    print(f"  A year of 2026 is {year['slices']} slices and "
          f"${year['usd_total']:.2f} at ANY pace; the pace decides how long it "
          "takes and")
    print("  how much lands in one month. Each further pass costs the same and "
          "reads new rows.")
    print()
    print("  It is NOT armed. Arming means a cron in "
          ".github/workflows/backfill-gnews-2026.yml,")
    print("  which is a spend decision. Queue a slice by hand instead:")
    print("    gh workflow run drain-writers.yml -f enqueue=backfill-gnews-2026.yml \\")
    print("         -f inputs_json='{\"start\":\"2026-01-01\",\"end\":\"2026-07-26\","
          "\"slice\":\"true\"}' \\")
    print("         -f reason='google news history walk, slice 1'")


# --------------------------------------------------------------------------
# fetching one historical day
# --------------------------------------------------------------------------

def historical_queries(lang: str, lo: date, hi: date) -> list[str]:
    """One edition's phrase pack, bounded to [lo, hi).

    `after:`/`before:` replace the daily collector's `when:Nd`. They are what
    makes the archive reachable, and mixing the two would be a recency filter
    intersected with a historical window — an empty set for every day older than
    the recency figure, silently.
    """
    phrases = registry.GOOGLE_NEWS_VOCAB.get(lang, registry.GOOGLE_NEWS_VOCAB["en"])
    return [f"{p} after:{lo.isoformat()} before:{hi.isoformat()}" for p in phrases]


def all_locales() -> list[tuple[str, str]]:
    """The anchor plus every rotation edition. The daily collector visits five a
    run; a historical day is visited once ever, so it gets all of them."""
    return [registry.GOOGLE_NEWS_ANCHOR] + list(registry.GOOGLE_NEWS_LOCALES)


def parse_locales(spec: str) -> list[tuple[str, str]]:
    """`--locales en:US,he:IL` -> [("en","US"), ("he","IL")].

    A locale whose language has no phrase pack is REFUSED rather than falling
    back to English: that fallback is what
    `tests/test_locale_rotation.py` exists to prevent, because an edition asked
    in the wrong language returns a silent near-zero that reads as coverage.
    """
    out = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        lang, _, country = chunk.partition(":")
        if not country:
            raise SystemExit(f"--locales wants lang:COUNTRY pairs, got {chunk!r}")
        if lang not in registry.GOOGLE_NEWS_VOCAB:
            raise SystemExit(
                f"no GOOGLE_NEWS_VOCAB phrase pack for {lang!r}. Asking an "
                "edition in the wrong language returns a silent near-zero that "
                "looks like coverage; add the pack first.")
        out.append((lang, country.upper()))
    return out


def fetch_day(lo: date, hi: date, locales, *, pause: float = 0.4,
              stats: Counter | None = None) -> list[dict]:
    """Every edition, every phrase, one day. URL-deduped within the day."""
    counters = stats if stats is not None else Counter()
    seen: set[str] = set()
    out: list[dict] = []
    for lang, country in locales:
        for query in historical_queries(lang, lo, hi):
            counters["queries"] += 1
            try:
                items = google_news.fetch(query, lang=lang, country=country)
            except Exception:
                # One edition being unreachable must not lose the other fifty.
                counters["query_errors"] += 1
                continue
            counters["items_raw"] += len(items)
            if len(items) >= RESULT_CAP:
                # See RESULT_CAP: this window was silently truncated. The window
                # has to get smaller, never the query broader.
                counters["truncated"] += 1
            for item in items:
                key = item["discovery_url"]
                if key in seen:
                    counters["duplicate_url"] += 1
                    continue
                seen.add(key)
                item["locale"] = f"{country}:{lang}"
                out.append(item)
            time.sleep(pause)
    return out


def iter_windows(start: date, end: date):
    """Inclusive [start, end] as half-open day windows, which is the shape
    `after:`/`before:` take."""
    day = start
    while day <= end:
        yield day, day + timedelta(days=WINDOW_DAYS)
        day += timedelta(days=WINDOW_DAYS)


# --------------------------------------------------------------------------

@gate_ledger.around_run(WORKFLOW)
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--plan-cost", action="store_true",
                    help="print what each pace costs and exit. Fetches nothing, "
                         "calls nothing, writes nothing.")
    ap.add_argument("--fetch-only", action="store_true",
                    help="fetch, resolve and free-filter only — calls no model, "
                         "spends nothing, stores nothing")
    ap.add_argument("--ration", type=int, default=None,
                    help=f"candidates GATED per day-window. Blank DERIVES it "
                         f"from what is left in the discretionary pot over the "
                         f"days left in the month (budget.walker_ration), so a "
                         f"lean month runs SMALLER rather than not at all; the "
                         f"static month-sizing figure is "
                         f"{DAILY_GATE_RATION_STATIC} at "
                         f"${MONTHLY_WALKER_BUDGET_USD:.2f}/month. The rest of "
                         f"the day is left unmarked and a later walk picks it "
                         f"up. See --plan-cost.")
    ap.add_argument("--max-readthroughs", type=int,
                    default=DEFAULT_MAX_READTHROUGHS,
                    help=f"per-run backstop on FULL classifications (default "
                         f"{DEFAULT_MAX_READTHROUGHS}). The ration is the "
                         f"mechanism; this only catches a window whose gate "
                         f"survival runs far above the measured "
                         f"{GATE_SURVIVAL * 100:.0f} percent.")
    ap.add_argument("--locales", help="restrict to lang:COUNTRY pairs, comma "
                                      "separated (default: every edition)")
    ap.add_argument("--slice", action="store_true",
                    help="do ONE bounded slice of --start..--end, resuming from "
                         "the committed cursor, then stop")
    ap.add_argument("--slice-days", type=int, default=SLICE_DAYS,
                    help=f"days per slice (default {SLICE_DAYS}; see the constant)")
    ap.add_argument("--budget-minutes", type=float,
                    default=backfill_slices.SLICE_BUDGET_MINUTES,
                    help="stop at the next day boundary after this long")
    ap.add_argument("--emit-next", help="write the slice ticket here, for "
                                        "backfill_slices.py record")
    ap.add_argument("--state", help="slice state file (default data/backfill_state.json)")
    args = ap.parse_args()
    # The decorator could only guess from kwargs, and this one comes from argv.
    # A rehearsal must not leave an uncommitted shard for a real run to push.
    gate_ledger.set_dry_run(args.dry_run)

    if args.plan_cost:
        print_cost_plan()
        return 0

    # WHAT THE BUDGET WILL PAY FOR TODAY. An explicit --ration still wins: a
    # number a human typed beats a derived one, which is the same precedence
    # classify.read_cap uses and for the same reason. With none given, the
    # ration is this walker's share of what REMAINS in the discretionary pot,
    # spread over the days left. A run with no headroom left buys nothing and
    # exits ZERO — it is not broken and not finished, and a red run here would
    # manufacture an alert for the budget behaving as designed.
    if args.ration is None:
        args.ration, ration_basis = live_ration()
        print(f"[gnews] {ration_basis}")
        if args.ration < 1:
            print("[gnews] NOTHING BOUGHT. No cursor moved, no candidate "
                  "marked, no ticket failed. The next funded run resumes on "
                  "the first window this one did not do.")
            return 0
    else:
        ration_basis = (f"ration {args.ration}/day-window, set explicitly on "
                        f"the command line, so the budget-derived figure was "
                        f"not used.")
        print(f"[gnews] {ration_basis}")

    locales = parse_locales(args.locales) if args.locales else all_locales()
    requested_start = date.fromisoformat(args.start)
    requested_end = min(date.fromisoformat(args.end), date.today())

    job = window = None
    if args.slice:
        job, window = backfill_slices.open_slice(
            workflow=WORKFLOW, unit="days",
            start=requested_start.isoformat(), end=requested_end.isoformat(),
            slice_size=args.slice_days, state_path=args.state,
            inputs={"dry_run": "false", "fetch_only": "false",
                    "ration": str(args.ration)})
        if window is None:
            print(f"[gnews] {backfill_slices.job_id(WORKFLOW, args.start, args.end)} "
                  "is already complete — nothing to do.")
            return 0
        start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        print(f"[gnews] SLICE {start}..{end} of {requested_start}..{requested_end} "
              f"(slice {job['slices'] + 1}, budget {args.budget_minutes:g} min)")
    else:
        start, end = requested_start, requested_end

    # A fetch-only run must be as inert as a dry run: it calls no model AND it
    # writes nothing. The free reducers below mark rejections seen, which is a
    # database write, so the flag is computed once and every write asks it.
    writes = not (args.dry_run or args.fetch_only)

    budget = backfill_slices.Budget(args.budget_minutes)
    conn = schema.connect()
    ranking = candidate_rank.Context.for_conn(conn)

    fetch_stats: Counter = Counter()
    filtered = Counter()
    stored_countries: Counter = Counter()
    gated_countries: Counter = Counter()

    fetched = candidates = gated = rationed_off = 0
    stored = duplicates = rejected = skipped = errors = 0
    cheap_closed = known_rounds = 0
    windows = empty_windows = unreached_windows = 0
    stopped_early = ""
    #: The last day-window this run FINISHED. The cursor is derived from it, so a
    #: run that stops on its budget half way through a slice resumes on the exact
    #: next day rather than repeating or skipping one. A window that spends its
    #: whole ration is still FINISHED — that is what a ration means, and it is
    #: the difference between this walker and the GDELT one.
    #:
    #: A window we COULD NOT FETCH is not finished, however, and until
    #: 2026-08-01 it was: this was assigned at the bottom of the loop body
    #: unconditionally, so run 30662474194 walked 2026-01-22, 23 and 24 with
    #: every one of its 576 queries failing and moved the cursor to 01-25 all
    #: the same. See backfill_slices.COLLECTED / EMPTY / UNREACHED.
    done_through: date | None = None

    print(f"[gnews] {len(locales)} editions x 3 queries x "
          f"{(end - start).days + 1} day-windows, ration {args.ration}/day")

    session = None
    try:
        import requests
        session = requests.Session()
    except Exception:
        pass

    for lo, hi in iter_windows(start, end):
        if budget.expired():
            stopped_early = budget.reason()
            break
        windows += 1
        errors_before = fetch_stats["query_errors"]
        items = fetch_day(lo, hi, locales, stats=fetch_stats)
        fetched += len(items)
        window_errors = fetch_stats["query_errors"] - errors_before

        # Three states, not two. `fetch_day` swallows a per-edition failure so
        # that one unreachable edition never loses the other fifty — which is
        # right, and which means "0 articles" arrives here identically whether
        # the day was quiet or the whole endpoint refused us.
        state = backfill_slices.sampled_window(len(items), window_errors)
        if state == backfill_slices.UNREACHED:
            unreached_windows += 1
            stopped_early = backfill_slices.unreached_reason(
                f"{lo:%Y-%m-%d}",
                f"all {window_errors} queries for the day failed")
            print(f"\nSTOPPING: {stopped_early}", file=sys.stderr)
            break
        if state == backfill_slices.EMPTY:
            empty_windows += 1

        kept = []
        for item in items:
            ok, reason = prefilter.passes(item.get("raw_text", ""))
            if ok:
                kept.append(item)
            else:
                filtered[reason.split("(")[0].strip()] += 1
        candidates += len(kept)

        # Google News hands back an aggregator redirect. Resolve BEFORE
        # already-seen and before precheck: the aggregator URL is not the key we
        # dedupe on and is not a receipt, and precheck's bare-domain and
        # job-board rules can only see the publisher's own URL. Measured at
        # 0.26s/item on a keep-alive session, and free.
        for item in kept:
            google_news.resolve_source_url(item, session=session)

        # Free reducers, in run_collect.py's order and for its reasons. Each one
        # runs BEFORE the ration is applied, so the ration is spent on
        # candidates that could actually become rows.
        eligible = []
        for item in kept:
            url = item.get("source_url") or item.get("discovery_url") or ""
            if url and store.already_seen(conn, url):
                skipped += 1
                continue
            try:
                validate.precheck(item)
            except validate.Rejected:
                rejected += 1
                if url and writes:
                    store.mark_seen(conn, url, COLLECTOR, "rejected")
                continue
            parsed = cheap_extract.parse_funding(item)
            known = parsed is not None and dedupe.funding_event_duplicate(
                    conn, parsed.company_key, parsed.amount_usd,
                    parsed.amount_canon)
            if known:
                known_rounds += 1
                duplicates += 1
                if url and writes:
                    # The article is dropped, but not the fact that a second
                    # outlet reported this round. See run_collect.py and
                    # pipeline/guardrails.CORROBORATION_MIN_OUTLETS.
                    store.record_corroboration(
                        conn, known, source_url=url,
                        source_name=item.get("source_name") or "",
                        amount_usd=parsed.amount_usd, collector=COLLECTOR)
                    store.mark_seen(conn, url, COLLECTOR, "duplicate")
                continue
            eligible.append(item)

        # THE RATION. `rank` is a permutation and costs nothing; taking the head
        # of it is what the budget buys. Everything after the cut is left
        # UNMARKED on purpose, so a later walk of the same range reads it.
        eligible = candidate_rank.rank(eligible, ranking)
        rationed_off += max(0, len(eligible) - args.ration)
        day_batch = eligible[:args.ration]

        print(f"\n[{lo:%Y-%m-%d}] {len(items)} articles, {len(kept)} past the "
              f"free filter, {len(eligible)} eligible, "
              f"{len(day_batch)} gated ({len(eligible) - len(day_batch)} left "
              f"for a later walk)")

        for item in day_batch:
            url = item.get("source_url") or item.get("discovery_url") or ""
            if args.fetch_only:
                gated_countries[candidate_rank.candidate_country(item) or "?"] += 1
                print(f"  WOULD GATE   {item['headline'][:70]}")
                continue
            if classify.STATS["full_calls"] >= args.max_readthroughs:
                stopped_early = (f"--max-readthroughs ({args.max_readthroughs}) "
                                 f"reached at {lo:%Y-%m-%d}")
                break

            # Cost lever 1: a headline that states every field closes for $0 and
            # goes through the same validate -> store path, marked on `notes`.
            classified = cheap_extract.extract(item)
            cheap = classified is not None
            if cheap:
                cheap_closed += 1
            else:
                try:
                    classified = classify.classify(item)
                except classify.CreditsExhausted:
                    stopped_early = "OpenRouter credits exhausted"
                    break
                except classify.AuthFailed as exc:
                    print(f"\nSTOPPING: {exc}", file=sys.stderr)
                    return 1
                except classify.BudgetDeferred as exc:
                    stopped_early = f"read-through cap: {exc}"
                    break
                except classify.Throttled:
                    # Historical news is not going anywhere: leave it unseen and
                    # a later walk of the same window picks it up.
                    errors += 1
                    gate_ledger.outcome(item, "deferred")
                    continue
                except classify.ClassifyError:
                    errors += 1
                    gate_ledger.outcome(item, "error")
                    continue
                gated += 1

            if classified is None:
                rejected += 1
                # A gate NO already closed its own line as `gate_reject` and
                # `outcome()` refuses to overwrite it. A cheap close never
                # reached the gate at all, so it has no line to close and
                # `outcome()` ignores it.
                gate_ledger.outcome(item, "model_reject")
                if writes:
                    store.mark_seen(conn, url, COLLECTOR, "rejected")
                continue
            try:
                # conn: without it identity.enrich() inside build_signal is a
                # no-op and the row lands with no ticker, type or HQ.
                signal = validate.build_signal(classified, item, COLLECTOR,
                                               conn=conn)
            except validate.Rejected:
                rejected += 1
                gate_ledger.outcome(item, "validate_reject")
                if writes:
                    store.mark_seen(conn, url, COLLECTOR, "rejected")
                continue

            if cheap:
                # The evidence marker: this row was parsed from stated text and
                # no model read it. Confidence is unchanged — the source is
                # exactly as credible either way and stays capped at "reported".
                signal.notes = cheap_extract.EVIDENCE_NOTE

            if args.dry_run:
                stored += 1
                gate_ledger.outcome(item, "would_store")
                stored_countries[signal.country or "-"] += 1
                print(f"  WOULD STORE  [{signal.country or '--'}] "
                      f"{signal.headline[:64]}")
                continue

            outcome = store.store(conn, signal)
            gate_ledger.outcome(item, outcome)
            store.mark_seen(conn, url, COLLECTOR, outcome)
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
        # A window that spent its whole ration is FINISHED. See done_through.
        done_through = lo

    print(f"\nBACKFILL {start}..{end}")
    print(f"  windows            {windows} ({empty_windows} empty, "
          f"{unreached_windows} UNREACHED)")
    print(f"  queries sent       {fetch_stats['queries']} "
          f"({fetch_stats['query_errors']} failed, "
          f"{fetch_stats['truncated']} truncated at the {RESULT_CAP} cap)")
    print(f"  articles           {fetch_stats['items_raw']} raw -> {fetched} "
          f"after de-dup (same URL {fetch_stats['duplicate_url']})")
    print(f"  free filter        {fetched} -> {candidates} candidates")
    for reason, count in filtered.most_common():
        print(f"      {count:5d}  {reason}")
    # NO SILENT CAPS. A truncated run has to say what it did not do, in the
    # same breath as what it did, and name the ceiling that decided it.
    print(f"  left for a later walk  {rationed_off} "
          f"(ration {args.ration}/day)")
    if rationed_off:
        print(f"      DROPPED FOR BUDGET, NOT FOR A VERDICT: {rationed_off} "
              f"candidate(s) reached the gate and were not gated. They are "
              f"UNMARKED, so a later funded walk of the same range reads them.")
        print(f"      {ration_basis}")
    print(f"  gate calls         {classify.STATS['gate_calls']} "
          f"({classify.STATS['gate_rejects']} rejected there, cost avoided)")
    print(f"  read-throughs      {classify.STATS['full_calls']}   "
          f"closed for $0: {cheap_closed}")
    print(f"  stored={stored} duplicate={duplicates} rejected={rejected} "
          f"already-seen={skipped} rounds-already-held={known_rounds} "
          f"transient-errors={errors}")
    if stored_countries:
        print(f"  countries stored   "
              f"{len([c for c in stored_countries if c != '-'])}: "
              + ", ".join(f"{c}={n}" for c, n in stored_countries.most_common()))
    if gated_countries:
        print("  would-gate by country: "
              + ", ".join(f"{c}={n}" for c, n in gated_countries.most_common(12)))
    if fetch_stats["truncated"]:
        print(f"  ** {fetch_stats['truncated']} queries came back at the "
              f"{RESULT_CAP}-item cap, so those windows were TRUNCATED. The "
              "window has to get smaller, not the query broader.")
    if stopped_early:
        print(f"  STOPPED EARLY      {stopped_early}")

    # Publishing is a SEPARATE gate from collecting and a slice must survive it
    # failing: the first live sliced GDELT run collected its quarter and then
    # died inside publish.publish on open guardrail findings, so its ticket was
    # never emitted and the chain stopped with nothing recorded.
    blocked = ""
    if not (args.dry_run or args.fetch_only):
        try:
            publish.publish(conn)
        except publish.PublishError as exc:
            blocked = f"publish refused: {exc}"
            print(f"\nPUBLISH FAILED: {exc}", file=sys.stderr)

    # Emitted BEFORE the fail-loud checks below, on purpose: a run that finished
    # four days and then hit a broken fetch has still done four days, and the
    # commit step runs `if: !cancelled()` for the same reason, so this ticket is
    # recorded and requeued even when this function returns 1.
    #
    # THAT IS WHY `done_through` CARRIES THE WHOLE GUARANTEE. Going red below
    # does not un-advance a cursor. The only thing standing between a broken
    # fetch and a permanently skipped day is that an UNREACHED window never sets
    # `done_through` — a run that fetched nothing therefore emits a cursor that
    # has not moved, which `backfill_slices record` refuses to requeue and goes
    # red on, so a broken chain stops itself rather than spinning.
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
                    "rejected": rejected, "windows": windows,
                    "left_for_later": rationed_off},
            stopped_early=stopped_early, halt=blocked))
        print(f"  next cursor        {cursor}")
    if blocked:
        return 1

    # FAIL LOUD, and this one is now the LOUD HALF OF A GUARD THAT ALREADY
    # WORKED. Run 30662474194 returned 1 here and the chain advanced anyway,
    # because the ticket above had already been written from a `done_through`
    # that counted an unfetchable day as a finished one. The cursor is what
    # decides whether a day is skipped forever; going red is what gets somebody
    # to look. Both are needed and neither substitutes for the other.
    if unreached_windows:
        print(f"\nSTOPPING: {unreached_windows} window(s) could not be fetched "
              f"at all — every query for the day errored. The cursor was NOT "
              f"moved past them, so the next slice starts on the same day and "
              f"nothing is skipped. Check that Google News is answering this "
              f"runner's IP and that after:/before: are still honoured.",
              file=sys.stderr)
        return 1

    # A day of world news across 52 editions cannot be empty. If every window
    # came back with nothing AND nothing errored, the FETCH is broken in a way
    # that reports success — a changed endpoint, an operator Google stopped
    # honouring, a parser returning [] — and the run must not exit green on it.
    # The first SEC backfill dispatch exited 0 after five silent 403s and looked
    # exactly like a successful run that found nothing.
    if windows and empty_windows == windows:
        print("\nSTOPPING: every window returned zero articles. A historical day "
              "of world news across every Google News edition cannot be empty, "
              "so the fetch itself is failing — check the query error count "
              "above, and check that after:/before: are still honoured.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
