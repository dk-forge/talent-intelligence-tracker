#!/usr/bin/env python3
"""Freshness alarm for the reference-data store: ONE issue, edited, closed on recovery.

    python3 reference_freshness.py            # report, offline, exit 0
    python3 reference_freshness.py --issue    # and open/edit/close the issue (GH_TOKEN)

Two clocks per source, read from data/reference/<source>/MANIFEST.json:

* collector -- days since we last stored a pull (`fetched_at`). Over the limit
  means our job stopped (red, evicted, disabled), whatever the source did.
* source    -- days since the newest period the source itself covers (`as_of`).
  Over the limit means the PUBLISHER stopped, even while our job runs green.

Limits are the release cadence plus a margin, derived per source in SPECS.
A missing manifest is stale (a source we promised and never stored).

One issue, by exact title, the same discipline as host-watch: opening and
closing it are two emails; every update in between edits the body and mails
nobody. Exit is 0 either way -- a stale source is an issue, not a red run that
the CI alerter would then mail a second time.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import reference_store

ISSUE_TITLE = "Reference data is stale"

#: source -> (collector days, source days). Derivations:
#: indeed_occupations: weekly job (7d) + one missed run = 10d; Hiring Lab rows
#:   lag ~1 week and are pushed ~weekly, so the newest date older than 21d means
#:   the repository stopped updating.
#: h1b_lca: monthly check (30d) + one missed run = 40d (checked_at counts, so a
#:   month with no new quarter is not a dead collector); DOL posts each quarter's
#:   cumulative FY file roughly one quarter after it ends, so the newest quarter
#:   end older than 92 + 92 + 16 = 200d means OFLC stopped publishing.
SPECS: dict[str, tuple[int, int]] = {
    "indeed_occupations": (10, 21),
    "h1b_lca": (40, 200),
}


def _days(iso: str | None, now: datetime) -> int | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).days


def check(store=reference_store.STORE, now: datetime | None = None,
          specs: dict | None = None) -> list[dict]:
    """One row per source: {source, collector_days, source_days, problems}."""
    now = now or datetime.now(timezone.utc)
    out = []
    for source, (c_lim, s_lim) in sorted((specs or SPECS).items()):
        m = reference_store.read_manifest(source, store)
        row = {"source": source, "problems": []}
        if not m:
            row["problems"].append("never stored (no MANIFEST.json)")
            out.append(row)
            continue
        cd = _days(m.get("checked_at") or m.get("fetched_at"), now)
        sd = _days(m.get("as_of"), now)
        row.update(collector_days=cd, source_days=sd, rows=m.get("rows"))
        if cd is None or cd > c_lim:
            row["problems"].append(f"collector last stored {cd}d ago (limit {c_lim}d)")
        if sd is None or sd > s_lim:
            row["problems"].append(f"source newest period {m.get('as_of')} is {sd}d old (limit {s_lim}d)")
        out.append(row)
    return out


def issue_body(rows: list[dict]) -> str:
    lines = ["The reference-data freshness check (`reference_freshness.py`) found:", ""]
    for r in rows:
        if r["problems"]:
            lines.append(f"- **{r['source']}**: " + "; ".join(r["problems"]))
    lines += ["", "Collectors: `indeed-occupations.yml` (weekly), `h1b-lca.yml` (monthly check, quarterly file). "
              "This issue is edited in place and closes itself on recovery."]
    return "\n".join(lines)


def sync_issue(rows: list[dict], *, repo: str, token: str, session=None) -> str:
    import requests
    s = session or requests.Session()
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{repo}/issues"
    r = s.get(api, params={"state": "open", "per_page": 100}, headers=h, timeout=30)
    r.raise_for_status()
    existing = next((i for i in r.json() if i.get("title") == ISSUE_TITLE
                     and "pull_request" not in i), None)
    stale = any(x["problems"] for x in rows)
    if stale and existing:
        s.patch(f"{api}/{existing['number']}", json={"body": issue_body(rows)},
                headers=h, timeout=30).raise_for_status()
        return f"edited #{existing['number']}"
    if stale:
        n = s.post(api, json={"title": ISSUE_TITLE, "body": issue_body(rows)},
                   headers=h, timeout=30)
        n.raise_for_status()
        return f"opened #{n.json()['number']}"
    if existing:
        s.post(f"{api}/{existing['number']}/comments",
               json={"body": "All reference sources are fresh again."},
               headers=h, timeout=30).raise_for_status()
        s.patch(f"{api}/{existing['number']}",
                json={"state": "closed", "state_reason": "completed"},
                headers=h, timeout=30).raise_for_status()
        return f"closed #{existing['number']}"
    return "no issue"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--issue", action="store_true")
    a = p.parse_args(argv)
    rows = check()
    for r in rows:
        state = "STALE " + "; ".join(r["problems"]) if r["problems"] else "fresh"
        reference_store.notice(
            f"reference {r['source']}: {state} collector={r.get('collector_days')}d "
            f"source={r.get('source_days')}d rows={r.get('rows')}")
    if a.issue:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not (token and repo):
            print("GH_TOKEN/GITHUB_REPOSITORY not set; issue not synced", file=sys.stderr)
            return 0
        reference_store.notice(f"freshness issue: {sync_issue(rows, repo=repo, token=token)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
