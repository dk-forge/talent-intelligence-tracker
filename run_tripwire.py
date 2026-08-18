#!/usr/bin/env python3
"""The discovery tripwire: ask an outside view what we are missing.

    python run_tripwire.py --offline          # no network, no spend, full plumbing
    python run_tripwire.py --dry-run          # ask for real, write nothing
    python run_tripwire.py                    # ask, write the work list, file health
    python run_tripwire.py --plan-only        # print the plan and the budget, ask nothing

WHAT IT IS
----------
The recall measurement grades us against a sealed gold set somebody assembled by
hand. This does the discovery-side job: it asks a search-backed model what
happened in the countries our own recall says we are blind in, and across every
industry, then diffs the answer against what we hold. What is left is a WORK
LIST.

WHAT IT IS NOT
--------------
It is not a collector and it never stores a row. Every value the model returns
travels under a `claimed_` name and dies in the work list. A lead becomes a
record only when `collectors/tripwire_chase.py` finds the PUBLISHER'S OWN
article and that article goes through classify -> validate -> store like any
other candidate. The project rule holds without exception: no figure exists
unless a source states it, and a model is not a source.

COST
----
The whole product's ceiling is about $5/month and the collection pipeline
already uses most of it, so this is budgeted at
`analysis/tripwire/plan.TRIPWIRE_MONTHLY_USD` and the query count is derived
from that number rather than the other way round. spend.py is the enforcement:
it measures the month against the product-wide allowance and this run refuses to
spend when that is exhausted, instead of inventing a second ceiling of its own.
Every run records what it actually cost, per query, per lead and per confirmed
miss, so a tripwire that is not worth its money is visible as that.

It is no longer a projection. The first live queries went out on 2026-07-30
(run 30506967802): 17 search-backed queries, $0.0977 billed, **$0.0057 a query
measured** against the $0.02 the plan was sized on — the estimate is 3.5x the
real price, so the cap is conservative in the right direction. The Israel query,
the one a human could check by eye, cost $0.0059 and returned 8 leads, of which
the diff had 10 of the run's 99 already. Both prices are printed on every run:
the estimate is what sizes the plan, the measurement is what it costs.

Exit codes: 0 ran | 1 the run itself failed or the outside view returned nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analysis.tripwire import ask, diff, plan as planner, report  # noqa: E402
from pipeline import classify, schema, store  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "tests", "fixtures", "tripwire_reply.json")

# Two runs a day would be a rotation the budget cannot pay for; the plan is
# sized on twice a WEEK. The cycle counter still needs a within-day component so
# a manual second run does not repeat the first one's slice.
RUNS_PER_DAY = 2


def cycle_for(today: date, run_index: int) -> int:
    return today.timetuple().tm_yday * RUNS_PER_DAY + run_index


def spend_guard(*, month_total: float | None = None, month: str | None = None,
                charged: dict | None = None,
                fetch_error: str | None = None) -> tuple[bool, str]:
    """May this run spend anything at all?

    Delegates to `budget.decide`, which is the project's one budget mechanism.
    A missing key is not a failure of this check — the caller reports that
    when it tries to ask. An unreachable spend API is: refusing to spend when
    we cannot see the meter is the only safe direction.

    THIS FUNCTION USED TO INVENT ITS OWN CEILING, AND ITS OWN DOCSTRING SAID
    WHY THAT WAS A BAD IDEA (fixed 2026-08-18). The old text read: "Inventing
    a second ceiling here would mean two numbers to keep in step and one of
    them silently wrong." It then compared the key's WHOLE-MONTH delta against
    `MONTHLY_ALLOWANCE_USD * STOP_AT_FRACTION`, which was the one ceiling in
    the project on 2026-07-30 and stopped being it on 2026-08-13, when
    `budget.py` split the allowance into a committed pot and a catch-up pot.
    `spend.py --gate` moved with the split. This did not.

    What that cost: this run declares no `TIT_RUN_KIND`, so it is COMMITTED
    work. Through August the committed pot held $3.20 of $7.11 and the
    workflow's own `spend.py --gate` step answered `over=false`, so the paid
    step ran — and then THIS guard, reading $12.18 against $7.20, returned
    "not spending" and `main()` returned 0. Green run, nothing bought,
    nothing written. The `over=true` marker step never fired, so
    `writer_queue` filed the ticket as **landed**: work claimed that was never
    done, which is the exact outcome that marker exists to prevent. One result
    file since 2026-08-02, and a 384h STALE with every check green.

    The fix is not a third number. It is asking `budget`, like everything else
    that decides whether to spend. Now the two guards cannot disagree, so the
    silent-green-and-landed state is unreachable by construction rather than
    by a second marker.

    The keyword arguments exist so a test can ask the question with no network
    and no key. The divergence went unnoticed for weeks precisely because this
    function always fetched, so nothing could assert on it.
    """
    import budget
    import spend

    if fetch_error is None and month_total is None:
        try:
            used = float(spend.fetch().get("usage") or 0)
        except SystemExit as exc:                 # spend.fetch raises this
            fetch_error = str(exc)
        except Exception as exc:                  # network, DNS, timeout
            fetch_error = f"{type(exc).__name__}: {exc}"
        else:
            month_total, month = spend.month_delta(used)

    if fetch_error is not None:
        return False, f"cannot read spend ({fetch_error})"

    if charged is None:
        charged = budget.charge(budget.ledger_spend(), month_total=month_total)
    kind = budget.run_kind()
    decision = budget.decide(kind=kind,
                             allowance=spend.MONTHLY_ALLOWANCE_USD,
                             charged=charged,
                             stop_at_fraction=spend.STOP_AT_FRACTION)
    spent = float(charged.get(kind, 0.0))
    pot = budget.pots(spend.MONTHLY_ALLOWANCE_USD)[kind]
    where = (f"${spent:.2f} of the ${pot:.2f} {kind} pot in "
             f"{month or 'this month'} (${month_total or 0:.2f} across the "
             f"whole ${spend.MONTHLY_ALLOWANCE_USD:.2f} allowance)")
    if decision.over:
        return False, f"{where} is at or past its stop line"
    return True, where


def _offline_replies(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def gather(queries: list[dict], *, offline: dict | None,
           model: str | None) -> tuple[list[dict], float, list[dict], list[str]]:
    """Ask every query. Returns (leads, usd, diagnostics, failures).

    One failed query never loses the rest: a tripwire that gives up on the first
    throttled request would report a quiet week whenever a provider was busy,
    which is the exact failure this instrument exists to catch elsewhere.
    """
    leads: list[dict] = []
    diagnostics: list[dict] = []
    failures: list[str] = []
    total = 0.0

    for query in queries:
        label = f"{query['dimension']}:{query['key']}"
        if offline is not None:
            payload = offline.get(label) or offline.get("default") or {}
            found = ask.parse_leads(json.dumps(payload), query)
            cost, diag = 0.0, {"model": "offline fixture"}
        else:
            try:
                found, cost, diag = ask.ask(query, model=model)
            except (classify.AuthFailed, classify.CreditsExhausted) as exc:
                failures.append(f"{label}: {exc}")
                print(f"  STOP    {label}: {exc}", file=sys.stderr)
                break
            except (classify.Throttled, classify.ClassifyError) as exc:
                failures.append(f"{label}: {type(exc).__name__}: {exc}")
                print(f"  DEFER   {label}: {type(exc).__name__}: {exc}")
                continue

        total += cost
        diagnostics.append({"query": label, "leads": len(found),
                            "usd": round(cost, 5), **diag})
        leads.extend(found)
        print(f"  asked   {label:<24} {len(found):>2} lead(s)   ${cost:.4f}")

    return leads, total, diagnostics, failures


def report_health(conn, result: dict, worklist: dict) -> str:
    """File the run in the same ledger every collector reports to.

    Degraded when the outside view produced nothing usable: a tripwire that
    returns no leads is either broken or being throttled, and both look exactly
    like a world in which nothing happened.
    """
    counts = result["counts"]
    detail = (f"{counts['leads']} leads, {counts['missing']} missing, "
              f"{counts['held']} already held, ${result['cost']['run_usd']:.4f} "
              f"over {result['cost']['queries']} queries")
    store.report_health(
        conn, "tripwire",
        status="ok" if counts["usable"] else "degraded",
        items_found=counts["leads"],
        items_stored=worklist["missing_total"],
        detail=detail,
    )
    conn.commit()
    return "recorded"


def print_report(result: dict, worklist: dict) -> None:
    counts, cost = result["counts"], result["cost"]
    print("\n" + "=" * 72)
    print(f"TRIPWIRE, {result['ran_on']}   {result['plan']['basis']}")
    print("=" * 72)
    print(f"  {cost['queries']} queries -> {counts['leads']} leads "
          f"({counts['usable']} usable, {counts['unusable']} unusable)")
    print(f"  already held  {counts['held']}")
    print(f"  MISSING       {counts['missing']}   <- the work list")
    print(f"\n  spent this run     ${cost['run_usd']:.4f}")
    print(f"  per query          {_money(cost['usd_per_query'])}")
    print(f"  per usable lead    {_money(cost['usd_per_lead'])}")
    print(f"  per candidate miss {_money(cost['usd_per_candidate_miss'])}")
    if cost["confirmed_misses_lifetime"]:
        print(f"  per CONFIRMED miss {_money(cost['usd_per_confirmed_miss'])} "
              f"(lifetime ${cost['lifetime_usd']:.4f} / "
              f"{cost['confirmed_misses_lifetime']} stored)")
    else:
        print("  per CONFIRMED miss not yet measurable — the chase has stored "
              "nothing yet")

    if worklist["country_misses"]:
        print("\n  misses by country (the feed roadmap):")
        for key, value in list(worklist["country_misses"].items())[:20]:
            print(f"    {key:<6} {value}")
    if worklist["industry_misses"]:
        print("\n  misses by industry:")
        for key, value in list(worklist["industry_misses"].items())[:20]:
            print(f"    {key:<26} {value}")

    missing = worklist["leads"]
    if missing:
        print(f"\n  WORK LIST ({len(missing)}), all CLAIMS, none of them records:")
        for lead in missing[:25]:
            print(f"    - {lead['claimed_company']} "
                  f"({lead['claimed_country'] or '??'}, "
                  f"{lead['claimed_signal_type'] or 'unknown'}, "
                  f"{lead['claimed_event_date'] or 'undated'}) "
                  f"claims {lead['claimed_amount'] or 'no amount'} "
                  f"via {lead['claimed_outlet'] or 'no outlet named'}")
        if len(missing) > 25:
            print(f"    ... and {len(missing) - 25} more in the work list file")


def _money(value):
    return "n/a" if value is None else f"${value:.4f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="ask for real, print everything, write nothing")
    parser.add_argument("--offline", nargs="?", const=FIXTURE, default=None,
                        help="replay a captured reply file: no network, no spend")
    parser.add_argument("--plan-only", action="store_true",
                        help="print the plan and the budget, ask nothing")
    parser.add_argument("--limit", type=int,
                        help="cap the queries this run (cheap testing)")
    parser.add_argument("--run-index", type=int, default=0)
    parser.add_argument("--countries", default=None,
                        help="override the rotation with a comma-separated list "
                             "of ISO2 codes (a targeted, cheaper run)")
    parser.add_argument("--model", default=None,
                        help=f"override the search model (default {ask.MODEL})")
    parser.add_argument("--sweep-industries", dest="sweep", action="store_true",
                        default=None, help="force the full industry sweep")
    parser.add_argument("--no-industries", dest="sweep", action="store_false",
                        help="skip the industry sweep this run")
    parser.add_argument("--no-health", action="store_true",
                        help="skip the source_health entry (database untouched)")
    parser.add_argument("--results-dir", default=None)
    args = parser.parse_args(argv)

    projection = planner.monthly_projection()
    print("=" * 72)
    print("TRIPWIRE BUDGET")
    print("=" * 72)
    print(f"  cap                 ${projection['cap_usd_per_month']:.2f}/month "
          f"(analysis/tripwire/plan.TRIPWIRE_MONTHLY_USD)")
    print(f"  plan                {projection['runs_per_month']} runs x "
          f"{projection['countries_per_run']} countries + "
          f"{projection['industry_queries']} industries once a month "
          f"= {projection['queries_per_month']} queries")
    print(f"  projected           ${projection['projected_usd_per_month']:.2f}/month "
          f"at ${projection['usd_per_query_estimate']:.3f}/query "
          f"(pessimistic — what the plan is SIZED on)")
    print(f"  measured            ${projection['measured_usd_per_month']:.2f}/month "
          f"at ${projection['usd_per_query_measured']:.4f}/query, "
          f"{projection['estimate_over_measured']}x under the estimate")
    print(f"                      {projection['usd_per_query_measured_source']}")

    recall = planner.latest_recall()
    results_dir = args.results_dir or report.RESULTS_DIR
    run_plan = planner.build_plan(
        cycle=cycle_for(date.today(), args.run_index),
        recall=recall,
        sweep_industries=args.sweep,
        results_dir=results_dir,
        limit=args.limit,
    )
    run_plan["leads_per_query"] = planner.LEADS_PER_QUERY

    if args.countries:
        wanted = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        tiers = {t["iso2"]: t for t in planner.country_tiers(recall)}
        run_plan["countries"] = [
            tiers.get(iso2, {"iso2": iso2, "name": iso2,
                             "tier": planner.TIER_UNMEASURED, "measured": None})
            for iso2 in wanted
        ]
        run_plan["query_count"] = len(run_plan["countries"]) + len(run_plan["industries"])
        run_plan["estimated_usd"] = round(
            run_plan["query_count"] * planner.USD_PER_QUERY_ESTIMATE, 4)
        run_plan["basis"] += " | countries overridden on the command line"

    print("\n" + "=" * 72)
    print("PLAN")
    print("=" * 72)
    print(f"  basis   {run_plan['basis']}")
    print(f"  window  last {run_plan['lookback_days']} days")
    for country in run_plan["countries"]:
        measured = country.get("measured")
        why = (f"recall held {measured['held']}/{measured['total']}"
               if measured else "never measured")
        print(f"  country {country['iso2']:<4} {country['name']:<22} "
              f"[{country['tier']}] {why}")
    if run_plan["industries"]:
        print(f"  industries  full sweep of {len(run_plan['industries'])} "
              f"({run_plan['industry_sweep_reason']})")
    else:
        print(f"  industries  none: {run_plan['industry_sweep_reason']}")
    print(f"  {run_plan['query_count']} queries, "
          f"estimated ${run_plan['estimated_usd']:.2f}")

    if run_plan["query_count"] > planner.MAX_QUERIES_PER_RUN:
        print(f"\nSTOPPING: {run_plan['query_count']} queries exceeds the "
              f"per-run guard of {planner.MAX_QUERIES_PER_RUN}", file=sys.stderr)
        return 1
    if args.plan_only:
        return 0
    if not run_plan["query_count"]:
        print("\nNothing to ask this run.")
        return 0

    offline = None
    if args.offline:
        offline = _offline_replies(args.offline)
        print(f"\nOFFLINE replay from {os.path.relpath(args.offline, HERE)} — "
              f"no network, no spend")
    else:
        allowed, why = spend_guard()
        print(f"\nspend: {why}")
        if not allowed:
            # Not an error. The product ceiling binding is the budget working,
            # and a red run here would train the owner to ignore red runs.
            # Discovery is the part that yields: collection reads sources we
            # already trust, and this asks a model to guess where to look.
            print(f"NOT SPENDING: {why}. Discovery yields to the collection "
                  f"pipeline; the next run asks again.")
            return 0

    queries = ask.build_queries(run_plan)
    print()
    leads, usd, diagnostics, failures = gather(queries, offline=offline,
                                               model=args.model)

    leads, duplicates = diff.dedupe(leads)
    conn = schema.connect()
    index = diff.load_index(conn)
    print(f"\ndiffing {len(leads)} lead(s) against {len(index)} stored signals"
          + (f" ({duplicates} duplicate lead(s) dropped)" if duplicates else ""))
    results = diff.run(leads, index)

    counts = report.tally(results)
    cost = report.cost_block(usd, len(diagnostics), counts,
                             results_dir=results_dir, conn=conn)
    result = report.build_result(run_plan, queries, results, cost, diagnostics)
    worklist = report.build_worklist(result)
    print_report(result, worklist)

    writing = not (args.dry_run or offline is not None)
    if writing:
        paths = report.write(result, worklist, results_dir=results_dir)
        print(f"\nwrote {os.path.relpath(paths[0], HERE)}")
        print(f"wrote {os.path.relpath(paths[1], HERE)}")
        if not args.no_health:
            print(f"health: {report_health(conn, result, worklist)}")
    else:
        # A replay's leads are fixture text, and a dry run's are real but
        # unreviewed. Either overwriting the file the chase collector reads
        # would put fiction on a stable path other tooling trusts.
        target = tempfile.mkdtemp(prefix="tripwire-dry-")
        report.write(result, worklist, results_dir=target,
                     worklist_path=os.path.join(target, "tripwire_worklist.json"))
        print(f"\nDRY RUN — nothing written to the repository. "
              f"Sample output in {target}")
        conn.rollback()

    if failures:
        print(f"\n{len(failures)} query/queries failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)

    # Fail loud on a silent zero, exactly as a collector does: an outside view
    # that returns nothing usable is broken or throttled, and both look like a
    # world in which nothing happened.
    if not counts["usable"]:
        print("\nDEGRADED: no usable lead came back at all.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
