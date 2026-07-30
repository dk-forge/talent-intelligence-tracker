#!/usr/bin/env python3
"""2026 catch-up for the structured registry collectors. No model, no spend.

WHY THIS IS NOT THE GDELT WALKER AGAIN
======================================
`backfill_gdelt_2026.py` walks NEWS, so its binding constraint is money and its
`--plan-cost` prices a pace. Every source here exposes `as_classified`, so
`run_collect` never reaches the classifier and the whole path costs **$0.00**.
The binding constraints are different and there are two of them:

  * **the API's own window ceiling**, which for two of these is smaller than the
    thing a human would type, and
  * **the single `talent-collect` writer lock**, which is why this runs in
    slices at all.

So `--plan` prints requests, wall clock and rate-limit headroom rather than
dollars. There is no read-through, no gate, no `--max-readthroughs`, and
`classify` is not imported.

WHAT NEEDED A WALKER AND WHAT DID NOT, measured 2026-07-30
==========================================================
All four registry collectors were built on 2026-07-30 and **none of them has
ever stored a row**, so "is 2026 already held" is `0` for every one. The
question that decided this file is the other one: can the existing
`collect-structured.yml` already reach January?

| source | window it can express | reachable today | here |
|---|---|---|---|
| `edinet_japan` | a LIST of calendar days, `MAX_DAYS` 366 | **yes** — `days=211` is one run of 211 calls, ~2 min | no |
| `companies_house`| `appointed_on` filter, any width, stateless | partly — 4 dispatches, no cursor | **yes** |
| `bse_india` | **32 days**, server-enforced (below) | **no** | **yes** |
| `opendart_korea` | 90 days, and anchored on TODAY | **no** — Jan..Apr unreachable | **yes** |

`edinet_japan` is deliberately absent. Its list endpoint takes one calendar day
per call and its own `MAX_DAYS` is 366, so the whole of 2026 is one dispatch of
the workflow that already exists:

    gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \
         -f inputs_json='{"source":"edinet_japan","days":"211","dry_run":"false"}' \
         -f reason='Japan 2026 catch-up'

Building a walker for that would be a second implementation of a cursor for a
job that is 211 requests and about two minutes. It is not built, on purpose.

THE BSE 32-DAY THRESHOLD, which is why India could not be back-filled at all
---------------------------------------------------------------------------
`collectors/bse_india.py` says "a backfill is a longer window through the same
path rather than a script of its own" and `collect-structured.yml` offers a
`days` input that says "a gap is back-filled by widening this". Measured against
the live API on 2026-07-30, that is **false above 32 days**:

    strToDate - strPrevDate  <= 32 days  ->  200, {"Table": [...]}
                                33 days  ->  200, {"Status":"False",
                                                   "Message":"Date range
                                                    exceeded threshold."}

Binary-searched: 30/31/32 accepted, 33/34/35/36/40/45/90/151/211 refused. The
threshold is undocumented and the refusal arrives as **HTTP 200 with no
`Table` key**, which the collector reported as "the response shape has changed"
— a message that sends a reader to look for a redesigned API. So India's
history was unreachable through the documented route and the error blamed the
wrong thing. `SPECS["bse_india"].window_cap_days` is that measurement, and the
slice size sits under it with four days to spare.

THE KOREA CEILING is documented rather than discovered: OpenDART limits a search
with no `corp_code` to three months, `days_from_env` refuses anything over 90,
and `window()` is anchored on TODAY. On 2026-07-30 that puts the earliest
reachable day at 2026-05-01. January to April is not a wide window away, it is
unreachable — which is what an explicit `--start` fixes.

USAGE
-----
    python backfill_structured_2026.py --plan                    # no requests
    python backfill_structured_2026.py --source bse_india --fetch-only \
        --start 2026-01-01 --end 2026-01-28
    python backfill_structured_2026.py --source bse_india --slice \
        --start 2026-01-01 --end 2026-07-30 --emit-next "$RUNNER_TEMP/slice.json"

NOT ARMED, and it must not become armed. Every source here is a database writer
sharing the one `talent-collect` lock, so a `schedule:` in the workflow would
enter that group uncoordinated and either evict the pending run or be evicted
and become an unreplayable orphan. Queue it:

    gh workflow run drain-writers.yml -f enqueue=backfill-structured-2026.yml \
         -f inputs_json='{"source":"bse_india","start":"2026-01-01",
                          "end":"2026-07-30","dry_run":"false"}' \
         -f reason='India 2026 catch-up, slice 1'

THE CURSOR ADVANCES PER RUN. `backfill_slices.record` moves it from the ticket
this run emitted and reads no clock, so a re-dispatch after a requeue resumes on
the first window this run did not finish and never repeats one. That is the
sibling's `now.toordinal()` mistake, asserted as a property rather than as a
symptom in `tests/test_backfill_pace.py`.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

import backfill_slices
from collectors import bse_india, companies_house, opendart_korea
from pipeline import publish, schema, store, validate

WORKFLOW = "backfill-structured-2026.yml"


@dataclass(frozen=True)
class Spec:
    """One source's shape, with the measurement behind every number."""

    module: object
    #: `days` walks the calendar. `slices` walks the ROSTER — see the note on
    #: `backfill_slices.UNITS`.
    unit: str
    #: Windows (or roster slices) per run.
    slice_size: int
    #: The widest window the API will actually answer, in days. `None` for a
    #: source whose cost is per-company rather than per-day.
    window_cap_days: int | None
    #: The collector's own inter-request pause, quoted so `--plan` reads the
    #: real value rather than a copy of it.
    request_delay: float
    #: Requests one slice sends, and the rows it is expected to yield. Both
    #: MEASURED where the source could be reached without a key; see the
    #: `evidence` line, which `--plan` prints beside them.
    requests_per_slice: int
    rows_per_slice: int
    rate_limit: str
    evidence: str
    #: Wall clock a slice actually took, where a slice has actually been run.
    #: `None` where it has not, and `--plan` says which is which — because the
    #: paced projection below is only the time spent WAITING on the API, and for
    #: a source that fetches 1,300 rows in 37 requests the pipeline is most of
    #: the run. Projecting from the pacing alone would understate BSE by 20x.
    measured_slice_minutes: float | None = None
    #: `True` when one `source_url` legitimately carries more than one event,
    #: so a seen URL must NOT be skipped. Read off the collector rather than
    #: restated, because restating it is how the two drift.
    revisits_source_url: bool = False


