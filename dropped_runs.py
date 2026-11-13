#!/usr/bin/env python3
"""Find writer runs that GitHub dropped out of the concurrency queue.

Every workflow that writes data/talent_intel.db shares one concurrency group
(`talent-collect`, cancel-in-progress: false). That is not negotiable: two
writers both end with reset-to-origin-then-copy-our-file-back, so whichever
pushes second silently destroys the other's rows. See the comment at the top of
collect.yml.

The lock has a queueing weakness the group cannot fix. GitHub Actions keeps
exactly ONE pending run per concurrency group. While a run is in progress and a
second waits, dispatching a third does not queue behind them: it REPLACES the
waiting one, which completes with conclusion `cancelled`. Nothing fails,
nothing is annotated, and `gh run list` shows a green-ish "cancelled" that
reads like somebody cancelled it on purpose.

That is exactly the silent-failure shape this project keeps paying for. On
2026-07-29 a GDELT backfill held the lock for over three hours and thirteen
dispatched writers evaporated inside that window, including two runs of
correct-sec-pillar, the job that re-issues 548 published SEC 8-K Item 5.02 rows
filed under the wrong pillar. It looked dispatched. It never ran.

So this watchdog reads the Actions API, finds the drops, and reports them
through the same keyed /alert path a dead collector uses. It is READ ONLY: it
writes no database, pushes no commit, and re-dispatches nothing. Re-dispatch is
a human step on purpose, because the dispatch INPUTS are not recoverable from
the API and correct-sec-pillar defaults to dry_run: true, so a blind
re-dispatch would report success having changed nothing.

    python3 dropped_runs.py                 # look back a day, alert if needed
    python3 dropped_runs.py --dry-run       # find and print, send nothing
    python3 dropped_runs.py --hours 168     # a wider window, for a backfill

Exit codes: 0 = the watchdog ran (whether or not it found drops)
            1 = the watchdog itself could not run

Like health_digest.py, and for the same reason: the email IS the alert. A red
daily run on top of it only trains the owner to ignore a red daily run.

Env: GITHUB_TOKEN or GH_TOKEN (needs `actions: read`), GITHUB_REPOSITORY,
WP_SITE_URL and WP_API_KEY to send.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
DEFAULT_REPO = "dk-forge/talent-intelligence-tracker"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# How far back to look. The workflow runs daily, so 26 hours covers the gap
# with a couple of hours of overlap: a drop reported twice is a nuisance, a
# drop that falls between two windows is the whole failure this exists to
# close.
DEFAULT_LOOKBACK_HOURS = 26

# A run is only a WRITER if its file touches the database. Derived from the
# files rather than listed here, so a new writer is watched the day it lands
# instead of the day somebody remembers to add it. tests/test_workflows.py
# imports writes_database() so the lock test and this watchdog cannot drift
# apart on what "a writer" means.
DB_FILE_MARKER = "talent_intel.db"


# --------------------------------------------------------------------------
# Which workflows hold the lock
# --------------------------------------------------------------------------

def writes_database(text: str) -> bool:
    """Does this workflow touch data/talent_intel.db?

    COMMENTS DO NOT COUNT. Naming the file while explaining the lock is not
    holding it: this watchdog writes nothing and describes the database at
    length, and a bare substring search would have filed it as a writer, put it
    in the concurrency group, and let it be evicted by the exact bug it exists
    to detect. Full-line comments are stripped, which is the shape every
    explanation in these files takes.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if DB_FILE_MARKER in line:
            return True
    return False


def _concurrency_group(text: str):
    """The `group:` under a top-level `concurrency:`, or None.

    A hand-rolled scan rather than yaml.safe_load so this stays runnable with
    nothing installed, like ops_status.py. It only has to read files in this
    repo, whose shape is fixed by tests/test_workflows.py.
    """
    in_block = False
    for line in text.splitlines():
        if re.match(r"^concurrency:\s*(#.*)?$", line):
            in_block = True
            continue
        if in_block:
            match = re.match(r"^\s+group:\s*(\S+)", line)
            if match:
                return match.group(1).strip("'\"")
            # A new top-level key ends the block.
            if line and not line[0].isspace() and not line.startswith("#"):
                in_block = False
    return None


def lock_group(workflows_dir: Path = WORKFLOWS):
    """The name of the one group the database writers share, or None.

    Read off the files rather than hardcoded, because the group has been
    renamed once already (two groups were merged into `talent-collect` on
    2026-07-28) and a watchdog pinned to a stale literal would report nothing
    at all while looking perfectly healthy.
    """
    groups = [
        _concurrency_group(p.read_text())
        for p in sorted(workflows_dir.glob("*.yml"))
        if writes_database(p.read_text())
    ]
    named = [g for g in groups if g]
    return max(set(named), key=named.count) if named else None


