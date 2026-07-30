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
_ATTEMPTS = 4
_BACKOFF_SECONDS = (2, 5, 11)


def _gh(args: list[str], *, attempts: int = _ATTEMPTS) -> str:
    last = ""
    for attempt in range(attempts):
        result = subprocess.run(["gh", *args], capture_output=True, text=True)
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
    raise RuntimeError(f"gh {' '.join(args)} failed: {last}")


def fetch(limit: int = 100, repo: str | None = None) -> list[dict]:
    args = ["run", "list", "-L", str(limit), "--json", FIELDS]
    if repo:
        args += ["-R", repo]
    runs = json.loads(_gh(args))

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
