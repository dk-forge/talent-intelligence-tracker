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
import subprocess
import sys

FIELDS = "databaseId,workflowName,status,conclusion,createdAt,updatedAt,event"


def _gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


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
