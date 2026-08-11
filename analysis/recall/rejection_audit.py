#!/usr/bin/env python3
"""Where the recall misses died: a filter problem, or a source problem?

    python3 -m analysis.recall.rejection_audit            # print the funnel
    python3 -m analysis.recall.rejection_audit --write    # ...and update data/

`measure_recall.py` says we hold 8 of 89 gold events. It does not say whether
the other 81 were in a feed we read and got dropped, or were never in any feed
we fetch. Those need opposite fixes — better filters versus more sources — and
until this ran, nobody had measured which.

MEASUREMENT, NEVER COLLECTION, which is the `analysis/` contract: no network
call, no model, no cent, no write to any table. It reads the sealed gold set,
the published recall result, `data/sources_catalogue.csv` (the feed list IS the
catalogue), and two read-only tables — `seen_urls`, which records every URL any
collector has ever resolved and what became of it, and `signals`.

WHAT DECIDES EACH VERDICT, in order, first match wins:

  held                   the gold set's own verdict was FOUND / FOUND_PARTIAL.
  stored_not_current     a row cites this exact URL but is superseded.
  stored_unmatched       a CURRENT row cites this exact URL and the gold set
                         still scored it MISSED. That is a matching or field
                         defect, not a coverage gap, and it is the one bucket
                         that would be embarrassing to leave uncounted.
  fetched_then_dropped   the exact URL is in `seen_urls` with an outcome that is
                         not `stored`. The pipeline saw this document and let it
                         go: keyword gate, gate model, extraction, validate or
                         dedupe. THE FILTER ANSWER.
  outside_our_history    we were not collecting when this was publishable. Every
                         news route has a reach — a run date minus a recency
                         window — and this event predates all of them. Neither a
                         filter problem nor a source problem: a HISTORY problem,
                         whose fix is a backfill, and it is reported first
                         because it is not one of the two answers anybody asked
                         for and it is the biggest bucket.
  feed_read_item_missed  a route was already reaching this far back, the
                         publisher's own feed is one we sweep, and the article
                         never arrived. Each item names the routes that were
                         live. A feed carries its most recent items only and
                         `MAX_ITEMS_PER_FEED` is 25, so a busy day buries an
                         article before we call; a Google News query reaches the
                         same publisher only if the query happened to match.
                         THE FILTER ANSWER's second form: plumbing, not sourcing.
  publisher_not_wired    the publisher is IN the catalogue with no feed URL, so
                         no collector reads it by name. Half a source problem:
                         the research is done and the connector is not.
  publisher_unknown      the publisher is not in the catalogue at all. THE
                         SOURCE ANSWER.

WHAT THIS CANNOT DO. It cannot say WHY a fetched document was dropped: no
rejection reason is persisted anywhere, only the URL and the word `rejected`.
And it cannot see a document that a feed carried and a run never reached,
because nothing records the items a collector skipped. Both limits push in the
same direction — they can only understate the filter side — so the verdict at
the bottom is stated with that asymmetry named.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_DB = ROOT / "data" / "talent_intel.db"
CATALOGUE = ROOT / "data" / "sources_catalogue.csv"
RESULTS_DIR = HERE / "results"
# Beside data/recall_worklist.json, produced the same way and for the same
# reason: a measurement that only produces a number is a report, one that
# produces a work list is a loop.
OUT_PATH = ROOT / "data" / "recall_rejection_audit.json"

# --- how far back each route could ever see ---------------------------------
#
# A route's reach is (the day it first ran) minus (the window it asks for). An
# event older than that was never available to it, however good the filters are.
#
#   google_news      run_collect asks Google News for `when:{N}d`, and N is
#                    source_registry.recency_window_days(LOCALES_PER_RUN=5,
#                    RUNS_PER_DAY=2) = 7 over 51 locales.
#   news_backstop    collectors/news_backstop.WINDOW_DAYS = 21.
#   national_press   a publisher's own RSS carries its most recent items and
#                    nothing else, and how many days that is depends on how much
#                    the publisher writes. There is no constant to read, so it
#                    is a PARAMETER here (--feed-backlog-days) and the report
#                    prints how the answer moves at 1, 3, 7 and 14 days.
#   gdelt            15 minutes to a few days; treated as national_press.
ROUTE_WINDOW_DAYS = {"google_news": 7, "news_backstop": 21, "gdelt": 3}
DEFAULT_FEED_BACKLOG_DAYS = 3

# Countries whose press is reached by search rather than by a named feed. Read
# from the catalogue's `feed_role` column, the same field news_backstop reads.
# The catalogue writes country names and the gold set writes ISO-2, so the names
# are normalised through the pipeline's own country vocabulary — imported
# lazily, because this module must still run if that import ever breaks.
BACKSTOP_ROLE = "backstop"


def _iso2(country_name: str) -> str:
    try:
        from pipeline import vocab
    except Exception:                                # pragma: no cover
        return ""
    return vocab.normalize_country(country_name or "") or ""

# The suffixes where the registrable domain is two labels deep. Enough for the
# gold set's publishers (co.il, com.au, co.jp, com.br, co.uk...) and deliberately
# not a public-suffix library: a dependency for a dozen strings is not worth it,
# and an unknown suffix falls back to the last two labels, which is right for
# every single-label TLD.
_TWO_LABEL_SUFFIXES = {
    "co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "go", "gob",
    "govt", "mil", "id", "in",
}


def registrable_domain(host: str) -> str:
    """example.co.il -> example.co.il, feeds.example.com -> example.com."""
    parts = [p for p in (host or "").lower().split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if parts[-2] in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_of(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return registrable_domain(host)


def _as_date(value) -> date | None:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


# --- the inputs --------------------------------------------------------------

def load_catalogue(path: Path = CATALOGUE) -> dict:
    """Every publisher we know of, and whether a collector actually reads it.

    `swept` mirrors collectors/national_press.load_feeds: a row with an http
    `rss` column is a feed that collector fetches every run. Re-derived here
    rather than imported so this module stays a leaf that a collector refactor
    cannot silently change, the same reason analysis/recall/match.py duplicates
    the company-key rule.
    """
    swept, known, backstop_countries = {}, {}, set()
    if not path.exists():
        return {"swept": swept, "known": known,
                "backstop_countries": backstop_countries, "rows": 0}
    rows = 0
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            name = (row.get("name") or "").strip()
            country = (row.get("country") or "").strip()
            if (row.get("feed_role") or "").strip().lower() == BACKSTOP_ROLE:
                backstop_countries.add(_iso2(country) or country)
            for column in ("url", "rss", "api"):
                value = (row.get(column) or "").strip()
                if not value.startswith("http"):
                    continue
                known.setdefault(domain_of(value), name)
            rss = (row.get("rss") or "").strip()
            if rss.startswith("http"):
                # The feed and the articles it points at are often on different
                # hosts (feeds.example.com -> example.com), so both count.
                for value in (rss, (row.get("url") or "").strip()):
                    if value.startswith("http"):
                        swept.setdefault(domain_of(value), name)
    return {"swept": swept, "known": known,
            "backstop_countries": backstop_countries, "rows": rows}


def load_seen(db: Path) -> tuple[dict, Counter, dict]:
    """`seen_urls` as {url: (collector, outcome)}, plus per-domain counts and
    the first time each collector resolved anything."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        seen, by_domain, first_run = {}, Counter(), {}
        for url, collector, outcome, first_seen in conn.execute(
                "SELECT url, collector, outcome, first_seen FROM seen_urls"):
            seen[url] = (collector, outcome)
            by_domain[domain_of(url)] += 1
            day = _as_date(first_seen)
            if day and (collector not in first_run or day < first_run[collector]):
                first_run[collector] = day
        for collector, run_at in conn.execute(
                "SELECT collector, min(run_at) FROM source_health GROUP BY 1"):
            day = _as_date(run_at)
            if day and (collector not in first_run or day < first_run[collector]):
                first_run[collector] = day
        return seen, by_domain, first_run
    finally:
        conn.close()


