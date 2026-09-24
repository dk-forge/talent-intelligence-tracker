#!/usr/bin/env python3
"""Choose where a collector runs: the self-hosted box, or GitHub's own runner.

WHY (coverage audit 2026-09-24)
-------------------------------
On 2026-09-23 the Contabo runner went offline. collect.yml hung in
`actions/checkout` for ten minutes and died, and EDINET and DART missed their
weekly slots, because every collector said `runs-on: [self-hosted, linux,
contabo]` and nothing could move the work. This repository is public, so a
GitHub-hosted runner costs nothing; collection ran on `ubuntu-latest` until
#139 (2026-09-16), so it is known to work there.

HOW
---
`runner-heartbeat.yml` runs on the box every 30 minutes and does nothing but
succeed. A `pick-runner` job on `ubuntu-latest` in each collector reads the
most recent SUCCESSFUL heartbeat through the Actions API (GITHUB_TOKEN,
`actions: read` -- listing self-hosted runners needs an admin token this repo
does not hand to workflows, which is why the heartbeat exists) and outputs the
labels the collector job's `runs-on: ${{ fromJSON(...) }}` reads.

    fresh heartbeat (<= STALE_AFTER_HOURS)  -> the box
    stale heartbeat                         -> ubuntu-latest
    no heartbeat ever / API error           -> the box (status quo)

The last row is deliberate: with no evidence the box is down, moving every
collector changes its egress from the EU to the US, which is an owner decision
and not a fallback. A queued job on a dead box is what the stale row fixes.

Stdlib-only: this runs before any pip install.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

SELF_HOSTED = ["self-hosted", "linux", "contabo"]
HOSTED = "ubuntu-latest"
HEARTBEAT_WORKFLOW = "runner-heartbeat.yml"

#: The heartbeat fires every 30 minutes. Two hours is four missed beats: a
#: single slow queue never moves collection, a dead box always does.
STALE_AFTER_HOURS = 2.0


def _parse(ts: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def choose(last_success: str | None, *, now: datetime | None = None,
           stale_after_hours: float = STALE_AFTER_HOURS):
    """The runs-on labels for a collector job."""
    if not last_success:
        return SELF_HOSTED
    when = _parse(last_success)
    if when is None:
        return SELF_HOSTED
    now = now or datetime.now(timezone.utc)
    age = (now - when).total_seconds() / 3600
    return HOSTED if age > stale_after_hours else SELF_HOSTED


def latest_success(payload: dict) -> str | None:
    """`updated_at` of the newest successful run in an Actions runs payload."""
    for run in payload.get("workflow_runs") or []:
        if run.get("conclusion") == "success":
            return run.get("updated_at")
    return None


def as_output(labels) -> str:
    return json.dumps(labels, separators=(",", ":"))


def _fetch(repo: str, token: str) -> dict:
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/"
           f"{HEARTBEAT_WORKFLOW}/runs?status=success&per_page=5")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "dk-forge/talent-intelligence-tracker")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    forced = os.environ.get("TIT_RUNNER", "").strip().lower()
    last = None
    if forced in ("hosted", "self-hosted"):
        labels = HOSTED if forced == "hosted" else SELF_HOSTED
        why = f"forced by TIT_RUNNER={forced}"
    else:
        try:
            last = latest_success(_fetch(repo, token))
            labels = choose(last)
            why = f"last successful heartbeat: {last or 'never'}"
        except Exception as exc:  # noqa: BLE001 - any failure keeps the status quo
            labels = SELF_HOSTED
            why = f"heartbeat unreadable ({exc}); keeping the self-hosted runner"
    out = as_output(labels)
    print(f"runner: {out} ({why})")
    if labels == HOSTED:
        print(f"::warning::self-hosted runner heartbeat is stale ({why}); "
              f"this run falls back to {HOSTED}")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a") as fh:
            fh.write(f"labels={out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
