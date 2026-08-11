#!/usr/bin/env python3
"""Do we still hold the events nobody could defend missing?

    python3 check_landmarks.py                 # stored lens only, offline
    python3 check_landmarks.py --live          # ...and ask the public site too
    python3 check_landmarks.py --live --write  # ...and commit the dated report
    python3 check_landmarks.py --check         # validate the landmark set only

WHAT THIS IS FOR. On 2026-08-04 the owner measured, by hand, that the three
largest private funding rounds ever recorded were not on this site. Two had
never been stored; the third was stored, correct and complete, and had been
withheld from every reader for five days by a publish guardrail nobody had
answered. Every automated check we had was green throughout, because each one
asked a question that cannot notice a specific enormous event going missing:
"did the collector run", "does the ledger look fresh", "does the corpus hold a
representative sample of the world".

This asks the narrow question instead, and it is the narrowness that makes it
useful: here are twenty named events with primary documents, do we hold each
one, at the right amount, where a reader can see it.

EXIT CODES
    0  no regression. Standing gaps are reported and are NOT failures.
    2  a REGRESSION: something a previous report recorded as held is not held
       now. That is the only red, on purpose.
    3  the check could not be performed: the landmark set is invalid or the
       database is unreadable. "Could not check" must never read as a pass.

The live lens failing is NOT exit 3 and NOT a regression. The host has 504'd
for seven minutes at a time, and a guard that turns somebody else's outage into
a red run and an alert email is the failure mode this repository has already
paid for twice.

Costs nothing: no model is called, the stored lens is a local SQLite read, and
the live lens is about fourteen public GETs.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analysis.landmarks import check, landmarks  # noqa: E402
from analysis.recall.match import company_key  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "talent_intel.db")
REPORT = os.path.join(HERE, "data", "landmarks_report.json")

API = os.environ.get(
    "TIT_API_BASE", "https://asktherecruiter.com/blog/wp-json/talent/v1"
).rstrip("/")

# ModSecurity on the WP host rejects python-requests and the default urllib
# agent outright. Same string measure_recall.py uses, for the same reason.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def api_rows(company: str, attempts: int = 3) -> list:
    """Everything the PUBLIC site will show for this employer.

    Queried on the normalised company key, which is what the endpoint indexes,
    and retried on 5xx because this is shared hosting and a random 500 under
    load is not evidence that a round is missing.
    """
    params = {"company": company_key(company), "per_page": 200}
    url = "%s/query?%s" % (API, urllib.parse.urlencode(params))
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8")).get("rows", [])
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == attempts - 1:
                raise
        except Exception:
            if attempt == attempts - 1:
                raise
        time.sleep(2 * (attempt + 1))
    return []


def read_report(path: str = REPORT) -> dict | None:
    """The previous run, which is the only definition of "previously held"."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError:
        return None


