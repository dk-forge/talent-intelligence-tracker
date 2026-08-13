#!/usr/bin/env python3
"""Which COUNTRY BUCKET did each US gold event's candidate actually land in?

    python3 -m analysis.ranking.gold_bucket --fetch    # free sweep, writes a cache
    python3 -m analysis.ranking.gold_bucket --report   # tables from the cache

WHY THIS EXISTS
---------------
`analysis/ranking/read_share.py` (2026-08-12) established that
`candidate_rank.interleave_by_country` gives every country's best candidate a
place before any country's second, that the stored news population holds 77
country buckets, that the US bucket sits 50th, and that at the walker's
`DAILY_GATE_RATION` the US therefore takes ZERO places. It then corrected
itself: `candidate_rank.candidate_country` reads the Google News EDITION or the
publisher's country, never where the event happened. So the ranking
deprioritises US-SOURCED candidates, and whether the 51 US funding gold events
were ever IN the US bucket was left UNKNOWN — which makes
"country-need ranking caused the 26 walked-never-read misses" an inference and
not a measurement.

This module measures it. For each day that carries a gold event it replays the
walker's OWN fetch (`backfill_gnews_2026.fetch_day`, every edition, the same
per-language phrase packs, the same `after:`/`before:` bounds, the same
anchor-first URL dedup), applies the same free prefilter, ranks with the same
`candidate_rank.rank`, and reports where each gold event's article landed.

IT SPENDS NOTHING AND WRITES NOTHING
------------------------------------
No model is called: `pipeline.classify` is never imported here. The database is
opened READ-ONLY through a `mode=ro` URI, so not even a schema migration can
run. Nothing is marked seen, no row is stored, no ledger line is written. The
only network traffic is the same keyless Google News RSS the collector already
uses, at the collector's own pace.

WHAT THE ANSWER TURNS ON, AND IT IS NOT SUBTLE
----------------------------------------------
`registry.GOOGLE_NEWS_ANCHOR` is `("en", "US")` and `all_locales()` puts it
FIRST, while `fetch_day` de-duplicates by `discovery_url` keeping the first
edition that returned an article. So an article the US edition surfaces is
stamped `locale = "US:en"` and `candidate_country` returns `US`. The
"a US round written up in Sao Paulo ranks as Brazil" case can only happen to an
article the US edition never returned at all.

WHAT THIS PROBE CANNOT TELL YOU
-------------------------------
* **It is the same window the reference set was drawn from.** It says what
  happened to these 51 events in 2026-06/07 and nothing about any other window,
  any other signal type or any other country. It is a diagnosis, never a recall
  figure, and no number here may be published as coverage.
* **It re-queries Google News TODAY for historical days.** The index churns:
  an article that answered the walker in August may not answer now, and the
  reverse. A NOT-SURFACED row is therefore weaker evidence than a bucketed row,
  and is reported as its own state rather than folded into a bucket.
* **The free reducers between the prefilter and the ration are not replayed.**
  `store.already_seen`, `validate.precheck` and the funding-duplicate check all
  shrink the eligible pool before the ration is applied, and `already_seen`
  today reflects rows stored SINCE the walk. Skipping them leaves the pool
  LARGER than the walker's, so every rank position reported here is a
  pessimistic bound: the real position is this one or better.
* **A rank position is not an outcome.** Being inside the ration buys a gate
  call, not a stored row.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backfill_gnews_2026 as walker  # noqa: E402
from pipeline import candidate_rank, prefilter  # noqa: E402

GOLDSET = ROOT / "analysis" / "recall" / "us" / "goldset-us-2026-06.json"
AUDIT = ROOT / "data" / "recall_us_rejection_audit.json"
DB = ROOT / "data" / "talent_intel.db"
CACHE = ROOT / "data" / "gold_bucket_sweep.json"

#: Coverage of an event announced on D routinely carries a pubDate of D+1, and
#: the walker swept both days, so both are swept here. An event is credited to
#: whichever of its two days surfaced it first, which is also the day whose
#: ration it would have had to win.
LAG_DAYS = 1

_CUE = re.compile(
    r"\b(raise[sd]?|secure[sd]?|close[sd]?|land[sd]?|net(?:s|ted)?|"
    r"funding|round|seed|series\s+[a-e]|million|billion|investment|"
    r"valuation|backed|invests?)\b", re.I)


#: When the google_news walker actually swept this window, from
#: `data/backfill_state.json` (job `backfill-gnews-2026:2026-01-01..2026-07-26`,
#: last slice 2026-08-03T21:53Z, cursor 2026-07-13). The ranking context is what
#: the database held THEN, not what it holds now: a country that was empty
#: during the walk scored `W_COUNTRY_EMPTY` and outranked the US, and it may
#: hold rows today precisely because that bonus worked.
WALK_AS_OF = "2026-08-04"


def read_only_conn() -> sqlite3.Connection | None:
    """The ranking context's row counts, without the possibility of a write."""
    if not DB.exists():
        return None
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def context_as_of(conn: sqlite3.Connection | None,
                  as_of: str | None) -> candidate_rank.Context:
    """`candidate_rank.Context`, restricted to rows captured by `as_of`.

    The same two queries `Context.for_conn` runs, with one predicate added, so
    the two cannot drift in what they count. `as_of=None` gives today's context.
    A database with no `captured_at` on a row counts it as present, which is the
    conservative direction: it can only ADD rows to a country and so only ever
    lowers that country's need bonus.
    """
    if conn is None or not as_of:
        return candidate_rank.Context.for_conn(conn)
    clause = " AND (captured_at IS NULL OR captured_at <= ?)"
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT country, COUNT(*) FROM signals "
        " WHERE is_current = 1 AND country IS NOT NULL AND country != ''"
        + clause + " GROUP BY country", (as_of,)) if r[0]}
    employers = frozenset(r[0] for r in conn.execute(
        "SELECT DISTINCT company_key FROM signals "
        " WHERE is_current = 1 AND company_key IS NOT NULL "
        "   AND company_key != ''" + clause, (as_of,)) if r[0])
    return candidate_rank.Context(rows, employers)


