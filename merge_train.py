#!/usr/bin/env python3
"""The merge train: land AT MOST ONE green pull request per run, unattended.

WHY THIS FILE EXISTS
--------------------
Work in these three repos repeatedly stalled with pull requests green,
mergeable, and nothing merging them, because the only thing that merges a pull
request is a session. Sessions end, hit rate limits, and hand off badly. This
runs on a schedule on the VPS runner and needs no session at all.

ONE PER RUN, DELIBERATELY. The single self-hosted runner must not be flooded,
and in the two tracker repos one-per-run buys deploy spacing for free.

WHAT IT REFUSES TO TRUST
------------------------
The rollup summary. `gh pr view --json statusCheckRollup` reports the PREVIOUS
commit's checks for a short window after a force-push -- a defect this project
has been bitten by (memory: "rollup shows the old commit"). So the head SHA is
resolved FIRST and every check run is judged against that exact SHA; anything
carrying a different `head_sha` is dropped as stale, not counted.

CANCELLED IS NOT GREEN. A job killed by a timeout, by load on the box, or by a
concurrency group reads as `cancelled`, and treating that as "not a failure" is
how a red queue looks green. `cancelled`, `timed_out`, `startup_failure` and
`action_required` all count as failing here.

AN EMPTY READ IS NEVER A PASS. Zero checks is "CI has not started", not "no
failures". Every repo declares a MINIMUM number of real checks in
`.github/merge-train.json`, derived from what that repo actually runs; below
the floor the train waits.

`fixture:` JOBS ARE EXCLUDED FROM EVERYTHING. They are red by design in the
sandbox (memory: "selftest fixtures are red by design").

NEVER AUTO-MERGE. That flag landed PR #954 untested in the sandbox when no
ruleset existed, so it is absent from this file by inspection and a test in
each repo holds it absent. This does a plain squash merge, after it has checked
everything itself, or it does nothing.

NEVER RE-RUN OR CANCEL AN IN-FLIGHT RUN. Re-running a live run cancels it,
cancelled reads as failed, and the loop feeds itself (memory: "never re-run
in-flight CI"). The unstick path below re-runs only COMPLETED jobs.

UNSTICKING IS BOOKKEEPING ONLY
------------------------------
The train may also un-stick a queue, and the boundary is drawn hard: it edits
NO program logic and never tries to make a failing test pass. It may

  1. resolve a merge conflict in known-mechanical files only (version-derived
     files take main's side; append-only logs keep BOTH entries),
  2. rebase a branch on main and push, only when nothing outside that set
     conflicts,
  3. re-run ONE completed job that failed in an infrastructure shape.

It never deletes or weakens a test, never widens a threshold, never adds a
suppression, and never touches a ledger, an invariant, a gold set, a
correction spec or a reference set. That is enforced twice: an allowlist of
touchable paths, and `inspect_diff()`, which reads the patch it is about to
push and refuses those shapes outright.

A real code defect, a failing assertion, or ANY data-integrity or live-data
failure is labelled `needs-human` and mailed once. A wrong number already
published needs a person.

IT UNSTICKS OR IT MERGES, NEVER BOTH IN ONE RUN, so the thing judging green is
never the thing that just changed the branch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

class MergeTrainError(RuntimeError):
    """An unexpected API or git failure. The run must go RED, not quiet."""


# A conclusion in this set is a failure. CANCELLED IS IN HERE ON PURPOSE.
FAILING_CONCLUSIONS = frozenset({
    "failure", "timed_out", "cancelled", "startup_failure", "action_required",
})

# The subset that is shaped like infrastructure rather than like a defect: the
# runner was killed, the box was busy, the network went away. These are the
# only ones the unstick path may re-run, and only twice per head SHA.
INFRA_CONCLUSIONS = frozenset({"timed_out", "cancelled", "startup_failure"})

# A conclusion in this set is fine: the job ran and did not fail, or the job
# was correctly skipped by its own path filter.
PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

# Not finished. Waiting is the only correct answer.
PENDING_STATUSES = frozenset({
    "queued", "in_progress", "waiting", "requested", "pending",
})

# Verdict states.
READY = "READY"                  # merge it
WAIT = "WAIT"                    # not yet; say nothing, try again next run
SKIP_RED = "SKIP_RED"            # red. Report it. Never fix it, never force it.
SKIP_CONFLICT = "SKIP_CONFLICT"  # conflicting with main
HOLD = "HOLD"                    # a human asked for it, or a major bump
SKIP_CLOSED = "SKIP_CLOSED"      # not open any more


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str
    # Populated where it helps the summary and the mail. Never a secret.
    failing: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    counted: int = 0
    stale_dropped: int = 0
    fixture_dropped: int = 0

    @property
    def mergeable_now(self) -> bool:
        return self.state == READY


@dataclass(frozen=True)
class Config:
    repo: str
    min_checks: int
    plugin_paths: tuple[str, ...] = ()
    deploy_workflow: str = ""
    deploy_window_minutes: int = 60
    main_branch: str = "main"
    # Workflows dispatched on main when main's head carries no run of them.
    # deploy_workflow is deliberately NOT one of them: a plugin deploy in this
    # repo stays a deliberate act, and from_dict refuses a config that lists it.
    post_merge_workflows: tuple[str, ...] = ()
    push_grace_minutes: int = 10
    hold_labels: tuple[str, ...] = ("hold", "needs-human", "do-not-merge")
    fixture_prefix: str = "fixture:"
    # Unstick
    unstick_enabled: bool = True
    mechanical_take_main: tuple[str, ...] = ()
    mechanical_keep_both: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    human_only_checks: tuple[str, ...] = ()
    max_unstick_per_sha: int = 2
    max_unstick_per_pr: int = 3
    needs_human_label: str = "needs-human"

    @property
    def mechanical_paths(self) -> tuple[str, ...]:
        return tuple(self.mechanical_take_main) + tuple(self.mechanical_keep_both)

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise MergeTrainError(f"no merge-train config at {path}") from exc
        except json.JSONDecodeError as exc:
            raise MergeTrainError(f"merge-train config at {path} is not JSON: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        missing = [k for k in ("repo", "min_checks") if k not in raw]
        if missing:
            raise MergeTrainError(f"merge-train config is missing {missing}")
        min_checks = int(raw["min_checks"])
        if min_checks < 2:
            # A floor of 1 is the defect this exists to prevent: one stray
            # check run is not "CI is green", it is "CI has barely started".
            raise MergeTrainError(
                "min_checks must be derived from what this repo actually runs; "
                f"{min_checks} is not a floor"
            )
        def tup(key: str, default: Sequence[str] = ()) -> tuple[str, ...]:
            return tuple(raw.get(key, default) or ())
        deploy = str(raw.get("deploy_workflow", ""))
        if deploy and deploy in tup("post_merge_workflows"):
            # The train must never deploy the plugin. deploy-plugin.yml is
            # dispatch-only, its dry_run defaults to true, and a deploy is
            # never made twice within an hour. That stays a person's act.
            raise MergeTrainError(
                f"post_merge_workflows lists the deploy workflow {deploy}; "
                "the train dispatches tests on main, never a deploy"
            )
        return cls(
            repo=str(raw["repo"]),
            min_checks=min_checks,
            plugin_paths=tup("plugin_paths"),
            deploy_workflow=str(raw.get("deploy_workflow", "")),
            deploy_window_minutes=int(raw.get("deploy_window_minutes", 60)),
            main_branch=str(raw.get("main_branch", "main")),
            post_merge_workflows=tup("post_merge_workflows"),
            push_grace_minutes=int(raw.get("push_grace_minutes", 10)),
            hold_labels=tup("hold_labels", ("hold", "needs-human", "do-not-merge")),
            fixture_prefix=str(raw.get("fixture_prefix", "fixture:")),
            unstick_enabled=bool(raw.get("unstick_enabled", True)),
            mechanical_take_main=tup("mechanical_take_main"),
            mechanical_keep_both=tup("mechanical_keep_both"),
            forbidden_paths=tup("forbidden_paths"),
            human_only_checks=tup("human_only_checks"),
            max_unstick_per_sha=int(raw.get("max_unstick_per_sha", 2)),
            max_unstick_per_pr=int(raw.get("max_unstick_per_pr", 3)),
            needs_human_label=str(raw.get("needs_human_label", "needs-human")),
        )


# --------------------------------------------------------------------------
# Pure judgement. Everything below here is testable without a network.
# --------------------------------------------------------------------------

def normalise_checks(raw_checks: Iterable[dict] | None,
                     head_sha: str,
                     fixture_prefix: str) -> tuple[list[dict], int, int]:
    """Return (checks judged against head_sha, stale dropped, fixtures dropped).

    `raw_checks is None` means NOREAD -- the query answered with nothing at
    all -- and the caller must treat it as UNKNOWN, never as "no failures".
    """
    if raw_checks is None:
        raise MergeTrainError("check runs were not read")
    kept: list[dict] = []
    stale = 0
    fixtures = 0
    for c in raw_checks:
        name = str(c.get("name", ""))
        sha = str(c.get("head_sha") or c.get("sha") or "")
        # THE STALE-SHA GUARD. A check whose head_sha is not the PR's current
        # head describes a commit that no longer exists on this branch.
        if sha and head_sha and sha != head_sha:
            stale += 1
            continue
        if fixture_prefix and name.startswith(fixture_prefix):
            fixtures += 1
            continue
        kept.append(c)
    return kept, stale, fixtures


def check_state(check: dict) -> str:
    """'failing' | 'pending' | 'passing' for one check run or commit status."""
    status = str(check.get("status") or "").lower()
    conclusion = str(check.get("conclusion") or "").lower()
    if status and status in PENDING_STATUSES and not conclusion:
        return "pending"
    if not conclusion:
        # Finished with no conclusion recorded is not a pass.
        return "pending"
    if conclusion in FAILING_CONCLUSIONS:
        return "failing"
    if conclusion in PASSING_CONCLUSIONS:
        return "passing"
    # An unknown conclusion string is UNKNOWN, and UNKNOWN is never a pass.
    return "failing"


_BUMP = re.compile(
    r"from\s+v?(\d+)(?:\.\d+)*\S*\s+to\s+v?(\d+)(?:\.\d+)*",
    re.IGNORECASE,
)
_DEP_HINT = re.compile(r"^(deps|build\(deps|chore\(deps|bump )", re.IGNORECASE)


def is_dependency_pr(pr: dict) -> bool:
    labels = {str(l).lower() for l in pr.get("labels", ())}
    if {"dependencies", "deps"} & labels:
        return True
    if str(pr.get("author", "")).lower().startswith("dependabot"):
        return True
    return bool(_DEP_HINT.match(str(pr.get("title", ""))))


def is_major_bump(pr: dict) -> bool:
    """Hold a MAJOR dependency bump for a human. Patch and minor are fine.

    A dependency pull request whose version move cannot be parsed is HELD too.
    "I could not tell" is not "it is fine"; the same rule as everywhere else
    in these repos.
    """
    if not is_dependency_pr(pr):
        return False
    pairs = _BUMP.findall(str(pr.get("title", "")))
    if not pairs:
        return True
    return any(int(a) != int(b) for a, b in pairs)


def touches_plugin(files: Sequence[str] | None, plugin_paths: Sequence[str]) -> bool:
    if files is None:
        # Could not read the file list. Assume it does, so the deploy window
        # is honoured rather than skipped on an unknown.
        return bool(plugin_paths)
    return any(f.startswith(p) for f in files for p in plugin_paths)


def judge(pr: dict,
          raw_checks: Iterable[dict] | None,
          cfg: Config,
          *,
          last_deploy_at: datetime | None = None,
          now: datetime | None = None) -> Verdict:
    """Everything the train must be sure of before it merges, in one place."""
    now = now or datetime.now(timezone.utc)

    if str(pr.get("state", "")).upper() != "OPEN":
        return Verdict(SKIP_CLOSED, "not open")
    if pr.get("isDraft"):
        return Verdict(WAIT, "draft")

    labels = {str(l).lower() for l in pr.get("labels", ())}
    held = sorted(labels & {l.lower() for l in cfg.hold_labels})
    if held:
        return Verdict(HOLD, f"carries the {held[0]} label")
    if is_major_bump(pr):
        return Verdict(HOLD, "major dependency bump, held for a human")

    mergeable = str(pr.get("mergeable", "")).upper()
    if mergeable == "CONFLICTING":
        return Verdict(SKIP_CONFLICT, "conflicts with main")
    if mergeable != "MERGEABLE":
        # UNKNOWN means GitHub is still computing it. Waiting is the answer.
        return Verdict(WAIT, f"mergeable is {mergeable or 'UNREAD'}")

    checks, stale, fixtures = normalise_checks(raw_checks, str(pr.get("headRefOid", "")),
                                               cfg.fixture_prefix)
    failing = tuple(sorted({str(c.get("name")) for c in checks
                            if check_state(c) == "failing"}))
    pending = tuple(sorted({str(c.get("name")) for c in checks
                            if check_state(c) == "pending"}))
    # CHECK RUNS, NOT DISTINCT NAMES. Two jobs can legitimately share a name
    # -- both trackers run a `compare` job out of two different workflows --
    # and de-duplicating by name made the talent tracker's real total of 3
    # read as 2, which is under its own measured floor. A floor nothing can
    # ever reach is a train that never merges, and it would have looked like
    # "CI has not started" forever.
    counted = len(checks)

    if failing:
        return Verdict(SKIP_RED, f"{len(failing)} failing check(s)",
                       failing=failing, pending=pending, counted=counted,
                       stale_dropped=stale, fixture_dropped=fixtures)
    if pending:
        return Verdict(WAIT, f"{len(pending)} check(s) still running",
                       pending=pending, counted=counted,
                       stale_dropped=stale, fixture_dropped=fixtures)
    if counted < cfg.min_checks:
        # AN EMPTY OR TINY ROLLUP IS "CI HAS NOT STARTED", NOT "GREEN".
        return Verdict(WAIT,
                       f"only {counted} check(s) on this head SHA, floor is "
                       f"{cfg.min_checks}; CI has not started",
                       counted=counted, stale_dropped=stale,
                       fixture_dropped=fixtures)

    if cfg.plugin_paths and touches_plugin(pr.get("files"), cfg.plugin_paths):
        if last_deploy_at is None:
            return Verdict(WAIT, "the last plugin deploy time could not be read",
                           counted=counted, stale_dropped=stale,
                           fixture_dropped=fixtures)
        age = now - last_deploy_at
        window = timedelta(minutes=cfg.deploy_window_minutes)
        if age < window:
            mins = int((window - age).total_seconds() // 60) + 1
            return Verdict(WAIT,
                           f"touches the plugin and a deploy ran "
                           f"{int(age.total_seconds() // 60)} min ago; waiting "
                           f"{mins} more min",
                           counted=counted, stale_dropped=stale,
                           fixture_dropped=fixtures)

    return Verdict(READY, f"{counted} check(s) green on {str(pr.get('headRefOid',''))[:8]}",
                   counted=counted, stale_dropped=stale, fixture_dropped=fixtures)


def read_order_file(text: str | None) -> list[int]:
    """`.github/merge-train-order.txt`: PR numbers, one per line, # comments."""
    if not text:
        return []
    out: list[int] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            n = int(line)
        except ValueError:
            raise MergeTrainError(f"merge-train-order.txt has a non-number line: {line!r}")
        if n not in out:
            out.append(n)
    return out


