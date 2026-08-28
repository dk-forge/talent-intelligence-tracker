#!/usr/bin/env python3
"""US executive-appointment wire discovery: the arm-first dry-run diagnostic.

    python us_exec_wire_probe.py            # live Google News, store nothing
    python us_exec_wire_probe.py --window 14

Shows WHICH US executive-appointment signals collectors/us_exec_wire.py WOULD
capture before the owner arms it: for each candidate, the headline, the resolved
outlet, and would-store (yes / no + why). It reads the live Google News index
and resolves redirects (both free of model spend), stores nothing, and calls no
model — the paid gate and read-through only ever run inside the ordinary
run_collect pipeline once the source is armed with TIT_US_EXEC_WIRE.

This is the coverage-gain preview: the count of would-store rows is the US
private-company leadership signal that sec_edgar (public employers only) cannot
see. Arming afterwards is a separate decision:

    TIT_US_EXEC_WIRE=on python run_collect.py --source us_exec_wire --dry-run
    TIT_US_EXEC_WIRE=on python run_collect.py --source us_exec_wire   # stores
"""

from __future__ import annotations

import argparse
from urllib.parse import urlparse

from collectors import google_news, us_exec_wire
from collectors.national_press import registrable_domain
from pipeline import prefilter


def _outlet(url: str) -> str:
    return registrable_domain(url) or (urlparse(url).hostname or "?")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, default=us_exec_wire.WINDOW_DAYS,
                        help="recency window in days (default "
                             f"{us_exec_wire.WINDOW_DAYS})")
    parser.add_argument("--budget", type=int, default=us_exec_wire.RESOLVE_BUDGET,
                        help="max redirect resolutions this run")
    args = parser.parse_args()

    print("us_exec_wire dry-run probe — live Google News, no store, no model spend\n")
    for query in us_exec_wire.registry.us_exec_wire_queries(window_days=args.window):
        print(f"  query: {query}")
    print()

    pointers = us_exec_wire.fetch(window_days=args.window)
    print(f"{len(pointers)} pointers from the index\n")

    passed, filtered = [], 0
    for item in pointers:
        if prefilter.passes(item.get("raw_text", ""))[0]:
            passed.append(item)
        else:
            filtered += 1
    passed = passed[:us_exec_wire.MAX_POINTERS]
    print(f"{filtered} dropped by the free prefilter, "
          f"{len(passed)} worth resolving\n")

    would_store = 0
    resolved_attempts = 0
    print(f"{'WOULD-STORE':11s}  {'OUTLET':26s}  HEADLINE")
    print(f"{'-'*11}  {'-'*26}  {'-'*40}")
    for item in passed:
        if resolved_attempts >= args.budget:
            print(f"{'(deferred)':11s}  {'':26s}  "
                  f"{item.get('headline','')[:60]}  (over resolve budget)")
            continue
        resolved_attempts += 1
        item["stated_publisher"] = item.get("source_url") or ""
        google_news.resolve_source_url(item)
        ok, why = us_exec_wire.storable(item)
        outlet = _outlet(item.get("source_url", "")) if ok else "-"
        verdict = "YES" if ok else "no"
        if ok:
            would_store += 1
        tail = "" if ok else f"  ({why})"
        print(f"{verdict:11s}  {outlet[:26]:26s}  "
              f"{item.get('headline','')[:60]}{tail}")

    print(f"\nWOULD STORE {would_store} US executive-appointment release(s) "
          f"this run — the added coverage over sec_edgar (public employers only).")
    print("Model spend this run: $0.00 (no classify call is made in the probe).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