def gold_events() -> list[dict]:
    return json.loads(GOLDSET.read_text())["items"]


def audit_stages() -> dict[str, str]:
    if not AUDIT.exists():
        return {}
    return {row["id"]: row["stage"]
            for row in json.loads(AUDIT.read_text()).get("items", [])}


def name_pattern(company: str) -> re.Pattern:
    """Match the company name as whole words, punctuation-tolerant.

    `logcat.ai` must match "logcat ai" and "Logcat.ai"; `Norm Ai` must not match
    "normal". Deliberately literal: a fuzzy matcher here would manufacture the
    finding it is supposed to measure.
    """
    parts = [re.escape(p) for p in re.split(r"[^A-Za-z0-9]+", company) if p]
    return re.compile(r"\b" + r"[^A-Za-z0-9]{0,2}".join(parts) + r"\b", re.I)


def sweep_dates(events: list[dict]) -> list[str]:
    days = set()
    for event in events:
        day = date.fromisoformat(event["event_date"])
        for offset in range(LAG_DAYS + 1):
            days.add((day + timedelta(days=offset)).isoformat())
    return sorted(days)


def fetch(argv_dates: list[str], *, ration: int, pause: float,
          as_of: str | None) -> dict:
    """One full-breadth sweep per day. Free, and the only network here."""
    events = gold_events()
    patterns = {e["id"]: name_pattern(e["company"]) for e in events}
    conn = read_only_conn()
    try:
        context = context_as_of(conn, as_of)
    finally:
        if conn is not None:
            conn.close()
    locales = walker.all_locales()
    editions = sorted({country for _, country in locales})
    empty = [c for c in editions if context.country_rows(c) == 0]
    thin = [c for c in editions
            if 0 < (context.country_rows(c) or 0) < candidate_rank.COUNTRY_THIN_ROWS]
    print(f"ranking context as of {as_of or 'today'}: "
          f"{len(context.rows_by_country)} countries hold rows, US holds "
          f"{context.rows_by_country.get('US', 0):,}. Of the {len(editions)} "
          f"edition countries, {len(empty)} hold nothing "
          f"({', '.join(empty) or '-'}) and {len(thin)} are thin "
          f"({', '.join(thin) or '-'})")

    out = {"generated_at": None, "ration": ration, "lag_days": LAG_DAYS,
           "as_of": as_of, "locales": len(locales), "days": {}}
    for day in argv_dates:
        lo = date.fromisoformat(day)
        stats: collections.Counter = collections.Counter()
        items = walker.fetch_day(lo, lo + timedelta(days=1), locales,
                                 pause=pause, stats=stats)
        kept = [i for i in items if prefilter.passes(i.get("raw_text", ""))[0]]
        ranked = candidate_rank.rank(kept, context)
        position = {id(item): index for index, item in enumerate(ranked)}
        survivors = {id(item) for item in kept}
        buckets = collections.Counter(
            candidate_rank.candidate_country(i) or "" for i in kept)
        # Depth WITHIN a country bucket, which is the number a floor or a quota
        # for that country would have to reach. The overall rank is what the
        # round robin produced; this is what is left when the robin is not the
        # constraint.
        depth: dict[int, int] = {}
        seen_in_bucket: collections.Counter = collections.Counter()
        for item in ranked:
            key = candidate_rank.candidate_country(item) or ""
            depth[id(item)] = seen_in_bucket[key]
            seen_in_bucket[key] += 1

        hits: dict[str, list[dict]] = {}
        for event in events:
            pattern = patterns[event["id"]]
            for item in items:
                headline = item.get("headline") or ""
                if not pattern.search(headline):
                    continue
                if not _CUE.search(headline):
                    continue
                surviving = id(item) in survivors
                hits.setdefault(event["id"], []).append({
                    "headline": headline,
                    "source_name": item.get("source_name") or "",
                    "locale": item.get("locale") or "",
                    "bucket": candidate_rank.candidate_country(item) or "",
                    "prefilter": surviving,
                    "rank": position.get(id(item)) if surviving else None,
                    "bucket_rank": depth.get(id(item)) if surviving else None,
                })
        out["days"][day] = {
            "articles": len(items),
            "past_prefilter": len(kept),
            "queries": stats["queries"],
            "query_errors": stats["query_errors"],
            "truncated": stats["truncated"],
            "buckets": dict(buckets.most_common()),
            "bucket_order": [candidate_rank.candidate_country(i) or ""
                             for i in ranked],
            "hits": hits,
        }
        print(f"[{day}] {len(items)} articles, {len(kept)} candidates, "
              f"{len(buckets)} buckets, {sum(len(v) for v in hits.values())} "
              f"gold matches ({stats['truncated']} truncated queries)")
    return out


