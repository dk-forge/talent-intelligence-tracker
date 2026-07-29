#!/usr/bin/env python3
"""Give every cited document a permanent third-party copy, so evidence outlives
the publisher's own URL.

    python3 archive_sources.py --dry-run                 # availability only, no writes
    python3 archive_sources.py --dry-run --limit 40
    python3 archive_sources.py --limit 400               # check + capture + record
    python3 archive_sources.py --spn-max 0               # free pass only, never capture

WHY
---
A source link that dies turns a sourced claim into an unsourced one. Checking
for that (link_check.py) tells you it happened; it does not give the reader the
document back. A Wayback snapshot does, and it is neutral third-party evidence
rather than a self-hosted screenshot, which invites "did you doctor it?".

THE TWO-PASS DESIGN, AND WHY IT IS NOT NEGOTIABLE
-------------------------------------------------
Borrowed wholesale from the sibling tracker, which paid for this knowledge over
a week of running it:

  PASS 1  ask the availability API whether a snapshot already EXISTS. Free,
          fast, and it lands the bulk of the links, because much of what we cite
          has already been crawled by somebody.
  PASS 2  spend a bounded number of Save Page Now captures on the misses only.

Save Page Now is heavily rate-limited for anonymous callers. Its hit rate on a
first attempt is roughly 48%, a full backfill takes about a week, and RAISING
THE CONCURRENCY DOES NOT SPEED IT UP — it gets you throttled and the run
captures less. So the captures are spaced, a 429 backs off harder, the run stops
at a deadline, and whatever is left is simply 'pending' for tomorrow. A slow
coverage climb is the system working, not a bug.

WAYBACK IS A BACKUP, NEVER A DISCOVERY SOURCE. Nothing here finds URLs; it only
preserves ones we already hold and already cite. If you ever find yourself
reading Wayback to FIND something, you are writing a different program and the
"aggregators are discovery pointers, never stored sources" rule applies to it.

INGEST-TIME, WITHOUT TOUCHING THE INGEST
-----------------------------------------
Candidates come from `source_links.archive_candidates`, which is the GAP
(distinct source URLs with no snapshot) ordered newest-capture first. A URL
stored an hour ago is therefore at the head of the queue and a brand new row
appears in it automatically, so running this after a collect archives what that
collect just stored while everything older backfills from behind. That gets
"archive on ingest" with NO change to the write path, which is where this
project's expensive bugs live.

WHAT IT WRITES
--------------
`source_links` (the permalink, per URL), and then `signals.archive_url` via
`source_links.project_archive_urls()` so the site can render the fallback link.
That projection touches one provenance column and can reach no claim, figure,
date or source URL. Nothing here can delete or retract anything.

COST
----
Zero. No model is called, ever.

Exit codes: 0 the run completed (even if Wayback throttled everything)
            1 the run could not do its job at all
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent

from collectors.national_press import USER_AGENT  # noqa: E402
from pipeline import schema, source_links, store  # noqa: E402

AVAILABILITY = "https://archive.org/wayback/available"
SAVE = "https://web.archive.org/save/"

# Defaults tuned to what Wayback actually tolerates, not to what we would like.
# Cranking SPN_MAX or shrinking the gap gets the caller throttled and captures
# LESS; the sibling measured that. Leave them alone.
DEFAULT_LIMIT = 600          # candidates examined per run (pass 1 is cheap)
DEFAULT_SPN_MAX = 40         # Save Page Now captures per run
DEFAULT_SPN_GAP = 6.0        # seconds between captures
DEFAULT_DEADLINE = 1500      # stop cleanly between URLs, never mid-capture

RATE_LIMITED = "__rate_limited__"


# --- pure helpers (tested offline) -----------------------------------------

def parse_availability(payload) -> str | None:
    """A permanent permalink from an availability-API payload, or None.

    Only 2xx/3xx snapshots count. A stored 404 is a receipt that the page was
    already dead when the crawler arrived, not an archive of the evidence, and
    accepting one would give a row a fallback link to a screenshot of nothing.
    """
    if not isinstance(payload, dict):
        return None
    closest = (payload.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available"):
        return None
    status = str(closest.get("status") or "")
    if status and not (status.startswith("2") or status.startswith("3")):
        return None
    url = str(closest.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    return url.replace("http://web.archive.org", "https://web.archive.org", 1)


def parse_save_response(status_code: int, headers, final_url: str) -> str | None:
    """A permalink from a Save Page Now response, RATE_LIMITED, or None."""
    if status_code == 429:
        return RATE_LIMITED
    location = (headers or {}).get("Content-Location") or ""
    if location.startswith("/web/"):
        return "https://web.archive.org" + location
    if status_code in (200, 301, 302) and "/web/" in (final_url or ""):
        return final_url.replace("http://web.archive.org",
                                 "https://web.archive.org", 1)
    return None


# --- network (fail-open) ---------------------------------------------------

def check_availability(url: str, session) -> str | None:
    """Free existence check. Never raises: a failure just means 'not found yet'."""
    try:
        resp = session.get(AVAILABILITY, params={"url": url},
                           headers={"User-Agent": USER_AGENT}, timeout=30)
        if resp.status_code != 200:
            return None
        return parse_availability(resp.json())
    except Exception:
        return None


def save_page_now(url: str, session) -> str | None:
    """Trigger a capture. Never raises: a failure leaves the URL pending."""
    try:
        resp = session.get(SAVE + url, headers={"User-Agent": USER_AGENT},
                           timeout=90, allow_redirects=True)
        return parse_save_response(resp.status_code, resp.headers,
                                   getattr(resp, "url", "") or "")
    except Exception:
        return None


# --- the run ---------------------------------------------------------------

def run(conn, *, limit: int, collector: str | None, dry_run: bool,
        spn_max: int, spn_gap: float, deadline: float,
        session=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    import requests

    session = session or requests.Session()
    candidates = source_links.archive_candidates(conn, limit=limit,
                                                 collector=collector)
    if not candidates:
        print("Every cited document already has a snapshot or is recorded "
              "unavailable. Nothing to do.")
        return {"archived": 0, "pending": 0, "checked": 0, "saves": 0,
                "throttled": 0, "unavailable": 0}

    print(f"{len(candidates)} URL(s) without a snapshot; pass 1 asks Wayback "
          f"whether it already has them (free), pass 2 captures at most "
          f"{0 if dry_run else spn_max}.")

    started = clock()
    archived = pending = unavailable = saves = throttled = 0
    misses: list[dict] = []

    # PASS 1 — free availability checks over EVERY candidate first. Most of what
    # we cite has been crawled by somebody, so this lands the bulk of the links
    # fast and independently of ordering. A miss is queued for the slow pass
    # rather than blocking the quick wins behind a 90-second capture.
    for row in candidates:
        if clock() - started >= deadline:
            print(f"  deadline ({deadline:.0f}s) reached during the free pass")
            break
        url = row["source_url"]
        snapshot = check_availability(url, session)
        if snapshot:
            archived += 1
            print(f"  [have]  {url}\n          -> {snapshot}")
            if not dry_run:
                source_links.record_archive(
                    conn, url, state="archived", archive_url=snapshot,
                    attempts=row.get("archive_attempts", 0),
                    source_name=row.get("source_name") or "",
                    host=(urlparse(url).hostname or "").lower())
        else:
            misses.append(row)

    free_hits = archived
    print(f"\npass 1: {free_hits}/{free_hits + len(misses)} already in Wayback "
          f"at no cost.")

    # PASS 2 — the bounded, rate-limited captures. Over budget, past the
    # deadline, or in a dry run, the URL is recorded 'pending' and retried on a
    # later run; after MAX_ARCHIVE_ATTEMPTS rounds it becomes 'unavailable' so a
    # page Wayback genuinely cannot capture is reported rather than retried
    # forever.
    for row in misses:
        url = row["source_url"]
        attempts = row.get("archive_attempts", 0)
        out_of_budget = dry_run or saves >= spn_max or clock() - started >= deadline

        capture = None
        if not out_of_budget:
            capture = save_page_now(url, session)
            saves += 1
            if capture == RATE_LIMITED:
                throttled += 1
                capture = None
                # Back off harder when throttled. Pushing through a 429 is how
                # a run captures less than one that waited.
                sleep(spn_gap * 3)
            else:
                sleep(spn_gap)
            attempts += 1

        state, permalink = source_links.classify_archive_outcome(
            None, capture, attempts)
        if state == "archived":
            archived += 1
            print(f"  [saved] {url}\n          -> {permalink}")
        elif state == "unavailable":
            unavailable += 1
            print(f"  [gave up after {attempts} rounds] {url}")
        else:
            pending += 1

        if not dry_run:
            source_links.record_archive(
                conn, url, state=state, archive_url=permalink, attempts=attempts,
                source_name=row.get("source_name") or "",
                host=(urlparse(url).hostname or "").lower())

    return {"archived": archived, "pending": pending, "checked": len(candidates),
            "saves": saves, "throttled": throttled, "unavailable": unavailable,
            "free_hits": free_hits}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="run the free availability pass, capture nothing, "
                             "write nothing")
    parser.add_argument("--plan-only", action="store_true",
                        help="show the gap and make no request at all")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--collector", default=None)
    parser.add_argument("--spn-max", type=int, default=DEFAULT_SPN_MAX,
                        help="Save Page Now captures this run. Raising it does "
                             "NOT speed the backfill up; it gets us throttled.")
    parser.add_argument("--spn-gap", type=float, default=DEFAULT_SPN_GAP)
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    conn = schema.connect(args.db)
    try:
        if args.plan_only:
            gap = source_links.archive_candidates(conn, limit=args.limit,
                                                  collector=args.collector)
            summary = source_links.rot_summary(conn)
            print(f"PLAN: {len(gap)} URL(s) in this run's window would be "
                  f"examined; no request made.")
            print(f"  coverage now: {summary['archived']}/"
                  f"{summary['distinct_source_urls']} distinct source URLs "
                  f"({summary['archive_pct']}%), "
                  f"{summary['archive_pending']} pending, "
                  f"{summary['archive_unavailable']} unavailable")
            for row in gap[:10]:
                print(f"    {row['source_url']}")
            return 0

        result = run(conn, limit=args.limit, collector=args.collector,
                     dry_run=args.dry_run, spn_max=args.spn_max,
                     spn_gap=args.spn_gap, deadline=args.deadline)

        if args.dry_run:
            print(f"\nDRY RUN: {result['archived']} of {result['checked']} "
                  f"already in Wayback, {result['pending']} would need a "
                  f"capture. Nothing recorded, nothing captured.")
            return 0

        projected = source_links.project_archive_urls(conn)
        summary = source_links.rot_summary(conn)
        detail = (f"{result['archived']} archived this run "
                  f"({result['free_hits']} free, {result['saves']} captures, "
                  f"{result['throttled']} throttled), {result['pending']} pending, "
                  f"{result['unavailable']} unavailable; coverage "
                  f"{summary['archived']}/{summary['distinct_source_urls']} "
                  f"distinct source URLs ({summary['archive_pct']}%); "
                  f"{projected} signal row(s) given a fallback link")

        # Degraded ONLY when every capture attempted was throttled and nothing
        # was found free. That is Wayback rate-limiting us, which is expected
        # often enough that it must not be a red run, and visible enough that it
        # must not be silent either. Ordinary slow progress is 'ok': a week-long
        # backfill is the design, not a fault.
        throttled_out = (result["archived"] == 0 and result["saves"] > 0
                         and result["throttled"] == result["saves"])
        store.report_health(
            conn, "archive_sources",
            status="degraded" if throttled_out else "ok",
            items_found=result["checked"] or summary["archived"],
            items_stored=result["archived"], detail=detail)
        conn.commit()
        print("\n" + detail)
        if throttled_out:
            print("::warning::every capture this run was rate-limited by "
                  "Wayback. This is expected periodically; the queue is "
                  "resumable and the next run picks it up.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