SPECS: dict[str, Spec] = {
    # 32-day server threshold, measured (see the module docstring). 28 is four
    # whole weeks and leaves four days of margin, and it keeps the busiest
    # sub-category ("Change in Management", ~640 filings a 28-day window) at
    # ~13 pages against the collector's own MAX_PAGES of 40 — so a slice can
    # neither be refused for width nor silently truncated for depth.
    "bse_india": Spec(
        module=bse_india, unit="days", slice_size=28, window_cap_days=32,
        request_delay=bse_india.REQUEST_DELAY,
        requests_per_slice=37, rows_per_slice=1130,
        rate_limit="no published limit; bseindia.com is a public API on shared "
                   "infrastructure, so the collector's own 0.25s pause stands",
        evidence="MEASURED live 2026-07-30, two whole slices through this "
                 "walker into a scratch database: 2026-01-01..01-28 fetched 898 "
                 "usable and stored 616 in 52s; 2026-01-29..02-25 read 1,427 "
                 "rows, fetched 1,368 usable and stored 866 in 108s. The ~35% "
                 "gap is dedupe.fuzzy_duplicate collapsing one employer's "
                 "filings inside 14 days, which is the intended behaviour.",
        measured_slice_minutes=1.8,
    ),
    # OpenDART caps a corp_code-less search at three months and `days_from_env`
    # refuses over 90. 60 keeps a slice comfortably inside that AND keeps the
    # page walk short: 8,363 disclosures crossed the two detail types in the
    # 90 days to 2026-07-29, so a 60-day slice is ~56 list pages of 100 plus
    # one company.json per distinct employer.
    "opendart_korea": Spec(
        module=opendart_korea, unit="days", slice_size=60, window_cap_days=90,
        request_delay=opendart_korea.REQUEST_DELAY,
        requests_per_slice=190, rows_per_slice=175,
        rate_limit="20,000 requests a day, documented. A whole 2026 walk is "
                   "~760 requests, under 4% of one day's quota",
        evidence="Derived from the collector's own measurement: 261 allowlisted "
                 "filings of 8,363 disclosures over 2026-05-01..07-29 (3.1%), "
                 "~1,060 a year, 12 to 49 a week. NOT re-measured here: "
                 "OPENDART_API_KEY_KR is a GitHub secret and is not set locally.",
    ),
    # The one source whose cost is per COMPANY and not per day, so its cursor
    # walks the roster. The date window is a filter over data the endpoint
    # returns anyway — widening it to seven months costs nothing, and that is
    # exactly why a date cursor here would advance over work never done.
    #
    # EIGHT slices, not the four the weekly rotation uses. The rotation's four
    # are ~2,600 requests and ~25 minutes each; that is fine for a job whose
    # only work is the fetch, and tight for one that then puts ~590 rows
    # through validate/store/publish inside SLICE_BUDGET_MINUTES. Eight is
    # ~1,155 companies, ~1,320 requests and ~12 minutes of fetching. The
    # partition is a blake2b digest of the company number, so any slice count
    # partitions the roster exactly once and the backfill's eight do not
    # disturb the rotation's four.
    "companies_house": Spec(
        module=companies_house, unit="slices", slice_size=1, window_cap_days=None,
        request_delay=companies_house.REQUEST_DELAY,
        requests_per_slice=1320, rows_per_slice=590,
        rate_limit="600 requests per 5 minutes = 2.00/s. At the collector's "
                   "0.55s pause a run sends 1.82/s, which is 91% of the "
                   "allowance and leaves the margin a 429 retry needs",
        evidence="Derived from the collector's own measurements: 9,230 rostered "
                 "employers, 1.145 requests each, 0.867 appointments per company "
                 "per year, x0.81 for the 14-day fuzzy-duplicate window. NOT "
                 "re-measured here: COMPANIES_HOUSE_API_KEY_UK is a GitHub "
                 "secret and is not set locally.",
        revisits_source_url=companies_house.REVISITS_ITS_SOURCE_URL,
    ),
}

