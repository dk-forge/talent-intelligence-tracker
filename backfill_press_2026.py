#!/usr/bin/env python3
"""Historical walker for the publisher catalogue, one ROSTER SLICE at a time.

WHAT PROBLEM THIS IS FOR, MEASURED
----------------------------------
`data/recall_rejection_audit.json`: of 81 gold-set misses, **zero were fetched
and rejected** — there is no filter defect — and **51 are `outside_our_history`**.
Every one of those 51 was published between 2026-07-01 and 2026-07-17. The news
collectors first ran on 2026-07-27 and `national_press` on 2026-07-29.

An RSS feed is a window, not an archive. `collectors/press_archive.py` reads the
same publishers' XML SITEMAPS instead, which is a document written for crawlers
and can reach years.

WHY THE CURSOR WALKS PUBLISHERS AND NOT DAYS
--------------------------------------------
This is the one structural difference from `backfill_gdelt_2026.py` and
`backfill_gnews_2026.py`, and it is not a preference.

A GDELT or Google News day costs a fetch. A publisher's sitemap costs the same
fetch whether the window is one day or six months — you download the index and
its children either way, and the date is a FILTER over rows the document
returned anyway. So a date cursor here would advance over work that had never
been done: it would "finish" 2026-01-01..04 having fetched every publisher's
whole 2026 and thrown 99% of it away, then fetch it all again for 01-05..08.

That is exactly the case `backfill_slices.UNITS` documents for
`companies_house`, so this uses the same `slices` unit: the cursor is an index
into a deterministic partition of the publisher roster, and the date window is a
fixed input carried on the job. The consequence is a good one — **widening the
window is free.** Walking 2026-01-01..2026-07-26 costs what walking one week
costs.

The property `tests/test_backfill_pace.py` asserts is unchanged and is asserted
for this walker too: progress advances per RUN. Two runs in the same clock
second walk two different slices of the roster.

WHAT IT COSTS, AND WHAT WAS REFUSED
-----------------------------------
Measured on 82 catalogue publishers with the SHIPPING CODE, 2026-07-30, against
2026-03 — a month that predates us and is out of reach of a 48-hour news
sitemap, so no section page's `<lastmod>` can fake it:

    reached 2026-03 at all                         34 / 82   (41%)
    ... with 50 URLs or more                       25 / 82
    ... with 100 or more                           23 / 82
    URLs per reaching publisher for that month     median 163, mean 233
    slug prefilter survival over all of them       244 / 7,910   (3.08%)
    real title+teaser survival, same URLs          ~5.6%     (11 of 60 vs slug's 6)
    wall clock                                     980s for 82 (median 5.3s each)

Scaled to the 653 catalogue feeds: about **271 publishers reach an arbitrary
2026 month**, about **233 URLs each**, about **5.6% of them candidates**, so
about **3,500 candidates per month of history** — call it 115 per day of
history against the Google News walker's measured 395.

At the ledger's measured prices (gate $0.00003, read $0.00128, gate survival
15%, so $0.000222 all-in per gated candidate):

    one month of 2026 history, every candidate gated      $0.79
    a year of 2026 the same way                           $9.42
    ... against GDELT's whole year                        $4.51
    ... against the Google News walker's year             $3.02

So a full-depth sweep of a year is more expensive than either walker already
built, and it is REFUSED here for the same reason the Google News walker refuses
its own full sweep. The gate is rationed instead, and the ration is DERIVED from
`MONTHLY_WALKER_BUDGET_USD` rather than typed.

THE RATION IS A RATION AND NOT A CEILING, AND THAT IS THE LOAD-BEARING PART
---------------------------------------------------------------------------
`backfill_gnews_2026.py` learned this and it applies with more force here. A
read-only STOP stalls a walker: the ceiling binds inside slice one, the slice
finishes no unit, the cursor never advances, and `backfill_slices.record`
correctly refuses to requeue it — a chain halted behind a green exit code.

A ration lets a slice FINISH partially read. Everything past the cut is left
**unmarked**, so a second walk over the same roster and the same window costs
the same and buys entirely different rows. Coverage converges by repetition at
whatever pace the owner sets, rather than demanding the whole bill up front.

    python backfill_press_2026.py --plan-cost      # the table; fetches nothing
    python backfill_press_2026.py --start 2026-07-01 --end 2026-07-26 --fetch-only
    python backfill_press_2026.py --start 2026-07-01 --end 2026-07-26 --dry-run

HOW MUCH OF THE MEASURED MISS IT WOULD ACTUALLY HAVE CAUGHT
-----------------------------------------------------------
Honestly: not much, and the number is in the header of the test file too.

Of the 51 `outside_our_history` misses, **11** are on a domain this collector
sweeps at all. The other 40 are on domains that are in the catalogue without a
feed (20) or not in the catalogue at all (20), so no amount of history-walking
our own publishers reaches them; they are a SOURCE problem wearing a history
problem's clothes.

Of those 11, each publisher was then run through this collector for the actual
gold window, 2026-07-01..07-26:

    SmartCompany   3 misses   218 URLs, 22 of 26 days covered            YES
    THE BRIDGE     1 miss      65 URLs, 12 of 26 days covered            LIKELY
    PR TIMES       2 misses     4 URLs — the root index points at /tv/,
                               and the main sitemap is the 48h news one  NO
    Globes         2 misses     0 URLs — news sitemap only               NO
    Wamda          2 misses     0 URLs — serves no sitemap at all        NO
    BetaKit        1 miss       0 URLs — news sitemap only               NO

So the reachable ceiling is **4 of 51, about 8%**, before the ration cuts it
further. That is the honest number and it is why this ships dispatch-only with
the cost table attached rather than as a recommendation to run it. An earlier
pass of this same measurement said 942 PR TIMES URLs and "reaches July": that
was its 48-hour news sitemap being counted as archive reach on the day the
probe ran, which is the single easiest mistake to make here.

IT IS NOT ARMED. Arming it is a spend decision that belongs to the owner, and it
is also the writer lock: every workflow in `talent-collect` that grows a cron
either evicts the pending run or becomes an unreplayable orphan.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from datetime import date

import requests

import backfill_slices
from collectors import press_archive
from collectors.national_press import load_feeds
from pipeline import (candidate_rank, cheap_extract, classify, dedupe,
                      gate_ledger, prefilter, publish, schema, store,
                      validate)

COLLECTOR = press_archive.COLLECTOR
WORKFLOW = "backfill-press-2026.yml"

#: Publishers per roster slice. Sized by WALL CLOCK, which is what bounds this
#: walker — the ration below is what bounds the money, and conflating the two is
#: how a walker either stalls or overspends.
#:
#: MEASURED 2026-07-30 with the shipping code. Enumeration alone, 82 publishers
#: over 2026-03: 980 seconds in total, a median of 5.3s each, a mean of 12.0s
#: and a worst case of 77.7s. But a real `--fetch-only` slice of the catalogue's
#: first twelve publishers, WITH head fetches, managed 11 publishers in 6
#: minutes — **33 seconds each**, because a publisher serving 400 URLs in the
#: window is also the one whose index takes four fetches to bisect.
#:
#: So this is sized on the 33s figure and not on the 12s one: 40 publishers is
#: about 22 minutes against `backfill_slices.SLICE_BUDGET_MINUTES` (50), which
#: leaves room for a slice made entirely of the slow kind. The Budget object is
#: the actual promise; this only decides how often it has to be used.
PUBLISHERS_PER_SLICE = 40


# --------------------------------------------------------------------------
# COST — measured per item, rationed per slice, derived from a monthly budget
# --------------------------------------------------------------------------

#: What this walker may spend in a month, all-in. Deliberately the smallest of
#: the three: GDELT's chain has $1.50, the Google News walker $1.00, and those
#: two together are already half the ~$5/month product budget. A third walker
#: taking an equal share would put backfills above the daily collector, which is
#: the wrong shape for a product whose promise is "before the ad appears".
#:
#: A SIZING input, not an enforcement point. Enforcement is spend.py at 90% of
#: the monthly allowance and the OpenRouter key's own hard cap behind it.
MONTHLY_WALKER_BUDGET_USD = 0.50

#: Measured per-item prices, from the ledger's own accounting rather than a rate
#: card. docs/TECHLOG.md, "cost levers, second pass": gate 141 tokens in / 35
#: out, read-through 3,100 in / 400 out.
GATE_USD_PER_ITEM = 0.00003
READ_USD_PER_ITEM = 0.00128

#: Fraction of gated candidates that survive the gate and buy a read. The one
#: number here taken from the daily collector rather than measured on historical
#: windows, because measuring it needs the API key and therefore real spend: a
#: real run screened ~1,050 and had 155 survivors. Named in one place so a
#: future session can correct it in one place.
GATE_SURVIVAL = 0.15

#: The all-in price of gating ONE candidate: the gate call plus the expected
#: fraction of a read it buys. Rationing the gate rather than the read is what
#: makes this the right unit — under a read-only ceiling the gate cost is
#: unbounded.
USD_PER_GATED_CANDIDATE = GATE_USD_PER_ITEM + GATE_SURVIVAL * READ_USD_PER_ITEM

#: Catalogue publishers whose sitemap reaches an arbitrary 2026 month. MEASURED
#: WITH THE SHIPPING CODE on 2026-07-30: 34 of 82 sampled reached 2026-03, 25 of
#: them with 50 URLs or more. Scaled to the 653 catalogue feeds.
REACHING_PUBLISHERS = 271

#: Dated article URLs one reaching publisher yields for one month of history.
#: MEASURED: mean 233, median 163, over the 34 that reached 2026-03. The mean is
#: used because the bill is a sum rather than a typical case, and it is the mean
#: AFTER `press_archive.MAX_URLS_PER_PUBLISHER` — 7 of the 34 hit that 400 cap,
#: so this is what the collector actually returns rather than what exists.
URLS_PER_PUBLISHER_MONTH = 233

#: Fraction of enumerated URLs that are real candidates. Two measurements:
#: 244 of 7,910 (3.08%) past the prefilter on the SLUG, and 11 of 60 against the
#: slug's 6 on one publisher's real title+teaser — so the slug recovers about
#: 55% of what the metadata does. 3.08% / 0.55 is 5.6%, and that is an estimate
#: built from two measurements rather than a measurement.
CANDIDATE_RATE = 0.056

#: Candidates gated per SLICE. DERIVED from the budget at one slice a day, so
#: changing the budget changes this and the two can never disagree.
SLICE_GATE_RATION_STATIC = max(
    1, int(MONTHLY_WALKER_BUDGET_USD / 30 / USD_PER_GATED_CANDIDATE))

#: The same figure under its old name, for --plan-cost and the month-sizing
#: arithmetic above, neither of which has a clock in it. A RUN uses
#: live_ration(), which does.
SLICE_GATE_RATION = SLICE_GATE_RATION_STATIC


def live_ration() -> tuple[int, str]:
    """This run's gate ration, from what is LEFT in the discretionary pot.

    See backfill_gnews_2026.live_ration and budget.py. A static monthly figure
    cannot slow down, and a catch-up walker that will not slow down is how
    August's collectors ended up degraded for nine days.
    """
    import budget

    return budget.walker_ration(
        monthly_walker_budget_usd=MONTHLY_WALKER_BUDGET_USD,
        usd_per_unit=USD_PER_GATED_CANDIDATE)

#: A per-run backstop on FULL read-throughs, for a slice whose gate survival
#: runs far above the measured 15%. NOT the mechanism — the ration is — and
#: sized well above the expectation so that in ordinary running it never binds
#: and therefore never stalls a slice.
DEFAULT_MAX_READTHROUGHS = max(1, int(SLICE_GATE_RATION * GATE_SURVIVAL * 3))


def candidates_per_month() -> float:
    """Candidates one month of history yields across the whole catalogue."""
    return REACHING_PUBLISHERS * URLS_PER_PUBLISHER_MONTH * CANDIDATE_RATE


def window_cost(*, gated: int, reads: float | None = None) -> dict:
    """What gating `gated` candidates costs at the measured prices. Pure."""
    if reads is None:
        reads = gated * GATE_SURVIVAL
    gate = gated * GATE_USD_PER_ITEM
    read = reads * READ_USD_PER_ITEM
    return {"gate_usd": gate, "read_usd": read, "usd": gate + read, "reads": reads}


def roster_slices(publishers: int = 653,
                  per_slice: int = PUBLISHERS_PER_SLICE) -> int:
    return math.ceil(publishers / max(per_slice, 1))


def pass_projection(*, ration: int = SLICE_GATE_RATION,
                    publishers: int = 653,
                    per_slice: int = PUBLISHERS_PER_SLICE) -> dict:
    """ONE full pass over the roster: slices, dollars, and how deep it reads.

    A "pass" is the unit here, not a year — because the window is a fixed input
    and widening it is free, one pass over the roster covers whatever window was
    dispatched.
    """
    slices = roster_slices(publishers, per_slice)
    per = window_cost(gated=ration)
    return {
        "slices": slices,
        "ration": ration,
        "usd_per_slice": per["usd"],
        "usd_total": per["usd"] * slices,
        "reads_total": per["reads"] * slices,
        "gated_total": ration * slices,
    }


def print_cost_plan(months: int = 1) -> None:
    """The table the owner needs, and the refusal that goes with it."""
    month_candidates = candidates_per_month()
    full_month = window_cost(gated=int(month_candidates))
    full_year = window_cost(gated=int(month_candidates * 12))

    print("PRESS ARCHIVE WALKER — measured reach, measured density, measured prices")
    print(f"  gate  ${GATE_USD_PER_ITEM:.5f}/item   "
          f"read  ${READ_USD_PER_ITEM:.5f}/item   "
          f"gate survival {GATE_SURVIVAL:.0%}")
    print()
    print("  WHAT THE SITEMAP ROUTE ACTUALLY REACHES (measured with this code on")
    print("  82 catalogue publishers, against 2026-03 — a month no 48h news")
    print("  sitemap and no section-page lastmod can fake)")
    print("    reached 2026-03 at all                   34 / 82   41%")
    print("    ... with 50 URLs or more                 25 / 82")
    print(f"    scaled to the catalogue                  ~{REACHING_PUBLISHERS} "
          f"of 653 publishers")
    print(f"    URLs per reaching publisher per month    ~{URLS_PER_PUBLISHER_MONTH}"
          f" (median 163)")
    print(f"    of which real candidates                 ~{CANDIDATE_RATE:.1%}"
          f"  (slug alone measured 3.08%)")
    print()
    print("  THE REFUSAL, WITH ITS NUMBERS")
    print(f"    one month of history, EVERY candidate gated   "
          f"${full_month['usd']:>7.2f}  ({int(month_candidates):,} candidates)")
    print(f"    a year of 2026 the same way                   "
          f"${full_year['usd']:>7.2f}")
    print("    GDELT's whole year                            $   4.51")
    print("    the Google News walker's year                 $   3.02")
    print("    So a full-depth sweep is NOT built. The gate is rationed.")
    print()
    print(f"  slice = {PUBLISHERS_PER_SLICE} publishers, ration "
          f"{SLICE_GATE_RATION} candidates gated per slice "
          f"(derived from ${MONTHLY_WALKER_BUDGET_USD:.2f}/month at 1 slice/day)")
    one = pass_projection()
    depth = one["gated_total"] / max(month_candidates, 1)
    print(f"  one full pass over the roster = {one['slices']} slices, "
          f"${one['usd_total']:.2f}, {one['gated_total']:,} candidates gated")
    print(f"  that reads {depth:.1%} of a one-month window, RANKED by country "
          f"need — the rest stay")
    print("  unmarked, so a second pass over the same roster and window costs the")
    print("  same again and buys entirely different rows.")
    print()
    print("  THE WINDOW IS FREE. A sitemap costs the same fetch for one day as for")
    print("  six months, which is why the cursor walks PUBLISHERS. Dispatching")
    print("  2026-01-01..2026-07-26 costs exactly what one week costs; only the")
    print("  ration decides the bill.")
    print()
    print(f"  {'pace':<18} {'a pass takes':>13} {'passes/mo':>10} {'$/month':>9} "
          f"{'$ per pass':>11} {'depth/mo':>9}")
    for label, per_day in (("1 slice/day", 1), ("2 slices/day", 2),
                           ("4 slices/day", 4)):
        days = math.ceil(one["slices"] / per_day)
        passes = 30 / max(days, 1)
        monthly = one["usd_per_slice"] * per_day * 30
        print(f"  {label:<18} {days:>8} days {passes:>10.1f} {monthly:>8.2f} "
              f"{one['usd_total']:>11.2f} {min(depth * passes, 1.0):>8.0%}")
    print("  A pass is the unit, not a year: the window is a fixed input and")
    print("  widening it is free. Each further pass costs the same and reads")
    print("  rows the last one left unmarked, so 'depth/mo' is what a month of")
    print("  that pace actually reads of the dispatched window.")
    print()
    print("  WHAT IT WOULD HAVE CAUGHT, of the 51 `outside_our_history` misses:")
    print("    on a domain this collector sweeps at all         11 of 51")
    print("    whose sitemap reaches the gold window 2026-07     4 of 51   ~8%")
    print("    (SmartCompany 3 at 218 URLs / 22 days, THE BRIDGE 1 at 65 / 12;")
    print("     PR TIMES, Globes, Wamda and BetaKit serve no archive sitemap)")
    print("    The other 40 are on domains with no feed, or not in the catalogue")
    print("    at all — a SOURCE problem, which no history walk can fix.")
    print()
    print("  ROUTE B (Wayback CDX) IS REFUSED AS A WALK ROUTE, measured:")
    print("    the date range is a CAPTURE window, not a publication window. A")
    print("    2026-07-01..20 query returned FINSMES articles from 2013 and 2014")
    print("    and Wamda articles from 2012, because a crawler visiting in July")
    print("    2026 re-captures a decade of pages. It cannot target a historical")
    print("    month at all.")
    print("    url=<domain>/* + a date range   HTTP 504 after 60s, every domain")
    print("    url=<domain>/&matchType=prefix  200 on 6 of 8 domains, 7-29s each,")
    print("                                    504 on the other 2")
    print("    a 6-query burst with no pause   5x200, 1x504, and NO 429 at any")
    print("                                    point in 20 queries")
    print("    It stays as `press_archive.wayback_urls` for a named dead")
    print("    publisher, called by hand, where a 429 or a 504 raises")
    print("    ArchiveUnknown rather than returning an empty list.")
    print()
    print("  It is NOT armed. Arming means a cron in "
          ".github/workflows/backfill-press-2026.yml,")
    print("  which is BOTH a spend decision and a writer-lock decision. Queue a")
    print("  slice by hand instead:")
    print("    gh workflow run drain-writers.yml -f enqueue=backfill-press-2026.yml \\")
    print("         -f inputs_json='{\"start\":\"2026-07-01\",\"end\":\"2026-07-26\","
          "\"slice\":\"true\"}' \\")
    print("         -f reason='press archive walk, slice 1'")


# --------------------------------------------------------------------------
# the roster partition
# --------------------------------------------------------------------------

def roster(feeds=None) -> list:
    """The publisher roster in a DETERMINISTIC order.

    Sorted by name, because the cursor is an index into this list and a list
    whose order depends on CSV row order would silently re-partition the moment
    somebody adds a catalogue row in the middle. That is a hole and a
    double-collection in one, and nothing would say so.
    """
    population = feeds if feeds is not None else load_feeds()
    return sorted(population, key=lambda f: (f.name.lower(), f.rss))


def partition(population: list, lo: int, hi: int,
              per_slice: int = PUBLISHERS_PER_SLICE) -> list:
    """Roster slice indices [lo, hi] inclusive, as publishers."""
    start = lo * per_slice
    stop = (hi + 1) * per_slice
    return population[start:stop]


def last_index(population: list, per_slice: int = PUBLISHERS_PER_SLICE) -> int:
    return max(0, math.ceil(len(population) / max(per_slice, 1)) - 1)


def roster_progress(lo: int, hi: int, attempted, answered, expected):
    """(done_through, unreached_index) for a run that walked roster [lo, hi].

    The cursor moves over roster indices that are COMPLETE, and a roster index
    is complete only when every publisher in it has been READ. A run that
    stopped on its budget after 5 of 60 publishers has finished no index, so it
    emits a cursor that has not moved and `backfill_slices.record` refuses to
    requeue it — which is correct and loud. Advancing on "we got some of the
    way through" would leave 55 publishers unvisited with the run count looking
    perfect, which is the same silent hole a date cursor produces.

    A publisher whose share of the ration ran out IS finished: that is what a
    ration means, and it is the difference between a walker that converges by
    repetition and one that stalls.

    AND an index that ANSWERED nothing is not finished either, which is the
    2026-08-01 fix. Every publisher in an index failing at the transport layer
    is not sixty dead newspapers, it is one blocked runner — the same fact the
    gnews walker read as "there was no news on 2026-01-24", and it cost three
    days of history that nothing will ever be sent back for. One `dead`
    publisher is ordinary and permanent, so the threshold is "did ANY of them
    answer", not "did all of them".

    Walked from `lo` and stopped at the first index that fails either test,
    because the cursor is a high-water mark: it cannot record a hole behind
    itself, so it must stop in front of one.
    """
    done_through: int | None = None
    for index in range(lo, hi + 1):
        due = expected(index)
        if not due or attempted[index] < due:
            break
        if not answered[index]:
            return done_through, index
        done_through = index
    return done_through, None


# --------------------------------------------------------------------------

@gate_ledger.around_run(WORKFLOW)
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2026-01-01",
                    help="first day of the HISTORICAL WINDOW (not a cursor — "
                         "the cursor walks publishers, and widening this is free)")
    ap.add_argument("--end", default="2026-07-26")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--plan-cost", action="store_true",
                    help="print what each pace costs and exit. Fetches nothing, "
                         "calls nothing, writes nothing.")
    ap.add_argument("--fetch-only", action="store_true",
                    help="enumerate, read heads and free-filter only — calls no "
                         "model, spends nothing, stores nothing")
    ap.add_argument("--ration", type=int, default=None,
                    help=f"candidates GATED per slice. Blank DERIVES it from "
                         f"what is left in the discretionary pot over the days "
                         f"left in the month (budget.walker_ration), so a lean "
                         f"month runs SMALLER rather than not at all; the "
                         f"static month-sizing figure is "
                         f"{SLICE_GATE_RATION_STATIC} at "
                         f"${MONTHLY_WALKER_BUDGET_USD:.2f}/month. The rest of "
                         f"the slice is left unmarked and a later pass picks it "
                         f"up. See --plan-cost.")
    ap.add_argument("--max-readthroughs", type=int,
                    default=DEFAULT_MAX_READTHROUGHS,
                    help=f"per-run backstop on FULL classifications (default "
                         f"{DEFAULT_MAX_READTHROUGHS}). The ration is the "
                         f"mechanism; this only catches a slice whose gate "
                         f"survival runs far above the measured "
                         f"{GATE_SURVIVAL * 100:.0f} percent.")
    ap.add_argument("--max-heads", type=int,
                    default=press_archive.MAX_HEADS_PER_PUBLISHER,
                    help="article heads read per publisher. This is the WALL "
                         "CLOCK budget and it is free; --ration is the money.")
    ap.add_argument("--slice", action="store_true",
                    help="do ONE bounded roster slice, resuming from the "
                         "committed cursor, then stop")
    ap.add_argument("--slice-size", type=int, default=1,
                    help="roster slices per run (default 1; each slice is "
                         f"{PUBLISHERS_PER_SLICE} publishers)")
    ap.add_argument("--publishers-per-slice", type=int,
                    default=PUBLISHERS_PER_SLICE)
    ap.add_argument("--budget-minutes", type=float,
                    default=backfill_slices.SLICE_BUDGET_MINUTES,
                    help="stop at the next publisher boundary after this long")
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

    # WHAT THE BUDGET WILL PAY FOR TODAY. An explicit --ration still wins — a
    # number a human typed beats a derived one. With none given this is the
    # walker's share of what REMAINS in the discretionary pot over the days
    # left, so it shrinks instead of stopping; zero is a skip, exit ZERO.
    if args.ration is None:
        args.ration, ration_basis = live_ration()
        print(f"[press] {ration_basis}")
        if args.ration < 1:
            print("[press] NOTHING BOUGHT. No cursor moved, no candidate "
                  "marked, no ticket failed. The next funded pass resumes on "
                  "the first publisher this one did not do.")
            return 0
    else:
        ration_basis = (f"ration {args.ration}/slice, set explicitly on the "
                        f"command line, so the budget-derived figure was not "
                        f"used.")
        print(f"[press] {ration_basis}")

    window_start = args.start
    window_end = min(args.end, date.today().isoformat())
    population = roster()
    if not population:
        print("STOPPING: the catalogue produced no publishers at all.",
              file=sys.stderr)
        return 1
    end_index = last_index(population, args.publishers_per_slice)

    job = window = None
    if args.slice:
        job, window = backfill_slices.open_slice(
            workflow=WORKFLOW, unit="slices",
            start="0", end=str(end_index),
            slice_size=args.slice_size, state_path=args.state,
            label=f"{window_start}..{window_end}",
            # The DATE WINDOW rides on the job's inputs, not on start/end:
            # start/end here are roster indices. `backfill_slices.next_inputs`
            # knows not to overwrite them for a `slices` job, which is the bug
            # that would otherwise turn "2026-01-01..2026-07-26" into "0".."10".
            inputs={"dry_run": "false", "fetch_only": "false",
                    "start": window_start, "end": window_end,
                    "ration": str(args.ration)})
        if window is None:
            print(f"[press] roster walk of {window_start}..{window_end} is "
                  "already complete — nothing to do.")
            return 0
        lo, hi = int(window[0]), int(window[1])
        batch = partition(population, lo, hi, args.publishers_per_slice)
        print(f"[press] SLICE roster {lo}..{hi} of 0..{end_index} "
              f"({len(batch)} publishers), window {window_start}..{window_end} "
              f"(slice {job['slices'] + 1}, budget {args.budget_minutes:g} min)")
    else:
        lo, hi = 0, end_index
        batch = population
        print(f"[press] whole roster ({len(batch)} publishers), window "
              f"{window_start}..{window_end}")

    writes = not (args.dry_run or args.fetch_only)
    budget = backfill_slices.Budget(args.budget_minutes)
    conn = schema.connect()
    ranking = candidate_rank.Context.for_conn(conn)

    session = None
    try:
        import requests
        session = requests.Session()
    except Exception:
        pass

    filtered: Counter = Counter()
    stored_countries: Counter = Counter()
    gated_countries: Counter = Counter()
    reached = enumerated = candidates = gated = rationed_off = 0
    stored = duplicates = rejected = skipped = errors = 0
    cheap_closed = known_rounds = 0
    publishers_done = 0
    stopped_early = ""
    #: Set once the PAID path closes for the rest of this run, to the sentence
    #: that says which ceiling closed it. It is a LATCH and not a stop, and the
    #: distinction is the 2026-08-05 incident.
    #:
    #: `spend.py --degrade` sets TIT_PAID_READS=off once the month's allowance
    #: is spent, and `classify.classify` then raises BudgetExhausted (a
    #: BudgetDeferred) on the first candidate it is handed. This loop used to
    #: `break` on that, out of the candidate loop and then out of the PUBLISHER
    #: loop, so a degraded run walked 2 of the 40 publishers in roster index 0
    #: and stopped. An index that is 2/40 walked is not finished, so
    #: `roster_progress` left `done_through` at None, the emitted ticket carried
    #: a `next_cursor` equal to the cursor it started from, and
    #: `backfill_slices.record` did exactly what it should: refused to requeue a
    #: chain that made no progress, and went red. Every later run repeated it,
    #: so the chain was dead for as long as the allowance stayed spent, and the
    #: degrade that promised it "always exits 0" reddened the run and stopped
    #: the backfill.
    #:
    #: A closed wallet is not an unread publisher. The fetch, the prefilter and
    #: every `cheap_extract` close are FREE and still run, so the walk finishes
    #: its publishers and the cursor advances honestly. What is lost is DEPTH,
    #: and depth already has a name here: the candidates past the cut are left
    #: UNMARKED, exactly like `rationed_off`, so a later pass reads them.
    paid_path_closed = ""
    #: Candidates that reached the gate and were left unread because the paid
    #: path was closed. Counted, printed and carried on the ticket, because a
    #: shallow slice that says so is a different thing from a full one.
    deferred_unread = 0
    #: The last roster index this run FINISHED. A publisher whose ration ran out
    #: is still FINISHED — that is what a ration means, and it is the difference
    #: between a walker that converges and one that stalls.
    #:
    #: A publisher we could not REACH is a different matter, and until
    #: 2026-08-01 it counted the same: `publishers_done` was incremented for
    #: every publisher attempted, so a roster index whose every fetch failed was
    #: recorded as walked and the cursor moved past publishers nobody had read.
    #: See the accounting below for where the line is actually drawn.
    done_through: int | None = None
    #: Per roster index: how many publishers were attempted, and how many gave
    #: any ANSWER at all (`dead` is a transport failure and is not one).
    attempted_by_index: Counter = Counter()
    answered_by_index: Counter = Counter()

    ration_left = args.ration

    for feed in batch:
        if budget.expired():
            stopped_early = budget.reason()
            break

        # A third party's failure is DATA, not an exception. `read_publisher`
        # already records the failures it anticipates, and `capped_fetch` now
        # translates urllib3's body-read errors into `requests.RequestException`
        # so they are anticipated too. This belt is for whatever transport
        # error is invented next: one publisher's dead host becomes that
        # publisher's `dead` record and the walk finishes its slice, instead of
        # one timeout 19 minutes in killing the run and stopping the chain
        # (neweralive.na, run 31013599896, 2026-08-05).
        try:
            items, record = press_archive.read_publisher(
                feed, window_start, window_end, session=session,
                max_heads=args.max_heads,
                order=lambda e: (0 if prefilter.passes(
                    press_archive.slug_words(e.url))[0] else 1, e.day))
        except (requests.RequestException, OSError) as exc:
            items = []
            record = {"name": feed.name, "country": feed.country,
                      "site": feed.site or feed.rss, "status": "dead",
                      "urls": 0, "heads": 0, "items": 0,
                      "detail": f"host failure escaped the reader: "
                                f"{type(exc).__name__}"}
            press_archive.STATS["dead"] += 1
        press_archive.PUBLISHER_HEALTH.append(record)
        index = lo + publishers_done // args.publishers_per_slice
        publishers_done += 1
        attempted_by_index[index] += 1
        # `dead` is the ONE status that means we did not get an answer: it is a
        # transport failure reaching the sitemap. `no_window` (the sitemap
        # loaded and holds nothing dated inside the window) and `hijacked` (the
        # domain now belongs to somebody else) are both real, durable answers
        # about that publisher, and a chain that refused to advance over them
        # would never move again.
        if record["status"] != "dead":
            answered_by_index[index] += 1
        enumerated += record["urls"]
        if record["status"] == "ok":
            reached += 1

        kept = []
        for item in items:
            ok, reason = prefilter.passes(item.get("raw_text", ""))
            if ok:
                kept.append(item)
            else:
                filtered[reason.split("(")[0].strip()] += 1
        candidates += len(kept)

        # Free reducers, in run_collect.py's order and for its reasons. Each one
        # runs BEFORE the ration is applied, so the ration is spent on
        # candidates that could actually become rows.
        eligible = []
        for item in kept:
            url = item.get("source_url") or ""
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

        # THE RATION, spent across the whole SLICE rather than per publisher: a
        # publisher-level ration would hand the same share to a national daily
        # and to an agency that published twice. `rank` is a permutation and
        # costs nothing; taking the head of it is what the budget buys, and
        # everything after the cut is left UNMARKED on purpose.
        eligible = candidate_rank.rank(eligible, ranking)
        take = eligible[:max(ration_left, 0)]
        rationed_off += len(eligible) - len(take)
        ration_left -= len(take)

        if record["urls"] or take:
            print(f"\n[{feed.country[:18] or '--':18s}] {feed.name[:34]:36s} "
                  f"{record['urls']:4d} URLs, {len(items):3d} with a headline, "
                  f"{len(kept):3d} past the free filter, {len(take):3d} gated "
                  f"({ration_left} of the ration left)")

        for item in take:
            url = item.get("source_url") or ""
            if args.fetch_only:
                gated_countries[candidate_rank.candidate_country(item) or "?"] += 1
                print(f"  WOULD GATE   {item['headline'][:70]}")
                continue
            # A headline that states every field closes for $0 and goes through
            # the same validate -> store path, marked on `notes`. Checked BEFORE
            # any ceiling, because none of the ceilings below are about it: they
            # ration money, and this costs none.
            classified = cheap_extract.extract(item)
            cheap = classified is not None
            if cheap:
                cheap_closed += 1
            elif paid_path_closed:
                # The wallet shut earlier in this run. Leave the candidate
                # UNMARKED so a later pass reads it, and keep walking.
                deferred_unread += 1
                gate_ledger.outcome(item, "deferred")
                continue
            else:
                if classify.STATS["full_calls"] >= args.max_readthroughs:
                    paid_path_closed = (f"--max-readthroughs "
                                        f"({args.max_readthroughs}) reached at "
                                        f"{feed.name}")
                    deferred_unread += 1
                    gate_ledger.outcome(item, "deferred")
                    continue
                try:
                    classified = classify.classify(item)
                except classify.AuthFailed as exc:
                    # The one wall a walk cannot spend its way past and cannot
                    # leave for later: a bad key is wrong for every run.
                    print(f"\nSTOPPING: {exc}", file=sys.stderr)
                    return 1
                except classify.CreditsExhausted as exc:
                    paid_path_closed = f"OpenRouter credits exhausted: {exc}"
                    deferred_unread += 1
                    gate_ledger.outcome(item, "deferred")
                    continue
                except classify.BudgetDeferred as exc:
                    paid_path_closed = f"read-through cap: {exc}"
                    deferred_unread += 1
                    gate_ledger.outcome(item, "deferred")
                    continue
                except classify.Throttled:
                    # History is not going anywhere: leave it unmarked and a
                    # later pass over the same roster picks it up.
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

    # A closed wallet is reported, and is NOT a stop. Nothing inside the loop
    # above breaks out of it any more: the only thing that ends a slice early is
    # the wall clock, which breaks at the publisher boundary at the top.
    if paid_path_closed and not stopped_early:
        stopped_early = (f"{paid_path_closed}; the walk continued on free "
                         f"extraction and left {deferred_unread} candidate(s) "
                         f"unread and UNMARKED for a later pass")

    # What counts as a finished roster index, and why, is `roster_progress` —
    # that is where the reasoning and the tests live.
    done_through, unreached_index = roster_progress(
        lo, hi, attempted_by_index, answered_by_index,
        lambda i: len(partition(population, i, i, args.publishers_per_slice)))

    if unreached_index is not None:
        stopped_early = backfill_slices.unreached_reason(
            f"roster index {unreached_index}",
            f"all {attempted_by_index[unreached_index]} publishers in it failed "
            f"at the transport layer")
        print(f"\nSTOPPING: {stopped_early}", file=sys.stderr)

    print(f"\nBACKFILL roster {lo}..{hi}, window {window_start}..{window_end}")
    print(f"  publishers         {publishers_done} read, {reached} reached back "
          f"into the window")
    if unreached_index is not None:
        print(f"  UNREACHED INDEX    {unreached_index} — cursor NOT advanced "
              f"past it")
    print(f"  URLs in window     {enumerated} -> {press_archive.STATS['items']} "
          f"with a headline ({press_archive.STATS['heads_fetched']} heads read, "
          f"{press_archive.STATS['heads_failed']} failed)")
    print(f"  free filter        {press_archive.STATS['items']} -> {candidates} "
          f"candidates")
    for reason, count in filtered.most_common():
        print(f"      {count:5d}  {reason}")
    # NO SILENT CAPS. A truncated run says what it did not do, and names the
    # ceiling that decided it, in the same breath as what it did.
    print(f"  left for a later pass  {rationed_off} (ration {args.ration}/slice)")
    if rationed_off:
        print(f"      DROPPED FOR BUDGET, NOT FOR A VERDICT: {rationed_off} "
              f"candidate(s) reached the gate and were not gated. They are "
              f"UNMARKED, so a later funded pass reads them.")
        print(f"      {ration_basis}")
    if deferred_unread:
        print(f"  left UNREAD (no paid path)  {deferred_unread}")
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
    if stopped_early:
        print(f"  STOPPED EARLY      {stopped_early}")

    press_archive._report(args.dry_run or args.fetch_only,
                          window_start, window_end)

    # Publishing is a SEPARATE gate from collecting and a slice must survive it
    # failing: the first live sliced GDELT run collected its quarter and then
    # died inside publish.publish on open guardrail findings, so its ticket was
    # never emitted and the chain stopped with nothing recorded.
    blocked = ""
    if writes:
        try:
            publish.publish(conn)
        except publish.PublishError as exc:
            blocked = f"publish refused: {exc}"
            print(f"\nPUBLISH FAILED: {exc}", file=sys.stderr)

    # Emitted BEFORE the fail-loud check below, on purpose: a run that finished
    # a publisher and then hit a broken fetch has still done that publisher, and
    # the commit step runs `if: !cancelled()` for the same reason, so this is
    # recorded and requeued even when the function returns 1 below. GOING RED
    # DOES NOT UN-ADVANCE A CURSOR. What keeps an unread roster index out of the
    # past is that it never sets `done_through`: a run that finished NOTHING
    # emits a cursor that has not moved, which `backfill_slices.record` refuses
    # to requeue and goes red on. Nothing is emitted for a dry or fetch-only
    # run: those store nothing, so a chain of them would advance the cursor over
    # publishers it never collected.
    if args.slice and args.emit_next and writes:
        cursor = (backfill_slices.advance(str(done_through), "slices")
                  if done_through is not None else job["cursor"])
        backfill_slices.emit(args.emit_next, backfill_slices.slice_ticket(
            job, str(lo), str(hi),
            next_cursor=cursor,
            totals={"stored": stored, "duplicates": duplicates,
                    "rejected": rejected, "publishers": publishers_done,
                    "reached": reached, "left_for_later": rationed_off,
                    "left_unread": deferred_unread},
            stopped_early=stopped_early, halt=blocked))
        print(f"  next cursor        {cursor}")

        # A slice that finished NO roster index cannot requeue: `record` will
        # refuse to move a cursor that did not move, and the workflow goes red
        # on it. That refusal is right. What was wrong is that the run which
        # CAUSED it exited 0 and said nothing, so on 2026-08-05 the only
        # explanation anywhere was `record` one step later saying "the cursor is
        # still 0" without saying why. The counters that explain it are here.
        if cursor == job["cursor"] and unreached_index is None:
            due = len(partition(population, lo, lo, args.publishers_per_slice))
            print(f"\nNOT REQUEUEING: roster index {lo} is unfinished at "
                  f"{attempted_by_index[lo]} of {due} publishers, so the cursor "
                  f"stays at {job['cursor']} and nothing can be queued behind "
                  f"this slice. The chain STOPS here until a human looks. The "
                  f"walk ended because: "
                  f"{stopped_early or 'no reason was recorded, which is itself the thing to investigate'}.",
                  file=sys.stderr)
    if blocked:
        return 1

    # FAIL LOUD on a roster index nothing could reach. The cursor already
    # declined to pass it; this is what gets a human to look at why.
    if unreached_index is not None:
        print(f"\nSTOPPING: every publisher in roster index {unreached_index} "
              f"failed at the transport layer. Sixty newspapers do not go "
              f"offline together, so this is the runner's network or its user "
              f"agent, not the catalogue. The cursor was NOT moved past that "
              f"index; the next slice re-reads it.", file=sys.stderr)
        return 1

    # A slice of sixty catalogue publishers cannot enumerate zero
    # URLs across a window of weeks: 88% of them serve a sitemap. If it does,
    # the ENUMERATION is broken — a changed user agent, a blocked runner IP, a
    # catalogue that loaded empty — and the run must not exit green on it. The
    # first SEC backfill dispatch exited 0 after five silent 403s and looked
    # exactly like a successful run that found nothing.
    if publishers_done and not enumerated:
        print(f"\nSTOPPING: {publishers_done} publishers produced zero URLs in "
              f"{window_start}..{window_end}. 88% of the catalogue serves a "
              "sitemap, so the enumeration itself is failing — check the "
              "per-publisher ledger for a wall of `no_window` or `dead`.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
