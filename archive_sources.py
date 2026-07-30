#!/usr/bin/env python3
"""Give every cited document a permanent third-party copy, so evidence outlives
the publisher's own URL.

    python3 archive_sources.py --dry-run                 # availability only, no writes
    python3 archive_sources.py --dry-run --limit 40
    python3 archive_sources.py --limit 400               # check + capture + record
    python3 archive_sources.py --spn-max 0               # free pass only, never capture
    python3 archive_sources.py --plan-only               # the gap and how it splits
    python3 archive_sources.py --recheck-terminal --dry-run   # count wrongly-retired URLs
    python3 archive_sources.py --recheck-terminal        # and put them back

A NON-ANSWER IS NOT AN ANSWER, AND IT COST US TWICE
---------------------------------------------------
This job asks two questions and neither has a two-valued answer. Both times the
code treated a refusal as a finding, the run stayed green and the damage was
invisible:

  * availability API 429 read as "no snapshot exists" — the run invented a gap
    and spent its capture budget on documents Wayback already held. Fixed in
    `check_availability`.
  * Save Page Now 429 still SPENT one of the five `archive_attempts` — so five
    throttled nights, an ordinary fortnight for an anonymous caller, retired a
    capturable document to the TERMINAL 'unavailable' state having never once
    been told it was uncapturable. Fixed in pass 2.

Two structural consequences, so a third guise cannot land:

  1. `unavailable` may only be reached from EVIDENCE — at least one round in
     which archive.org answered and said it holds nothing
     (`source_links.MIN_PROBES_BEFORE_TERMINAL`). A blind round is counted
     separately and spends nothing.
  2. The gap is REPORTED split, not as one percentage:
     `never answered about` versus `confirmed absent from Wayback`. A percentage
     that climbs slowly because Save Page Now is rate-limited (the design) and
     one that climbs slowly because nothing can get an answer (a fault) are
     indistinguishable until those two numbers are printed apart.
     `source_links.archive_gap()`, `ops_status.py [2c]`.

`--recheck-terminal` is the one-time repair for rows already retired that way.
It is idempotent and stays in the tool: both bugs above shipped green, and a
third route into the terminal state would need exactly this again.

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

# Pass 1 is "free" in money and not in politeness. Measured 2026-07-30: the
# availability API answered 429 to the FIRST request from a residential IP and
# was still answering 429 twenty seconds later, so being cheap is not the same
# as being welcome. One call every half second caps the free pass at 2/s; with
# the observed 0.2-1.0s of latency per call the real rate is lower still, and a
# 600-URL pass costs about twelve minutes of the deadline rather than all of it.
DEFAULT_AVAIL_GAP = 0.5

# Pacing, and the reason it is a ladder rather than one number.
#
# A fixed 0.5s gap is polite when archive.org is answering and useless when it
# is not: a blinded run walks all 600 candidates at 2/s, learns nothing about any
# of them, and spends five minutes of its deadline proving that archive.org is
# still refusing. Wayback's own rate limiting is a cooldown, so the only thing
# that helps is waiting longer, and the only thing that helps more is stopping.
#
# So consecutive non-answers back the gap off geometrically to a ceiling, one
# answer resets it, and a long enough unbroken run of them ends the free pass.
# The run then reports `blinded` and every URL is still in the gap with no
# attempt spent — which is the whole point: a bad afternoon costs a cycle, never
# a record.
AVAIL_BACKOFF_FACTOR = 2.0
AVAIL_BACKOFF_MAX = 30.0
#: Consecutive unanswered availability calls after which the free pass gives up.
#: 12 at the backoff above is about two and a half minutes of waiting, which is
#: long enough to ride out a burst limit and short enough that a genuine block
#: does not eat the deadline.
AVAIL_BLIND_STREAK_MAX = 12

RATE_LIMITED = "__rate_limited__"

#: Codes that mean "archive.org would not answer", never "there is no snapshot".
#: Reading a 429 as a miss is how a throttled run invents a gap it then spends
#: its capture budget on. See check_availability.
AVAILABILITY_UNKNOWN_CODES = frozenset({429, 500, 502, 503, 504})


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
    """Free existence check: a permalink, RATE_LIMITED, or None.

    Never raises. But it distinguishes THREE outcomes rather than two, and that
    distinction is the whole reason this function has a docstring this long.

    This used to return None for anything that was not a 200, so a 429 — which
    archive.org hands out freely and which it handed to the first request of a
    measurement on 2026-07-30 — was recorded as "Wayback has no snapshot of
    this". Every consequence of that is wrong in the same direction:

      * pass 1 reports a gap that does not exist, so the run looks like it has
        more work than it has;
      * the phantom misses go to pass 2, which spends a bounded capture budget
        re-archiving documents Wayback already holds;
      * each attempt increments `archive_attempts`, and at
        MAX_ARCHIVE_ATTEMPTS the URL is recorded `unavailable` — which
        `archive_candidates` treats as TERMINAL. A capturable document would be
        dropped from the queue forever on the strength of archive.org having a
        bad afternoon, and only a hand-written UPDATE could put it back.
      * and none of it is visible: `throttled_out` only fires when Save Page
        Now was throttled too, so a run blinded in pass 1 reports `ok`.

    RATE_LIMITED means "we did not learn anything", which is not a miss and is
    not a hit. The caller neither captures nor records on the back of it.
    """
    try:
        resp = session.get(AVAILABILITY, params={"url": url},
                           headers={"User-Agent": USER_AGENT}, timeout=30)
        if resp.status_code in AVAILABILITY_UNKNOWN_CODES:
            return RATE_LIMITED
        if resp.status_code != 200:
            return None
        return parse_availability(resp.json())
    except Exception:
        # A timeout or a dropped connection is also "we do not know". Calling it
        # a miss would burn the capture budget on the strength of our own
        # network, which is the same error as above with a different cause.
        return RATE_LIMITED


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
        avail_gap: float = DEFAULT_AVAIL_GAP,
        session=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    import requests

    session = session or requests.Session()
    candidates = source_links.archive_candidates(conn, limit=limit,
                                                 collector=collector)
    if not candidates:
        print("Every cited document already has a snapshot or is recorded "
              "unavailable. Nothing to do.")
        return {"archived": 0, "pending": 0, "checked": 0, "saves": 0,
                "throttled": 0, "unavailable": 0, "unknown": 0, "free_hits": 0}

    print(f"{len(candidates)} URL(s) without a snapshot; pass 1 asks Wayback "
          f"whether it already has them (free), pass 2 captures at most "
          f"{0 if dry_run else spn_max}.")

    started = clock()
    archived = pending = unavailable = saves = throttled = unknown = 0
    misses: list[dict] = []
    blind_streak = 0
    gap = avail_gap
    gave_up_blind = 0

    # PASS 1 — free availability checks over EVERY candidate first. Most of what
    # we cite has been crawled by somebody, so this lands the bulk of the links
    # fast and independently of ordering. A miss is queued for the slow pass
    # rather than blocking the quick wins behind a 90-second capture.
    for i, row in enumerate(candidates):
        if clock() - started >= deadline:
            print(f"  deadline ({deadline:.0f}s) reached during the free pass")
            break
        url = row["source_url"]
        if i:
            sleep(gap)
        snapshot = check_availability(url, session)
        if snapshot is RATE_LIMITED:
            # We learned nothing about this URL. It is NOT a miss, so it does
            # not go to pass 2, does not spend a capture and does not touch
            # archive_attempts — which is what stops a throttled night walking
            # a capturable document to the terminal 'unavailable' state. It is
            # simply still in the gap, and tomorrow's run asks again.
            unknown += 1
            blind_streak += 1
            if not dry_run:
                # Recorded, because "we have been unable to get an answer about
                # this URL for six nights" is the only way a persistent block
                # is distinguishable from a slow backfill. State stays pending;
                # attempts are untouched.
                source_links.record_archive(
                    conn, url, state="pending",
                    attempts=row.get("archive_attempts", 0),
                    probes=row.get("archive_probes", 0),
                    blind_rounds=row.get("archive_blind_rounds", 0) + 1,
                    detail="availability API did not answer",
                    source_name=row.get("source_name") or "",
                    host=(urlparse(url).hostname or "").lower())
            gap = min(gap * AVAIL_BACKOFF_FACTOR, AVAIL_BACKOFF_MAX)
            if blind_streak >= AVAIL_BLIND_STREAK_MAX:
                gave_up_blind = len(candidates) - i - 1
                print(f"  {blind_streak} consecutive unanswered availability "
                      f"calls: ending the free pass with {gave_up_blind} URL(s) "
                      f"unexamined rather than waiting on a closed door.")
                break
            continue
        blind_streak = 0
        gap = avail_gap
        if snapshot:
            archived += 1
            print(f"  [have]  {url}\n          -> {snapshot}")
            if not dry_run:
                source_links.record_archive(
                    conn, url, state="archived", archive_url=snapshot,
                    attempts=row.get("archive_attempts", 0),
                    probes=row.get("archive_probes", 0) + 1,
                    blind_rounds=row.get("archive_blind_rounds", 0),
                    detail="availability API returned a snapshot",
                    source_name=row.get("source_name") or "",
                    host=(urlparse(url).hostname or "").lower())
        else:
            # A DEFINITIVE negative: archive.org answered and holds nothing. The
            # one thing that may ever put this URL on the road to terminal.
            row["archive_probes"] = row.get("archive_probes", 0) + 1
            misses.append(row)

    free_hits = archived
    print(f"\npass 1: {free_hits}/{free_hits + len(misses)} already in Wayback "
          f"at no cost."
          + (f" {unknown} URL(s) went UNANSWERED (throttled or unreachable) and "
             f"were left in the gap rather than guessed at." if unknown else ""))

    # PASS 2 — the bounded, rate-limited captures. Over budget, past the
    # deadline, or in a dry run, the URL is recorded 'pending' and retried on a
    # later run; after MAX_ARCHIVE_ATTEMPTS rounds it becomes 'unavailable' so a
    # page Wayback genuinely cannot capture is reported rather than retried
    # forever.
    for row in misses:
        url = row["source_url"]
        attempts = row.get("archive_attempts", 0)
        blind = row.get("archive_blind_rounds", 0)
        detail = ""
        out_of_budget = dry_run or saves >= spn_max or clock() - started >= deadline

        capture = None
        if not out_of_budget:
            capture = save_page_now(url, session)
            saves += 1
            if capture == RATE_LIMITED:
                throttled += 1
                capture = None
                # A REFUSED capture is not a failed capture, and this is the
                # second guise of the 429 bug. `attempts` is the counter that
                # walks a URL to the terminal 'unavailable' state after five
                # rounds, and it used to be incremented here — so five throttled
                # nights retired a document archive.org had never once declined
                # to capture, silently, out of five green runs. Save Page Now
                # throttles anonymous callers constantly; five such nights is an
                # ordinary fortnight, not an outlier. It counts as a blind round
                # and spends nothing.
                blind += 1
                detail = "Save Page Now refused (429), no attempt spent"
                # Back off harder when throttled. Pushing through a 429 is how
                # a run captures less than one that waited.
                sleep(spn_gap * 3)
            else:
                sleep(spn_gap)
                attempts += 1
                detail = ("capture succeeded" if capture
                          else "capture attempted, no snapshot returned")

        state, permalink = source_links.classify_archive_outcome(
            None, capture, attempts, probes=row.get("archive_probes", 0))
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
                probes=row.get("archive_probes", 0), blind_rounds=blind,
                detail=detail,
                source_name=row.get("source_name") or "",
                host=(urlparse(url).hostname or "").lower())

    return {"archived": archived, "pending": pending, "checked": len(candidates),
            "saves": saves, "throttled": throttled, "unavailable": unavailable,
            "unknown": unknown, "free_hits": free_hits,
            "unexamined": gave_up_blind}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="run the free availability pass, capture nothing, "
                             "write nothing")
    parser.add_argument("--plan-only", action="store_true",
                        help="show the gap and make no request at all")
    parser.add_argument("--recheck-terminal", action="store_true",
                        help="put back every URL recorded 'unavailable' without "
                             "a definitive negative from archive.org, then stop. "
                             "Makes no request. Combine with --dry-run to count "
                             "them without writing.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--collector", default=None)
    parser.add_argument("--spn-max", type=int, default=DEFAULT_SPN_MAX,
                        help="Save Page Now captures this run. Raising it does "
                             "NOT speed the backfill up; it gets us throttled.")
    parser.add_argument("--spn-gap", type=float, default=DEFAULT_SPN_GAP)
    parser.add_argument("--avail-gap", type=float, default=DEFAULT_AVAIL_GAP,
                        help="seconds between free availability calls. The free "
                             "pass costs no money and is not therefore welcome "
                             "at any rate we like.")
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    conn = schema.connect(args.db)
    try:
        if args.recheck_terminal:
            moved = source_links.reset_blinded_terminal(conn,
                                                        dry_run=args.dry_run)
            if not moved:
                print("0 URL(s) reached 'unavailable' without a definitive "
                      "negative from archive.org. Nothing to put back.")
                print("That is the state this should stay in: "
                      "classify_archive_outcome now refuses to record terminal "
                      "without at least one real probe, so a throttle can no "
                      "longer retire a document.")
                return 0
            for url in moved[:20]:
                print(f"  reset  {url}")
            if len(moved) > 20:
                print(f"  ... and {len(moved) - 20} more")
            if args.dry_run:
                print(f"\nDRY RUN: {len(moved)} URL(s) WOULD go back to "
                      f"'pending'. Nothing written.")
                return 0
            conn.commit()
            print(f"\n{len(moved)} URL(s) put back to 'pending' with their "
                  f"attempt count cleared. They re-enter the candidate list on "
                  f"the next run; no snapshot we already hold was touched.")
            return 0

        if args.plan_only:
            gap = source_links.archive_candidates(conn, limit=args.limit,
                                                  collector=args.collector)
            summary = source_links.rot_summary(conn)
            split = source_links.archive_gap(conn)
            print(f"PLAN: {len(gap)} URL(s) in this run's window would be "
                  f"examined; no request made.")
            print(f"  coverage now: {summary['archived']}/"
                  f"{summary['distinct_source_urls']} distinct source URLs "
                  f"({summary['archive_pct']}%), "
                  f"{summary['archive_pending']} pending, "
                  f"{summary['archive_unavailable']} unavailable")
            print(f"  the gap splits: {split['never_probed']} never answered "
                  f"about, {split['probed_absent']} confirmed absent from "
                  f"Wayback (the real capture queue), "
                  f"{split['blind_recently']} with a blind round on record")
            for row in gap[:10]:
                print(f"    {row['source_url']}")
            return 0

        result = run(conn, limit=args.limit, collector=args.collector,
                     dry_run=args.dry_run, spn_max=args.spn_max,
                     spn_gap=args.spn_gap, avail_gap=args.avail_gap,
                     deadline=args.deadline)

        if args.dry_run:
            print(f"\nDRY RUN: {result['archived']} of {result['checked']} "
                  f"already in Wayback, {result['pending']} would need a "
                  f"capture, {result.get('unknown', 0)} went unanswered. "
                  f"Nothing recorded, nothing captured.")
            return 0

        projected = source_links.project_archive_urls(conn)
        summary = source_links.rot_summary(conn)
        split = source_links.archive_gap(conn)
        unknown = result.get("unknown", 0)
        detail = (f"{result['archived']} archived this run "
                  f"({result['free_hits']} free, {result['saves']} captures, "
                  f"{result['throttled']} throttled), {result['pending']} pending, "
                  f"{result['unavailable']} unavailable, {unknown} unanswered; "
                  f"coverage "
                  f"{summary['archived']}/{summary['distinct_source_urls']} "
                  f"distinct source URLs ({summary['archive_pct']}%); "
                  f"gap = {split['never_probed']} never answered about + "
                  f"{split['probed_absent']} confirmed absent; "
                  f"{projected} signal row(s) given a fallback link")

        # Degraded when Wayback would not talk to us, in either pass.
        #
        # throttled_out: every capture attempted was refused and nothing was
        # found free. Expected often enough that it must not be a red run, and
        # consequential enough that it must not be silent.
        #
        # blinded: the free pass could not get an answer for a large share of
        # the candidates. This one is new, and it is the more dangerous of the
        # two, because before check_availability distinguished "unanswered" from
        # "absent" a fully throttled free pass reported `ok` with a healthy
        # capture count next to it. Ordinary slow progress is still 'ok': a
        # week-long backfill is the design, not a fault.
        throttled_out = (result["archived"] == 0 and result["saves"] > 0
                         and result["throttled"] == result["saves"])
        answered = result["free_hits"] + result["pending"] + unknown
        blinded = bool(unknown) and unknown * 2 >= (answered or 1)
        if result.get("unexamined"):
            print(f"::warning::the free pass stopped early on an unbroken run "
                  f"of {AVAIL_BLIND_STREAK_MAX} unanswered availability calls, "
                  f"leaving {result['unexamined']} URL(s) unexamined. None of "
                  f"them spent an attempt and none changed state, so the only "
                  f"cost is a cycle. If it repeats, archive.org is refusing "
                  f"this IP range rather than having an afternoon.")
        store.report_health(
            conn, "archive_sources",
            status="degraded" if (throttled_out or blinded) else "ok",
            items_found=result["checked"] or summary["archived"],
            items_stored=result["archived"], detail=detail)
        conn.commit()
        print("\n" + detail)
        if throttled_out:
            print("::warning::every capture this run was rate-limited by "
                  "Wayback. This is expected periodically; the queue is "
                  "resumable and the next run picks it up.")
        if blinded:
            print(f"::warning::the availability API left {unknown} of "
                  f"{answered} URL(s) unanswered this run, so most of the free "
                  f"pass learned nothing. Nothing was captured or recorded on "
                  f"the strength of a non-answer, and none of those URLs spent "
                  f"an attempt — they are still in the gap. If this repeats for "
                  f"several runs, archive.org is refusing this IP range rather "
                  f"than having an afternoon.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
