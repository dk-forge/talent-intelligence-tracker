#!/usr/bin/env python3
"""What full worldwide coverage costs per month, with the arithmetic shown.

    python3 cost_projection.py              # live prices from OpenRouter
    python3 cost_projection.py --offline    # the committed price snapshot

WHY THIS EXISTS AS A PROGRAM AND NOT A PARAGRAPH
------------------------------------------------
Every cost claim in this repo has been re-derived by hand, in a document, and
gone stale within a week — the read-through moved to a frontier model, the cap
moved 60 -> 200, the allowance moved $10 -> $25, and each time somebody had to
find the last set of numbers and redo them. This reads the ledger and the live
price list and prints the answer, so the arithmetic is checkable and current
rather than remembered.

THE THREE KINDS OF NUMBER IN HERE, LABELLED EVERYWHERE
------------------------------------------------------
MEASURED   the provider's own usage accounting, out of `source_health`. What a
           run actually charged. Never arithmetic.
COUNTED    the funnel: candidates, gate calls, gate rejects, reads, and reads
           the budget refused. Out of `source_health` too, once the columns
           added on 2026-07-30 have data; until then, out of the two named
           workflow runs in FUNNEL below, which is why each entry carries its
           run id.
MODELLED   everything with a "would be" in it: a price list times a token
           count. A model, and it says so. The calibration factor in section
           [3] is how far the model sits from the measurement, and it is
           applied to every projection rather than quietly ignored.

WHAT IT DELIBERATELY WILL NOT DO
--------------------------------
It will not quote a saving for a lever that has not been measured. Prompt
caching is the standing example: `deepseek/deepseek-chat` publishes no cache
read price on any endpoint, so the saving is exactly $0 and is printed as $0
however attractive the arithmetic would be if it did.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "talent_intel.db"
PRICES_URL = "https://openrouter.ai/api/v1/models"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# --- COUNTED: the funnel, per scheduled invocation ---------------------------
#
# Seeded from two real runs because the columns that will carry this
# (source_health.candidates / gate_calls / gate_rejects / budget_deferred) were
# added on 2026-07-30 and no run has filled them yet. `measured_funnel()` below
# prefers the ledger the moment it has rows, and says which it used. Each entry
# names the run it came from so it can be re-read rather than trusted.
#
#   collect.yml       run 30571205733, 2026-07-30 18:37Z
#   collect-press.yml run 30532073727, 2026-07-30 09:47Z
#
# `survivors` is what the gate KEPT — the number of reads full coverage of this
# collector demands. `reads` is what it was allowed to buy. The difference is
# the coverage gap, and for national_press on that run it was 49 stories in
# Hebrew, German, Serbian, Vietnamese and Korean.
FUNNEL = {
    # collector:      (candidates, gate_calls, survivors, reads, runs_per_day)
    "national_press": (1148, 627, 249, 200, 2),
    "google_news":    (640, 498, 153, 153, 2),
    "gdelt":          (74, 40, 26, 26, 2),
    "sec_edgar":      (10, 2, 2, 2, 2),
    "sec_form_d":     (7, 1, 1, 1, 2),
}

DAYS = 30

#: What `pipeline/cheap_extract.py` closes on FUNDING headlines alone, measured
#: over the 289 stored funding rows on the paid path: 33.2%, against 2.2%
#: across the whole paid path. Funding headlines state every field ("X raises
#: $25M Series B led by Y"), which is why the same parser is fifteen times more
#: effective on them. Used only for the funding-only scenario below.
FREE_CLOSE_ON_FUNDING = 0.332

# --- MODELLED: token counts, from exact character counts ---------------------
#
# The character counts are exact (`len(classify.SCHEMA_HINT)` and friends); the
# tokens are this repo's own calibration of 4.39 chars/token, with a 1.3x
# multiplier on the Anthropic call for its heavier tokenizer. Section [3]
# reports how far the resulting model sits from what the provider actually
# charged, and every projection below is scaled by that factor.
GATE_IN, GATE_OUT = 504, 2          # system 217 + item; one word back
EXTRACT_IN, EXTRACT_OUT = 3100, 254  # prefix 2,509 of the input, byte-stable
READ_IN, READ_OUT = 550, 90          # the small prompt, one or two sentences
# What a cache would serve, if one existed: len(classify.extract_stable_prefix())
# = 11,016 characters / 4.39 = 2,509 tokens. The previous value here (2,754)
# was 9.8% above what the prompt actually holds and nothing supported it — it
# quietly flattered every cached-extraction row below by ~$0.57/month. A test
# (tests/test_preamble_cache_exit.py) now pins this constant to the live
# prefix, so it moves when the prompt does and never on its own.
EXTRACT_PREFIX = 2509

MODELS = {
    "gate": os.environ.get("TIT_GATE_MODEL", "google/gemini-2.5-flash-lite"),
    "extract": os.environ.get("TIT_MODEL", "deepseek/deepseek-chat"),
    "read": os.environ.get("TIT_READ_MODEL", "anthropic/claude-sonnet-5"),
}

# Candidate swaps priced beside the incumbent. Nothing here is switched on by
# this program; it prices decisions, it does not take them.
ALTERNATIVES = {
    "extract": ["deepseek/deepseek-chat-v3.1", "google/gemini-2.5-flash-lite"],
    "read": ["anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-5:batch",
             "anthropic/claude-haiku-4.5:batch"],
}

SNAPSHOT = Path(__file__).resolve().parent / "data" / "model_prices.json"


# --- prices ------------------------------------------------------------------

def fetch_prices(offline: bool) -> tuple[dict, str]:
    """{model_id: {prompt, completion, cache_read}} and where it came from.

    The snapshot is written on every successful live fetch, so `--offline`
    reproduces the last real price list rather than a hand-typed one. A price
    somebody typed is how a rate card becomes a forecast.
    """
    if not offline:
        try:
            req = urllib.request.Request(PRICES_URL,
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.load(resp)
            prices = {
                m["id"]: {
                    "prompt": float(m["pricing"].get("prompt") or 0),
                    "completion": float(m["pricing"].get("completion") or 0),
                    # None, not 0. "This provider does not price a cache read"
                    # and "a cache read is free" are different facts, and the
                    # first one is the reason no caching saving is claimed.
                    "cache_read": (float(m["pricing"]["input_cache_read"])
                                   if m["pricing"].get("input_cache_read")
                                   else None),
                }
                for m in payload.get("data") or []
            }
            try:
                SNAPSHOT.write_text(json.dumps(prices, indent=1, sort_keys=True))
            except OSError:
                pass
            return prices, "live from OpenRouter"
        except Exception as exc:  # noqa: BLE001 - any failure falls back
            print(f"  (live prices unavailable: {exc}; using the snapshot)")
    try:
        return json.loads(SNAPSHOT.read_text()), f"snapshot {SNAPSHOT.name}"
    except (OSError, ValueError):
        raise SystemExit(
            "No live prices and no snapshot. Run once with a network so "
            f"{SNAPSHOT} is written; this program will not invent a price.")


def call_cost(prices: dict, model: str, tokens_in: int, tokens_out: int,
              cached_prefix: int = 0) -> float | None:
    """One call, at this model's published price. None when the model is not
    served — a missing price is reported, never guessed at."""
    p = prices.get(model)
    if not p:
        return None
    fresh_in = tokens_in
    total = 0.0
    if cached_prefix and p.get("cache_read") is not None:
        fresh_in = max(0, tokens_in - cached_prefix)
        total += cached_prefix * p["cache_read"]
    total += fresh_in * p["prompt"] + tokens_out * p["completion"]
    return total


# --- MEASURED ----------------------------------------------------------------

def measured(conn) -> dict:
    """What the model actually charged, out of the ledger."""
    rows = list(conn.execute(
        "SELECT collector, run_at, cost_usd, reads_bought, rows_from_reads, "
        "       prompt_tokens, completion_tokens, cached_tokens, detail "
        "  FROM source_health "
        " WHERE cost_usd IS NOT NULL ORDER BY run_at DESC LIMIT 40"))
    if not rows:
        raise SystemExit("The ledger holds no priced run yet. Collect once.")

    import re
    written = 0
    for r in rows:
        m = re.search(r"read-through [^:]+: (\d+) written", r[8] or "")
        written += int(m.group(1)) if m else 0

    return {
        "runs": len(rows),
        "cost": sum(r[2] or 0 for r in rows),
        "reads": sum(r[3] or 0 for r in rows),
        "rows": sum(r[4] or 0 for r in rows),
        "read_throughs": written,
        "prompt_tokens": sum(r[5] or 0 for r in rows),
        "completion_tokens": sum(r[6] or 0 for r in rows),
        "cached_tokens": sum(r[7] or 0 for r in rows),
        "newest": rows[0][1],
        "oldest": rows[-1][1],
    }


def paid_path_pillars(conn) -> dict:
    """The pillar mix of rows the MODEL bought, as fractions.

    Read from the rows themselves rather than typed, because the mix moves
    every time a collector is added. Used only to price the scenario where the
    leadership pillar leaves the paid path for company registries.
    """
    paid = ("google_news", "national_press", "gdelt")
    marks = ",".join("?" * len(paid))
    rows = list(conn.execute(
        f"SELECT pillar, COUNT(*) FROM signals "
        f" WHERE is_current = 1 AND collector IN ({marks}) GROUP BY pillar",
        paid))
    total = sum(n for _, n in rows)
    return {p: n / total for p, n in rows} if total else {}


def measured_funnel(conn) -> tuple[dict, str, set]:
    """The funnel per collector: the ledger where it has data, the seed elsewhere.

    MERGED, NOT REPLACED, and the distinction cost a wrong answer once already.
    The funnel columns landed on 2026-07-30, so for a while only the collectors
    that have run since then have rows. Taking the ledger wholesale dropped
    `national_press` — the hungriest collector, 249 reads a run — along with
    gdelt and the SEC pair, and the projected bill fell from $75.99 to $57.24
    on nothing but four missing collectors. A number that looks more
    authoritative and is less complete is worse than the estimate it replaced.

    So each collector is taken from the ledger if the ledger has seen it and
    from the seed if not, and the third return value is the set that came from
    the ledger, so the report can mark every row.
    """
    try:
        rows = list(conn.execute(
            "SELECT collector, AVG(candidates), AVG(gate_calls), "
            "       AVG(gate_rejects), AVG(reads_bought), AVG(budget_deferred) "
            "  FROM source_health WHERE gate_calls IS NOT NULL "
            "   AND candidates IS NOT NULL GROUP BY collector"))
    except sqlite3.OperationalError:
        rows = []

    funnel = dict(FUNNEL)
    measured_set = set()
    for coll, cands, gate, rejects, reads, deferred in rows:
        if coll not in funnel:
            continue  # a collector with no known cadence cannot be projected
        runs_per_day = funnel[coll][4]
        kept = round((gate or 0) - (rejects or 0))
        funnel[coll] = (round(cands or 0), round(gate or 0), kept,
                        round(reads or 0), runs_per_day)
        measured_set.add(coll)

    if not measured_set:
        return funnel, "seeded from two named workflow runs (see FUNNEL)", set()
    return (funnel,
            f"{len(measured_set)} of {len(funnel)} collector(s) MEASURED from "
            f"the ledger, the rest still seeded",
            measured_set)


# --- the report --------------------------------------------------------------

def bar() -> None:
    print("-" * 72)


def _discovery_cost() -> dict:
    """What the tripwire costs the allowance per month, and whether it is armed.

    ARMED is read from the schedule that actually dispatches it rather than
    asserted here. The tripwire is a database writer, so it may not carry a cron
    of its own; the Mon+Thu slot lives in `schedule-link-hygiene.yml`, which
    writes a queue ticket. Deriving it from that file means this figure cannot
    drift from the deployment the day somebody disarms it — the same reason
    `writer_queue.lock_group_workflows` reads the workflows instead of a list.

    A dormant tripwire is charged at $0.00 and still PRINTED, with the price
    arming it would cost. A reserve held against a job that is not running is a
    ration taken out of collection for nothing.
    """
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    from analysis.tripwire import plan as tripwire_plan

    projection = tripwire_plan.monthly_projection()
    schedule = root / ".github" / "workflows" / "schedule-link-hygiene.yml"
    armed = False
    if schedule.exists():
        text = schedule.read_text()
        armed = "tripwire.yml" in text and "cron: '0 7 * * 1,4'" in text
    return {
        "armed": armed,
        "queries_per_month": projection["queries_per_month"],
        "usd_per_query": projection["usd_per_query_measured"],
        "usd_per_query_source": projection["usd_per_query_measured_source"],
        "usd_per_query_estimate": projection["usd_per_query_estimate"],
        "usd_per_month": projection["measured_usd_per_month"] if armed else 0.0,
        "usd_per_month_if_armed": projection["measured_usd_per_month"],
        "cap_usd_per_month": projection["cap_usd_per_month"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true",
                    help="use the committed price snapshot")
    ap.add_argument("--allowance", type=float, default=None,
                    help="the monthly ceiling to size the cap against "
                         "(default: the COMMITTED pot, budget.pots of "
                         "spend.MONTHLY_ALLOWANCE_USD)")
    args = ap.parse_args()

    allowance = args.allowance
    if allowance is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import spend

        # THE COMMITTED POT, NOT THE WHOLE ALLOWANCE. Everything this program
        # sizes — the read caps, "what the allowance actually buys", the
        # full-coverage verdict — is about the SCHEDULED collectors, and since
        # 2026-08-13 they do not have the whole allowance to spend: the
        # catch-up walkers hold a pot of their own, and neither side can borrow
        # from the other. Sizing the caps against the full figure would
        # recommend a depth the collectors cannot pay for.
        import budget

        allowance = budget.pots(spend.MONTHLY_ALLOWANCE_USD)[budget.COMMITTED]
        print(f"[sizing against the COMMITTED pot: ${allowance:,.2f} of a "
              f"${spend.MONTHLY_ALLOWANCE_USD:,.2f} allowance. The remaining "
              f"${spend.MONTHLY_ALLOWANCE_USD - allowance:,.2f} is the "
              f"discretionary pot the backfill walkers draw on, and the "
              f"collectors cannot spend it. See budget.py.]")

    # COLLECTION IS NOT THE ONLY THING THAT SPENDS, AND THIS FILE USED TO SAY IT
    # WAS. The discovery tripwire has been armed since 2026-07-30 — twice a
    # week, from schedule-link-hygiene.yml — and it bills the same OpenRouter
    # key against the same monthly allowance. It appeared nowhere in this
    # program, so every "what the allowance actually buys" figure below was
    # computed against a ceiling that had already been partly spent by
    # something else.
    #
    # MEASURED, not estimated. `plan.USD_PER_QUERY_ESTIMATE` ($0.02) is
    # deliberately pessimistic and exists to SIZE the plan; what a query
    # actually costs is $0.0057, from OpenRouter's own usage accounting over 17
    # live search-backed queries, and that is the number a budget should be
    # reconciled against. Both are printed below so the gap between them stays
    # visible as the safety margin it is, rather than one of them quietly
    # standing in for the other.
    tripwire = _discovery_cost()
    collection_allowance = max(0.0, allowance - tripwire["usd_per_month"])

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        m = measured(conn)
        funnel, funnel_source, from_ledger = measured_funnel(conn)
        paid_mix = paid_path_pillars(conn)
    finally:
        conn.close()
    prices, price_source = fetch_prices(args.offline)

    print("=" * 72)
    print("WHAT WORLDWIDE COVERAGE COSTS")
    print("=" * 72)

    # [1] ------------------------------------------------------------------
    print(f"\n[1] MEASURED  {m['runs']} priced run(s), "
          f"{m['oldest'][:10]}..{m['newest'][:10]}")
    print(f"    ${m['cost']:.4f} charged for {m['reads']} read(s) -> "
          f"{m['rows']} row(s)")
    print(f"    ${m['cost'] / max(m['reads'], 1):.5f} per read, "
          f"${m['cost'] / max(m['rows'], 1):.5f} per stored row")
    waste = m["read_throughs"] - m["rows"]
    if m["read_throughs"]:
        print(f"    {m['read_throughs']} interpretation(s) bought for "
              f"{m['rows']} row(s): {waste} "
              f"({100 * waste // m['read_throughs']}%) went to records a free "
              f"guard settled")
        print("      -> that is the read-late lever, and it is now shipped")
    print(f"    prompt tokens {m['prompt_tokens']:,}, of which "
          f"{m['cached_tokens']:,} served from cache "
          f"({100 * m['cached_tokens'] // max(m['prompt_tokens'], 1)}%)")

    # [2] ------------------------------------------------------------------
    print(f"\n[2] COUNTED  the funnel per day  ({funnel_source})")
    print(f"    {'collector':16} {'cands':>7} {'gated':>7} {'kept':>7} "
          f"{'read':>7} {'UNREAD':>7}")
    day_gate = day_full = day_kept = 0
    for coll, (cands, gate, survivors, reads, per_day) in sorted(funnel.items()):
        day_gate += gate * per_day
        day_full += reads * per_day
        day_kept += survivors * per_day
        mark = "  measured" if coll in from_ledger else "  seeded"
        print(f"    {coll:16} {cands * per_day:7} {gate * per_day:7} "
              f"{survivors * per_day:7} {reads * per_day:7} "
              f"{(survivors - reads) * per_day:7}{mark}")
    print(f"    {'TOTAL/day':16} {'':7} {day_gate:7} {day_kept:7} "
          f"{day_full:7} {day_kept - day_full:7}")
    print(f"\n    FULL COVERAGE means reading all {day_kept:,}/day = "
          f"{day_kept * DAYS:,}/month.")
    print(f"    Today's caps buy {day_full * DAYS:,}/month, so "
          f"{(day_kept - day_full) * DAYS:,} stories a month are refused for "
          f"budget\n    rather than for a verdict. That is the coverage gap, "
          f"and it is where\n    Germany's story count comes from.")

    # [3] ------------------------------------------------------------------
    print(f"\n[3] MODELLED  unit prices  ({price_source})")
    unit = {
        "gate": call_cost(prices, MODELS["gate"], GATE_IN, GATE_OUT),
        "extract": call_cost(prices, MODELS["extract"], EXTRACT_IN, EXTRACT_OUT),
        "read": call_cost(prices, MODELS["read"], READ_IN, READ_OUT),
    }
    for stage in ("gate", "extract", "read"):
        model = MODELS[stage]
        cache = prices.get(model, {}).get("cache_read")
        cache_note = ("no cache read price on this slug -> caching is worth $0"
                      if cache is None else f"cache read ${cache * 1e6:.3f}/M")
        if unit[stage] is None:
            print(f"    {stage:9} {model:34} NOT SERVED — no price")
            continue
        print(f"    {stage:9} {model:34} ${unit[stage]:.6f}/call   {cache_note}")

    # Calibration. The model above times the measured call counts, against what
    # the provider actually charged. Retries, longer texts and refused
    # read-throughs all live in this gap, and it is applied to everything below
    # rather than explained away.
    modelled = (unit["extract"] or 0) * m["reads"] + (unit["read"] or 0) * m["read_throughs"]
    modelled += (unit["gate"] or 0) * m["reads"] * 3  # ~3 screened per read
    factor = m["cost"] / modelled if modelled else 1.0
    print(f"\n    calibration: the model says ${modelled:.4f} for the runs the "
          f"provider charged\n    ${m['cost']:.4f} for, so every projection "
          f"below is multiplied by {factor:.2f}.")

    # [4] ------------------------------------------------------------------
    print("\n[4] THE BILL, per month, at full cadence")
    # Interpretations bought per READ, under each policy. Both are ratios of
    # the same denominator on purpose: the bug this replaces divided rows by
    # read-throughs (0.671) and then multiplied by reads, which overstated the
    # read-through line by 28% and understated read-late's saving by the same.
    #
    #   before read-late   every read that extraction called a signal bought one
    #   after read-late    only a record that will actually store buys one
    #   conditional        ...and only when the free triage flags extraction's
    #                      own sentence. 8.8% measured over 4,171 rows of fused
    #                      deepseek prose; 12% used here, because the flagged
    #                      share rises as coverage moves to languages the
    #                      triage declines to score.
    rt_per_read_eager = m["read_throughs"] / max(m["reads"], 1)
    rt_per_read_late = m["rows"] / max(m["reads"], 1)
    CONDITIONAL_SHARE = 0.12

    def bill(reads_per_month: int, *, read_late: bool, conditional: bool = False,
             extract_model: str | None = None,
             read_model: str | None = None) -> tuple[float, dict]:
        gate_calls = day_gate * DAYS
        e = call_cost(prices, extract_model or MODELS["extract"],
                      EXTRACT_IN, EXTRACT_OUT, cached_prefix=EXTRACT_PREFIX)
        r = call_cost(prices, read_model or MODELS["read"], READ_IN, READ_OUT)
        g = unit["gate"] or 0
        if e is None or r is None:
            return float("nan"), {}
        # Read-late buys an interpretation only for a record that stores.
        # Without it, every read that extraction called a signal bought one.
        rate = rt_per_read_late if read_late else rt_per_read_eager
        if conditional:
            rate *= CONDITIONAL_SHARE
        interpretations = reads_per_month * rate
        parts = {"gate": g * gate_calls * factor,
                 "extract": e * reads_per_month * factor,
                 "read": r * interpretations * factor}
        return sum(parts.values()), parts

    today_reads = day_full * DAYS
    full_reads = day_kept * DAYS

    rows = [
        ("today's caps, before read-late", today_reads, False, False, None, None),
        ("today's caps, WITH read-late", today_reads, True, False, None, None),
        ("FULL coverage, read-late", full_reads, True, False, None, None),
        ("FULL coverage, second pass CONDITIONAL", full_reads, True, True, None, None),
    ]
    for alt in ALTERNATIVES["extract"]:
        rows.append((f"  ... extraction on {alt.split('/')[-1]}",
                     full_reads, True, True, alt, None))
    rows.append(("  ... both cheapest, together", full_reads, True, True,
                 ALTERNATIVES["extract"][-1], ALTERNATIVES["read"][-1]))

    # The architecture moving underneath this. Two other agents are taking
    # LEADERSHIP to company registries and HIRING to job boards, both free and
    # both primary sources. If they land, the paid path is what has no global
    # filing regime behind it, which is funding news. Scaled by the measured
    # share of paid-path rows that are NOT leadership — an approximation,
    # because reads are not rows, and labelled as one.
    if paid_mix:
        non_leadership = 1 - paid_mix.get("leadership_change", 0)
        left = int(full_reads * non_leadership)
        rows.append(
            (f"  ... leadership offloaded, {non_leadership:.0%} of reads left",
             left, True, True, None, None))
        # Funding news is the pillar with no global filing regime behind it,
        # and it is also the most structured text in the corpus. Measured on
        # 289 stored funding rows, the FREE parser closes 33.2% from the
        # headline alone against 2.2% across the whole paid path. So the
        # funding-only paid path is what the free parser declines.
        funding_only = int(left * (1 - FREE_CLOSE_ON_FUNDING))
        rows.append(
            (f"  ... and free extraction takes {FREE_CLOSE_ON_FUNDING:.0%} of funding",
             funding_only, True, True, None, None))
        rows.append(
            ("  ... all of it, on the cheapest models",
             funding_only, True, True,
             ALTERNATIVES["extract"][-1], ALTERNATIVES["read"][-1]))

    print(f"    {'configuration':44} {'gate':>6} {'extr':>6} {'read':>6} "
          f"{'TOTAL':>7}")
    for label, reads, late, cond, em, rm in rows:
        total, parts = bill(reads, read_late=late, conditional=cond,
                            extract_model=em, read_model=rm)
        if not parts:
            print(f"    {label:44} {'not served at any price':>28}")
            continue
        flag = "" if total <= collection_allowance else "  OVER"
        print(f"    {label:44} {parts['gate']:6.2f} {parts['extract']:6.2f} "
              f"{parts['read']:6.2f} {total:7.2f}{flag}")

    # DISCOVERY, priced beside collection rather than left out of it. This is
    # the only paid thing in the product that is not a read of a source we
    # already trust, and it was invisible here until 2026-08-02.
    state = "ARMED, Mon+Thu" if tripwire["armed"] else "DORMANT"
    print(f"\n    discovery (the tripwire, {state}): "
          f"{tripwire['queries_per_month']} search-backed queries/month at "
          f"${tripwire['usd_per_query']:.4f} MEASURED\n"
          f"      = ${tripwire['usd_per_month_if_armed']:.2f}/month"
          + ("" if tripwire["armed"] else " if it were armed; $0.00 today")
          + f", against its own ${tripwire['cap_usd_per_month']:.2f} cap and an\n"
          f"      estimate of ${tripwire['usd_per_query_estimate']:.3f}/query "
          f"that sizes the plan and never reports it.\n"
          f"      {tripwire['usd_per_query_source']}")
    print(f"\n    allowance ${allowance:.2f}/month (spend.py), of which "
          f"${tripwire['usd_per_month']:.2f} is discovery, leaving\n"
          f"    ${collection_allowance:.2f} for collection. Anything marked OVER "
          f"would trip spend.py --degrade\n    partway through the month: free "
          f"collection continues, paid reads stop.")

    # [5] ------------------------------------------------------------------
    print("\n[5] WHAT THE ALLOWANCE ACTUALLY BUYS")
    total_full, _ = bill(full_reads, read_late=True, conditional=True)
    e = call_cost(prices, MODELS["extract"], EXTRACT_IN, EXTRACT_OUT,
                  cached_prefix=EXTRACT_PREFIX)
    per_read = ((e or 0)
                + (unit["read"] or 0) * rt_per_read_late * CONDITIONAL_SHARE) * factor
    gate_bill = (unit["gate"] or 0) * day_gate * DAYS * factor
    affordable = (max(0, int((collection_allowance - gate_bill) / per_read))
                  if per_read else 0)
    print(f"    the gate costs ${gate_bill:.2f}/month and is not optional: it "
          f"is how we know\n    which stories are worth reading. That leaves "
          f"${collection_allowance - gate_bill:.2f} for reads at "
          f"${per_read:.5f} each.")
    print(f"    affordable: {affordable:,} reads/month = "
          f"{affordable // DAYS:,}/day, against demand of {day_kept:,}/day "
          f"({100 * affordable // max(day_kept * DAYS, 1)}% of full coverage)")
    print("\n    per-run caps that spend exactly that, split by measured demand:")
    # A collector whose whole demand is a rounding error gets its whole demand.
    # Rationing sec_edgar to one read a run to save $0.10 would cost a filing
    # to buy nothing, and a cap that binds on a collector finding two items is
    # a cap that will one day bind on the day it finds fifty.
    SMALL = 20  # reads/run below which rationing is not worth the arithmetic
    small = {c: v for c, v in funnel.items() if v[2] <= SMALL}
    big = {c: v for c, v in funnel.items() if v[2] > SMALL}
    reserved = sum(v[2] * v[4] for v in small.values()) * DAYS
    for_big = max(0, affordable - reserved)
    big_demand = sum(v[2] * v[4] for v in big.values()) * DAYS
    for coll, (_c, _g, survivors, _r, per_day) in sorted(funnel.items()):
        if coll in small:
            cap, why = max(survivors * 3, 40), "whole demand, with headroom"
        else:
            share = survivors * per_day * DAYS / max(big_demand, 1)
            cap = max(1, int(for_big * share / (per_day * DAYS)))
            why = f"{100 * cap // max(survivors, 1)}% of demand"
        print(f"      TIT_READTHROUGH_CAP={cap:<5} {coll:16} "
              f"(demand {survivors}/run, {why})")
    print("\n    A cap is a ceiling, not a spend. The ordering in "
          "pipeline/candidate_rank.py\n    decides WHICH stories fit inside "
          "it, and it now gives every country's\n    best story a place before "
          "any country's second.")
    bar()
    if total_full > collection_allowance:
        print(f"FULL COVERAGE DOES NOT FIT: ${total_full:.2f} against "
              f"${collection_allowance:.2f} (${allowance:.2f} allowance less "
              f"${tripwire['usd_per_month']:.2f} of discovery).")
        print("Read section [4] for what would have to change to close it.")
        return 2
    print(f"Full coverage fits: ${total_full:.2f} against "
          f"${collection_allowance:.2f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
