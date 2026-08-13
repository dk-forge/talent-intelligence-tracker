#!/usr/bin/env python3
"""Take back the 37 headquarters countries that had no city behind them.

    python3 reverse_cityless_hq.py            # DRY RUN, the default here
    python3 reverse_cityless_hq.py --apply    # writes

Queue it, never dispatch it (CLAUDE.md, "Never dispatch a database writer
directly"):

    gh workflow run drain-writers.yml -f enqueue=reverse-cityless-hq.yml \
      -f inputs_json='{"dry_run":"false"}' \
      -f reason='take back the cityless hq_country values'

WHAT WENT WRONG, said plainly, because it was this session's own doing
--------------------------------------------------------------------
The first live run of `place-unplaced.yml` used a bar that declined only
AMBIGUOUS names. It was cancelled a few minutes in, on purpose, because
checking it against the US recall set showed it resolving Premier Lacrosse
League to Canada. The cancellation was correct and it was too late: the job's
commit step had already run, so 37 rows carry an `hq_country` that came with no
headquarters city behind it, and `/enrich` then carried them to the live site.

`hq_country` is read from P17 of the entity's HEADQUARTERS and falls back to
P17 of the entity itself. The fallback is a much weaker fact, and this is the
list it produced. Some of it is right (Beretta IT, CyrusOne US). Some of it is
wrong on a public page right now:

    Synthesia            CZ   the Czech chemical works, not the UK AI company
                              that raised GBP 146m from GV; live, twice
    Ash Games            DE   a German namesake
    CFS                  CA   the same employer that appears three rows later
                              as Commonwealth Fusion Systems, US

`pipeline.identity.is_placeable` refuses this whole class since the bar moved,
so nothing new joins the list. This takes back what landed before it moved.

WHY THIS REFUSES TO RUN, AND WHAT HAS TO CHANGE FIRST
-----------------------------------------------------
`tit_clearable_columns()` in includes/api.php returns exactly
`funding_amount_usd` and `funding_stage`. `/enrich` cannot blank anything else:
an absent or empty field means "we still do not know", deliberately, so that a
gap can never erase a known value. There is no other door — `/correct` does not
carry `hq_country` and ignores empty values for the same reason.

So the site cannot accept this correction yet, and this script REFUSES to make
it locally while that is true. A corrected database in front of an uncorrected
page is a divergence nobody would notice, which is the rule
`correct_city_country.py` already states and the reason it is stated there.

What has to change, in order, and the deploy is the owner's call and not a
delegated one:

    1. tit_clearable_columns() must return 'hq_city' and 'hq_country' too.
    2. Bump Version: and TIT_VERSION, deploy the plugin, verify the page.
    3. Queue this with --apply.

The rows are listed in data/cityless_hq_to_reverse.json, by content_hash, with
the value each one carries. The list is a FILE and not a derived query on
purpose: it names exactly what one cancelled run wrote, and a derived worklist
would also sweep up cityless values that were there before and are nobody's
mistake.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROWS_PATH = Path(__file__).resolve().parent / "data" / "cityless_hq_to_reverse.json"

#: Where the site decides what may be blanked, named so the refusal below can
#: point at the exact function rather than at a general shrug.
SITE_ALLOWLIST = "tit_clearable_columns() in includes/api.php"

#: What that function has to return before this correction has a door.
REQUIRED_CLEARABLE = ("hq_city", "hq_country")

API_PATH = (Path(__file__).resolve().parent / "wordpress-plugin"
            / "talent-intelligence-tracker" / "includes" / "api.php")


def site_can_clear() -> bool:
    """Does the DEPLOYED allowlist admit these columns?

    Read out of the plugin source in this checkout rather than asked of the
    site, because the site would answer `skipped_no_fields` either way and that
    is indistinguishable from "no row matched".
    """
    try:
        body = API_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        after = body[body.index("function tit_clearable_columns"):]
    except ValueError:
        return False
    block = after[: after.index("}")]
    return all(f"'{col}'" in block for col in REQUIRED_CLEARABLE)


def targets(conn: sqlite3.Connection) -> list[dict]:
    """The listed rows that STILL carry the value, with what they carry now.

    Idempotent by construction: a row already reversed is not a target, so a
    second run is a no-op rather than a second correction.
    """
    listed = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    out = []
    for row in listed:
        found = conn.execute(
            "SELECT company, hq_city, hq_country FROM signals "
            "WHERE is_current = 1 AND content_hash = ?",
            (row["content_hash"],)).fetchone()
        if found is None:
            continue
        company, hq_city, hq_country = found
        if not hq_country or hq_city:
            continue          # already reversed, or a city arrived since
        out.append({"content_hash": row["content_hash"], "company": company,
                    "hq_country": hq_country})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="write; without it this reports and changes nothing")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    from pipeline import schema
    conn = schema.connect(args.db)
    work = targets(conn)

    print(f"listed rows          : {len(json.loads(ROWS_PATH.read_text()))}")
    print(f"still carrying it    : {len(work)}")
    for row in work:
        print(f"  {row['company'][:38]:<38} hq_country={row['hq_country']} -> NULL")

    if not work:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print("\nDRY RUN. Nothing was written. Pass --apply to write.")
        return 0

    if not site_can_clear():
        print(f"\nREFUSED. {SITE_ALLOWLIST} does not admit "
              f"{', '.join(REQUIRED_CLEARABLE)}, so /enrich cannot blank these "
              "on the live site and a corrected database would sit in front of "
              "an uncorrected page.\n"
              f"  1. {SITE_ALLOWLIST} must return "
              f"{' and '.join(repr(c) for c in REQUIRED_CLEARABLE)}.\n"
              "  2. Bump Version: and TIT_VERSION, deploy, verify the page.\n"
              "  3. Run this again.", file=sys.stderr)
        return 2

    # The site FIRST, then the local database — the same ordering
    # correct_city_country.py uses and for the same reason: a run killed
    # between the two retries both, rather than leaving the page wrong with
    # nothing left to find it.
    import requests
    base = (os.environ.get("WP_SITE_URL") or "").rstrip("/")
    key = os.environ.get("WP_API_KEY") or ""
    if not base or not key:
        print("WP_SITE_URL and WP_API_KEY are required to --apply", file=sys.stderr)
        return 2
    payload = {"rows": [{"content_hash": r["content_hash"],
                         "clear": list(REQUIRED_CLEARABLE)} for r in work]}
    resp = requests.post(f"{base}/wp-json/talent/v1/enrich", json=payload,
                         headers={"X-TIT-Key": key,
                                  "User-Agent": "TalentIntel/1.0 "
                                                "(+https://asktherecruiter.com)"},
                         timeout=120)
    resp.raise_for_status()
    print("site:", resp.json())

    for row in work:
        conn.execute("UPDATE signals SET hq_city = NULL, hq_country = NULL "
                     "WHERE is_current = 1 AND content_hash = ?",
                     (row["content_hash"],))
    conn.commit()
    print(f"local: {len(work)} row(s) reversed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