def write_report(report: dict, path: str = REPORT) -> None:
    """Atomic, because a truncated report is an EMPTY history, and an empty
    history cannot produce a regression: the next run would silently forgive
    every landmark that had gone missing."""
    directory = os.path.dirname(path) or "."
    handle, tmp = tempfile.mkstemp(dir=directory, suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def build(data: dict, conn, live_by_company, previous, today: date,
          digest: str) -> dict:
    body = check.evaluate(
        landmarks.entries(data),
        check.stored_rows(conn),
        live_by_company,
        today=today,
        history=check.previous_history(previous),
        tolerance=float(data.get("amount_tolerance") or check.AMOUNT_TOLERANCE),
        window_days=int(data.get("window_days") or check.WINDOW_DAYS),
    )
    return {
        "checked_on": today.isoformat(),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "landmarks_version": data.get("version"),
        "landmarks_digest": digest,
        "summary": body["summary"],
        "by_quarter": body["by_quarter"],
        "entries": body["entries"],
        "history": body["history"],
    }


def print_report(report: dict, live_note: str | None) -> None:
    summary = report["summary"]
    print("=" * 64)
    print("LANDMARK CHECK  (set %s, digest %s)"
          % (report.get("landmarks_version"), report.get("landmarks_digest")))
    print("=" * 64)
    print("  " + summary["one_line"])
    if live_note:
        print("  live lens: %s" % live_note)

    print("\n  by quarter")
    for quarter, cell in report["by_quarter"].items():
        print("    %-8s %d of %d held%s" % (
            quarter, cell["held"], cell["total"],
            "" if cell["total"] == cell["held"]
            else "   (%s)" % ", ".join(
                "%d %s" % (cell[k], k) for k in
                ("missing", "wrong_amount", "held_not_live") if cell.get(k))))

    for item in report["entries"]:
        if item["regression"]:
            print("\n  REGRESSION  %-12s %s  $%s"
                  % (item["quarter"], item["company"],
                     _money(item["amount_usd"])))
            for line in item["regression"]:
                print("      %s" % line)
            print("      %s" % item["source_url"])

    gaps = [i for i in report["entries"]
            if i["status"] != "held" and not i["regression"]]
    if gaps:
        print("\n  STANDING GAPS  (never held; a work list, not a failure)")
        for item in gaps:
            print("    %-8s %-16s $%-8s %s"
                  % (item["quarter"], item["company"][:16],
                     _money(item["amount_usd"]), item["status"]))
            detail = item["live_detail"] if item["status"] == "held_not_live" \
                else item["stored_detail"]
            if detail:
                print("             %s" % detail[:96])
            print("             %s" % item["source_url"])


def _money(value) -> str:
    value = float(value or 0)
    if value >= 1e9:
        return "%.4gbn" % (value / 1e9)
    if value >= 1e6:
        return "%.4gm" % (value / 1e6)
    return "%.0f" % value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the committed landmark set against what we hold.")
    parser.add_argument("--live", action="store_true",
                        help="also ask the public /query endpoint what a "
                             "reader can actually see")
    parser.add_argument("--write", action="store_true",
                        help="write data/landmarks_report.json")
    parser.add_argument("--check", action="store_true",
                        help="validate the landmark set and stop")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        data = landmarks.load()
    except landmarks.InvalidLandmarkSet as exc:
        print("FATAL: %s" % exc, file=sys.stderr)
        return 3

    digest = landmarks.digest()
    if args.check:
        print("Landmark set %s is valid: %d entries, %d quarters, digest %s"
              % (data.get("version"), len(landmarks.entries(data)),
                 len({e["quarter"] for e in landmarks.entries(data)}), digest))
        return 0

    if not os.path.exists(DB):
        print("FATAL: no database at %s, so nothing could be checked." % DB,
              file=sys.stderr)
        return 3
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    except sqlite3.Error as exc:
        print("FATAL: could not open the database: %s" % exc, file=sys.stderr)
        return 3

    live_by_company, live_note = None, None
    if args.live:
        try:
            live_by_company = check.live_rows(api_rows, landmarks.entries(data))
            live_note = "read %d employer(s) from %s" % (len(live_by_company), API)
        except Exception as exc:
            # Deliberately not fatal and deliberately not a regression. The
            # host going down for seven minutes must not manufacture a red run
            # and an alert email about our own coverage.
            live_by_company = None
            live_note = "UNAVAILABLE (%s). Not a pass and not a failure: the " \
                        "reader-facing lens was simply not read." % str(exc)[:120]
            print("::warning::landmark live lens unavailable: %s" % str(exc)[:200])

    report = build(data, conn, live_by_company, read_report(), date.today(), digest)
    conn.close()

    if not args.quiet:
        print_report(report, live_note)

    if args.write:
        write_report(report)
        print("\nwrote %s" % os.path.relpath(REPORT, HERE))

    regressions = report["summary"]["regressions"]
    if regressions:
        print("\n::error::%d landmark regression(s): something this repository "
              "recorded as held is not held any more." % regressions)
        return 2

    gaps = report["summary"]["standing_gaps"]
    if gaps:
        print("\n%d standing gap(s). Reported, not red: a landmark that has "
              "never been held is a work list item, and a permanent red is a "
              "red nobody reads." % gaps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