def order_candidates(prs: Sequence[dict], pinned: Sequence[int]) -> list[dict]:
    """Pinned order first (in the file's order), then oldest-ready-first."""
    by_number = {int(p["number"]): p for p in prs}
    ordered: list[dict] = []
    for n in pinned:
        if n in by_number:
            ordered.append(by_number.pop(n))
    rest = sorted(by_number.values(), key=lambda p: (str(p.get("createdAt", "")),
                                                     int(p["number"])))
    return ordered + rest


# --------------------------------------------------------------------------
# The unstick guard rails. Pure, and mutation-proved by the tests.
# --------------------------------------------------------------------------

_SUPPRESSIONS = (
    "noqa", "nosemgrep", "type: ignore", "pylint: disable", "eslint-disable",
    "istanbul ignore", "pytest.mark.skip", "pytest.mark.xfail", "unittest.skip",
    "@pytest.mark.skip", "xfail", "it.skip(", "describe.skip(", "test.skip(",
    "@Disabled", "# pragma: no cover",
)

_THRESHOLD_WORDS = re.compile(
    r"(?i)(threshold|tolerance|ceiling|floor|limit|max_|min_|timeout|budget|"
    r"allowance|grace|alpha|window|factor|margin|deadline|retries|attempts)")

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_ASSERT = re.compile(r"(?i)\b(assert\w*|expect|self\.assert\w+|should\b)")



