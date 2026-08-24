#!/usr/bin/env python3
"""Pull the Indeed Hiring Lab US job-postings macro index into a display seed.

    python3 build_indeed_index.py                 # write the shipped seed file
    python3 build_indeed_index.py --stdout        # look at it first
    python3 build_indeed_index.py --publish       # POST it to the live site

CONTEXT, NOT OUR DATA. Indeed Hiring Lab publishes the US job-postings index and
the share of postings mentioning AI as free, keyless CSVs on GitHub under CC BY
4.0. This is the hiring-demand *backdrop* to the tracker's own per-employer
signals -- a macro number a reader reads next to "we hold N updates", never a
number added into it. It never touches the database, the signals table, or any
of the tracker's own totals. It writes one JSON seed the plugin renders in a
clearly-labelled, separately-sourced panel.

Two sources, both CC BY 4.0, both keyless raw.githubusercontent.com CSVs:

* National index  -- github.com/hiring-lab/job_postings_tracker
  `US/aggregate_job_postings_US.csv`, the `total postings` / seasonally-adjusted
  series. Baseline is 100 = February 1, 2020. This is the headline "Indeed Job
  Postings Index" Indeed reports (e.g. ~101.8).
* AI in postings  -- github.com/hiring-lab/ai-tracker
  `AI_posting.csv`, the US `AI_share_postings` percentage: the share of US
  postings mentioning AI-related terms (Machine Learning, Data Science,
  Generative AI, ...), a seven-day trailing average.

WHAT WE MODIFY, STATED FOR THE LICENCE. The index value and the AI share are
shown EXACTLY as Indeed published them. What this script derives from those same
published series -- and the panel says so -- is two comparisons: the change
against the Feb-2020 baseline of 100, and the change against roughly a month
earlier. It also trims each series to the most recent window for a sparkline.
No value is re-indexed or recomputed.

FAIL LOUDLY. A fetch that fails, a body cut at the byte cap, a US row that is
missing, or a schema that changed all raise rather than write a stale or empty
panel. The seed carries each series' real "as of" date so staleness is visible
to a reader even between refreshes; the plugin prefers a pushed copy over the
shipped file (same contract as recall/board_series), so a scheduled `--publish`
keeps the live number current without a plugin deploy.

Dormant on purpose in the repo, like build_board_series.py: nothing here writes
a row or calls a model, and the committed seed is only the shipping-day value.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collectors import capped_fetch

REPO_ROOT = Path(__file__).resolve().parent
OUT_PATH = (REPO_ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
            / "data" / "indeed_index.json")

# The public, keyless CSVs. Pinned to each repo's default branch.
NATIONAL_CSV_URL = (
    "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/"
    "master/US/aggregate_job_postings_US.csv")
AI_CSV_URL = (
    "https://raw.githubusercontent.com/hiring-lab/ai-tracker/"
    "main/AI_posting.csv")

# Human-facing landing pages for the "counted by Indeed Hiring Lab, see source"
# links. A number on a page with no source is what this tracker exists not to be.
NATIONAL_SOURCE_PAGE = "https://github.com/hiring-lab/job_postings_tracker"
AI_SOURCE_PAGE = "https://github.com/hiring-lab/ai-tracker"
HIRING_LAB_HOME = "https://www.hiringlab.org/"

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# These grow daily and travel to the site as one option blob, so bound them.
# The AI CSV (every country, 2019->) is already ~0.8 MB, so give generous
# headroom and treat a body that hit the cap as a truncated feed (fail loudly).
MAX_CSV_BYTES = 12_000_000
SPARK_DAYS = 180          # how much history the sparkline draws
MONTH_AGO_DAYS = 30       # the "vs a month ago" comparison offset

BASELINE_NOTE = "February 1, 2020 = 100"

RULE = (
    "US job postings context from Indeed Hiring Lab, not the tracker's own "
    "records. The Indeed Job Postings Index measures the level of job postings "
    "on Indeed against a baseline of 100 set on February 1, 2020, seasonally "
    "adjusted. The AI figure is the share of US postings that mention "
    "AI-related terms, a seven-day trailing average. Both are shown as Indeed "
    "published them; the change against the 2020 baseline and against a month "
    "earlier are computed by us from the same series. These figures describe "
    "the whole US labour market and are never added into the tracker's own "
    "signal counts. Source: Indeed Hiring Lab, licensed CC BY 4.0."
)


class SchemaError(RuntimeError):
    """The CSV did not carry the columns or rows this build depends on."""


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _need_columns(rows: list[dict], columns: set[str], what: str) -> None:
    if not rows:
        raise SchemaError(f"{what}: no rows")
    have = set(rows[0].keys())
    missing = columns - have
    if missing:
        raise SchemaError(f"{what}: missing column(s) {sorted(missing)}; "
                          f"the source schema changed. Have {sorted(have)}.")


def _closest_before(series: list[list], target_ordinal: int) -> list | None:
    """The point on or nearest before `target_ordinal`; None if none qualifies."""
    best = None
    for point in series:
        ordinal = datetime.strptime(point[0], "%Y-%m-%d").toordinal()
        if ordinal <= target_ordinal:
            best = point
    return best


def parse_national(text: str, *, spark_days: int = SPARK_DAYS) -> dict:
    """The seasonally-adjusted 'total postings' US index, latest + comparisons."""
    rows = _rows(text)
    _need_columns(
        rows,
        {"date", "jobcountry", "indeed_job_postings_index_SA", "variable"},
        "national index CSV")

    series = []
    for row in rows:
        if row.get("jobcountry") != "US":
            continue
        if (row.get("variable") or "").strip() != "total postings":
            continue
        value = row.get("indeed_job_postings_index_SA")
        if value in (None, ""):
            continue
        try:
            series.append([row["date"], round(float(value), 2)])
        except (TypeError, ValueError):
            continue

    if not series:
        raise SchemaError("national index CSV: no US 'total postings' rows")
    series.sort(key=lambda p: p[0])

    latest = series[-1]
    latest_ordinal = datetime.strptime(latest[0], "%Y-%m-%d").toordinal()
    month_ago = _closest_before(series[:-1], latest_ordinal - MONTH_AGO_DAYS)

    cutoff = latest_ordinal - spark_days
    spark = [p for p in series
             if datetime.strptime(p[0], "%Y-%m-%d").toordinal() >= cutoff]

    block = {
        "as_of": latest[0],
        "index": latest[1],
        "seasonally_adjusted": True,
        "baseline": BASELINE_NOTE,
        "vs_baseline": round(latest[1] - 100.0, 2),
        "source_name": "Indeed Hiring Lab Job Postings Tracker",
        "source_url": NATIONAL_SOURCE_PAGE,
        "series": spark,
    }
    if month_ago is not None:
        block["month_ago"] = {
            "date": month_ago[0],
            "index": month_ago[1],
            "delta": round(latest[1] - month_ago[1], 2),
        }
    return block


def parse_ai(text: str, *, spark_days: int = SPARK_DAYS) -> dict:
    """The US share of postings mentioning AI, latest + month-ago comparison."""
    rows = _rows(text)
    _need_columns(rows, {"date", "jobcountry", "AI_share_postings"},
                  "AI postings CSV")

    series = []
    for row in rows:
        if row.get("jobcountry") != "US":
            continue
        value = row.get("AI_share_postings")
        if value in (None, ""):
            continue
        try:
            series.append([row["date"], round(float(value), 2)])
        except (TypeError, ValueError):
            continue

    if not series:
        raise SchemaError("AI postings CSV: no US rows")
    series.sort(key=lambda p: p[0])

    latest = series[-1]
    latest_ordinal = datetime.strptime(latest[0], "%Y-%m-%d").toordinal()
    month_ago = _closest_before(series[:-1], latest_ordinal - MONTH_AGO_DAYS)

    cutoff = latest_ordinal - spark_days
    spark = [p for p in series
             if datetime.strptime(p[0], "%Y-%m-%d").toordinal() >= cutoff]

    block = {
        "as_of": latest[0],
        "share_pct": latest[1],
        "trailing_average_days": 7,
        "source_name": "Indeed Hiring Lab AI Tracker",
        "source_url": AI_SOURCE_PAGE,
        "series": spark,
    }
    if month_ago is not None:
        block["month_ago"] = {
            "date": month_ago[0],
            "share_pct": month_ago[1],
            "delta": round(latest[1] - month_ago[1], 2),
        }
    return block


def build(national_text: str, ai_text: str, *,
          spark_days: int = SPARK_DAYS) -> dict:
    """The publishable payload. Pure: give it two CSV bodies, get the seed."""
    national = parse_national(national_text, spark_days=spark_days)
    ai = parse_ai(ai_text, spark_days=spark_days)

    # The headline "as of" is the national index's date; the AI series lags it
    # by a few weeks and carries its own date so a reader sees the difference.
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": national["as_of"],
        "source": "Indeed Hiring Lab",
        "source_url": HIRING_LAB_HOME,
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        # Stated for the licence: values are as-published, comparisons are ours.
        "values_as_published": True,
        "computed_by_us": [
            "change vs the Feb 2020 baseline of 100",
            "change vs roughly a month earlier",
        ],
        "rule": RULE,
        "national": national,
        "ai": ai,
    }


def _fetch_one(url: str) -> str:
    response, body = capped_fetch.capped_get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        max_bytes=MAX_CSV_BYTES,
    )
    if response.status_code >= 400:
        raise SchemaError(f"{url}: HTTP {response.status_code}")
    if not body:
        raise SchemaError(f"{url}: empty body")
    if len(body) >= MAX_CSV_BYTES:
        # Cut at the cap: the tail is missing, so the newest rows may be gone.
        # A truncated feed is a dead feed here, never a silently half-read one.
        raise SchemaError(f"{url}: body hit the {MAX_CSV_BYTES}-byte cap; "
                          "raise MAX_CSV_BYTES rather than publish a truncated series")
    return body.decode("utf-8", errors="replace")


def fetch() -> tuple[str, str]:
    """The two CSVs, keyless. Raises on any transport or HTTP failure."""
    return _fetch_one(NATIONAL_CSV_URL), _fetch_one(AI_CSV_URL)


def publish(payload: dict) -> str:
    """POST the seed to the keyed endpoint. Same contract as publish_health."""
    import requests

    from pipeline.publish import TIMEOUT, USER_AGENT as WP_UA, PublishError, _config

    site, key = _config()
    resp = requests.post(
        f"{site}/wp-json/talent/v1/indeed-index",
        json=payload,
        headers={"X-Talent-API-Key": key, "User-Agent": WP_UA},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise PublishError(f"{resp.status_code}: {resp.text[:300]}")
    return payload["as_of"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument("--stdout", action="store_true",
                        help="print the payload and write nothing")
    parser.add_argument("--publish", action="store_true",
                        help="POST to the site as well (needs WP_API_KEY)")
    parser.add_argument("--national-csv", default=None,
                        help="read the national CSV from a file instead of the network")
    parser.add_argument("--ai-csv", default=None,
                        help="read the AI CSV from a file instead of the network")
    args = parser.parse_args()

    if args.national_csv or args.ai_csv:
        if not (args.national_csv and args.ai_csv):
            print("Give both --national-csv and --ai-csv, or neither.",
                  file=sys.stderr)
            return 2
        national_text = Path(args.national_csv).read_text()
        ai_text = Path(args.ai_csv).read_text()
    else:
        national_text, ai_text = fetch()

    payload = build(national_text, ai_text)

    if args.stdout:
        json.dump(payload, sys.stdout, indent=1)
        print()
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        print(f"index {payload['national']['index']} as of "
              f"{payload['national']['as_of']}, AI share "
              f"{payload['ai']['share_pct']}% as of {payload['ai']['as_of']} "
              f"-> {out}")

    if args.publish:
        if not os.environ.get("WP_API_KEY"):
            print("WP_API_KEY is not set — nothing was published.", file=sys.stderr)
            return 1
        print(f"published as of {publish(payload)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