def bucket_visiting_order(bucket_order: list[str]) -> list[str]:
    """The round robin's visiting order, recovered from the ranked sequence."""
    seen: list[str] = []
    for key in bucket_order:
        if key not in seen:
            seen.append(key)
    return seen


def report(cache: dict) -> None:
    events = {e["id"]: e for e in gold_events()}
    stages = audit_stages()
    ration = cache.get("ration", walker.DAILY_GATE_RATION)

    rows = []
    for event_id, event in events.items():
        hits = [dict(hit, day=day)
                for day, payload in sorted(cache["days"].items())
                for hit in payload["hits"].get(event_id, [])]
        best = best_us = None
        for hit in hits:
            if best is None or _better(hit, best):
                best = hit
            if hit["bucket"] == "US" and (best_us is None
                                          or _better(hit, best_us)):
                best_us = hit
        rows.append({
            "id": event_id,
            "company": event["company"],
            "date": event["event_date"],
            "stage": stages.get(event_id, "held"),
            # The bucket question is "was it ever in the US bucket", so a US
            # copy represents the event whenever one exists. `best` keeps the
            # cross-bucket best for the tally of what was seen at all.
            "hit": best_us or best,
            "buckets": sorted({h["bucket"] for h in hits if h["bucket"]}),
            "surfaced": bool(hits),
        })

    print(f"\nGOLD BUCKETS — {len(rows)} US funding events, "
          f"{len(cache['days'])} day-windows swept, "
          f"{cache['locales']} editions each, ration {ration}, "
          f"ranking context as of {cache.get('as_of') or 'today'}\n")
    head = (f"{'company':24}{'date':12}{'stage':22}{'bucket':8}"
            f"{'edition':9}{'rank':>6}{'in bucket':>10}")
    print(head)
    print("-" * len(head))
    for row in sorted(rows, key=lambda r: (r["stage"], r["id"])):
        hit = row["hit"]
        bucket = (hit["bucket"] or "-") if hit else "NOT-SURFACED"
        edition = (hit["locale"] if hit else "-") or "-"
        rank = ("-" if not hit or hit["rank"] is None else str(hit["rank"]))
        depth = ("-" if not hit or hit.get("bucket_rank") is None
                 else str(hit["bucket_rank"]))
        print(f"{row['company'][:23]:24}{row['date']:12}"
              f"{row['stage']:22}{bucket:8}{edition:9}{rank:>6}{depth:>10}")

    def tally(subset: list[dict]) -> collections.Counter:
        out: collections.Counter = collections.Counter()
        for row in subset:
            if not row["surfaced"]:
                out["NOT SURFACED by the query set at all"] += 1
            elif "US" in row["buckets"]:
                extra = [b for b in row["buckets"] if b != "US"]
                out["in the US bucket" + (f" (also {'/'.join(extra)})"
                                          if extra else "")] += 1
            elif row["buckets"]:
                out[f"ONLY a foreign bucket ({'/'.join(row['buckets'])})"] += 1
            else:
                out["surfaced with no country hint at all"] += 1
        return out

    print("\nALL 51")
    for label, count in tally(rows).most_common():
        print(f"  {count:>3}  {label}")

    walked = [r for r in rows if r["stage"] == "walked_never_read"]
    print(f"\nTHE {len(walked)} CLASSIFIED walked_never_read")
    for label, count in tally(walked).most_common():
        print(f"  {count:>3}  {label}")

    inside = [r for r in walked
              if r["hit"] and r["hit"]["rank"] is not None
              and r["hit"]["rank"] < ration]
    print(f"\n  of those, {len(inside)} sat inside the ration of {ration} in "
          f"this sweep's own ordering")

    print(f"\nWHAT DEPTH ALONE WOULD HAVE COLLECTED, of the {len(walked)}")
    print("  The ordering left exactly as it is, the cut moved. This is the "
          "lever that\n  costs money and takes nothing from any other country.")
    print(f"  {'cut':>8}{'collected':>11}")
    for cut in (ration, 99, 118, 217, 395, 10_000):
        got = sum(1 for r in walked
                  if r["hit"] and r["hit"]["rank"] is not None
                  and r["hit"]["rank"] < cut)
        label = "full day" if cut == 10_000 else str(cut)
        print(f"  {label:>8}{got:>11}")

    print(f"\nWHAT A US FLOOR WOULD HAVE COLLECTED, of the {len(walked)}")
    print("  A floor reserving `share` of the ration for the US buys the top-N "
          "of the\n  US bucket, so an event is collected exactly when its "
          "IN-BUCKET depth is\n  under N. Nothing here converts a place into a "
          "stored row.")
    print(f"  {'floor':>8}{'US places':>11}{'collected':>11}")
    for share in (0.10, 0.20, 0.35, 0.50, 1.00):
        places = max(1, int(ration * share))
        got = sum(1 for r in walked
                  if r["hit"] and r["hit"].get("bucket_rank") is not None
                  and r["hit"]["bucket"] == "US"
                  and r["hit"]["bucket_rank"] < places)
        label = "whole ration" if share == 1.00 else f"{share:.0%}"
        print(f"  {label:>8}{places:>11}{got:>11}")

    print("\nWHERE THE US BUCKET SITS, PER DAY")
    print(f"{'day':12}{'buckets':>9}{'US position':>13}{'US bucket size':>16}"
          f"{'candidates':>12}")
    for day, payload in sorted(cache["days"].items()):
        order = bucket_visiting_order(payload["bucket_order"])
        position = order.index("US") + 1 if "US" in order else 0
        print(f"{day:12}{len(order):>9}"
              f"{(position or '-'):>13}"
              f"{payload['buckets'].get('US', 0):>16}"
              f"{payload['past_prefilter']:>12}")