def path_matches(path: str, pattern: str) -> bool:
    """Does `path` fall under `pattern`?

    Three spellings, because the two repos that already keep a FORBIDDEN list
    write them differently and this must honour BOTH without either being
    rewritten: an exact path, a directory prefix ending in `/`, and a glob
    (`supabase/migrations/*`, which is how the sandbox's healer spells it).
    """
    if not pattern:
        return False
    if "*" in pattern or "?" in pattern:
        stem = pattern.rstrip("*").rstrip("/")
        return fnmatch(path, pattern) or (bool(stem) and (path == stem or path.startswith(stem + "/")))
    return path == pattern or path.startswith(pattern if pattern.endswith("/") else pattern + "/")


def any_match(path: str, patterns: Sequence[str]) -> bool:
    return any(path_matches(path, p) for p in patterns)


def _diff_files(diff_text: str) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", diff_text, re.MULTILINE)


def inspect_diff(diff_text: str,
                 cfg: Config,
                 *,
                 allowed_paths: Sequence[str] | None = None) -> list[str]:
    """Read a patch we are about to push and list every reason to refuse it.

    Empty list means nothing objectionable was found. This is the second of
    the two guards (the first is the path allowlist); it exists because a path
    can be allowlisted and the CONTENT still be the wrong shape.
    """
    refusals: list[str] = []
    allowed = tuple(allowed_paths) if allowed_paths is not None else cfg.mechanical_paths

    for path in _diff_files(diff_text):
        if any_match(path, cfg.forbidden_paths):
            refusals.append(f"touches a forbidden path: {path}")
        elif allowed and not any_match(path, allowed):
            refusals.append(f"touches a path outside the mechanical allowlist: {path}")

    removed = [l[1:] for l in diff_text.splitlines()
               if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in diff_text.splitlines()
             if l.startswith("+") and not l.startswith("+++")]

    for line in removed:
        if _ASSERT.search(line):
            refusals.append("removes an assertion: " + line.strip()[:80])
            break

    for line in added:
        low = line.lower()
        for token in _SUPPRESSIONS:
            if token.lower() in low:
                refusals.append("adds a suppression: " + line.strip()[:80])
                break
        else:
            continue
        break

    # A threshold move is a REPLACED line: the same named knob with a
    # different number on each side.
    for rem in removed:
        if not _THRESHOLD_WORDS.search(rem):
            continue
        rem_nums = _NUM.findall(rem)
        if not rem_nums:
            continue
        key = _THRESHOLD_WORDS.search(rem).group(0).lower()
        for add in added:
            if key not in add.lower():
                continue
            add_nums = _NUM.findall(add)
            if add_nums and add_nums != rem_nums:
                refusals.append("changes a threshold, tolerance or ceiling: "
                                + rem.strip()[:60] + " -> " + add.strip()[:60])
                break
        if refusals and refusals[-1].startswith("changes a threshold"):
            break

    return refusals