#: How the roster is partitioned for a Companies House backfill. See the Spec
#: comment; it is deliberately not the collector's own `SLICES`.
CH_BACKFILL_SLICES = 8


def spec_for(source: str) -> Spec:
    try:
        return SPECS[source]
    except KeyError:
        raise SystemExit(
            f"{source!r} is not a source this walker covers. It covers "
            f"{', '.join(sorted(SPECS))}. `edinet_japan` is deliberately absent: "
            f"its list endpoint is one call a calendar day and its own MAX_DAYS "
            f"is 366, so the whole of 2026 is a single dispatch of "
            f"collect-structured.yml with days=211. See the module docstring.")


def slice_count(spec: Spec, start: date, end: date) -> int:
    """Runs a whole window takes, at this source's slice size."""
    if spec.unit == "slices":
        return math.ceil(CH_BACKFILL_SLICES / max(spec.slice_size, 1))
    return math.ceil(((end - start).days + 1) / max(spec.slice_size, 1))


def paced_minutes(spec: Spec) -> float:
    """Wall clock ONE slice spends WAITING on the API, at its own pacing.

    This is a floor and never an estimate of the run. For companies_house it is
    almost the whole thing — 1,320 requests at 0.55s and four appointments to
    store. For bse_india it is a twentieth of it: 37 requests carry 1,368 rows,
    and validate/store/dedupe on those is the run. `--plan` prints the MEASURED
    figure wherever a slice has actually been run, and marks the rest.
    """
    return spec.requests_per_slice * spec.request_delay / 60


def slice_minutes(spec: Spec) -> float:
    """Best available wall clock for one slice: measured if it exists."""
    return (spec.measured_slice_minutes if spec.measured_slice_minutes is not None
            else paced_minutes(spec))


