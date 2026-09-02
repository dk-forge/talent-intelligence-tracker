#!/usr/bin/env python3
"""Where the rows with no country are, and WHY each one is blank.

    python3 measure_unplaced.py                 # the census, from the committed DB
    python3 measure_unplaced.py --db path/to/talent_intel.db

READ-ONLY, always: `schema.connect_ro`, and there is no write path in this file.
A placement is a separate, queued job; this only says what one would buy.

WHY A CENSUS AND NOT A NUMBER. A row with no `country` and no `hq_country` is
invisible to every country and region filter on the site (the clause is
`country IN (...) OR (country IS NULL AND hq_country IN (...))`). "2,611 rows
have no country" is true and says nothing about what to do, because the blanks
are blank for different reasons and only one of the reasons is actionable
without paying:

  (a) unknowable from what we hold: the classifier read a headline and a
      snippet that never said where, and the free identity spine (SEC +
      Wikidata) has never heard of the employer or knows no seat for it.
      Measured 2026-09-02: 1,944 of the 2,147 rows with neither field.
  (b) knowable from a signal already in hand: a clean, city-backed cache
      resolution exists for the employer and was never applied to the row.
      Three rows. `--apply-cache` places them, and since the placement bar
      was put on every writer (tests/test_placement_bar_on_every_writer.py)
      that command places ONLY this class.
  (declined) a cache resolution exists and does not clear the bar: a country
      with no headquarters city behind it, or a name two organisations share.
      200 rows. These stay blank on purpose; `identity.is_placeable` says why.
  (c) knowable only by reading the article body, which for this pipeline means
      a paid classification per row. Not run here, never run by this file.

THE MIRROR, which the same census has to print because it is the same defect
read the other way: rows that carry a country from the DECLINED class, written
before the bar reached `enrich()` and `apply_cache()`. A wrong country on a
filter is worse than a blank one, and these are the rows a human should look
at before anyone widens anything.

What this deliberately refuses to count as a signal, with the reason recorded
where the refusal was made: the Google News edition (`collectors/google_news`,
module docstring: the edition says where we asked, not where the story is),
the publisher's ccTLD (same fact, weaker), and a country or city name in the
headline (`measure_city_placement.py` reads those and it is the classifier's
job; on the 2026-09-02 corpus a headline scan put HPE Aruba in Aruba, Siigo in
the United States off "Latin America" and GoGig in Bulgaria off "Sofia").
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from pipeline import identity, schema

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "talent_intel.db"

BLANK = "({col} IS NULL OR {col} = '')"
NO_PLACE = BLANK.format(col="s.country") + " AND " + BLANK.format(col="s.hq_country")
AMBIGUOUS = f"e.detail LIKE '%{identity.AMBIGUOUS_MARKER}%'"


def _one(conn: sqlite3.Connection, sql: str, *params) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def census(conn: sqlite3.Connection) -> dict:
    """Every number the report prints, as a dict, so a test can pin them."""
    out = {}
    out["current"] = _one(conn, "SELECT count(*) FROM signals s WHERE s.is_current = 1")
    out["blank_country"] = _one(
        conn, f"SELECT count(*) FROM signals s WHERE s.is_current = 1 AND {BLANK.format(col='s.country')}")
    out["hq_only"] = _one(
        conn, f"SELECT count(*) FROM signals s WHERE s.is_current = 1 AND {BLANK.format(col='s.country')}"
        f" AND NOT {BLANK.format(col='s.hq_country')}")
    out["no_place"] = _one(
        conn, f"SELECT count(*) FROM signals s WHERE s.is_current = 1 AND {NO_PLACE}")
    out["by_collector"] = conn.execute(
        f"SELECT s.collector, count(*) FROM signals s WHERE s.is_current = 1 AND {NO_PLACE}"
        " GROUP BY s.collector ORDER BY 2 DESC").fetchall()

    join = (f"FROM signals s LEFT JOIN {schema.CACHE_SCHEMA}.employer_identity e"
            " ON e.company_key = s.company_key WHERE s.is_current = 1 AND " + NO_PLACE)
    has_hq = "e.resolved = 1 AND NOT " + BLANK.format(col="e.hq_country")
    out["cause"] = {
        "a_no_cache_row": _one(conn, f"SELECT count(*) {join} AND e.company_key IS NULL"),
        "a_unresolved": _one(conn, f"SELECT count(*) {join} AND e.company_key IS NOT NULL AND e.resolved = 0"),
        "a_resolved_no_seat": _one(
            conn, f"SELECT count(*) {join} AND e.resolved = 1 AND {BLANK.format(col='e.hq_country')}"),
        "declined_cityless": _one(
            conn, f"SELECT count(*) {join} AND {has_hq} AND {BLANK.format(col='e.hq_city')} AND NOT {AMBIGUOUS}"),
        "declined_ambiguous": _one(conn, f"SELECT count(*) {join} AND {has_hq} AND {AMBIGUOUS}"),
        "b_placeable_now": _one(
            conn, f"SELECT count(*) {join} AND {has_hq} AND NOT {BLANK.format(col='e.hq_city')} AND NOT {AMBIGUOUS}"),
    }
    # The mirror: a place on the row that the bar would have refused.
    mirror = (f"FROM signals s JOIN {schema.CACHE_SCHEMA}.employer_identity e"
              " ON e.company_key = s.company_key WHERE s.is_current = 1"
              f" AND {BLANK.format(col='s.country')} AND NOT {BLANK.format(col='s.hq_country')}"
              " AND e.resolved = 1 AND e.hq_country = s.hq_country")
    out["mirror"] = {
        "cityless": _one(
            conn, f"SELECT count(*) {mirror} AND {BLANK.format(col='s.hq_city')}"
            f" AND {BLANK.format(col='e.hq_city')} AND NOT {AMBIGUOUS}"),
        "ambiguous": _one(conn, f"SELECT count(*) {mirror} AND {AMBIGUOUS}"),
    }
    return out


def report(c: dict) -> str:
    pct = lambda n, d: f"{100 * n / d:.1f}%" if d else "n/a"
    lines = [
        f"current rows                         {c['current']:>7}",
        f"  no job-location country            {c['blank_country']:>7}  ({pct(c['blank_country'], c['current'])})",
        f"    findable by employer HQ           {c['hq_only']:>7}  (country_basis=any reaches these)",
        f"    NO place in either column         {c['no_place']:>7}  (invisible to every country and region filter)",
        "",
        "  no place, by collector",
    ]
    for collector, n in c["by_collector"]:
        lines.append(f"    {collector:<22} {n:>7}")
    k = c["cause"]
    lines += [
        "",
        "  no place, by cause",
        f"    (a) unknowable from what we hold",
        f"          employer never looked up      {k['a_no_cache_row']:>7}",
        f"          Wikidata does not know it     {k['a_unresolved']:>7}",
        f"          known, no seat recorded       {k['a_resolved_no_seat']:>7}",
        f"    declined by the placement bar, on purpose",
        f"          country with no HQ city       {k['declined_cityless']:>7}",
        f"          name two organisations share  {k['declined_ambiguous']:>7}",
        f"    (b) placeable now from the cache    {k['b_placeable_now']:>7}"
        "  -> queue place-unplaced / --apply-cache",
        f"    (c) needs the article body read     {c['no_place'] - k['b_placeable_now']:>7}"
        "  (paid classification per row; not run here)",
        "",
        "  mirror: rows whose ONLY place is a country the bar refuses",
        f"          from a cityless cache row     {c['mirror']['cityless']:>7}",
        f"          from an ambiguous cache row   {c['mirror']['ambiguous']:>7}",
        "          (review before any widening; reverse_cityless_hq.py is the shape of the fix)",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 2
    conn = schema.connect_ro(args.db)
    try:
        print(report(census(conn)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