def conflict_plan(conflicted: Sequence[str], cfg: Config) -> tuple[dict[str, str], list[str]]:
    """Map each conflicted path to a mechanical strategy, or refuse.

    Returns (plan, escalations). A non-empty escalations list means the whole
    rebase is abandoned: a conflict in any file outside the mechanical set is
    untouched and handed to a human.
    """
    plan: dict[str, str] = {}
    escalate: list[str] = []
    for path in conflicted:
        if any_match(path, cfg.forbidden_paths):
            escalate.append(f"{path} (forbidden)")
            continue
        if any_match(path, cfg.mechanical_take_main):
            plan[path] = "take-main"
        elif any_match(path, cfg.mechanical_keep_both):
            plan[path] = "keep-both"
        else:
            escalate.append(path)
    return plan, escalate


def is_human_only(name: str, cfg: Config) -> bool:
    low = name.lower()
    return any(p.lower() in low for p in cfg.human_only_checks)


def classify_failures(checks: Sequence[dict], cfg: Config) -> tuple[list[dict], list[str]]:
    """(re-runnable infra-shaped completed checks, reasons a human is needed).

    A data-integrity or live-data check that is anything but green is a human's
    problem in EVERY shape, including a cancel: a wrong number already
    published is not something a re-run settles.
    """
    rerunnable: list[dict] = []
    human: list[str] = []
    for c in checks:
        if check_state(c) != "failing":
            continue
        name = str(c.get("name", ""))
        if is_human_only(name, cfg):
            human.append(f"{name} (data-integrity / live-data: never healed)")
            continue
        if str(c.get("status", "")).lower() != "completed":
            # NEVER RE-RUN AN IN-FLIGHT RUN. Re-running a live run cancels it.
            continue
        if str(c.get("conclusion", "")).lower() in INFRA_CONCLUSIONS:
            rerunnable.append(c)
        else:
            human.append(f"{name} (failing assertion or code defect)")
    return rerunnable, human


UNSTICK_MARKER = "<!-- merge-train:unstick"


def unstick_attempts(comments: Iterable[dict] | None, head_sha: str) -> tuple[int, int]:
    """(attempts on this head SHA, attempts on this pull request), from our own
    comments. Stateless on purpose: no ledger to drift, and the record of what
    was done is the thing that counts it."""
    if comments is None:
        raise MergeTrainError("pull request comments were not read")
    per_sha = 0
    per_pr = 0
    for c in comments:
        body = str(c.get("body", ""))
        if UNSTICK_MARKER not in body:
            continue
        per_pr += 1
        if head_sha and f"sha={head_sha}" in body:
            per_sha += 1
    return per_sha, per_pr


def may_unstick(verdict: Verdict, per_sha: int, per_pr: int, cfg: Config) -> tuple[bool, str]:
    if not cfg.unstick_enabled:
        return False, "unsticking is disabled for this repo"
    if verdict.state not in (SKIP_CONFLICT, SKIP_RED):
        return False, "nothing to unstick"
    if per_sha >= cfg.max_unstick_per_sha:
        return False, (f"{per_sha} unstick attempts on this head SHA, cap is "
                       f"{cfg.max_unstick_per_sha}")
    if per_pr >= cfg.max_unstick_per_pr:
        return False, (f"{per_pr} unstick attempts on this pull request, cap is "
                       f"{cfg.max_unstick_per_pr}")
    return True, ""


# --------------------------------------------------------------------------
# The client. One place that talks to GitHub and to git.
# --------------------------------------------------------------------------

