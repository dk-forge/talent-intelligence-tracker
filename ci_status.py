#!/usr/bin/env python3
"""Is GitHub Actions red? Run it first, every session, beside ops_status.py.

`ops_status.py` is the offline authority — it reads the database, the collectors,
the writer queue and the link ledger, and it needs no network and no key. That
property is deliberate and worth keeping, which is exactly why it cannot answer
this question: **nothing in it knows whether a workflow run failed.**

So a session opened, ran ops_status, read ALL CLEAR, and worked for hours beside
a repo whose `tests` had been red on main the whole time, whose `enrich` had died
on a read timeout, and whose sibling had a dozen red runs. The owner was reading
the failure emails; the session was reading a green report. This file closes
that gap, and it is a separate command because it needs `gh`, a credential and a
network — the three things ops_status must never need.

It reports, for BOTH trackers:

  * every workflow whose newest run on the default branch is red, at any age —
    that is the state that persists and the one that matters;
  * every failure inside a recent window, including ones since recovered, so a
    flapping job is visible before it becomes a permanent one;
  * every run that ended `cancelled` having created ZERO jobs. That is the
    eviction signature this project has been bitten by repeatedly: a run pushed
    out of the concurrency group's single pending slot, with no steps, no logs
    and no annotation anywhere. It is invisible in the GitHub UI and it is the
    reason this file exists at all.

Exit codes:  0 green | 2 something needs a human | 3 COULD NOT CHECK

Three is the whole point of three. "I could not reach GitHub" must never render
as "everything is fine" — that is the same false-healthy failure this project
keeps finding, and it is the one an exit code can actually prevent.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import writer_queue
import writer_queue_runs
from writer_queue_runs import GhUnavailable

#: Both trackers. They share a host and nothing else — no code, no database —
#: so this is a list of names and not an import of the sibling's anything. The
#: sibling gets watched from here because the owner watches both inboxes and
#: this is the tool that reads run state; if the sibling ever wants its own, it
#: is a separate implementation in its own repo.
REPOS = (
    ("dk-forge/talent-intelligence-tracker", "this repo"),
    ("dk-forge/ai-layoff-tracker", "sibling — AI Layoff Tracker"),
)

#: What counts as red. `failure` is the common one; `timed_out` and
#: `startup_failure` are rarer and worse — the second means the workflow file
#: itself would not parse, so the job never existed.
RED = ("failure", "timed_out", "startup_failure")

FIELDS = ("databaseId,workflowName,status,conclusion,createdAt,updatedAt,"
          "event,headBranch,url")

#: How far back a failure is still worth printing as news. Anything red NOW is
#: reported regardless of age; this only bounds the "and these also failed"
#: list, which is context rather than an alarm.
WINDOW_HOURS = 24

#: How many rows to ask for per query. The filters make this go a long way: on
#: a repo doing forty runs an hour an unfiltered 100 covers four hours, while
#: 40 *failures* usually covers weeks.
LIMIT = 40


# --------------------------------------------------------------------------
# pure — everything here takes run dicts and returns a verdict
# --------------------------------------------------------------------------

def is_red(run: dict) -> bool:
    return run.get("conclusion") in RED


def was_evicted(run: dict) -> bool:
    """Cancelled having created no jobs — displaced from the pending slot.

    `writer_queue.never_started` is the definition, verified against the seven
    runs lost on 2026-07-29, and it is imported rather than restated so the two
    tools cannot drift apart about what an eviction looks like. A run whose job
    count could NOT be read is `None` there and is not called an eviction, which
    is the right way round: unknown is not zero.
    """
    return (run.get("conclusion") == "cancelled"
            and run.get("job_count") is not None
            and writer_queue.never_started(run))


def _parse(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _ago(stamp: str | None, now: datetime) -> str:
    moment = _parse(stamp)
    if not moment:
        return "?"
    hours = (now - moment).total_seconds() / 3600
    if hours < 1:
        return f"{hours * 60:.0f}m ago"
    if hours < 48:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.0f}d ago"


def assess(repo: str, *, failures: list[dict], cancelled: list[dict],
           latest: dict[str, dict | None], default_branch: str,
           lock_group: set[str] | None = None,
           already_recorded: set[str] | None = None,
           truncated: bool = False,
           window_hours: int = WINDOW_HOURS,
           now: datetime | None = None) -> dict:
    """Turn three run lists into a verdict. No network, no gh, no I/O.

    `latest` maps a workflow name to its newest run on the default branch, or
    None when that could not be read. A workflow is RED NOW when that newest run
    is itself red — which is the only claim worth exiting 2 over, because a
    failure with a green run after it has already been answered by somebody.

    `lock_group` is the set of workflows sharing the database-writer lock, and
    it is what turns 24 evictions into the one or two that mean something. Not
    every eviction is a loss: `drain-writers` sits in its own group and says so
    in its own concurrency comment — a tick that loses its slot reconciles from
    scratch next time and costs a cycle, no data. A WRITER evicted from
    `talent-collect` is the unreplayable one. None means "this repo's groups are
    unknown to us", which is the sibling, and there every eviction is reported
    rather than assumed harmless.

    `already_recorded` is the run ids the writer queue has already booked as
    orphans. Those are ops_status [2b]'s to raise; repeating them here is how
    seventeen resolved incidents from two days ago drown one from this morning.
    """
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(hours=window_hours)
    booked = already_recorded or set()

    red_now, recovered, unknown = [], [], []
    for name in sorted({run.get("workflowName", "?") for run in failures
                        if run.get("headBranch") == default_branch}):
        newest = latest.get(name)
        if newest is None:
            unknown.append(name)
        elif is_red(newest):
            red_now.append(newest)
        else:
            recovered.append(name)

    recent = [run for run in failures
              if (_parse(run.get("createdAt")) or moment) >= cutoff]
    off_branch = sorted({run.get("workflowName", "?") for run in recent
                         if run.get("headBranch") != default_branch})

    fresh = [run for run in cancelled
             if (_parse(run.get("createdAt")) or moment) >= cutoff]
    evicted = [run for run in fresh if was_evicted(run)]
    older = len(cancelled) - len(fresh)
    unreadable = [run for run in fresh if run.get("job_count") is None
                  and run.get("conclusion") == "cancelled"]

    lost, benign, recorded = [], [], []
    for run in evicted:
        if str(run.get("databaseId")) in booked:
            recorded.append(run)
        elif lock_group is None or run.get("workflowName") in lock_group:
            lost.append(run)
        else:
            benign.append(run)

    problems = []
    for run in red_now:
        problems.append(
            f"{repo}: {run.get('workflowName')} is RED on {default_branch} "
            f"({_ago(run.get('createdAt'), moment)}, run "
            f"{run.get('databaseId')}) and nothing has gone green since")
    for name in unknown:
        problems.append(
            f"{repo}: {name} failed and its current state COULD NOT BE READ, "
            f"which is not the same as recovered — check it by hand")
    for run in lost:
        problems.append(
            f"{repo}: {run.get('workflowName')} run {run.get('databaseId')} "
            f"ended cancelled having created ZERO jobs "
            f"({_ago(run.get('createdAt'), moment)}) and the writer queue never "
            f"recorded it. That is an eviction from the single pending slot: "
            f"invisible in the UI, and GitHub does not expose a dispatched "
            f"run's inputs, so decide by hand what it was doing rather than "
            f"re-dispatching with defaults")

    return {
        "repo": repo,
        "default_branch": default_branch,
        "red_now": red_now,
        "recovered": recovered,
        "unknown": unknown,
        "recent": recent,
        "off_branch": off_branch,
        "evicted": evicted,
        "lost": lost,
        "benign": benign,
        "recorded": recorded,
        "older_cancelled": older,
        "unreadable": unreadable,
        "truncated": truncated,
        "problems": problems,
    }


# --------------------------------------------------------------------------
# the network half
# --------------------------------------------------------------------------

def local_lock_group() -> set[str]:
    """The workflows sharing this repo's database-writer lock, by display name.

    Straight out of writer_queue, which parses the workflow files. Only valid
    for THIS repo — the sibling shares no code and its groups are not knowable
    from here, which is why read_repo passes None for it.
    """
    return set(writer_queue.lock_group_workflows().values())


def recorded_orphans() -> set[str]:
    """Run ids the writer queue has already booked, resolved or not."""
    from pathlib import Path

    path = Path(__file__).resolve().parent / "data" / "writer_queue.json"
    if not path.exists():
        return set()
    return {str(o.get("run_id")) for o in writer_queue.load(path).get("orphans", [])}


def read_repo(repo: str, *, limit: int = LIMIT,
              window_hours: int = WINDOW_HOURS, local: bool = False,
              now: datetime | None = None) -> dict:
    """Ask GitHub about one repo. Raises GhUnavailable if it cannot be asked."""
    moment = now or datetime.now(timezone.utc)

    # The default branch is asked rather than assumed: guessing "main" and
    # guessing wrong would make every workflow look off-branch, and off-branch
    # is the bucket this tool deliberately does NOT go red on. A wrong guess
    # here would print an all-clear over a red main.
    with ThreadPoolExecutor(max_workers=len(RED) + 2) as pool:
        branch_call = pool.submit(
            writer_queue_runs._gh,
            ["repo", "view", repo, "--json", "defaultBranchRef",
             "--jq", ".defaultBranchRef.name"])
        failure_calls = [
            pool.submit(writer_queue_runs.run_list, repo=repo, limit=limit,
                        fields=FIELDS, status=conclusion)
            for conclusion in RED]
        cancelled_call = pool.submit(
            writer_queue_runs.run_list, repo=repo, limit=limit, fields=FIELDS,
            status="cancelled")

        branch = branch_call.result().strip() or "main"
        failures: list[dict] = []
        truncated = False
        for call in failure_calls:
            page = call.result()
            truncated = truncated or len(page) >= limit
            failures += page
        cancelled = cancelled_call.result()
    failures.sort(key=lambda r: r.get("createdAt") or "", reverse=True)

    # The job count costs one API call per cancelled run, so it is bought only
    # for the ones inside the window. Two days of this repo's cancellations was
    # eighty calls and half a minute at a session-start prompt nobody would then
    # run twice.
    cutoff = moment - timedelta(hours=window_hours)
    writer_queue_runs.attach_job_counts(
        [run for run in cancelled
         if (_parse(run.get("createdAt")) or moment) >= cutoff], repo)

    # One targeted question per workflow that has ever failed here, rather than
    # a big list filtered locally: the answer must be right for a workflow whose
    # last failure was days ago, and a paged list cannot promise that. They are
    # independent reads of the same API, so they go in parallel — sequentially
    # this was the slowest part of a command meant to run at every session
    # start, and a check nobody waits for is a check nobody runs.
    def newest(name: str) -> dict | None:
        try:
            rows = writer_queue_runs.run_list(
                repo=repo, limit=1, fields=FIELDS, workflow=name, branch=branch)
        except GhUnavailable:
            raise
        except RuntimeError:
            # A renamed or deleted workflow answers 404 here. Unknown, not green.
            return None
        return rows[0] if rows else None

    names = sorted({run.get("workflowName", "") for run in failures
                    if run.get("headBranch") == branch and run.get("workflowName")})
    latest: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, result in zip(names, pool.map(newest, names)):
            latest[name] = result

    return assess(repo, failures=failures, cancelled=cancelled, latest=latest,
                  default_branch=branch,
                  lock_group=local_lock_group() if local else None,
                  already_recorded=recorded_orphans() if local else set(),
                  truncated=truncated,
                  window_hours=window_hours, now=moment)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render(report: dict, index: int, label: str, window_hours: int,
           now: datetime) -> None:
    print(f"\n[{index}] {report['repo']}  ({label})")

    if report["red_now"]:
        print(f"    RED NOW — newest run on {report['default_branch']} is red, "
              f"nothing green since:")
        for run in report["red_now"]:
            print(f"      {run.get('workflowName', '?'):<26} "
                  f"{run.get('conclusion'):<15} "
                  f"{_ago(run.get('createdAt'), now):>8}  "
                  f"run {run.get('databaseId')}")
            if run.get("url"):
                print(f"        {run['url']}")
    else:
        print(f"    RED NOW   none — every workflow that has failed recently "
              f"has since gone green")

    for name in report["unknown"]:
        print(f"    UNKNOWN   {name}: failed, and its current state could not "
              f"be read")

    recent = report["recent"]
    print(f"    last {window_hours}h: {len(recent)} red run(s)"
          + (f" across {len({r.get('workflowName') for r in recent})} workflow(s)"
             if recent else ""))
    for run in recent[:8]:
        branch = run.get("headBranch") or "?"
        mark = "" if branch == report["default_branch"] else f"  [{branch}]"
        print(f"      {_ago(run.get('createdAt'), now):>8}  "
              f"{run.get('workflowName', '?'):<26} {run.get('conclusion')}"
              f"  run {run.get('databaseId')}{mark}")
    if len(recent) > 8:
        print(f"      ... and {len(recent) - 8} more")
    if report["recovered"]:
        print(f"      recovered since: {', '.join(report['recovered'])}")
    if report["off_branch"]:
        print(f"      (failures off {report['default_branch']} are listed but "
              f"not counted red: {', '.join(report['off_branch'])})")

    if report["lost"]:
        print("    EVICTED — cancelled with ZERO jobs, and the writer queue "
              "never recorded it.")
        print("              Displaced from the single pending slot: no steps, "
              "no logs, and")
        print("              nothing anywhere in the UI says work was lost.")
        for run in report["lost"]:
            print(f"      {run.get('workflowName', '?'):<26} "
                  f"{_ago(run.get('createdAt'), now):>8}  "
                  f"run {run.get('databaseId')}")
    else:
        print(f"    EVICTED   no unrecorded writer eviction in the last "
              f"{window_hours}h")
    if report["recorded"]:
        print(f"      {len(report['recorded'])} more the writer queue already "
              f"booked as orphans — ops_status.py [2b] owns those")
    if report["benign"]:
        names = ", ".join(sorted({r.get("workflowName", "?")
                                  for r in report["benign"]}))
        print(f"      {len(report['benign'])} eviction(s) outside the writer "
              f"lock ({names}): a lost")
        print("      slot there costs a cycle, not data — each of those groups "
              "reconciles")
        print("      from scratch on its next run.")
    if report["unreadable"]:
        print(f"    {len(report['unreadable'])} cancelled run(s) would not "
              f"report a job count, so whether they were evicted is UNKNOWN")
    if report["older_cancelled"]:
        print(f"    ({report['older_cancelled']} cancelled run(s) older than "
              f"{window_hours}h were not examined — a job count costs a call "
              f"each)")

    if report["truncated"]:
        print(f"    (the failure list came back full at {LIMIT} rows, so there "
              f"are older red runs this did not read — raise --limit)")


def _report_writer_queue(now: datetime) -> None:
    """The queue's own state, from the same module ops_status reads.

    Printed here because an eviction above and an orphan in the queue are the
    same event seen from two sides, and reading one without the other is how
    a lost run gets written off as noise. It is NOT counted into this tool's
    exit code: `ops_status.py [2b]` already exits 2 on exactly these, and one
    problem raising two alarms is how an alarm stops being read.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parent / "data" / "writer_queue.json"
    print("\n[Q] WRITER QUEUE  (the other half of the eviction story)")
    if not path.exists():
        print("    no queue file in this checkout — see ops_status.py [2b]")
        return

    state = writer_queue.summary(writer_queue.load(path))
    counts = ", ".join(f"{k}={v}" for k, v in sorted(state["counts"].items()))
    print(f"    tickets: {counts or 'none'}")
    if state.get("last_tick"):
        print(f"    last drain tick: {state['last_tick']} "
              f"({_ago(state['last_tick'], now)})")
    for orphan in state["orphans"]:
        print(f"    ORPHAN   {orphan['workflow']} run {orphan['run_id']}")
    if state["problems"]:
        print(f"    {len(state['problems'])} queue problem(s) — ops_status.py "
              f"[2b] is the authority and exits 2 on them:")
        for problem in state["problems"]:
            print(f"      -> {problem}")
    else:
        print("    no queue problems.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=int, default=WINDOW_HOURS,
                        help="how far back a recovered failure is still news")
    parser.add_argument("--limit", type=int, default=LIMIT,
                        help="rows per query")
    parser.add_argument("--repo", action="append", default=None,
                        help="check only this repo (repeatable)")
    parser.add_argument("--no-queue", action="store_true",
                        help="skip the writer-queue section")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    labels = dict(REPOS)
    wanted = [(r, labels.get(r, "requested"))
              for r in (args.repo or [name for name, _ in REPOS])]

    print("=" * 64)
    print("CI STATUS — GitHub Actions, both trackers")
    print("=" * 64)
    print(f"    as of {now.isoformat(timespec='seconds')}   "
          f"window {args.hours}h")
    print("    ops_status.py is the offline authority on the DATA; this is the")
    print("    only thing that knows whether the runs behind it went red.")

    problems: list[str] = []
    for index, (repo, label) in enumerate(wanted, start=1):
        try:
            report = read_repo(repo, limit=args.limit, window_hours=args.hours,
                               local=(repo == REPOS[0][0]), now=now)
        except GhUnavailable as exc:
            return _cannot_check(exc)
        except RuntimeError as exc:
            # Deterministic and repo-specific — a 404 on a repo name, say. That
            # is a broken check rather than a broken network, and it is still
            # not an all-clear.
            print(f"\n[{index}] {repo}  ({label})")
            print(f"    COULD NOT READ: {exc}")
            problems.append(f"{repo} could not be read: {exc}")
            continue
        render(report, index, label, args.hours, now)
        problems += report["problems"]

    if not args.no_queue:
        _report_writer_queue(now)

    print("\n" + "-" * 64)
    if problems:
        print(f"ACTION NEEDED: {len(problems)} item(s)")
        for problem in problems:
            print(f"  -> {problem}")
        return 2
    print("All green.")
    return 0


def _cannot_check(exc: Exception) -> int:
    """Exit 3, loudly. The one thing this must never look like is exit 0."""
    print("\n" + "-" * 64)
    print("COULD NOT CHECK — this is NOT an all-clear.")
    print(f"    {exc}")
    print("    Nothing above tells you whether Actions is green, because")
    print("    GitHub was never asked. Fix one of:")
    print("      gh --version          # brew install gh")
    print("      gh auth status        # gh auth login")
    print("      curl -sSf https://api.github.com >/dev/null   # network")
    print("    ops_status.py still works offline and is unaffected.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