def writer_workflows(workflows_dir: Path = WORKFLOWS) -> dict:
    """{'.github/workflows/x.yml': group} for every workflow that holds the lock.

    Keyed by the path the Actions API reports on a run, so matching a run to a
    writer is an equality check rather than a guess at the display name.

    A workflow qualifies by NAMING THE DATABASE or by DECLARING THE GROUP, and
    the second half is not redundant. enrich.yml writes the database through a
    heredoc that never spells the filename, so it joined the group by hand and
    was invisible to every check that looked for `talent_intel.db` — including
    the lock test that is supposed to catch a writer with no group. It was one
    of the runs dropped on 2026-07-29 and the first draft of this watchdog
    would have missed it. Declaring the group IS the contract; mentioning the
    file is only evidence.
    """
    group = lock_group(workflows_dir)
    found = {}
    for path in sorted(workflows_dir.glob("*.yml")):
        text = path.read_text()
        declared = _concurrency_group(text)
        if writes_database(text) or (group and declared == group):
            found[f".github/workflows/{path.name}"] = declared
    return found


# --------------------------------------------------------------------------
# Reading the Actions API
# --------------------------------------------------------------------------

def api(repo: str, path: str, token: str, params: dict | None = None) -> dict:
    url = f"https://api.github.com/repos/{repo}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def stamp(value):
    """An ISO timestamp as an aware datetime, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Classification (pure, so it is testable without a network)
# --------------------------------------------------------------------------

def executed_anything(jobs: list) -> bool:
    """Did this run get as far as running a step?

    This is the discriminator between the two kinds of `cancelled`, and it is
    not a heuristic about durations: a run evicted from the concurrency queue
    never has a job assigned at all, so the jobs endpoint returns an empty
    list. Verified against all thirteen drops of 2026-07-29, every one of which
    reported total_count 0, and against the backfill holding the lock, which
    reported one job with ten steps.

    A human cancelling a RUNNING job leaves at least "Set up job" behind, so it
    is not reported here. A human cancelling a QUEUED one looks identical to an
    eviction, which is the right way round: over-reporting a deliberate cancel
    costs one glance at an email, under-reporting a drop costs 548 rows.
    """
    for job in jobs:
        if job.get("started_at") and (job.get("steps") or []):
            return True
    return False


def overlapping_holder(run: dict, candidates: list, now: datetime):
    """The run that plausibly held the lock while `run` sat in the queue.

    Evidence for the report, never a condition for it. A drop with no
    identifiable holder is still a drop.
    """
    dropped_from = stamp(run.get("created_at"))
    dropped_to = stamp(run.get("updated_at")) or dropped_from
    if not dropped_from:
        return None

    best = None
    for other in candidates:
        if other.get("id") == run.get("id"):
            continue
        started = stamp(other.get("run_started_at") or other.get("created_at"))
        if not started:
            continue
        # An in-progress run has no end yet, so it is still holding it now.
        ended = now if other.get("status") != "completed" else (
            stamp(other.get("updated_at")) or now)
        if started <= dropped_to and ended >= dropped_from:
            # Prefer the longest overlapping run: with several candidates the
            # hours-long backfill is the one worth naming.
            span = (ended - started).total_seconds()
            if best is None or span > best[0]:
                best = (span, other)
    return best[1] if best else None


def find_drops(repo: str, token: str, hours: int, now: datetime) -> tuple:
    """(drops, considered, writers). Each drop carries its holder, if found."""
    writers = writer_workflows()
    if not writers:
        raise RuntimeError("no database writers found in .github/workflows")

    cutoff = now - timedelta(hours=hours)

    # Everything recent, so the same page serves both the cancelled runs and
    # the pool of possible lock holders. 100 runs covers a day comfortably.
    recent = api(repo, "actions/runs", token, {"per_page": 100}).get("workflow_runs", [])
    in_group = [r for r in recent if r.get("path") in writers]

    drops, considered = [], 0
    for run in in_group:
        if run.get("conclusion") != "cancelled":
            continue
        created = stamp(run.get("created_at"))
        if not created or created < cutoff:
            continue
        considered += 1
        jobs = api(repo, f"actions/runs/{run['id']}/jobs", token).get("jobs", [])
        if executed_anything(jobs):
            continue  # cancelled after it started: a human, not the queue
        drops.append({
            "id": run.get("id"),
            "name": run.get("name"),
            "path": run.get("path"),
            "event": run.get("event"),
            "created_at": run.get("created_at"),
            "url": run.get("html_url"),
            "group": writers.get(run.get("path")),
            "holder": overlapping_holder(run, in_group, now),
        })

    drops.sort(key=lambda d: d["created_at"] or "")
    return drops, considered, writers


# --------------------------------------------------------------------------
# The email
# --------------------------------------------------------------------------

def workflow_file(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def build_email(drops: list, repo: str, hours: int) -> tuple:
    """Subject and plain-text body. No em-dashes: this is owner-facing copy."""
    names = sorted({d["name"] or workflow_file(d["path"]) for d in drops})
    subject = "%d writer run(s) dropped from the queue: %s" % (
        len(drops), ", ".join(names[:4]))

    lines = [
        "These runs were dispatched and never ran. GitHub keeps only one",
        "pending run per concurrency group, so a third dispatch replaces the",
        "one already waiting. The replaced run completes as `cancelled`, which",
        "looks deliberate and is not.",
        "",
        "Nothing failed, so nothing else would have told you.",
        "",
    ]

    for drop in drops:
        lines.append("DROPPED: %s at %s (%s)" % (
            drop["name"] or workflow_file(drop["path"]),
            drop["created_at"], drop["event"]))
        lines.append("  %s" % drop["url"])
        holder = drop.get("holder")
        if holder:
            lines.append("  The lock was held by %s (run %s), started %s." % (
                holder.get("name"), holder.get("id"),
                holder.get("run_started_at") or holder.get("created_at")))

    lines += [
        "",
        "Re-dispatch is a HUMAN step and this watchdog deliberately does not do",
        "it. The Actions API does not return the inputs a workflow_dispatch was",
        "given, so a blind re-run would use the defaults. correct-sec-pillar",
        "defaults to dry_run: true, which would report success having changed",
        "nothing at all. Check what each run was for before re-dispatching:",
        "",
    ]
    for name in names:
        matching = next(d for d in drops
                        if (d["name"] or workflow_file(d["path"])) == name)
        lines.append("  gh workflow run %s -R %s --ref main" % (
            workflow_file(matching["path"]), repo))

    lines += [
        "",
        "Do NOT fix this by removing the shared lock. Two writers at once",
        "destroy each other's rows, which is the incident the group exists to",
        "prevent. Dispatch them one at a time, or wait for the long run to",
        "finish before queueing more than one.",
        "",
        "What to do: open a Claude Code session in the talent-intelligence-tracker",
        "repo and paste the line below. That is the whole job.",
        "",
        ('  "The dropped-run watchdog says these writer runs were evicted from '
         'the talent-collect queue and never ran: %s. Work out what each one '
         'was dispatched to do, confirm from the database whether that work is '
         'still outstanding, and re-dispatch them ONE AT A TIME with the right '
         'inputs. Do not remove the concurrency group."' % ", ".join(names)),
        "",
        "Window searched: the last %d hours in %s." % (hours, repo),
    ]

    return subject, "\n".join(lines)


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Report writer runs silently dropped from the Actions queue.")
    parser.add_argument("--dry-run", action="store_true",
                        help="find and print, send no email")
    parser.add_argument("--hours", type=int, default=DEFAULT_LOOKBACK_HOURS,
                        help="how far back to look (default %d)" % DEFAULT_LOOKBACK_HOURS)
    args = parser.parse_args(argv)

    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        print("FATAL: no GITHUB_TOKEN or GH_TOKEN. This needs `actions: read`.",
              file=sys.stderr)
        return 1
    repo = (os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO).strip()

    now = datetime.now(timezone.utc)
    try:
        drops, considered, writers = find_drops(repo, token, args.hours, now)
    except (urllib.error.URLError, RuntimeError, ValueError) as exc:
        # The watchdog itself failing IS a real failure of the alerting and
        # must be loud, unlike the findings, which go by email.
        print("FATAL: could not read the Actions API: %s" % exc, file=sys.stderr)
        return 1

    print("DROPPED RUN WATCHDOG  (%s, last %dh)" % (repo, args.hours))
    print("  %d writer workflows share the lock, %d cancelled in the window, "
          "%d never started" % (len(writers), considered, len(drops)))
    for drop in drops:
        print("  ::warning:: DROPPED %s (run %s, dispatched %s) never ran: %s"
              % (drop["name"], drop["id"], drop["created_at"], drop["url"]))
        if drop.get("holder"):
            print("      lock held by %s (run %s)"
                  % (drop["holder"].get("name"), drop["holder"].get("id")))

    if not drops:
        print("  No dropped writers. Nothing needs a human.")
        return 0

    subject, body = build_email(drops, repo, args.hours)
    print("\n--- email ---\nSubject: %s\n%s\n--- end ---\n" % (subject, body))

    if args.dry_run:
        print("dry run: no email sent")
        return 0

    # One alert path for the whole project: a dropped writer reaches the owner
    # the same way a dead collector does.
    from health_digest import send_alert

    sent, note = send_alert(
        subject, body,
        site=(os.environ.get("WP_SITE_URL") or "").strip().rstrip("/"),
        key=(os.environ.get("WP_API_KEY") or "").strip(),
    )
    print("alert delivery: %s" % note)
    if not sent:
        print("::warning:: the watchdog found dropped runs but could not email them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
