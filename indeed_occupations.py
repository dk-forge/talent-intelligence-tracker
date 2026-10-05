#!/usr/bin/env python3
"""Indeed Hiring Lab postings by occupation category and country -> reference store.

    python3 indeed_occupations.py --stdout          # build, print a summary, store nothing
    python3 indeed_occupations.py --store           # write data/reference/indeed_occupations/
    python3 indeed_occupations.py --store --publish # and POST the compact copy to the site
    python3 indeed_occupations.py --dir FIXTURES    # read CSVs from a folder (offline)

The weekly extension of build_indeed_index.py. That script carries the single US
headline index; this one carries the depth behind it, from the same CC BY 4.0
repository (github.com/hiring-lab/job_postings_tracker):

* For all 11 countries Hiring Lab publishes (AU CA DE EA ES FR GB IE IT NL US):
  the seasonally-adjusted `total postings` index, `aggregate_job_postings_<CC>.csv`.
* For the countries that publish one (today AU CA DE FR GB US): the
  `total postings` index per occupational category,
  `job_postings_by_sector_<CC>.csv` (not seasonally adjusted; ~48 categories).
  The other five publish no category file -- recorded in the payload as
  `categories_unpublished`, never filled in or guessed.

Every value is as Indeed published it (100 = Feb 1 2020). What we derive, and
the attribution says so: the change against ~4 and ~52 weeks earlier, and a
weekly (every 7th day) thinning of the daily series to the last 26 weeks.

Never touches the database or the signals. Fails loudly: a missing aggregate
for any of the 11 countries, a changed schema, or fewer than
MIN_CATEGORY_COUNTRIES category files raises before anything is stored, so a bad
pull never overwrites a good one.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from build_indeed_index import USER_AGENT, SchemaError, _need_columns

import reference_store

SOURCE = "indeed_occupations"
COUNTRIES = ("AU", "CA", "DE", "EA", "ES", "FR", "GB", "IE", "IT", "NL", "US")
BASE = "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master"
AGGREGATE = "{c}/aggregate_job_postings_{c}.csv"
SECTOR = "{c}/job_postings_by_sector_{c}.csv"
SOURCE_PAGE = "https://github.com/hiring-lab/job_postings_tracker"
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "Job postings by occupation and country: Indeed Hiring Lab, Job Postings "
    "Tracker (github.com/hiring-lab/job_postings_tracker), licensed CC BY 4.0. "
    "Index values as published (100 = Feb 1, 2020); 4- and 52-week changes and "
    "the weekly thinning are computed by us.")

#: Six countries publish a category file today; losing more than one at once is
#: a source change to look at, not a quiet narrowing.
MIN_CATEGORY_COUNTRIES = 5
WEEKS_KEPT = 26
#: The category files are ~12 MB each (daily rows since 2020, ~48 categories)
#: and grow ~2 MB a year; build_indeed_index's 12 MB cap is sized for the
#: national file and truncated them on the first live run. A body AT the cap is
#: still refused as truncated.
MAX_CSV_BYTES = 60_000_000


def _ord(d: str) -> int:
    return date.fromisoformat(d).toordinal()


def _summarise(points: list[tuple[str, float]]) -> dict:
    """Latest, 4w/52w comparisons and a weekly thinning, from one daily series."""
    points.sort()
    latest_d, latest_v = points[-1]
    by_ord = {_ord(d): v for d, v in points}
    lo = _ord(latest_d)

    def at(days: int):
        # On or nearest before; the source is daily, so allow a 6-day gap.
        for back in range(days, days + 7):
            if lo - back in by_ord:
                return by_ord[lo - back]
        return None

    weekly = [[date.fromordinal(lo - 7 * k).isoformat(), by_ord[lo - 7 * k]]
              for k in range(WEEKS_KEPT - 1, -1, -1) if lo - 7 * k in by_ord]
    out = {"as_of": latest_d, "index": latest_v, "weekly": weekly}
    for key, days in (("chg_4w", 28), ("chg_52w", 364)):
        prior = at(days)
        if prior is not None:
            out[key] = round(latest_v - prior, 2)
    return out


def parse_aggregate(text: str, country: str) -> dict:
    rows = list(csv.DictReader(io.StringIO(text)))
    _need_columns(rows, {"date", "jobcountry", "indeed_job_postings_index_SA",
                         "variable"}, f"aggregate CSV {country}")
    pts = []
    for r in rows:
        if r["jobcountry"] != country or r["variable"].strip() != "total postings":
            continue
        try:
            pts.append((r["date"], round(float(r["indeed_job_postings_index_SA"]), 2)))
        except (TypeError, ValueError):
            continue
    if not pts:
        raise SchemaError(f"aggregate CSV {country}: no 'total postings' rows")
    return _summarise(pts)


def parse_sector(text: str, country: str) -> dict:
    rows = list(csv.DictReader(io.StringIO(text)))
    _need_columns(rows, {"date", "jobcountry", "indeed_job_postings_index",
                         "variable", "display_name"}, f"sector CSV {country}")
    series: dict[str, list] = {}
    for r in rows:
        if r["jobcountry"] != country or r["variable"].strip() != "total postings":
            continue
        name = (r["display_name"] or "").strip()
        try:
            value = round(float(r["indeed_job_postings_index"]), 2)
        except (TypeError, ValueError):
            continue
        if name:
            series.setdefault(name, []).append((r["date"], value))
    if not series:
        raise SchemaError(f"sector CSV {country}: no 'total postings' category rows")
    return {name: _summarise(pts) for name, pts in sorted(series.items())}


def build(aggregates: dict[str, str], sectors: dict[str, str | None], *,
          now: datetime | None = None) -> dict:
    """Pure. aggregates/sectors map country -> CSV text (sector None = unpublished)."""
    missing = [c for c in COUNTRIES if not aggregates.get(c)]
    if missing:
        raise SchemaError(f"aggregate index missing for {missing}; all 11 are required")
    published = [c for c in COUNTRIES if sectors.get(c)]
    if len(published) < MIN_CATEGORY_COUNTRIES:
        raise SchemaError(f"only {published} publish a category file; expected "
                          f">= {MIN_CATEGORY_COUNTRIES}. The source changed.")

    countries = {}
    rows = 0
    for c in COUNTRIES:
        block = {"total": parse_aggregate(aggregates[c], c), "categories": None}
        rows += 1
        if sectors.get(c):
            block["categories"] = parse_sector(sectors[c], c)
            rows += len(block["categories"])
        countries[c] = block

    as_of = max(b["total"]["as_of"] for b in countries.values())
    return {
        "source": SOURCE,
        "schema": 1,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "as_of": as_of,
        "rows": rows,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "source_url": SOURCE_PAGE,
        "baseline": "February 1, 2020 = 100",
        "seasonally_adjusted": {"total": True, "categories": False},
        "categories_unpublished": [c for c in COUNTRIES if c not in published],
        "countries": countries,
    }


def compact(payload: dict) -> dict:
    """The site copy: no weekly arrays, so one option stays small."""
    slim = {k: v for k, v in payload.items() if k != "countries"}
    slim["countries"] = {}
    for c, block in payload["countries"].items():
        strip = lambda s: {k: v for k, v in s.items() if k != "weekly"}  # noqa: E731
        slim["countries"][c] = {
            "total": strip(block["total"]),
            "categories": ({n: strip(s) for n, s in block["categories"].items()}
                           if block["categories"] else None),
        }
    return slim


def _get(url: str) -> tuple[int, str]:
    from collectors import capped_fetch
    response, body = capped_fetch.capped_get(
        url, headers={"User-Agent": USER_AGENT}, timeout=60, max_bytes=MAX_CSV_BYTES)
    if body and len(body) >= MAX_CSV_BYTES:
        raise SchemaError(f"{url}: body hit the {MAX_CSV_BYTES}-byte cap")
    return response.status_code, (body or b"").decode("utf-8", errors="replace")


def fetch(get=_get) -> tuple[dict, dict]:
    aggregates, sectors = {}, {}
    for c in COUNTRIES:
        status, text = get(f"{BASE}/{AGGREGATE.format(c=c)}")
        if status >= 400 or not text:
            raise SchemaError(f"aggregate {c}: HTTP {status}")
        aggregates[c] = text
        status, text = get(f"{BASE}/{SECTOR.format(c=c)}")
        if status == 404:
            sectors[c] = None          # this country publishes no category file
        elif status >= 400 or not text:
            raise SchemaError(f"sector {c}: HTTP {status}")
        else:
            sectors[c] = text
    return aggregates, sectors


def read_dir(folder: Path) -> tuple[dict, dict]:
    aggregates, sectors = {}, {}
    for c in COUNTRIES:
        a = folder / Path(AGGREGATE.format(c=c)).name
        s = folder / Path(SECTOR.format(c=c)).name
        aggregates[c] = a.read_text() if a.is_file() else None
        sectors[c] = s.read_text() if s.is_file() else None
    return aggregates, sectors


def store(payload: dict, store_dir: Path = reference_store.STORE) -> dict:
    body = (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode()
    return reference_store.write(
        SOURCE, {"latest.json": body}, rows=payload["rows"], as_of=payload["as_of"],
        licence=LICENCE, attribution=ATTRIBUTION, source_url=SOURCE_PAGE,
        store=store_dir,
        extra={"categories_unpublished": payload["categories_unpublished"]})


def main(argv=None) -> int:
    try:
        return _main(argv)
    except Exception as exc:  # one readable annotation; Actions logs are not always reachable
        print(f"::error::indeed_occupations failed: {type(exc).__name__}: {exc}", flush=True)
        raise


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", help="read CSVs from this folder instead of the network")
    p.add_argument("--stdout", action="store_true")
    p.add_argument("--store", action="store_true")
    p.add_argument("--publish", action="store_true")
    a = p.parse_args(argv)

    aggregates, sectors = read_dir(Path(a.dir)) if a.dir else fetch()
    payload = build(aggregates, sectors)
    cats = sum(len(b["categories"] or {}) for b in payload["countries"].values())
    summary = (f"indeed_occupations rows={payload['rows']} countries={len(COUNTRIES)} "
               f"category_series={cats} as_of={payload['as_of']} "
               f"no_category_file={','.join(payload['categories_unpublished']) or '-'}")
    if a.stdout:
        print(summary)
    if a.store:
        m = store(payload)
        summary += f" stored={m['files']['latest.json']['bytes']}B"
    if a.publish:
        summary += f" site={reference_store.publish(SOURCE, compact(payload))}"
    reference_store.notice(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