def _run(cmd: Sequence[str], *, cwd: Path | None = None,
         check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(list(cmd), cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, input=input_text)
    if check and proc.returncode != 0:
        raise MergeTrainError(
            f"command failed ({proc.returncode}): {' '.join(cmd[:4])}...\n"
            f"{(proc.stderr or proc.stdout).strip()[:2000]}")
    return proc


class GitHubClient:
    """`gh` CLI, and it RAISES on anything unexpected rather than returning []."""

    def __init__(self, repo: str, root: Path):
        self.repo = repo
        self.root = root

    # -- reads ------------------------------------------------------------
    def _api(self, path: str, *, paginate: bool = False, method: str = "GET",
             fields: Sequence[str] = ()) -> Any:
        cmd = ["gh", "api"]
        if method != "GET":
            cmd += ["-X", method]
        if paginate:
            cmd += ["--paginate", "--slurp"]
        cmd.append(path)
        for f in fields:
            cmd += ["-f", f]
        proc = _run(cmd)
        text = proc.stdout.strip()
        if not text:
            raise MergeTrainError(f"empty response from gh api {path} (NOREAD, not zero)")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise MergeTrainError(f"gh api {path} did not return JSON: {exc}") from exc

    def open_pulls(self) -> list[dict]:
        proc = _run(["gh", "pr", "list", "-R", self.repo, "--state", "open",
                     "-L", "60", "--json",
                     "number,title,isDraft,mergeable,headRefOid,createdAt,labels,author,state,headRefName"])
        data = json.loads(proc.stdout or "[]")
        out = []
        for p in data:
            out.append({
                "number": p["number"],
                "title": p.get("title", ""),
                "isDraft": p.get("isDraft", False),
                "mergeable": p.get("mergeable", "UNKNOWN"),
                "headRefOid": p.get("headRefOid", ""),
                "headRefName": p.get("headRefName", ""),
                "createdAt": p.get("createdAt", ""),
                "state": p.get("state", "OPEN"),
                "labels": [l.get("name", "") for l in (p.get("labels") or [])],
                "author": (p.get("author") or {}).get("login", ""),
            })
        return out

    def pull_files(self, number: int) -> list[str]:
        pages = self._api(f"repos/{self.repo}/pulls/{number}/files?per_page=100",
                          paginate=True)
        files: list[str] = []
        for page in pages:
            for f in page:
                files.append(f["filename"])
        return files

    def checks_for(self, sha: str) -> list[dict]:
        """Check runs AND legacy commit statuses, for this exact SHA."""
        pages = self._api(
            f"repos/{self.repo}/commits/{sha}/check-runs?per_page=100&filter=latest",
            paginate=True)
        out: list[dict] = []
        for page in pages:
            if "check_runs" not in page:
                raise MergeTrainError("check-runs response had no check_runs key (NOREAD)")
            for c in page["check_runs"]:
                out.append({
                    "name": c.get("name", ""),
                    "status": c.get("status", ""),
                    "conclusion": c.get("conclusion") or "",
                    "head_sha": c.get("head_sha", ""),
                    "id": c.get("id"),
                    "run_id": ((c.get("check_suite") or {}).get("id")),
                    "details_url": c.get("details_url", ""),
                })
        status = self._api(f"repos/{self.repo}/commits/{sha}/status")
        for s in status.get("statuses", []):
            state = str(s.get("state", "")).lower()
            out.append({
                "name": s.get("context", ""),
                "status": "completed" if state in ("success", "failure", "error") else "pending",
                "conclusion": {"success": "success", "failure": "failure",
                               "error": "failure"}.get(state, ""),
                "head_sha": sha,
                "id": None,
                "run_id": None,
                "details_url": s.get("target_url", ""),
            })
        return out

    def comments(self, number: int) -> list[dict]:
        pages = self._api(f"repos/{self.repo}/issues/{number}/comments?per_page=100",
                          paginate=True)
        return [c for page in pages for c in page]

    def last_deploy_at(self, workflow_file: str) -> datetime | None:
        """When did the plugin deploy last RUN? Read, never guessed.

        An in-flight deploy returns 'now', which keeps the window shut.
        """
        if not workflow_file:
            return None
        data = self._api(
            f"repos/{self.repo}/actions/workflows/{workflow_file}/runs?per_page=20")
        if "total_count" not in data:
            raise MergeTrainError("deploy runs response had no total_count (NOREAD)")
        runs = data.get("workflow_runs") or []
        if not runs:
            # A genuine zero, positively read: this workflow has never run.
            return datetime.fromtimestamp(0, tz=timezone.utc)
        newest = None
        for r in runs:
            if str(r.get("status")) != "completed":
                return datetime.now(timezone.utc)
            for key in ("updated_at", "run_started_at", "created_at"):
                if r.get(key):
                    ts = datetime.fromisoformat(str(r[key]).replace("Z", "+00:00"))
                    newest = ts if newest is None or ts > newest else newest
                    break
        return newest

    def workflow_run_of_check(self, check: dict) -> int | None:
        url = str(check.get("details_url") or "")
        m = re.search(r"/actions/runs/(\d+)", url)
        return int(m.group(1)) if m else None

    # -- writes -----------------------------------------------------------
    def squash_merge(self, number: int) -> None:
        # PLAIN SQUASH, judged by this file and nothing else. Auto-merge
        # landed PR #954 untested and is not reachable from here.
        _run(["gh", "pr", "merge", str(number), "-R", self.repo, "--squash",
              "--delete-branch"])

    def comment(self, number: int, body: str) -> None:
        _run(["gh", "pr", "comment", str(number), "-R", self.repo, "--body", body])

    def add_label(self, number: int, label: str) -> None:
        """Label the pull request, creating the label if the repo lacks it.

        None of the three repos had a `needs-human` label when this shipped,
        and `gh pr edit --add-label` on a label that does not exist FAILS. The
        escalation would then have been a comment and an email with no visible
        mark on the pull request itself, which is the half a person actually
        scans. `--force` makes the create idempotent.
        """
        _run(["gh", "label", "create", label, "-R", self.repo, "--force",
              "--color", "B60205",
              "--description", "the merge train stopped here; a person is needed"],
             check=False)
        _run(["gh", "pr", "edit", str(number), "-R", self.repo, "--add-label", label],
             check=False)

    # -- main, after a merge ----------------------------------------------
    def main_head(self, branch: str) -> tuple[str, datetime]:
        data = self._api(f"repos/{self.repo}/commits/{branch}")
        sha = str(data.get("sha") or "")
        when = str(((data.get("commit") or {}).get("committer") or {}).get("date") or "")
        if not sha or not when:
            raise MergeTrainError(f"could not read the head of {branch} (NOREAD)")
        return sha, datetime.fromisoformat(when.replace("Z", "+00:00"))

    def workflow_has_run_for(self, workflow_file: str, sha: str) -> bool:
        data = self._api(
            f"repos/{self.repo}/actions/workflows/{workflow_file}/runs"
            f"?head_sha={sha}&per_page=1")
        if not isinstance(data, dict) or "total_count" not in data:
            # An empty read is not zero runs.
            raise MergeTrainError(f"{workflow_file} runs response had no total_count (NOREAD)")
        return int(data["total_count"]) > 0

    def dispatch(self, workflow_file: str, ref: str) -> None:
        # workflow_dispatch is the ONE event the default Actions token may
        # raise. A push made with it starts nothing, by GitHub's design.
        _run(["gh", "workflow", "run", workflow_file, "-R", self.repo, "--ref", ref])

    def rerun_failed(self, run_id: int) -> None:
        _run(["gh", "run", "rerun", str(run_id), "-R", self.repo, "--failed"])

    def run_status(self, run_id: int) -> str:
        data = self._api(f"repos/{self.repo}/actions/runs/{run_id}")
        return str(data.get("status", ""))


# --------------------------------------------------------------------------
# Notification. One door per repo, deduped BY CAUSE.
# --------------------------------------------------------------------------

def notify(cfg: Config, subject: str, body: str, dedupe_key: str) -> str:
    """Send one operational notice through whatever this repo already has."""
    door = os.environ.get("MERGE_TRAIN_NOTIFIER", "").strip() or _default_door()
    if door == "none":
        return "notification suppressed (MERGE_TRAIN_NOTIFIER=none)"
    if door == "ops_notify":
        try:
            import ops_notify  # noqa: F401  (repo-local, sits beside this file)
        except ImportError:
            return "ops_notify is not importable here; nothing sent"
        ok = ops_notify.notify(subject, body, dedupe_key=dedupe_key,
                               what="merge train notice")
        return "sent" if ok else "not sent"
    if door == "gh_issue":
        return _notify_gh_issue(cfg, subject, body, dedupe_key)
    return f"unknown notifier {door!r}"


def _default_door() -> str:
    here = Path(__file__).resolve().parent
    return "ops_notify" if (here / "ops_notify.py").exists() else "gh_issue"


def _notify_gh_issue(cfg: Config, subject: str, body: str, dedupe_key: str) -> str:
    """The sandbox's existing operational door: one issue per cause.

    Deduped by a marker in the body, exactly as the other watchers here do, so
    a pull request that is red for a week comments instead of opening a
    seventh issue.
    """
    marker = f"<!-- merge-train:{dedupe_key} -->"
    proc = _run(["gh", "issue", "list", "-R", cfg.repo, "--state", "open",
                 "--search", f'in:body "{marker}"', "--json", "number",
                 "--jq", ".[0].number // empty"], check=False)
    existing = (proc.stdout or "").strip()
    full = f"{body}\n\n{marker}"
    if existing:
        _run(["gh", "issue", "comment", existing, "-R", cfg.repo, "--body", full],
             check=False)
        return f"commented on issue #{existing}"
    _run(["gh", "issue", "create", "-R", cfg.repo, "--title", subject,
          "--body", full], check=False)
    return "opened an issue"


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    action: str = "nothing to do"
    merged: list[int] = field(default_factory=list)
    unstuck: list[int] = field(default_factory=list)
    dispatched: list[str] = field(default_factory=list)
    dry_run: bool = True

    def say(self, text: str) -> None:
        self.lines.append(text)
        print(text, flush=True)

    @property
    def summary(self) -> str:
        return self.action


def run(client: GitHubClient, cfg: Config, *, dry_run: bool = True,
        order_text: str | None = None, now: datetime | None = None,
        worktree: Path | None = None) -> Report:
    now = now or datetime.now(timezone.utc)
    rep = Report(dry_run=dry_run)
    rep.say(f"merge train: {cfg.repo}  dry_run={dry_run}  floor={cfg.min_checks}")

    prs = client.open_pulls()
    if not prs:
        rep.action = "no open pull requests"
        rep.say(rep.action)
        return rep

    pinned = read_order_file(order_text)
    if pinned:
        rep.say(f"pinned order: {', '.join(str(n) for n in pinned)}")
    candidates = order_candidates(prs, pinned)

    last_deploy: datetime | None = None
    verdicts: list[tuple[dict, Verdict]] = []

    for pr in candidates:
        number = int(pr["number"])
        # The plugin/deploy read is only needed for a pull request that has
        # got that far, so it is fetched lazily and at most once per run.
        if cfg.plugin_paths and pr.get("files") is None and not pr.get("isDraft"):
            try:
                pr["files"] = client.pull_files(number)
            except MergeTrainError:
                pr["files"] = None
        if cfg.plugin_paths and last_deploy is None:
            last_deploy = client.last_deploy_at(cfg.deploy_workflow)

        head = str(pr.get("headRefOid", ""))
        raw_checks: list[dict] | None
        if pr.get("isDraft") or str(pr.get("mergeable", "")).upper() != "MERGEABLE":
            raw_checks = []   # judged before checks are reached
        else:
            raw_checks = client.checks_for(head)
        v = judge(pr, raw_checks, cfg, last_deploy_at=last_deploy, now=now)
        verdicts.append((pr, v))
        rep.say(f"  #{number} {v.state}: {v.reason}"
                + (f"  [stale dropped {v.stale_dropped}]" if v.stale_dropped else "")
                + (f"  [fixture dropped {v.fixture_dropped}]" if v.fixture_dropped else ""))

        if v.state == READY:
            # ONE MERGE PER RUN, AND THEN WE EXIT. Not a `break` into more
            # work: the run is over.
            if dry_run:
                rep.action = f"WOULD MERGE #{number} ({v.reason})"
                rep.say(rep.action)
            else:
                client.squash_merge(number)
                rep.merged.append(number)
                rep.action = f"merged #{number} ({v.reason})"
                rep.say(rep.action)
                notify(cfg, f"merge train merged #{number}",
                       f"{pr.get('title','')}\n\n{v.reason}\n"
                       f"https://github.com/{cfg.repo}/pull/{number}",
                       dedupe_key=f"merge-train:merged:{number}")
            return rep

    # NOTHING WAS READY. Now, and only now, consider unsticking ONE.
    if cfg.unstick_enabled:
        for pr, v in verdicts:
            if v.state not in (SKIP_CONFLICT, SKIP_RED):
                continue
            if _try_unstick(client, cfg, pr, v, rep, dry_run=dry_run,
                            worktree=worktree):
                return rep

    _report_blocked(client, cfg, verdicts, rep, dry_run=dry_run)
    if rep.action == "nothing to do":
        rep.action = f"nothing ready to merge ({len(verdicts)} open)"
        rep.say(rep.action)
    return rep


def sync_main(client: GitHubClient, cfg: Config, rep: Report, *,
              dry_run: bool = True, now: datetime | None = None) -> list[str]:
    """START ON MAIN WHAT A TOKEN MERGE CANNOT START.

    The train merges with the default Actions token (MERGE_TRAIN_TOKEN does
    not exist), and GitHub never starts an `on: push` workflow for a push made
    with that token. So after a train merge tests.yml, card-contract.yml and
    style-standard.yml never ran on main, and main's colour was UNKNOWN while
    reading as "no failures".

    This is written as a RECONCILE, not as "after I merge": every tick it asks
    whether main's head carries a run of each listed workflow, so a dispatch
    that failed is caught by the next tick.

    TESTS ONLY. It never dispatches the plugin deploy; Config refuses a config
    that asks it to.

    Returns the problems it hit. It never raises and never undoes a merge.
    """
    now = now or datetime.now(timezone.utc)
    problems: list[str] = []
    if not cfg.post_merge_workflows:
        return problems
    try:
        head, head_at = client.main_head(cfg.main_branch)
    except MergeTrainError as exc:
        problems.append(f"main could not be read, nothing dispatched: {exc}")
        rep.say(f"  sync main: {problems[-1]}")
        return problems

    # A push by a person starts its own runs, a moment after the push. Only a
    # head old enough to have had that chance, or one this run made itself, is
    # treated as having been left without one.
    settled = bool(rep.merged) or (now - head_at) >= timedelta(minutes=cfg.push_grace_minutes)
    for wf in cfg.post_merge_workflows:
        try:
            if client.workflow_has_run_for(wf, head):
                continue
            if not settled:
                rep.say(f"  sync main: {wf} has no run on {head[:8]} yet, head is fresh; waiting")
                continue
            if dry_run:
                rep.say(f"  sync main: WOULD DISPATCH {wf} on {head[:8]} (no run on main's head)")
                continue
            client.dispatch(wf, cfg.main_branch)
            rep.dispatched.append(wf)
            rep.say(f"  sync main: dispatched {wf} on {head[:8]} (no run on main's head)")
        except MergeTrainError as exc:
            problems.append(f"{wf} dispatch UNKNOWN or FAILED: {exc}")
            rep.say(f"  sync main: {problems[-1]}")
    return problems


def _report_blocked(client: GitHubClient, cfg: Config,
                    verdicts: Sequence[tuple[dict, Verdict]], rep: Report,
                    *, dry_run: bool) -> None:
    """Red and conflicting pull requests are REPORTED, never fixed, never forced."""
    for pr, v in verdicts:
        if v.state not in (SKIP_RED, SKIP_CONFLICT):
            continue
        number = int(pr["number"])
        head = str(pr.get("headRefOid", ""))
        if v.state == SKIP_RED:
            subject = f"merge train: #{number} is red"
            detail = "failing: " + ", ".join(v.failing)
        else:
            subject = f"merge train: #{number} conflicts with main"
            detail = "conflicts with main; not fixed, not forced"
        body = (f"{pr.get('title','')}\n"
                f"https://github.com/{cfg.repo}/pull/{number}\n"
                f"head {head[:8]}\n{detail}\n\n"
                "The merge train skipped it. It never fixes a red pull request "
                "and never force-merges one.")
        # DEDUPED BY CAUSE, NOT BY RUN: the failing check names (or the
        # conflict) plus the pull request. Red for a week is one notice.
        cause = "|".join(v.failing) if v.failing else "conflict"
        key = f"merge-train:blocked:{number}:{abs(hash(cause)) % (10**10)}"
        if dry_run:
            rep.say(f"  would notify: {subject} [{key}]")
        else:
            rep.say(f"  notify: {subject}: {notify(cfg, subject, body, key)}")


def _try_unstick(client: GitHubClient, cfg: Config, pr: dict, v: Verdict,
                 rep: Report, *, dry_run: bool, worktree: Path | None) -> bool:
    """Bookkeeping only. Returns True if this run's one action was used."""
    number = int(pr["number"])
    head = str(pr.get("headRefOid", ""))
    per_sha, per_pr = unstick_attempts(client.comments(number), head)
    ok, why = may_unstick(v, per_sha, per_pr, cfg)
    if not ok:
        rep.say(f"  #{number} not unstuck: {why}")
        if per_sha >= cfg.max_unstick_per_sha or per_pr >= cfg.max_unstick_per_pr:
            _escalate(client, cfg, number, rep,
                      f"the unstick cap is reached ({per_sha} on this head SHA, "
                      f"{per_pr} on this pull request). Stopping permanently.",
                      dry_run=dry_run)
        return False

    if v.state == SKIP_RED:
        checks = client.checks_for(head)
        kept, _, _ = normalise_checks(checks, head, cfg.fixture_prefix)
        rerunnable, human = classify_failures(kept, cfg)
        if human:
            _escalate(client, cfg, number, rep,
                      "a human is needed: " + "; ".join(human), dry_run=dry_run)
            return False
        if not rerunnable:
            rep.say(f"  #{number} has nothing infrastructure-shaped to re-run")
            return False
        target = rerunnable[0]
        run_id = client.workflow_run_of_check(target)
        if run_id is None:
            rep.say(f"  #{number} could not resolve a run id for "
                    f"{target.get('name')}")
            return False
        # NEVER RE-RUN A LIVE RUN. Confirm it is completed at the run level too.
        if client.run_status(run_id) != "completed":
            rep.say(f"  #{number} run {run_id} is still in flight; not re-run")
            return False
        note = (f"{UNSTICK_MARKER} sha={head} action=rerun -->\n"
                f"`{target.get('name')}` concluded "
                f"`{target.get('conclusion')}`, which is infrastructure-shaped "
                f"(a killed runner, a busy box, a network error), not a failing "
                f"assertion. Re-running that run once. Attempt "
                f"{per_sha + 1}/{cfg.max_unstick_per_sha} on this head SHA.")
        if dry_run:
            rep.action = (f"WOULD RE-RUN run {run_id} for #{number} "
                          f"({target.get('name')} = {target.get('conclusion')})")
            rep.say(rep.action)
        else:
            client.rerun_failed(run_id)
            client.comment(number, note)
            rep.unstuck.append(number)
            rep.action = f"re-ran run {run_id} for #{number}"
            rep.say(rep.action)
        return True

    # SKIP_CONFLICT -> a mechanical rebase, or nothing.
    return _try_rebase(client, cfg, pr, rep, dry_run=dry_run, worktree=worktree,
                       attempt=(per_sha + 1))


def _try_rebase(client: GitHubClient, cfg: Config, pr: dict, rep: Report, *,
                dry_run: bool, worktree: Path | None, attempt: int) -> bool:
    number = int(pr["number"])
    head = str(pr.get("headRefOid", ""))
    branch = str(pr.get("headRefName", ""))
    root = worktree or Path.cwd()
    if not branch:
        rep.say(f"  #{number} has no head branch name; not rebased")
        return False

    push_token = os.environ.get("MERGE_TRAIN_PUSH_TOKEN", "").strip()

    _run(["git", "fetch", "origin", "main", branch], cwd=root, check=False)
    _run(["git", "checkout", "-B", f"mt/{branch}", f"origin/{branch}"], cwd=root)
    before_files = set(_changed_files(root, "origin/main", "HEAD"))

    proc = _run(["git", "rebase", "origin/main"], cwd=root, check=False)
    resolved: dict[str, str] = {}
    if proc.returncode != 0:
        conflicted = _conflicted_paths(root)
        plan, escalate = conflict_plan(conflicted, cfg)
        if escalate or not plan:
            _run(["git", "rebase", "--abort"], cwd=root, check=False)
            _escalate(client, cfg, number, rep,
                      "the rebase conflicts outside the mechanical set "
                      f"({', '.join(escalate) or 'nothing resolvable'}); left "
                      "untouched for a human.", dry_run=dry_run)
            return False
        for path, how in plan.items():
            _resolve_mechanical(root, path, how)
            resolved[path] = how
        _run(["git", "add", "--"] + list(plan), cwd=root)
        cont = _run(["git", "-c", "core.editor=true", "rebase", "--continue"],
                    cwd=root, check=False)
        if cont.returncode != 0:
            _run(["git", "rebase", "--abort"], cwd=root, check=False)
            _escalate(client, cfg, number, rep,
                      "the rebase did not complete after the mechanical "
                      "resolution; left untouched for a human.", dry_run=dry_run)
            return False

    after_files = set(_changed_files(root, "origin/main", "HEAD"))
    new_paths = sorted(after_files - before_files)
    allowed = cfg.mechanical_paths
    stray = [p for p in new_paths if not any_match(p, allowed)]
    if stray:
        _run(["git", "rebase", "--abort"], cwd=root, check=False)
        _escalate(client, cfg, number, rep,
                  f"the rebase would change files this pull request never "
                  f"touched: {', '.join(stray[:5])}", dry_run=dry_run)
        return False

    touched = sorted(resolved) or new_paths
    diff = ""
    if touched:
        diff = _run(["git", "diff", head, "HEAD", "--"] + touched,
                    cwd=root, check=False).stdout
    refusals = inspect_diff(diff, cfg, allowed_paths=allowed) if diff else []
    if refusals:
        _run(["git", "rebase", "--abort"], cwd=root, check=False)
        _escalate(client, cfg, number, rep,
                  "the patch it would have pushed has a shape it must never "
                  "push: " + "; ".join(refusals), dry_run=dry_run)
        return False

    note = (f"{UNSTICK_MARKER} sha={head} action=rebase -->\n"
            f"Rebased on `main`. "
            + (f"Mechanical conflict resolution: "
               f"{', '.join(f'{p} ({h})' for p, h in sorted(resolved.items()))}. "
               if resolved else "No conflicts outside the fast-forward. ")
            + "No program logic was changed and no test was touched. "
            f"Attempt {attempt}/{cfg.max_unstick_per_sha} on this head SHA.")

    if dry_run:
        rep.action = f"WOULD REBASE AND PUSH #{number} ({branch})"
        rep.say(rep.action)
        rep.say("  --- the exact diff it would push ---")
        for line in (diff or "(no textual change beyond the rebase)").splitlines()[:200]:
            rep.say("  " + line)
        _run(["git", "rebase", "--abort"], cwd=root, check=False)
        _run(["git", "checkout", "-"], cwd=root, check=False)
        return True

    if not push_token:
        # A push made with GITHUB_TOKEN does not start workflows, so the
        # rebased head would carry ZERO checks and the train would wait on it
        # forever. Refusing is the honest answer.
        _escalate(client, cfg, number, rep,
                  "it can rebase this cleanly, but no MERGE_TRAIN_PUSH_TOKEN is "
                  "configured. A push made with the default Actions token does "
                  "not start workflows, so the rebased head would carry no "
                  "checks at all. Left for a human.", dry_run=False)
        _run(["git", "rebase", "--abort"], cwd=root, check=False)
        return False

    _run(["git", "push", "--force-with-lease", "origin", f"HEAD:{branch}"], cwd=root)
    client.comment(number, note)
    rep.unstuck.append(number)
    rep.action = f"rebased and pushed #{number}"
    rep.say(rep.action)
    return True


def _changed_files(root: Path, base: str, head: str) -> list[str]:
    out = _run(["git", "diff", "--name-only", f"{base}...{head}"], cwd=root,
               check=False).stdout
    return [l for l in out.splitlines() if l.strip()]


def _conflicted_paths(root: Path) -> list[str]:
    out = _run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=root,
               check=False).stdout
    return [l for l in out.splitlines() if l.strip()]