def print_plan(start: date, end: date) -> None:
    """What a full 2026 walk costs in requests and wall clock. Calls nothing."""
    print("STRUCTURED 2026 WALK — requests and wall clock, not dollars")
    print(f"  window {start}..{end} ({(end - start).days + 1} days)")
    print("  every source here exposes as_classified, so MODEL SPEND IS $0.00 "
          "and no gate,")
    print("  read-through or spend guard exists on this path at all.")
    print()
    header = (f"  {'source':<17}{'slices':>7}{'req/slice':>11}{'min/slice':>11}"
              f"{'rows/slice':>12}{'rows total':>12}{'req total':>11}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name in sorted(SPECS):
        spec = SPECS[name]
        slices = slice_count(spec, start, end)
        mark = "" if spec.measured_slice_minutes is not None else "*"
        print(f"  {name:<17}{slices:>7}{spec.requests_per_slice:>11}"
              f"{slice_minutes(spec):>10.1f}{mark:<1}{spec.rows_per_slice:>12}"
              f"{spec.rows_per_slice * slices:>12}"
              f"{spec.requests_per_slice * slices:>11}")
    print("  min/slice is MEASURED except where marked *, which is the paced "
          "fetch time only")
    print(f"  (the slice budget is {backfill_slices.SLICE_BUDGET_MINUTES:g} min "
          f"and the workflow timeout is "
          f"{backfill_slices.SLICE_TIMEOUT_MINUTES:g})")
    print()
    for name in sorted(SPECS):
        spec = SPECS[name]
        cap = (f"{spec.window_cap_days}-day API window ceiling"
               if spec.window_cap_days else "no window ceiling; cost is per company")
        print(f"  {name}")
        print(f"      unit {spec.unit}, {spec.slice_size} per run, {cap}")
        print(f"      rate limit: {spec.rate_limit}")
        print(f"      evidence:   {spec.evidence}")
    print()
    print("  edinet_japan is NOT here. Its list endpoint is one call per "
          "calendar day and its")
    print("  own MAX_DAYS is 366, so 2026 is one dispatch of the workflow that "
          "already exists:")
    print("    gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \\")
    print("         -f inputs_json='{\"source\":\"edinet_japan\",\"days\":\"211\"}' \\")
    print("         -f reason='Japan 2026 catch-up'")
    print()
    print("  NOT ARMED, and it must not be. These are database writers on the "
          "single")
    print("  talent-collect lock, so a cron here evicts a pending run or "
          "becomes an orphan.")
    print("  Queue a slice instead:")
    print("    gh workflow run drain-writers.yml "
          "-f enqueue=backfill-structured-2026.yml \\")
    print("         -f inputs_json='{\"source\":\"bse_india\","
          "\"start\":\"2026-01-01\",\"end\":\"2026-07-30\"}' \\")
    print("         -f reason='India 2026 catch-up'")


# --------------------------------------------------------------------------
# fetching one slice
# --------------------------------------------------------------------------

def fetch_slice(source: str, spec: Spec, window: tuple[str, str], *,
                job_start: date, job_end: date, roster_slices: int,
                session=None) -> list[dict]:
    """One slice's raw dicts, through the collector's OWN path.

    Nothing is reimplemented here: the pagination, the guards, the emptiness
    floors and the traps each collector documents all apply, because this calls
    `collect()` exactly as `run_collect` does and only chooses the window.
    """
    if spec.unit == "days":
        lo, hi = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        span = (hi - lo).days
        if spec.window_cap_days is not None and span > spec.window_cap_days:
            # Fail here rather than at the API, because at the API this is an
            # HTTP 200 with a body that says nothing about width. See the BSE
            # note in the module docstring.
            raise SystemExit(
                f"{source}: a {span}-day window is wider than the "
                f"{spec.window_cap_days} days this API will answer. BSE refuses "
                f"one with HTTP 200 and no `Table` key, which reads as a "
                f"redesigned response; OpenDART silently returns a shorter "
                f"window. Lower --slice-size.")
        return spec.module.collect(
            days=span, today=datetime(hi.year, hi.month, hi.day), session=session)

    # `slices`: the roster is what is walked, and the date window is the whole
    # job's, applied as a filter for free.
    index = int(window[0])
    return spec.module.collect(
        days=(job_end - job_start).days, today=job_end,
        slices=roster_slices, slice_index=index, session=session)


def main(argv: list[str] | None = None, *, session=None) -> int:
    """`session` is the collectors' own injection point, so a test can drive
    this whole path — real pagination, real parsing, real guards — with canned
    responses and no network."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=sorted(SPECS),
                    help="which registry collector to walk")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be stored, write nothing, and do NOT "
                         "advance the cursor")
    ap.add_argument("--fetch-only", action="store_true",
                    help="fetch and derive, store nothing — proves the "
                         "collector and the window without touching the "
                         "database")
    ap.add_argument("--plan", action="store_true",
                    help="print requests, wall clock and rate-limit headroom "
                         "for a whole 2026 walk, then exit. Fetches nothing.")
    ap.add_argument("--slice", action="store_true",
                    help="do ONE bounded slice, resuming from the committed "
                         "cursor, then stop")
    ap.add_argument("--slice-size", type=int,
                    help="windows (or roster slices) per run; default is the "
                         "source's own, derived from its API ceiling")
    ap.add_argument("--roster-slices", type=int, default=CH_BACKFILL_SLICES,
                    help=f"companies_house only: how many ways to partition the "
                         f"9,230-employer roster (default {CH_BACKFILL_SLICES})")
    ap.add_argument("--budget-minutes", type=float,
                    default=backfill_slices.SLICE_BUDGET_MINUTES)
    ap.add_argument("--emit-next", help="write the slice ticket here, for "
                                        "backfill_slices.py record")
    ap.add_argument("--state", help="slice state file "
                                    "(default data/backfill_state.json)")
    args = ap.parse_args(argv)

    requested_start = date.fromisoformat(args.start)
    requested_end = min(date.fromisoformat(args.end), date.today())

    if args.plan:
        print_plan(requested_start, requested_end)
        return 0
    if not args.source:
        ap.error("--source is required unless --plan is given")

    source = args.source
    spec = spec_for(source)
    collector = spec.module.COLLECTOR
    size = args.slice_size or spec.slice_size

    if spec.unit == "days" and spec.window_cap_days is not None \
            and size > spec.window_cap_days:
        raise SystemExit(
            f"--slice-size {size} exceeds {source}'s measured "
            f"{spec.window_cap_days}-day API window ceiling")

    # The job's start/end are the CALENDAR window for a days-unit source and the
    # ROSTER SLICE INDICES for companies_house. `label` keeps three sources
    # walking one window from sharing a cursor.
    if spec.unit == "slices":
        job_start, job_end = "0", str(args.roster_slices - 1)
    else:
        job_start, job_end = requested_start.isoformat(), requested_end.isoformat()

    job = window = None
    if args.slice:
        job, window = backfill_slices.open_slice(
            workflow=WORKFLOW, unit=spec.unit, start=job_start, end=job_end,
            slice_size=size, state_path=args.state, label=source,
            inputs={"source": source, "start": requested_start.isoformat(),
                    "end": requested_end.isoformat(), "dry_run": "false",
                    "fetch_only": "false"})
        if window is None:
            print(f"[{collector}] "
                  f"{backfill_slices.job_id(WORKFLOW, job_start, job_end, source)} "
                  "is already complete — nothing to do.")
            return 0
        print(f"[{collector}] SLICE {window[0]}..{window[1]} of "
              f"{job_start}..{job_end} (slice {job['slices'] + 1} of "
              f"{slice_count(spec, requested_start, requested_end)}, "
              f"unit {spec.unit})")
    else:
        first = backfill_slices.next_slice(job_start, job_end, spec.unit, size)
        if first is None:
            print(f"[{collector}] nothing to do for {job_start}..{job_end}")
            return 0
        window = first

    budget = backfill_slices.Budget(args.budget_minutes)
    conn = schema.connect()

    fetched = stored = duplicates = rejected = skipped = 0
    stored_countries: Counter = Counter()
    stopped_early = ""

    try:
        items = fetch_slice(source, spec, window, job_start=requested_start,
                            job_end=requested_end,
                            roster_slices=args.roster_slices, session=session)
    except SystemExit:
        raise
    except Exception as exc:
        # No health row is written here, deliberately. `staleness.py` leashes
        # each of these collectors to its WEEKLY cron, and a backfill reporting
        # health would reset that leash — so a broken weekly run would be
        # masked by a backfill that happened to succeed. The backfill's own
        # failure is this non-zero exit and a red run.
        print(f"[{collector}] FETCH FAILED: {exc}", file=sys.stderr)
        return 1

    fetched = len(items)
    print(f"[{collector}] {fetched} rows fetched, derived from typed fields "
          f"(no model call, $0.00)")

    for item in items:
        url = item.get("source_url") or item.get("discovery_url") or ""

        # Free, and BEFORE anything else. A re-dispatched slice — which is the
        # normal case while WRITER_QUEUE_TOKEN is unset and tickets requeue —
        # costs one fetch and nothing else.
        #
        # Except where the collector says one URL legitimately carries more
        # than one event: `companies_house` cites a PERSON's appointments page,
        # and a person can be appointed twice, so marking it seen would make
        # the first appointment the last one this source ever reports. Those
        # rows dedupe on content_hash inside `store.store` instead. This is the
        # ats_boards lesson, and the flag is read off the collector rather than
        # restated.
        if url and not spec.revisits_source_url and store.already_seen(conn, url):
            skipped += 1
            continue

        if args.fetch_only:
            # Deliberately stops here rather than deriving. `build_signal`
            # takes `conn` so that identity.enrich can fill ticker and HQ, and
            # that path caches what it resolves — so a "free rehearsal" that
            # called it would write to the database it claims not to touch.
            print(f"  WOULD DERIVE  {item.get('headline', '')[:70]}")
            continue

        classified = spec.module.as_classified(item)
        try:
            # conn: without it identity.enrich() inside build_signal is a no-op
            # and the row lands with no ticker, type or HQ. Same call, same
            # reason, as run_collect.py.
            signal = validate.build_signal(classified, item, collector, conn=conn)
        except validate.Rejected as exc:
            rejected += 1
            print(f"  REJECT  {item.get('headline', '')[:70]}\n          {exc}")
            if url and not args.dry_run:
                store.mark_seen(conn, url, collector, "rejected")
            continue

        if args.dry_run:
            stored += 1
            stored_countries[signal.country or "-"] += 1
            print(f"  WOULD STORE  [{signal.country or '--'}] "
                  f"{signal.headline[:64]}")
            continue

        outcome = store.store(conn, signal)
        if url:
            store.mark_seen(conn, url, collector, outcome)
        if outcome == "stored":
            stored += 1
            stored_countries[signal.country or "-"] += 1
        else:
            duplicates += 1

    if not (args.dry_run or args.fetch_only):
        conn.commit()

    if budget.expired():
        # It cannot stop this slice — a slice here is ONE atomic API window or
        # ONE roster slice, and there is no boundary inside it at which a
        # cursor could honestly point. So this is reported rather than acted
        # on, and it is the number that says the slice size is wrong.
        stopped_early = (f"slice took {budget.elapsed_minutes():.0f} min, past "
                         f"the {args.budget_minutes:g} min budget — lower "
                         f"--slice-size before queueing more")

    print(f"\nBACKFILL {source} {window[0]}..{window[1]}")
    print(f"  fetched            {fetched}")
    print(f"  already seen       {skipped} (skipped before any work)")
    print(f"  stored={stored} duplicate={duplicates} rejected={rejected}")
    print(f"  elapsed            {budget.elapsed_minutes():.1f} min "
          f"of a {args.budget_minutes:g} min budget")
    print(f"  model spend        $0.00 (as_classified; no gate, no read-through)")
    if stored_countries:
        print("  countries stored   "
              + ", ".join(f"{c}={n}" for c, n in stored_countries.most_common()))
    if stopped_early:
        print(f"  OVER BUDGET        {stopped_early}")

    # Publishing is a SEPARATE gate from collecting and a slice must survive it
    # failing: the first live sliced GDELT run collected its quarter and then
    # died inside publish because the guardrails held eight open findings, so
    # the ticket was never emitted and the cursor never moved. The rows are
    # real either way.
    blocked = ""
    if not (args.dry_run or args.fetch_only):
        try:
            publish.publish(conn)
        except publish.PublishError as exc:
            blocked = f"publish refused: {exc}"
            print(f"\nPUBLISH FAILED: {exc}", file=sys.stderr)

    if args.slice and args.emit_next and not (args.dry_run or args.fetch_only):
        cursor = backfill_slices.advance(window[1], spec.unit)
        backfill_slices.emit(args.emit_next, backfill_slices.slice_ticket(
            job, window[0], window[1], next_cursor=cursor,
            totals={"fetched": fetched, "stored": stored,
                    "duplicates": duplicates, "rejected": rejected,
                    "skipped": skipped},
            stopped_early=stopped_early, halt=blocked))
        print(f"  next cursor        {cursor}")
    if blocked:
        return 1

    # FAIL LOUD on an empty slice. Each collector already carries its own
    # emptiness floor and raises rather than returning zero — bse_india below
    # 10 rows a window, opendart_korea below 5, companies_house below one per
    # 200 companies polled — so reaching here with nothing means the floor
    # itself has been removed or a window was legitimately empty and is worth a
    # human reading. A backfill that walks a year returning zero and exits
    # green is the exact shape of the first SEC dispatch, which exited 0 after
    # five silent 403s.
    if fetched == 0:
        print(f"\nSTOPPING: {source} returned zero rows for "
              f"{window[0]}..{window[1]}. Every collector here raises below its "
              f"own emptiness floor, so a clean zero means the floor is gone or "
              f"the window is wrong.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
