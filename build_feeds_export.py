#!/usr/bin/env python3
"""Write data/feeds.csv — the verified feed list as a portable artifact.

WHY IT IS SEPARATE FROM THE CATALOGUE

`data/sources_catalogue.csv` is this product's working file: sixteen columns of
signals, coverage, licensing notes and audit history that only mean anything
next to this repo's collectors. What another product needs from it is five
columns and no interpretation.

The intended consumer is the SIBLING AI Layoff Tracker, which is a different
repo with a different database. The same national outlets that report a funding
round report a redundancy programme, and that product's non-US discovery
currently leans entirely on GDELT. Handing it a flat, verified list of national
publishers costs us nothing and is worth a great deal there.

So this file is deliberately dumb: no signals vocabulary, no status history, no
opinion. Country, publisher, feed URL, kind, language. Anything that wants it
can read it with a stdlib CSV reader and no knowledge of this repo at all.

Regenerate after any catalogue change:

    python build_feeds_export.py

A test asserts the two stay in sync, the same way the sources manifest does.
"""

from __future__ import annotations

import csv
from pathlib import Path

CATALOGUE = Path(__file__).parent / "data" / "sources_catalogue.csv"
OUT = Path(__file__).parent / "data" / "feeds.csv"

FIELDS = ["country", "publisher", "feed_url", "feed_kind", "language",
          "coverage", "site_url"]


def rows() -> list[dict]:
    """Every catalogue row carrying a feed we actually fetched and parsed.

    `unreachable` is included with its kind stated rather than dropped: a
    consumer deciding whether to retry a feed we could not reach today is
    better served by the fact than by its absence.
    """
    out = []
    with CATALOGUE.open(newline="") as fh:
        for row in csv.DictReader(fh):
            feed = (row.get("rss") or "").strip()
            if not feed.startswith("http"):
                continue
            out.append({
                "country": (row.get("country") or "").strip(),
                "publisher": (row.get("name") or "").strip(),
                "feed_url": feed,
                "feed_kind": (row.get("feed_kind") or "").strip() or "rss",
                "language": (row.get("language") or "").strip(),
                "coverage": (row.get("coverage") or "").strip(),
                "site_url": (row.get("url") or "").strip(),
            })
    out.sort(key=lambda r: (r["country"].lower(), r["publisher"].lower()))
    return out


def main() -> int:
    data = rows()
    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(data)
    countries = len({r["country"] for r in data if r["country"]})
    print(f"{len(data)} feeds across {countries} countries -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
