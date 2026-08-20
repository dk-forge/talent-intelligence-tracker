#!/usr/bin/env python3
"""Weekly CI-noise report: one email naming the causes, or no email at all.

WHY THIS EXISTS. 2026-07-26..08-02 this repo produced ~190 non-green runs, of
which a handful were new facts and the rest were repeats of already-known
facts: 180 red drain-writers ticks re-reporting the same few needs-human
items, plus displaced scheduled runs. Every red scheduled run is a GitHub
failure email to the owner, so the inbox carried the noise even after the
alert path itself was deduped by cause. The structural fixes (red-once in
writer_queue.select_red, schedule-eviction auto-resolve) remove the noise at
the source; THIS report is the regression alarm that says whether they are
holding, from the only vantage point that can see it — the week's run list.

WHAT COUNTS AS NOISE, exactly:
  * repeats  — for each (workflow, normalised cause), every failed run beyond
               the first. The first red of a cause is signal and is never
               counted here; category-(a) failures stay loud elsewhere.
  * evictions — runs that ended `cancelled` having created ZERO jobs: the
               concurrency-lock displacement fingerprint. Each should have
               been either prevented (queued work cannot be displaced) or
               recorded as an orphan; existing at all is worth a count.

WHAT THIS NEVER DOES: silence anything. It reads run history and posts at most
ONE alert a week, and only when noise > 0. A quiet week posts nothing, so the
owner's inbox stays empty when nothing needs him. It is not a writer (no
database, no lock), and its own failure is a normal red run that the armed
ci-alert listener reports once.

Causes are read with ci_alert.extract_cause — the SAME extractor the alert
email and ci_status use, so all three name one failure one way. Reading a log
costs one gh call per failed run, capped by --max-logs; runs beyond the cap
are grouped as "(cause not read)" rather than pretended identical.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import ci_alert
import opsmail
import writer_queue_runs

#: Conclusions that count as failures, same set the alerter reacts to.
FAILED = frozenset({"failure", "timed_out", "startup_failure"})

#: Placeholder cause for failed runs past the --max-logs cap. Grouped per
#: WORKFLOW, never across workflows: two unread causes in one workflow are
#: plausibly one fact; across workflows they are plausibly two.
UNREAD = "(cause not read)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def classify(runs: list[dict], causes: dict[str, str],
             since: datetime) -> dict:
    """The week's run list -> noise, as plain data. Pure and offline.

    `causes` maps run id (str) -> extracted cause for whichever failed runs
    the caller could afford to read; missing ids group as UNREAD.
    """
    recent = []
    for run in runs:
        created = run.get("createdAt") or ""
        try:
            when = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        if when >= since and run.get("status") == "completed":
            recent.append(run)

    groups: Counter = Counter()
    for run in recent:
        if run.get("conclusion") not in FAILED:
            continue
        run_id = str(run.get("databaseId"))
        cause = causes.get(run_id, "").strip()
        label = ci_alert.normalise(cause) if cause else UNREAD
        groups[(run.get("workflowName") or "?", label)] += 1

    evictions = [run for run in recent
                 if run.get("conclusion") == "cancelled"
                 and run.get("job_count") == 0]

    repeats = sum(n - 1 for n in groups.values())
    return {
        "window_runs": len(recent),
        "failed_runs": sum(groups.values()),
        "causes": sorted(((wf, cause, n) for (wf, cause), n in groups.items()),
                         key=lambda item: -item[2]),
        "repeats": repeats,
        "evictions": [{"run_id": str(r.get("databaseId")),
                       "workflow": r.get("workflowName"),
                       "event": r.get("event"),
                       "created_at": r.get("createdAt")} for r in evictions],
        "noise": repeats + len(evictions),
    }


def compose(result: dict, *, repo: str, days: int,
            now: datetime | None = None) -> tuple[str, str, str]:
    """-> (subject, body, dedupe_key). Only called when noise > 0."""
    moment = now or _now()
    # LOWERCASE `w`, and it is load-bearing. The ISO-week token goes into the
    # dedupe key, and /alert accepts `^[a-z0-9][a-z0-9:._-]{0,159}$` — an
    # uppercase W is a SETTLED 400, not a retryable one, so the report was held,
    # retried 16 times, went `stuck`, and host-watch failed every tick from
    # 2026-08-03T21:55Z on "alerts are stuck with the host up". Asserted against
    # ci_alert.KEY_SAFE in tests/test_ci_noise_report.py.
    week = moment.strftime("%G-w%V")
    subject = (f"CI noise, week {week}: {result['noise']} noisy run(s) "
               f"in {repo.split('/')[-1]}")
    lines = [
        f"Last {days} days in {repo}: {result['window_runs']} completed runs, "
        f"{result['failed_runs']} failed across {len(result['causes'])} "
        f"cause(s), {result['repeats']} repeat red(s), "
        f"{len(result['evictions'])} zero-job eviction(s).",
        "",
        "A repeat red is a run that went red for a cause an earlier run",
        "already reported. The first red of each cause is signal, was",
        "already emailed once by the CI alerter, and is not counted here.",
        "",
    ]
    for wf, cause, n in result["causes"]:
        note = "reported once, correctly" if n == 1 else f"{n - 1} repeat red(s)"
        lines.append(f"  {wf}: {n} run(s) — {note}")
        lines.append(f"      cause: {cause}")
    if result["evictions"]:
        lines.append("")
        lines.append("Zero-job cancelled runs (the lock-displacement fingerprint):")
        for orphan in result["evictions"]:
            lines.append(f"  {orphan['workflow']} run {orphan['run_id']} "
                         f"({orphan['event']}, {orphan['created_at']})")
    lines.append("")
    lines.append("Noise means a structural fix regressed or is missing; the fix")
    lines.append("is never to silence the run. See drain-writers red-once and")
    lines.append("the schedule-eviction auto-resolve in writer_queue.py.")
    # The week is part of the key so next week's report is a NEW cause to the
    # endpoint rather than a suppressed repeat of an open one.
    return subject, "\n".join(lines), f"ci-noise:{week}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=400,
                    help="how many runs to read from gh")
    ap.add_argument("--max-logs", type=int, default=25,
                    help="failed-run logs to read for causes; the rest group "
                         "as unread rather than pretending to match")
    ap.add_argument("--repo", default=os.environ.get(
        "GITHUB_REPOSITORY", "dk-forge/talent-intelligence-tracker"))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the report; post nothing")
    args = ap.parse_args(argv)

    try:
        runs = writer_queue_runs.run_list(limit=args.limit, repo=args.repo)
        writer_queue_runs.attach_job_counts(runs, repo=args.repo)
    except writer_queue_runs.GhUnavailable as exc:
        # UNKNOWN, not clear: without the run list there is no report, and
        # exiting 0 here would read as "a quiet week".
        print(f"::error::could not read the run list at all: {exc}")
        return 3

    since = _now() - timedelta(days=args.days)
    failed_ids = [str(r.get("databaseId")) for r in runs
                  if r.get("conclusion") in FAILED]
    causes: dict[str, str] = {}
    for run_id in failed_ids[:args.max_logs]:
        cause, _context = ci_alert.extract_cause(
            ci_alert.fetch_failed_log(args.repo, run_id))
        if cause:
            causes[run_id] = cause

    result = classify(runs, causes, since)
    subject, body, dedupe_key = compose(result, repo=args.repo, days=args.days)

    print(f"window: {result['window_runs']} completed runs / {args.days} days")
    print(f"failed: {result['failed_runs']}  repeats: {result['repeats']}  "
          f"evictions: {len(result['evictions'])}  noise: {result['noise']}")

    if result["noise"] == 0:
        print("quiet week: no repeat reds, no evictions — nothing to send, "
              "and that silence is the product working.")
        return 0

    print("--- subject ---")
    print(subject)
    print("--- body ---")
    print(body)
    if args.dry_run:
        return 0

    if not opsmail.configured():
        print("::error::RESEND_API_KEY is not set - the noise report was NOT "
              "sent.")
        return 1

    payload = {"subject": subject, "body": body, "dedupe_key": dedupe_key}
    ok, note, transient = ci_alert.post_alert("", "", payload)
    print(f"noise report {dedupe_key}: {note}")
    if ok:
        return 0
    return ci_alert.hold(envelope=os.environ.get("ALERT_ENVELOPE", ""),
                         key=dedupe_key, kind="alert", scope="ci-noise",
                         payload=payload, note=note, transient=transient,
                         run_url="")


if __name__ == "__main__":
    sys.exit(main())
