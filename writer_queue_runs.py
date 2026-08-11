#!/usr/bin/env python3
"""Fetch the recent run list in the shape writer_queue.tick expects.

Split out from writer_queue.py so that module stays offline and dependency-free
(ops_status.py imports it and must never need a network or a key).

The one field the plain run list does not carry is the number of jobs a run
created, and that is the whole diagnosis: a run evicted from the concurrency
group's single pending slot ends `cancelled` having created ZERO jobs. So the
job count is fetched for cancelled runs only — a handful of extra calls.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

FIELDS = "databaseId,workflowName,status,conclusion,createdAt,updatedAt,event"

# GitHub's own API 502s and 503s under load, and one of those took the whole
# drainer down on 2026-07-30 with `HTTP 502: Server Error` on page 2 of the run
# list -- a queue that had just started moving stopped again for somebody else's
# bad minute. Retrying is only safe because these calls are READS; nothing here
# dispatches or writes, so a repeat costs a request and nothing else.
#
# Only TRANSIENT failures retry. A 401, 404 or 422 is deterministic and means
# the call is wrong, and retrying a deterministic refusal is the infinite silent
# loop this queue already learned about the hard way -- it fails immediately and
# loudly instead.
_TRANSIENT = re.compile(
    r"HTTP (?:429|5\d\d)|timed? ?out|timeout|connection (?:reset|refused|aborted)"
    r"|EOF occurred|TLS handshake|temporary failure|try again",
    re.I,
)

# "I could not check" is a different answer from "nothing is wrong", and the
# distance between them is the whole of this project's false-healthy history.
# These shapes mean gh itself could not answer — no binary, no credential, no
# route to github.com — so the caller must say so and exit differently rather
# than report an empty, reassuring list.
_UNAVAILABLE = re.compile(
    r"gh auth login|not logged in|authentication|bad credentials|HTTP 401"
    r"|could not resolve host|no such host|dial tcp|network is unreachable"
    r"|connection refused|proxyconnect|certificate|timed? ?out|timeout"
    r"|EOF occurred|TLS handshake",
    re.I,
)
_ATTEMPTS = 4
_BACKOFF_SECONDS = (2, 5, 11)


class GhUnavailable(RuntimeError):
    """gh could not answer at all: not installed, not authenticated, no route.

    A RuntimeError subclass on purpose — every existing caller catches
    RuntimeError and keeps its behaviour — but nameable, so a tool whose job is
    to report red runs can exit "I could not check" instead of "all clear".
    """


def _gh(args: list[str], *, attempts: int = _ATTEMPTS) -> str:
    last = ""
    for attempt in range(attempts):
        try:
            result = subprocess.run(["gh", *args], capture_output=True, text=True)
        except FileNotFoundError:
            # Uncaught this was a traceback, which reads as a broken tool rather
            # than as a missing one.
            raise GhUnavailable(
                "the GitHub CLI (gh) is not installed or is not on PATH, so no "
                "run state could be read at all") from None
        if result.returncode == 0:
            return result.stdout
        last = result.stderr.strip()
        if not _TRANSIENT.search(last) or attempt == attempts - 1:
            break
        pause = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
        print(f"::warning::gh {' '.join(args[:2])} failed transiently "
              f"(attempt {attempt + 1}/{attempts}), retrying in {pause}s: {last}",
              file=sys.stderr)
        time.sleep(pause)
    message = f"gh {' '.join(args)} failed: {last}"
    if _UNAVAILABLE.search(last):
        raise GhUnavailable(message)
    raise RuntimeError(message)


def run_list(*, limit: int = 100, repo: str | None = None,
             fields: str = FIELDS, status: str | None = None,
             branch: str | None = None, workflow: str | None = None) -> list[dict]:
    """One `gh run list` query, as data.

    The filters exist because ci_status.py asks narrower questions than the
    drainer does — "the last N FAILURES", "the newest run of workflow W on the
    default branch" — and on a repo doing forty runs an hour an unfiltered list
    of 100 covers about four hours, so filtering server-side is the difference
    between seeing a day and thinking a day was quiet.

    `fields` is a parameter for the same reason: ci_status needs `headBranch`
    and `url` on top of what the drainer reads, and a second subprocess wrapper
    to add two columns would have been the third one in this repo.
    """
    args = ["run", "list", "-L", str(limit), "--json", fields]
    if repo:
        args += ["-R", repo]
    if status:
        args += ["--status", status]
    if branch:
        args += ["-b", branch]
    if workflow:
        args += ["-w", workflow]
    return json.loads(_gh(args))


def attach_job_counts(runs: list[dict], repo: str | None = None) -> list[dict]:
    """Fill in `job_count` on every cancelled run, in place.

    Separate from the query so a caller that already has a cancelled-only list
    can ask the same question without re-reading everything.
    """
    for run in runs:
        # Only cancelled runs can be displacement victims, and only they need
        # the extra call.
        if run.get("conclusion") != "cancelled":
            continue
        path = f"repos/{{owner}}/{{repo}}/actions/runs/{run['databaseId']}/jobs"
        call = ["api", path, "--jq", ".total_count"]
        if repo:
            call = ["api", path.replace("{owner}/{repo}", repo), "--jq", ".total_count"]
        try:
            run["job_count"] = int(_gh(call).strip() or 0)
        except (RuntimeError, ValueError) as exc:
            # Unknown is NOT the same as zero. Leaving it absent makes
            # never_started() fall back to the jobs list, and an unreadable run
            # is better treated as "started" than wrongly re-dispatched.
            print(f"::warning::could not read jobs for run {run['databaseId']}: {exc}",
                  file=sys.stderr)
            run["job_count"] = None
    return runs


def fetch(limit: int = 100, repo: str | None = None,
          fields: str = FIELDS) -> list[dict]:
    """The recent run list in the shape writer_queue.tick expects."""
    return attach_job_counts(run_list(limit=limit, repo=repo, fields=fields), repo)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    runs = fetch(args.limit, args.repo)
    with open(args.out, "w") as handle:
        json.dump(runs, handle, indent=2)
    print(f"read {len(runs)} run(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
