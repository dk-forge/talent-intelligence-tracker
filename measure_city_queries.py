#!/usr/bin/env python3
"""What the city-led discovery queries add to a run, in candidates.

    python3 measure_city_queries.py                 # today's rotation, run 0
    python3 measure_city_queries.py --run-index 1
    python3 measure_city_queries.py --editions 5

READ-ONLY AND FREE. It fetches the same Google News RSS the collector fetches,
counts what comes back, and stops. No model is called, nothing is classified,
nothing is stored, and `spend.py` is not touched. That is the whole reason this
script can exist: a QUERY is free and a READ is not, so the honest way to size a
vocabulary change is to fetch the results and refuse to read them.

WHY IT MATTERS THAT THE NUMBER IS MEASURED. `cost_projection.py` already exits 2
because full coverage does not fit the allowance, and the caps in force buy a
fraction of the demand. City terms cost nothing to ASK, but they change what
ARRIVES, and arrivals are what the gate spends money looking at. So the correct
form of "we added city terms" is a candidate-volume delta with the run it was
measured on, not an assurance that queries are free.

WHAT IT DOES NOT MEASURE, and do not quote it as if it did:

  * the prefilter and the two dedup layers run AFTER this and throw most of it
    away, so the delta here is an upper bound on new gate load, not the load;
  * a story already reached by a phrase query counts once for the run and twice
    here unless --dedup is passed, which is the default;
  * Google News moves, so two runs an hour apart differ by a few per cent. The
    baseline and the city half are fetched in the SAME run for that reason.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

import source_registry as registry
from collectors import google_news

RUNS_PER_DAY = 2
LOCALES_PER_RUN = 4


def editions(run_index: int, how_many: int) -> list[tuple[str, str]]:
    rotating = registry.rotate(
        list(registry.GOOGLE_NEWS_LOCALES),
        day_of_year=date.today().timetuple().tm_yday,
        run_index=run_index, runs_per_day=RUNS_PER_DAY,
        per_run=LOCALES_PER_RUN,
    )
    return ([registry.GOOGLE_NEWS_ANCHOR] + rotating)[:how_many]


def fetch_all(queries, lang, country, pause):
    """Every discovery URL one edition's queries return, with failures counted."""
    urls, failures = [], 0
    for query in queries:
        try:
            for item in google_news.fetch(query, lang=lang, country=country):
                urls.append(item["discovery_url"])
        except Exception as exc:                       # noqa: BLE001
            failures += 1
            print(f"    query failed ({exc.__class__.__name__}): {query[:60]}",
                  file=sys.stderr)
        time.sleep(pause)
    return urls, failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-index", type=int, default=0)
    ap.add_argument("--editions", type=int, default=5,
                    help="how many of the run's editions to sample (default 5, "
                         "which is the whole run)")
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--window", type=int, default=None)
    args = ap.parse_args(argv)

    window = args.window or registry.recency_window_days(
        LOCALES_PER_RUN, RUNS_PER_DAY)
    day = date.today().timetuple().tm_yday

    base_total = city_total = new_total = 0
    base_queries = city_queries = 0
    failures = 0

    print(f"day {day}, run index {args.run_index}, window {window}d, "
          f"{registry.CITY_QUERIES_PER_EDITION} city terms per edition\n")
    for lang, country in editions(args.run_index, args.editions):
        phrases = registry.google_news_queries(lang, window_days=window)
        cities = registry.google_news_city_queries(
            lang, country, day_of_year=day, run_index=args.run_index,
            runs_per_day=RUNS_PER_DAY, window_days=window)
        terms = registry.city_terms_for_edition(
            lang, country, day_of_year=day, run_index=args.run_index,
            runs_per_day=RUNS_PER_DAY)

        base_urls, f1 = fetch_all(phrases, lang, country, args.pause)
        city_urls, f2 = fetch_all(cities, lang, country, args.pause)
        failures += f1 + f2

        base, city = set(base_urls), set(city_urls)
        new = city - base
        base_total += len(base)
        city_total += len(city)
        new_total += len(new)
        base_queries += len(phrases)
        city_queries += len(cities)

        print(f"{country}:{lang}  cities={', '.join(terms) or 'none'}")
        print(f"    phrase queries {len(phrases):>2} -> {len(base):>4} items")
        print(f"    city queries   {len(cities):>2} -> {len(city):>4} items, "
              f"{len(new)} of them not already returned")

    print()
    print(f"queries per run     {base_queries:>5} -> "
          f"{base_queries + city_queries}  "
          f"(+{city_queries}, +{city_queries / max(base_queries, 1):.0%})")
    print(f"candidates per run  {base_total:>5} -> {base_total + new_total}  "
          f"(+{new_total}, +{new_total / max(base_total, 1):.0%})")
    if failures:
        print(f"\n{failures} queries could not be fetched. The delta above is "
              f"a floor, not the answer.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
