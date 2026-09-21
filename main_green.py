#!/usr/bin/env python3
"""Once a day, independently: what is main's ACTUAL state?

On 2026-09-21 we found that merges made by the merge train (the default
Actions token) never started an on-push workflow, so main's colour was simply
unknown for a day and nobody was told. The train now dispatches follow-up
jobs, but that is the train marking its own homework. This asks from outside.

For every workflow in WORKFLOWS it reads the runs on main and reports one of
three states. They are three states, not two:

  PASS     the newest conclusive completed run succeeded, and it is current
  FAIL     the newest conclusive completed run did not succeed
  UNKNOWN  no run at all; or no conclusive completed run; or the newest one is
           older than its ceiling while main has moved on (main's head carries
           no run of it and is past the grace); or the read failed or came
           back empty. ABSENCE OF A RUN IS NEVER GREEN.

Exit 0 only when every workflow is PASS. 1 = at least one FAIL, 3 = no FAIL
but at least one UNKNOWN. Any non-zero is a red run, which the repo's red-CI
alerting already carries to the owner.

It also keeps ONE running issue, found by a hidden marker: opened on the first
non-green run, its body rewritten only when the SET of failing or unknown
workflows changes, closed on the next all-PASS run. Never a second one.

Stdlib only. Every read goes through `gh api`; tests replace `gh_json`.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

DEFAULT_REPO = "dk-forge/talent-intelligence-tracker"
BRANCH = "main"
GRACE = timedelta(hours=2)
ISSUE_TITLE = "Main is not green"
MARKER = "<!-- main-green-check:running-issue -->"
SET_PREFIX = "<!-- main-green-check:set="

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
# A cancelled or skipped run decided nothing, so the judge looks past it.
INCONCLUSIVE = frozenset({"cancelled", "skipped", "stale", ""})


@dataclass(frozen=True)
class Workflow:
    file: str
    why: str
    # Days after which a green run stops vouching for a main that has moved.
    # None = path-filtered with no schedule, so age alone says nothing.
    max_age_days: float | None = 3.0


WORKFLOWS: tuple[Workflow, ...] = (
    Workflow("tests.yml", "the test suite: every push, hourly, and train merges"),
    Workflow("style-standard.yml", "required gate, on push and daily"),
    Workflow("card-contract.yml", "required gate, on push and daily"),
    # deploy-plugin.yml is deliberately absent: it is dispatch-only and its
    # default is a dry run, so a green run there says nothing about main.
)


class ReadError(RuntimeError):
    """A read that did not happen. Always UNKNOWN, never zero and never PASS."""


def gh_json(path: str, *, method: str = "GET", payload: dict | None = None) -> Any:
    cmd = ["gh", "api", path]
    if method != "GET":
        cmd += ["-X", method]
    if payload is not None:
        cmd += ["--input", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                              input=json.dumps(payload) if payload is not None else None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadError(f"gh api {path}: {exc}") from exc
    if proc.returncode != 0:
        raise ReadError(f"gh api {path} exited {proc.returncode}: "
                        f"{(proc.stderr or proc.stdout).strip()[:300]}")
    text = proc.stdout.strip()
    if not text:
        raise ReadError(f"gh api {path} returned nothing (NOREAD, not zero)")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReadError(f"gh api {path} did not return JSON: {exc}") from exc


def _when(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


@dataclass(frozen=True)
class Verdict:
    file: str
    state: str
    detail: str
    url: str = ""


def judge(wf: Workflow, payload: Any, head_sha: str, head_at: datetime,
          now: datetime) -> Verdict:
    """Pure. `payload` is the Actions runs listing for one workflow on main."""
    if not isinstance(payload, dict) or "workflow_runs" not in payload:
        return Verdict(wf.file, UNKNOWN, "runs listing had no workflow_runs (NOREAD)")
    runs = payload.get("workflow_runs") or []
    if not runs:
        return Verdict(wf.file, UNKNOWN, "no run on main at all")
    decided = [r for r in runs if r.get("status") == "completed"
               and str(r.get("conclusion") or "") not in INCONCLUSIVE]
    if not decided:
        return Verdict(wf.file, UNKNOWN,
                       f"{len(runs)} run(s) on main, none completed with a verdict")
    # Never trust the listing's order: one live read on 2026-09-21 put a
    # six day old run first. ISO timestamps sort as text.
    newest = max(decided, key=lambda r: str(r.get("created_at") or ""))
    url = str(newest.get("html_url") or "")
    sha = str(newest.get("head_sha") or "")[:8]
    conclusion = str(newest.get("conclusion"))
    if conclusion != "success":
        return Verdict(wf.file, FAIL, f"newest completed run is {conclusion} on {sha}", url)
    try:
        age = now - _when(newest.get("updated_at") or newest.get("created_at"))
    except (TypeError, ValueError):
        return Verdict(wf.file, UNKNOWN, "newest run carries no readable date", url)
    if wf.max_age_days is not None and age > timedelta(days=wf.max_age_days):
        head_has_run = any(r.get("head_sha") == head_sha for r in runs)
        if not head_has_run and (now - head_at) > GRACE:
            return Verdict(
                wf.file, UNKNOWN,
                f"newest green run is {age.days}d old (ceiling {wf.max_age_days:g}d) "
                f"and main's head {head_sha[:8]} has no run", url)
    return Verdict(wf.file, PASS, f"success on {sha}, {_age_text(age)} ago", url)


def _age_text(age: timedelta) -> str:
    hours = int(age.total_seconds() // 3600)
    return f"{hours}h" if hours < 48 else f"{age.days}d"


def read_head(repo: str, api: Callable[..., Any]) -> tuple[str, datetime]:
    data = api(f"repos/{repo}/commits/{BRANCH}")
    sha = str((data or {}).get("sha") or "") if isinstance(data, dict) else ""
    when = str((((data or {}).get("commit") or {}).get("committer") or {}).get("date") or "") \
        if isinstance(data, dict) else ""
    if not sha or not when:
        raise ReadError(f"could not read the head of {BRANCH} (NOREAD)")
    return sha, _when(when)


def check(repo: str, api: Callable[..., Any], now: datetime,
          workflows: Sequence[Workflow] = WORKFLOWS) -> list[Verdict]:
    try:
        head_sha, head_at = read_head(repo, api)
    except ReadError as exc:
        return [Verdict(wf.file, UNKNOWN, f"main's head unreadable: {exc}") for wf in workflows]
    out = []
    for wf in workflows:
        try:
            payload = api(f"repos/{repo}/actions/workflows/{wf.file}/runs"
                          f"?branch={BRANCH}&per_page=30")
        except ReadError as exc:
            out.append(Verdict(wf.file, UNKNOWN, f"read failed: {exc}"))
            continue
        out.append(judge(wf, payload, head_sha, head_at, now))
    return out


def exit_code(verdicts: Sequence[Verdict]) -> int:
    states = {v.state for v in verdicts}
    if not verdicts or states - {PASS, FAIL, UNKNOWN}:
        return 3
    if FAIL in states:
        return 1
    if UNKNOWN in states:
        return 3
    return 0


# --------------------------------------------------------------------------
# The one running issue
# --------------------------------------------------------------------------
def problem_set(verdicts: Sequence[Verdict]) -> str:
    """Identity of the incident: WHICH workflows are not green, and how.
    No SHA, no age, no URL, so a daily re-run of the same trouble is quiet."""
    bad = sorted(f"{v.file}={v.state}" for v in verdicts if v.state != PASS)
    return hashlib.sha256("|".join(bad).encode()).hexdigest()[:16] if bad else ""


def issue_body(verdicts: Sequence[Verdict], now: datetime) -> str:
    lines = [MARKER, f"{SET_PREFIX}{problem_set(verdicts)} -->", "",
             "The daily main-green check found main is not verifiably green. "
             "UNKNOWN is not a pass: it means nothing vouches for main.", "",
             "| Workflow | State | Detail |", "|---|---|---|"]
    for v in verdicts:
        link = f" ([run]({v.url}))" if v.url else ""
        lines.append(f"| `{v.file}` | {v.state} | {v.detail}{link} |")
    lines += ["", f"As of {now.strftime('%Y-%m-%d %H:%M')} UTC. This body changes only "
              "when the set of failing or unknown workflows changes, and the issue "
              "closes itself on the next all-PASS run. Do not open a second one."]
    return "\n".join(lines)


def find_issue(repo: str, api: Callable[..., Any]) -> dict | None:
    found = []
    for page in range(1, 11):
        data = api(f"repos/{repo}/issues?state=open&per_page=100&page={page}")
        if not isinstance(data, list):
            raise ReadError("open-issues listing was not a list (NOREAD)")
        found += [i for i in data if "pull_request" not in i
                  and MARKER in str(i.get("body") or "")]
        if len(data) < 100:
            break
    return min(found, key=lambda i: int(i["number"])) if found else None


def sync_issue(repo: str, verdicts: Sequence[Verdict], api: Callable[..., Any],
               now: datetime) -> str:
    """Returns what it did, for the log. Raises ReadError if it could not."""
    current = find_issue(repo, api)
    wanted = problem_set(verdicts)
    if not wanted:
        if current is None:
            return "no open issue, none needed"
        n = current["number"]
        api(f"repos/{repo}/issues/{n}/comments", method="POST", payload={
            "body": f"All PASS at {now.strftime('%Y-%m-%d %H:%M')} UTC. Closing."})
        api(f"repos/{repo}/issues/{n}", method="PATCH",
            payload={"state": "closed", "state_reason": "completed"})
        return f"closed issue #{n}"
    if current is None:
        made = api(f"repos/{repo}/issues", method="POST",
                   payload={"title": ISSUE_TITLE, "body": issue_body(verdicts, now)})
        return f"opened issue #{(made or {}).get('number', '?')}"
    n = current["number"]
    if f"{SET_PREFIX}{wanted} -->" in str(current.get("body") or ""):
        return f"issue #{n} already describes this set, left alone"
    api(f"repos/{repo}/issues/{n}", method="PATCH",
        payload={"body": issue_body(verdicts, now)})
    return f"updated issue #{n} (the set changed)"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    repo = os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    now = datetime.now(timezone.utc)
    verdicts = check(repo, gh_json, now)
    print(f"main-green check, {repo}@{BRANCH}, {now.strftime('%Y-%m-%d %H:%M')} UTC")
    for v in verdicts:
        print(f"  {v.state:<8}{v.file:<28}{v.detail}")
    code = exit_code(verdicts)
    if "--no-issue" not in args:
        try:
            print(f"issue: {sync_issue(repo, verdicts, gh_json, now)}")
        except ReadError as exc:
            print(f"issue: UNKNOWN, the running issue could not be synced: {exc}")
    bad = [v for v in verdicts if v.state != PASS]
    if code:
        print(f"\nerror: main is NOT verifiably green: "
              + "; ".join(f"{v.file} {v.state} ({v.detail})" for v in bad))
    else:
        print("\nmain is green: every listed workflow PASS")
    return code


if __name__ == "__main__":
    sys.exit(main())
