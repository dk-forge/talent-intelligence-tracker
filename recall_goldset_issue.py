#!/usr/bin/env python3
"""Due recall gold set alarm: ONE issue, edited in place, closed on recovery.

    python3 recall_goldset_issue.py            # report, offline, exit 0
    python3 recall_goldset_issue.py --issue    # and open/edit/close the issue (GH_TOKEN)

Reads each family's work list (data/recall*_worklist.json, written by
measure_recall.py) and its `next_goldset` verdict. From 2026-09 to 2026-11 all
three families were DEGRADED because their sets aged out, and the only signal
was a line in the weekly digest email, which is not where the steward sweep or
an AI session looks. A GitHub issue is. Building the new set is AI work (the
`instruction` in the work list), so the issue carries it verbatim.

Same discipline as reference_freshness.py: opening and closing are two emails,
every edit in between mails nobody, and exit is always 0 (the issue is the
alarm; a red run would make ci_alert mail the same news a second time). A
missing work list counts as due, because "never measured" is not "fresh".
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ISSUE_TITLE = "Recall gold set is due"
FAMILIES = {"world": "recall_worklist.json", "us": "recall_us_worklist.json",
            "eu": "recall_eu_worklist.json"}


def default_paths() -> dict[str, str]:
    return {fam: os.path.join(HERE, "data", name) for fam, name in FAMILIES.items()}


def check(paths: dict[str, str] | None = None) -> list[dict]:
    rows = []
    for fam, path in (paths or default_paths()).items():
        try:
            with open(path, encoding="utf-8") as handle:
                due = json.load(handle).get("next_goldset") or {}
        except (OSError, ValueError):
            due = {"due": True, "reason": f"no readable work list at {os.path.basename(path)}",
                   "instruction": "Run measure_recall.py for this family."}
        rows.append({"family": fam, "due": bool(due.get("due")),
                     "reason": due.get("reason", ""), "instruction": due.get("instruction", "")})
    return rows


def issue_body(rows: list[dict]) -> str:
    lines = ["The weekly recall run (`recall.yml`) marks these reference sets as due, "
             "so `recall`, `recall_us` and `recall_eu` report DEGRADED until a new "
             "set is built and sealed:", ""]
    for r in rows:
        if r["due"]:
            lines += [f"### {r['family']}", f"- Why: {r['reason']}",
                      f"- What to do: {r['instruction']}", ""]
    lines.append("This issue is edited in place and closes itself on the first run "
                 "where no family is due.")
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
    due = any(x["due"] for x in rows)
    if due and existing:
        s.patch(f"{api}/{existing['number']}", json={"body": issue_body(rows)},
                headers=h, timeout=30).raise_for_status()
        return f"edited #{existing['number']}"
    if due:
        n = s.post(api, json={"title": ISSUE_TITLE, "body": issue_body(rows)},
                   headers=h, timeout=30)
        n.raise_for_status()
        return f"opened #{n.json()['number']}"
    if existing:
        s.post(f"{api}/{existing['number']}/comments",
               json={"body": "Every recall gold set is current again."},
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
        print(f"recall gold set {r['family']}: " + (f"DUE ({r['reason']})" if r["due"] else "current"))
    if a.issue:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not (token and repo):
            print("GH_TOKEN/GITHUB_REPOSITORY not set; issue not synced", file=sys.stderr)
            return 0
        try:
            print(f"gold set issue: {sync_issue(rows, repo=repo, token=token)}")
        except Exception as exc:  # the alarm must never redden the measurement run
            print(f"::warning::gold set issue not synced: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