def _resolve_mechanical(root: Path, path: str, how: str) -> None:
    """The only two resolutions this thing knows.

    During a rebase `--ours` is the branch being rebased ONTO, which is main.
    """
    if how == "take-main":
        _run(["git", "checkout", "--ours", "--", path], cwd=root)
        return
    # keep-both: an append-only log. Keep every entry from both sides, in
    # order, and drop only the conflict fences.
    p = root / path
    text = p.read_text(encoding="utf-8", errors="replace")
    kept = [l for l in text.splitlines()
            if not (l.startswith("<<<<<<<") or l.startswith("=======")
                    or l.startswith(">>>>>>>") or l.startswith("|||||||"))]
    p.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _escalate(client: GitHubClient, cfg: Config, number: int, rep: Report,
              why: str, *, dry_run: bool) -> None:
    body = (f"{UNSTICK_MARKER} sha=escalated action=needs-human -->\n"
            f"The merge train stopped here and is not going to try again "
            f"automatically.\n\n{why}")
    if dry_run:
        rep.say(f"  would label #{number} `{cfg.needs_human_label}`: {why}")
        return
    client.add_label(number, cfg.needs_human_label)
    client.comment(number, body)
    rep.say(f"  labelled #{number} `{cfg.needs_human_label}`: {why}")
    notify(cfg, f"merge train: #{number} needs a human",
           f"https://github.com/{cfg.repo}/pull/{number}\n\n{why}",
           dedupe_key=f"merge-train:needs-human:{number}")


# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="", help="path to .github/merge-train.json")
    ap.add_argument("--dry-run", dest="dry_run", default=None,
                    help="true/false; default true")
    ap.add_argument("--repo", default="", help="override owner/name")
    args = ap.parse_args(argv)

    root = Path(os.environ.get("GITHUB_WORKSPACE") or Path.cwd())
    cfg_path = Path(args.config) if args.config else root / ".github" / "merge-train.json"
    cfg = Config.load(cfg_path)
    if args.repo:
        cfg = Config.from_dict({**json.loads(cfg_path.read_text()), "repo": args.repo})

    raw = args.dry_run if args.dry_run is not None else os.environ.get("MERGE_TRAIN_DRY_RUN", "true")
    dry_run = str(raw).strip().lower() not in ("false", "0", "no")

    order_file = root / ".github" / "merge-train-order.txt"
    order_text = order_file.read_text(encoding="utf-8") if order_file.exists() else None

    client = GitHubClient(cfg.repo, root)
    try:
        rep = run(client, cfg, dry_run=dry_run, order_text=order_text, worktree=root)
    except MergeTrainError as exc:
        # FAIL LOUDLY. A quiet exit 0 on an API error is a train that looks
        # healthy while it merges nothing.
        print(f"::error::merge train could not complete: {exc}")
        _write_summary(f"merge train FAILED: {exc}")
        return 1

    problems = sync_main(client, cfg, rep, dry_run=dry_run)
    tail = f"; dispatched {', '.join(rep.dispatched)}" if rep.dispatched else ""
    _write_summary(rep.summary + tail + ("  (dry run)" if dry_run else ""))
    if problems:
        # LOUD, AND AFTER THE FACT. The merge stands; the run goes red so the
        # alerter mails it, and the next tick reconciles main again.
        for p in problems:
            print(f"::error::merge train sync main: {p}")
        _write_summary("sync main FAILED: " + " | ".join(problems))
        return 1
    return 0


def _write_summary(line: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"### Merge train\n\n{line}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