def load_cited(db: Path) -> dict:
    """{source_url: is_current} for every row we hold, current or superseded."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cited: dict[str, int] = {}
        for url, is_current in conn.execute(
                "SELECT source_url, max(is_current) FROM signals GROUP BY 1"):
            if url:
                cited[url] = int(is_current or 0)
        return cited
    finally:
        conn.close()


def latest_result(results_dir: Path = RESULTS_DIR) -> dict:
    """The most recent published recall result. Its verdicts are the input here:
    re-deciding FOUND/MISSED in a second place is how two numbers appear."""
    files = sorted(results_dir.glob("recall-*.json"))
    if not files:
        raise SystemExit(f"no recall result in {results_dir}")
    return json.loads(files[-1].read_text())


# --- the funnel --------------------------------------------------------------

def routes_for(item: dict, first_run: dict, backstop_countries: set,
               feed_backlog_days: int) -> list[tuple[date, str]]:
    """(earliest event date it could carry, route name) for every live route."""
    routes: list[tuple[date, str]] = []
    for collector, window in ROUTE_WINDOW_DAYS.items():
        started = first_run.get(collector)
        if started:
            routes.append((started - timedelta(days=window), collector))
    started = first_run.get("national_press")
    if started:
        routes.append((started - timedelta(days=feed_backlog_days),
                       "national_press"))
        # news_backstop reports inside national_press's own ledger, so it has no
        # first_run of its own; it starts when national_press does.
        if item.get("country") in backstop_countries:
            routes.append((started - timedelta(days=ROUTE_WINDOW_DAYS["news_backstop"]),
                           "news_backstop"))
    return routes


def reach_start(item: dict, first_run: dict, backstop_countries: set,
                swept: dict, feed_backlog_days: int) -> tuple[date | None, str]:
    """The earliest event date ANY route could still have been carrying, and
    which route reaches furthest. (None, "") when no route existed at all."""
    routes = routes_for(item, first_run, backstop_countries, feed_backlog_days)
    if not routes:
        return None, ""
    return min(routes, key=lambda r: r[0])


def classify_miss(item: dict, *, seen: dict, cited: dict, catalogue: dict,
                  by_domain: Counter, first_run: dict,
                  feed_backlog_days: int) -> dict:
    """One gold miss, placed in the funnel. Pure: no I/O, no clock."""
    url = item.get("source_url") or ""
    domain = domain_of(url)
    swept = catalogue["swept"]
    out = {
        "id": item.get("id"),
        "country": item.get("country"),
        "signal_type": item.get("signal_type"),
        "event_date": item.get("event_date"),
        "publisher": item.get("source_name"),
        "domain": domain,
        "domain_is_swept_feed": domain in swept,
        "domain_in_catalogue": domain in catalogue["known"],
        "urls_fetched_from_this_domain": by_domain.get(domain, 0),
    }

    if url in cited:
        out["stage"] = "stored_unmatched" if cited[url] else "stored_not_current"
        return out
    if url in seen:
        collector, outcome = seen[url]
        out["stage"] = "fetched_then_dropped"
        out["dropped_by"] = collector
        out["outcome"] = outcome
        return out

    event = _as_date(item.get("event_date"))
    earliest, route = reach_start(item, first_run, catalogue["backstop_countries"],
                                  swept, feed_backlog_days)
    # Which routes were actually reaching this far back, named rather than
    # summarised: "the publisher's feed is swept" and "the only live route was a
    # Google News query" are different findings and lead to different fixes.
    out["live_routes"] = sorted(
        name for start, name in routes_for(
            item, first_run, catalogue["backstop_countries"], feed_backlog_days)
        if event and start <= event)
    if event and earliest and event < earliest:
        out["stage"] = "outside_our_history"
        out["earliest_reachable"] = earliest.isoformat()
        out["widest_route"] = route
        return out

    if domain in swept:
        out["stage"] = "feed_read_item_missed"
    elif domain in catalogue["known"]:
        out["stage"] = "publisher_not_wired"
    else:
        out["stage"] = "publisher_unknown"
    return out


# What each stage means for the roadmap. The whole point of the audit is that
# these are different jobs, so the mapping is written down rather than inferred.
ANSWER = {
    "stored_unmatched": "neither — a matching or field defect",
    "stored_not_current": "neither — held, then superseded",
    "fetched_then_dropped": "filter",
    "outside_our_history": "history — backfill, not filters and not sources",
    "feed_read_item_missed": "filter (plumbing: feed depth, run cadence)",
    "publisher_not_wired": "source (researched, not connected)",
    "publisher_unknown": "source (not researched)",
}

STAGE_ORDER = ("stored_unmatched", "stored_not_current", "fetched_then_dropped",
               "outside_our_history", "feed_read_item_missed",
               "publisher_not_wired", "publisher_unknown")


def audit(result: dict, *, seen: dict, cited: dict, catalogue: dict,
          by_domain: Counter, first_run: dict,
          feed_backlog_days: int = DEFAULT_FEED_BACKLOG_DAYS) -> dict:
    items = result.get("items") or []
    misses = [i for i in items if i.get("verdict") == "MISSED"]
    placed = [classify_miss(i, seen=seen, cited=cited, catalogue=catalogue,
                            by_domain=by_domain, first_run=first_run,
                            feed_backlog_days=feed_backlog_days)
              for i in misses]
    stages = Counter(p["stage"] for p in placed)

    filter_side = sum(stages[s] for s in
                      ("fetched_then_dropped", "feed_read_item_missed"))
    source_side = sum(stages[s] for s in
                      ("publisher_not_wired", "publisher_unknown"))
    history = stages["outside_our_history"]

    # Sensitivity: the only judgement call in the whole funnel is how many days
    # of backlog a publisher's RSS holds. Printed at four settings so a reader
    # can see how much of the answer rests on it. The counts move — at 14 days,
    # 17 events cross from "we were not running" to "we were" — and the ORDERING
    # does not: history is the largest bucket at every setting, and
    # fetched_then_dropped is zero at all of them because that one is an
    # exact-URL lookup and no guess enters it.
    sensitivity = {}
    for days in (1, 3, 7, 14):
        counts = Counter(
            classify_miss(i, seen=seen, cited=cited, catalogue=catalogue,
                          by_domain=by_domain, first_run=first_run,
                          feed_backlog_days=days)["stage"]
            for i in misses)
        sensitivity[str(days)] = {
            "outside_our_history": counts["outside_our_history"],
            "feed_read_item_missed": counts["feed_read_item_missed"],
            "publisher_not_wired": counts["publisher_not_wired"],
            "publisher_unknown": counts["publisher_unknown"],
        }

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"),
        "measured_on": result.get("measured_on"),
        "goldset_version": (result.get("goldset") or {}).get("version"),
        "gold_events": len(items),
        "misses": len(misses),
        "feed_backlog_days": feed_backlog_days,
        "catalogue_rows": catalogue["rows"],
        "feeds_swept": len(catalogue["swept"]),
        # Recorded because it is load-bearing and invisible: not one of the gold
        # set's 29 countries is a backstop country, so the 21-day search route
        # never applies to a single one of these events.
        "gold_countries_on_the_backstop_route": sorted(
            {i.get("country") for i in misses} & catalogue["backstop_countries"]),
        "first_run": {k: v.isoformat() for k, v in sorted(first_run.items())},
        "stages": {s: stages[s] for s in STAGE_ORDER},
        "answers": {s: ANSWER[s] for s in STAGE_ORDER},
        "split": {"filter": filter_side, "source": source_side,
                  "history": history,
                  "neither": stages["stored_unmatched"] + stages["stored_not_current"]},
        "sensitivity_to_feed_backlog_days": sensitivity,
        "by_country": {
            country: dict(Counter(p["stage"] for p in placed
                                  if p["country"] == country))
            for country in sorted({p["country"] for p in placed})
        },
        "unwired_publishers": [
            {"domain": d, "misses": n, "publisher": catalogue["known"].get(d, "")}
            for d, n in Counter(
                p["domain"] for p in placed
                if p["stage"] == "publisher_not_wired").most_common()
        ],
        "unknown_publishers": [
            {"domain": d, "misses": n} for d, n in Counter(
                p["domain"] for p in placed
                if p["stage"] == "publisher_unknown").most_common()
        ],
        "items": placed,
        "verdict": verdict(stages, len(misses)),
        "limits": [
            "No rejection reason is persisted: seen_urls holds a URL and the "
            "word 'rejected'. A fetched-and-dropped item cannot be attributed "
            "to the keyword gate, the gate model, extraction, validate or "
            "dedupe.",
            "Nothing records the items a feed carried and a run did not reach, "
            "so 'feed_read_item_missed' is inferred from the publisher being "
            "swept, not observed.",
            "Both limits can only UNDERSTATE the filter side.",
            "The gold set's own answers were never consulted to decide which "
            "publishers we sweep: the feed list comes from "
            "data/sources_catalogue.csv, which predates this measurement.",
        ],
    }


def verdict(stages: Counter, misses: int) -> str:
    """One sentence, with the confidence it has earned."""
    if not misses:
        return "no misses to explain"
    history = stages["outside_our_history"]
    dropped = stages["fetched_then_dropped"]
    source = stages["publisher_not_wired"] + stages["publisher_unknown"]
    plumbing = stages["feed_read_item_missed"]
    if history >= misses / 2:
        return (
            f"NEITHER, yet: {history} of {misses} misses predate the day any "
            f"route could have carried them, and exactly {dropped} were fetched "
            f"and dropped — so on today's evidence this is a HISTORY problem "
            f"(backfill), with {source} genuinely unsourced publishers behind "
            f"it and {plumbing} inside feeds we already read. HIGH confidence "
            f"on the {dropped}: it is an exact-URL lookup. MEDIUM on the split "
            f"between the rest, which rests on publication dates and feed "
            f"reach rather than on a record of what each run saw."
        )
    if source > plumbing + dropped:
        return (f"SOURCE problem: {source} of {misses} misses are publishers no "
                f"collector reads, against {plumbing + dropped} that reached a "
                f"feed we sweep. MEDIUM confidence.")
    return (f"FILTER problem: {plumbing + dropped} of {misses} misses were "
            f"inside something we already read, against {source} unsourced. "
            f"MEDIUM confidence.")


# --- the runner --------------------------------------------------------------

def build(db: Path, feed_backlog_days: int) -> dict:
    seen, by_domain, first_run = load_seen(db)
    return audit(latest_result(), seen=seen, cited=load_cited(db),
                 catalogue=load_catalogue(), by_domain=by_domain,
                 first_run=first_run, feed_backlog_days=feed_backlog_days)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--feed-backlog-days", type=int,
                    default=DEFAULT_FEED_BACKLOG_DAYS)
    ap.add_argument("--write", action="store_true",
                    help=f"write {OUT_PATH.relative_to(ROOT)}")
    ap.add_argument("--examples", type=int, default=6)
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 2
    out = build(args.db, args.feed_backlog_days)

    print(f"gold set {out['goldset_version']}, measured {out['measured_on']}: "
          f"{out['misses']} misses of {out['gold_events']} events")
    print(f"catalogue {out['catalogue_rows']} rows, {out['feeds_swept']} "
          f"publisher domains swept by feed")
    print("first run: " + ", ".join(f"{k} {v}" for k, v in out["first_run"].items()))
    print()
    width = max(len(s) for s in STAGE_ORDER)
    for stage in STAGE_ORDER:
        n = out["stages"][stage]
        bar = "#" * n
        print(f"  {stage:<{width}}  {n:>3}  {ANSWER[stage]:<48} {bar}")
    print()
    split = out["split"]
    print(f"  filter {split['filter']}   source {split['source']}   "
          f"history {split['history']}   neither {split['neither']}")
    print()
    print("sensitivity to the one guess (days of RSS backlog a publisher holds):")
    for days, counts in out["sensitivity_to_feed_backlog_days"].items():
        print(f"  {days:>2}d -> history {counts['outside_our_history']}, "
              f"feed-read {counts['feed_read_item_missed']}, "
              f"not-wired {counts['publisher_not_wired']}, "
              f"unknown {counts['publisher_unknown']}")
    print()
    if out["unknown_publishers"]:
        print("publishers we do not know at all (the buy-or-build list):")
        for row in out["unknown_publishers"][: args.examples]:
            print(f"  {row['misses']:>2}  {row['domain']}")
    if out["unwired_publishers"]:
        print("publishers in the catalogue with no feed (the cheap list):")
        for row in out["unwired_publishers"][: args.examples]:
            print(f"  {row['misses']:>2}  {row['domain']}  {row['publisher']}")
    print()
    print("VERDICT: " + out["verdict"])
    for line in out["limits"]:
        print(f"  limit: {line}")

    if args.write:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUT_PATH.open("w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
            fh.write("\n")
        print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
