#!/usr/bin/env python3
"""Write the reader-facing archive re-check promise into the plugin.

    python3 build_archive_promise.py          # regenerate the shipped JSON
    python3 build_archive_promise.py --check  # verify only, write nothing

The dashboard, the company profiles and the place pages print, on every
publisher-sourced row that has no Wayback snapshot yet:

    No archive snapshot yet. We re-check weekly; next check by <date>.

That sentence is a commitment, and every value behind it is DERIVED here from
the files the schedule actually runs from — never typed into PHP:

  recheck_days    pipeline.source_links.RECHECK_PROMISE_DAYS, the single
                  definition. ops_status.py [2c] goes red when reality breaks it.
  cadence_hours   parsed from the archive slot's cron in
                  schedule-link-hygiene.yml. A promise read from a schedule that
                  changed is a promise nobody is keeping, so
                  tests/test_archive_promise.py re-derives this and fails when
                  the shipped copy drifts.
  collectors      scheduled_archive_scope(): the publisher tail the schedule
                  actually covers. Rows from any other collector (SEC, GOV.UK,
                  the registries) render NO pending note, because promising a
                  re-check the schedule will never make is worse than silence.

THE CAPACITY CHECK is the part that keeps "weekly" honest. Before writing, this
refuses to build if the schedule could not sweep the whole current in-scope
unarchived queue inside the promise window (runs-per-window x per-run limit
against the queue measured from the committed database). A cron edit or a
queue explosion that breaks the arithmetic is a red build here, not a quiet
false sentence on the live site.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / "data"
       / "archive_promise.json")

from pipeline import schema, source_links  # noqa: E402


def build() -> dict:
    promise = source_links.archive_promise(ROOT)
    if not promise["cadence_hours"]:
        raise SystemExit(
            "The archive slot could not be read from "
            "schedule-link-hygiene.yml, so there is no schedule to derive the "
            "promise from. If the slot was disarmed, remove the pending-state "
            "copy from the plugin rather than shipping a promise nothing keeps.")

    limit = source_links.scheduled_archive_limit(ROOT)
    runs_per_window = (promise["recheck_days"] * 24) // promise["cadence_hours"]
    capacity = runs_per_window * limit

    conn = schema.connect(ROOT / "data" / "talent_intel.db")
    try:
        cover = source_links.archive_coverage(conn, promise["collectors"])
    finally:
        conn.close()
    queue = cover["capture_queue"] + cover["never_probed"]
    if queue > capacity:
        raise SystemExit(
            f"The promise does not fit the schedule: {queue} in-scope URLs "
            f"await a snapshot but {runs_per_window} runs x {limit} "
            f"URLs = {capacity} can be examined in {promise['recheck_days']} "
            f"days. Fix the schedule or the backlog before shipping the "
            f"sentence; do NOT widen the promise to fit.")

    promise["capacity_per_window"] = capacity
    promise["queue_at_build"] = queue
    promise["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return promise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="verify the shipped file still matches the "
                             "derivation; write nothing")
    args = parser.parse_args(argv)

    fresh = build()
    if args.check:
        if not OUT.exists():
            print(f"MISSING: {OUT}")
            return 1
        shipped = json.loads(OUT.read_text())
        drift = {k: (shipped.get(k), fresh[k])
                 for k in ("recheck_days", "cadence_hours", "collectors")
                 if shipped.get(k) != fresh[k]}
        if drift:
            print("DRIFT between the shipped promise and the schedule:")
            for key, (old, new) in drift.items():
                print(f"  {key}: shipped {old!r}, derived {new!r}")
            print("Run: python3 build_archive_promise.py")
            return 1
        print("The shipped promise matches the schedule.")
        return 0

    OUT.write_text(json.dumps(fresh, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: re-check every "
          f"{fresh['recheck_days']} days, pass every {fresh['cadence_hours']}h "
          f"over {len(fresh['collectors'])} collector(s), queue "
          f"{fresh['queue_at_build']} against capacity "
          f"{fresh['capacity_per_window']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