def _better(candidate: dict, incumbent: dict) -> bool:
    """Prefer a real candidate over a prefiltered one, then the better rank."""
    if candidate["prefilter"] != incumbent["prefilter"]:
        return candidate["prefilter"]
    if candidate["rank"] is None:
        return False
    if incumbent["rank"] is None:
        return True
    return candidate["rank"] < incumbent["rank"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--ration", type=int, default=walker.DAILY_GATE_RATION)
    ap.add_argument("--pause", type=float, default=0.4)
    ap.add_argument("--days", help="comma-separated ISO days to sweep "
                                   "(default: every gold event's day and the "
                                   "day after)")
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--as-of", default=WALK_AS_OF,
                    help="build the ranking context from rows captured by this "
                         "date (default: when the walker swept this window). "
                         "Pass an empty string for today's context.")
    args = ap.parse_args(argv)
    cache_path = Path(args.cache)

    if args.fetch:
        days = ([d.strip() for d in args.days.split(",") if d.strip()]
                if args.days else sweep_dates(gold_events()))
        payload = fetch(days, ration=args.ration, pause=args.pause,
                        as_of=args.as_of or None)
        if cache_path.exists():
            old = json.loads(cache_path.read_text())
            old["days"].update(payload["days"])
            payload = old
        cache_path.write_text(json.dumps(payload, indent=1, sort_keys=True))
        print(f"\nwrote {cache_path}")
    if args.report or not args.fetch:
        if not cache_path.exists():
            print("no sweep cache — run --fetch first. UNKNOWN, not zero.")
            return 2
        report(json.loads(cache_path.read_text()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
