#!/usr/bin/env python3
"""Backfills that run in bounded slices and put themselves back in the queue.

THE FAILURE THIS EXISTS TO KILL
-------------------------------
On 2026-07-29 `backfill-gdelt-2026` took the single `talent-collect` writer
lock at 04:59 UTC, ran for 350 minutes, hit its own `timeout-minutes: 350`,
and was CANCELLED. Its "Commit the database" step is guarded by
`if: !cancelled()`, so it was SKIPPED, and roughly six hours of collection
existed only on a runner that was then deleted. The same six hours starved
every correction queued behind it, because one lock plus one long holder is
one long wait.

Two fixes landed that day and neither of them can fix this. Priority ordering
decides who goes NEXT; **it cannot preempt a job already running**. Starvation
reporting says the lock has been held for two hours; saying so does not hand it
back. The only thing that actually bounds a lock hold is a job that finishes.

SO: A BACKFILL IS A CHAIN OF SHORT RUNS, NOT ONE LONG ONE
--------------------------------------------------------
Each run takes ONE slice of the work, does it, COMMITS it, and appends a ticket
for the next slice to `data/writer_queue.json` in the same commit. The run then
ends, the group empties, `drain-writers.yml` fires on its `workflow_run:
completed` trigger, and the next slice is dispatched — behind whatever short
corrections arrived while this slice ran, because a `backfill-*` ticket still
carries `writer_queue.BACKFILL_PRIORITY`.

Three properties follow, and each one is the direct negation of a thing that
went wrong:

  * **The lock is never held longer than a slice.** Not by convention: the
    workflow's `timeout-minutes` is the ceiling and the slice is sized to
    finish far inside it.
  * **Progress is durable after every slice**, in a committed file, the way the
    writer queue is. A run that dies loses at most its own slice.
  * **The next run works out where to resume by itself.** The cursor is the
    authority; nobody has to read a log to find out how far a dead run got.

WHAT THIS DOES NOT DO
---------------------
It does not weaken the lock and it does not split the concurrency group. Every
one of these workflows stays in `talent-collect` with `cancel-in-progress:
false`, which `tests/test_workflows.py::test_every_database_writer_shares_one_lock`
requires and which two separate 2026-07-28 incidents paid for. Slicing changes
how LONG the lock is held, never how MANY writers may hold it.

CLI, used from the workflows:

    python backfill_slices.py record --from "$RUNNER_TEMP/slice.json" --queue
    python backfill_slices.py status

Stdlib only, on purpose: `ops_status.py` imports it and takes no dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import writer_queue

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "backfill_state.json"

# --------------------------------------------------------------------------
# The two numbers that make this work, and the measurements behind them
# --------------------------------------------------------------------------

#: The wall clock a slice may spend COLLECTING before it stops itself at the
#: next window boundary and commits what it has.
#:
#: This has to be comfortably below the job's `timeout-minutes`, because a run
#: that reaches its timeout is CANCELLED and a cancelled run's commit step is
#: skipped — that is the exact 350-minute failure above, and a slice that runs
#: to its own timeout would reproduce it in miniature. The gap covers pip
#: install, whatever request is in flight when the budget expires, publish, the
#: database merge, and up to five push attempts.
SLICE_BUDGET_MINUTES = 50

#: What every sliced backfill workflow sets as `timeout-minutes`. Chosen to sit
#: below `writer_queue.LONG_HOLD_MINUTES` (120), the point at which the drainer
#: reports the queue as starved: with this ceiling a sliced backfill can no
#: longer be the thing that starves it, by construction rather than by hope.
#: Asserted in tests/test_workflows.py.
SLICE_TIMEOUT_MINUTES = 90

#: A chain that never ends is worse than a job that runs too long, because
#: nothing about it looks wrong. A year of GDELT at four days a slice is 92
#: slices and a year of 8-Ks at a week is 53, so this stops a runaway without
#: ever being reached by real work.
MAX_SLICES_PER_JOB = 200

UNITS = ("days", "quarters")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# slice arithmetic
# --------------------------------------------------------------------------

def _quarter(value: str) -> tuple[int, int]:
    text = value.strip().lower().replace("-", "")
    if "q" not in text:
        raise ValueError(f"not a quarter: {value!r} (expected e.g. 2026q1)")
    year, _, quarter = text.partition("q")
    return int(year), int(quarter)


def _quarter_str(year: int, quarter: int) -> str:
    return f"{year}q{quarter}"


def _next_quarter(value: str) -> str:
    year, quarter = _quarter(value)
    return _quarter_str(year + 1, 1) if quarter >= 4 else _quarter_str(year, quarter + 1)


def _quarter_index(value: str) -> int:
    year, quarter = _quarter(value)
    return year * 4 + (quarter - 1)


def next_slice(cursor: str | None, end: str, unit: str, size: int) -> tuple[str, str] | None:
    """The next slice as an INCLUSIVE (lo, hi), or None when the job is done.

    Inclusive on both ends because that is what every one of these backfill
    scripts already takes on the command line, and a half-open window that
    looks like the inclusive one it replaced is a silently-skipped day.
    """
    if not cursor:
        return None
    if unit == "days":
        lo = date.fromisoformat(cursor)
        stop = date.fromisoformat(end)
        if lo > stop:
            return None
        return lo.isoformat(), min(lo + timedelta(days=size - 1), stop).isoformat()
    if unit == "quarters":
        if _quarter_index(cursor) > _quarter_index(end):
            return None
        out, current = [], cursor
        for _ in range(size):
            if _quarter_index(current) > _quarter_index(end):
                break
            out.append(current)
            current = _next_quarter(current)
        return (out[0], out[-1]) if out else None
    raise ValueError(f"unknown slice unit {unit!r}")


def advance(hi: str, unit: str) -> str:
    """The cursor value that follows a slice ending at `hi`."""
    if unit == "days":
        return (date.fromisoformat(hi) + timedelta(days=1)).isoformat()
    if unit == "quarters":
        return _next_quarter(hi)
    raise ValueError(f"unknown slice unit {unit!r}")


def past_end(cursor: str | None, end: str, unit: str) -> bool:
    if not cursor:
        return True
    if unit == "days":
        return date.fromisoformat(cursor) > date.fromisoformat(end)
    return _quarter_index(cursor) > _quarter_index(end)


def slice_members(lo: str, hi: str, unit: str) -> list[str]:
    """Every unit in an inclusive slice — what a quarter-shaped script needs."""
    if unit == "quarters":
        out, current = [], lo
        while _quarter_index(current) <= _quarter_index(hi):
            out.append(current)
            current = _next_quarter(current)
        return out
    out, day = [], date.fromisoformat(lo)
    stop = date.fromisoformat(hi)
    while day <= stop:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# the state file
# --------------------------------------------------------------------------

def empty_state() -> dict:
    return {"version": 1, "jobs": {}}


def load(path: Path | None = None) -> dict:
    target = path or STATE_PATH
    if not target.exists():
        return empty_state()
    try:
        data = json.loads(target.read_text())
    except ValueError:
        return empty_state()
    if not isinstance(data, dict) or "jobs" not in data:
        return empty_state()
    return data


def save(state: dict, path: Path | None = None) -> None:
    target = path or STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def job_id(workflow: str, start: str, end: str) -> str:
    """Stable across every slice of one backfill, so the chain finds itself."""
    return f"{workflow.removesuffix('.yml')}:{start}..{end}"


def open_job(state: dict, *, workflow: str, unit: str, start: str, end: str,
             slice_size: int, inputs: dict | None = None) -> dict:
    """Find this backfill's record, creating it at `start` the first time.

    Creating it here rather than at dispatch is what makes the FIRST run of a
    chain identical to every later one: there is no special case to get wrong,
    and re-dispatching a finished job resumes as complete instead of silently
    starting over.
    """
    if unit not in UNITS:
        raise ValueError(f"unknown slice unit {unit!r}, expected one of {UNITS}")
    key = job_id(workflow, start, end)
    job = state.setdefault("jobs", {}).get(key)
    if job is None:
        job = {
            "workflow": workflow,
            "unit": unit,
            "start": start,
            "end": end,
            "cursor": start,
            "slice_size": slice_size,
            "inputs": dict(inputs or {}),
            "slices": 0,
            "state": "running",
            "totals": {},
            "history": [],
            "opened_at": _iso(_now()),
            "updated_at": _iso(_now()),
        }
        state["jobs"][key] = job
    else:
        # The size is allowed to change between slices (a human tuning it after
        # watching one run), but the window is the job's identity and does not.
        job["slice_size"] = slice_size
        if inputs:
            job["inputs"] = dict(inputs)
    return job


def plan(state: dict, *, workflow: str, unit: str, start: str, end: str,
         slice_size: int, inputs: dict | None = None) -> tuple[dict, tuple[str, str] | None]:
    """(job, next slice) — the whole "where do I resume" question, answered."""
    job = open_job(state, workflow=workflow, unit=unit, start=start, end=end,
                   slice_size=slice_size, inputs=inputs)
    return job, next_slice(job.get("cursor"), end, unit, slice_size)


def record(state: dict, ticket: dict, now: datetime | None = None) -> dict:
    """Apply one finished slice. Returns what happened, for the caller to print.

    Written to be applied AFTER `git reset --hard origin/main`, against
    whatever state the remote holds, so it merges rather than overwrites. The
    same reasoning as merge_db.py: a run that copies its whole file back
    destroys every change anyone else pushed while it was working.
    """
    moment = now or _now()
    key = ticket["job_id"]
    job = state.setdefault("jobs", {}).get(key)
    if job is None:
        job = open_job(state, workflow=ticket["workflow"], unit=ticket["unit"],
                       start=ticket["start"], end=ticket["end"],
                       slice_size=ticket["slice_size"], inputs=ticket.get("inputs"))

    unit = job["unit"]
    was = job.get("cursor")
    now_cursor = ticket["next_cursor"]

    # The runaway guard. A slice that ends where it began would be requeued
    # forever, and a chain of green runs collecting nothing is the quietest
    # possible failure — precisely the class this repo keeps being bitten by.
    advanced = was is None or now_cursor != was
    if not advanced:
        job["state"] = "stalled"
        job["updated_at"] = _iso(moment)
        return {"job": job, "advanced": False, "complete": False,
                "problem": (f"{key} made no progress: the cursor is still {was}. "
                            "Not requeueing — a chain that cannot advance would "
                            "run forever without ever going red.")}

    job["cursor"] = now_cursor
    job["slices"] = int(job.get("slices", 0)) + 1
    job["updated_at"] = _iso(moment)
    for name, value in (ticket.get("totals") or {}).items():
        if isinstance(value, (int, float)):
            job["totals"][name] = job["totals"].get(name, 0) + value
    job.setdefault("history", []).append({
        "slice": ticket.get("slice", ""),
        "at": _iso(moment),
        "run": ticket.get("run_url") or ticket.get("run_id") or "",
        "totals": ticket.get("totals") or {},
        "stopped_early": ticket.get("stopped_early") or "",
    })
    job["history"] = job["history"][-120:]

    complete = past_end(now_cursor, job["end"], unit)
    problem = None
    if complete:
        job["state"] = "done"
        job["cursor"] = None
    elif ticket.get("halt"):
        # The slice's rows are kept and the cursor moves — they were collected
        # and must not be collected again. What does NOT happen is the requeue:
        # whatever stopped this slice stops the next one too, and a chain that
        # requeues into a wall produces one red run per slice and buries the
        # first, real one.
        job["state"] = "halted"
        problem = (f"{key} stopped the chain at {now_cursor}: {ticket['halt']}. "
                   "The slice is recorded, nothing was queued behind it. Fix "
                   "the cause, then re-queue the backfill; it resumes here.")
    elif job["slices"] >= MAX_SLICES_PER_JOB:
        job["state"] = "stalled"
        problem = (f"{key} has run {job['slices']} slices without finishing, over "
                   f"the {MAX_SLICES_PER_JOB} ceiling. Not requeueing.")
    else:
        job["state"] = "running"
    return {"job": job, "advanced": True, "complete": complete, "problem": problem}


def next_inputs(job: dict, unit: str | None = None) -> dict | None:
    """The workflow inputs for the slice after the one just recorded.

    The dispatch inputs still carry start/end so a run is readable in the
    Actions list, but they are NOT what decides where it resumes: the committed
    cursor is. That matters because a ticket can sit in the queue for hours
    behind other work, and an input that said where to start would be a
    second, staler source of truth.
    """
    if job.get("state") != "running" or not job.get("cursor"):
        return None
    inputs = dict(job.get("inputs") or {})
    inputs.update({"start": job["start"], "end": job["end"]})
    if job.get("unit") == "quarters":
        inputs.pop("start", None)
        inputs.pop("end", None)
        inputs["quarters"] = f"{job['start']}..{job['end']}"
    return inputs


# --------------------------------------------------------------------------
# what a backfill SCRIPT uses: one window, one clock, one ticket
# --------------------------------------------------------------------------

class Budget:
    """A wall clock a slice checks between windows.

    The date window is the nominal slice; this is the thing that actually
    guarantees the run ends cleanly. GDELT throttles erratically and an 8-K
    week can be twice the size of the one before it, so a slice sized by
    measurement is a good estimate and never a promise. The promise is here: at
    the budget the run stops at the NEXT window boundary, reports why, and lets
    its commit step run — which is the single thing the 350-minute run did not
    get to do.
    """

    def __init__(self, minutes: float = SLICE_BUDGET_MINUTES, now=None):
        self.minutes = float(minutes)
        self._now = now or _now
        self.started = self._now()

    def elapsed_minutes(self) -> float:
        return (self._now() - self.started).total_seconds() / 60

    def expired(self) -> bool:
        return self.elapsed_minutes() >= self.minutes

    def reason(self) -> str:
        return (f"slice budget of {self.minutes:g} minutes reached "
                f"({self.elapsed_minutes():.0f} min elapsed) — stopping at a "
                "window boundary so the commit step runs")


def open_slice(*, workflow: str, unit: str, start: str, end: str, slice_size: int,
               inputs: dict | None = None, state_path: str | Path | None = None):
    """(job, (lo, hi) or None) for the run that is starting right now."""
    path = Path(state_path) if state_path else None
    state = load(path)
    return plan(state, workflow=workflow, unit=unit, start=start, end=end,
                slice_size=slice_size, inputs=inputs)


def slice_ticket(job: dict, lo: str, hi: str, *, next_cursor: str | None = None,
                 totals: dict | None = None, stopped_early: str = "",
                 halt: str = "") -> dict:
    """What a backfill script emits for `record` to apply after the reset.

    `next_cursor` defaults to the unit after `hi`, and is passed explicitly by
    a run that stopped mid-slice on its budget: the cursor then points at the
    first window it did NOT do, so the chain resumes exactly there and no day
    is collected twice or skipped.

    `halt` is for a failure that the NEXT slice would hit too. The progress is
    still recorded — rows were collected and must not be re-collected — but
    nothing is queued behind it, because a chain that requeues into a wall
    produces one red run per slice and buries the first, real one. The measured
    case is `publish.publish` refusing while the publish guardrails hold open
    findings: a human clears those, then re-queues the backfill and it picks up
    from the cursor.
    """
    return {
        "job_id": job_id(job["workflow"], job["start"], job["end"]),
        "workflow": job["workflow"],
        "unit": job["unit"],
        "start": job["start"],
        "end": job["end"],
        "slice_size": job["slice_size"],
        "inputs": dict(job.get("inputs") or {}),
        "slice": f"{lo}..{hi}",
        "next_cursor": next_cursor or advance(hi, job["unit"]),
        "totals": totals or {},
        "stopped_early": stopped_early,
        "halt": halt,
    }


def emit(path: str | Path, ticket: dict) -> None:
    Path(path).write_text(json.dumps(ticket, indent=2, sort_keys=True) + "\n")


def summary(state: dict | None = None) -> dict:
    """What ops_status.py prints. Problems mean a human is needed."""
    data = state if state is not None else load()
    jobs, problems = [], []
    for key, job in sorted(data.get("jobs", {}).items()):
        done = job.get("slices", 0)
        jobs.append({
            "id": key, "state": job.get("state"), "slices": done,
            "cursor": job.get("cursor"), "end": job.get("end"),
            "unit": job.get("unit"), "updated_at": job.get("updated_at"),
            "totals": job.get("totals", {}),
        })
        if job.get("state") in ("stalled", "halted"):
            problems.append(
                f"backfill {key} is {job['state']} at {job.get('cursor')} after "
                f"{done} slice(s) — it will NOT requeue itself; read the last run")
    return {"jobs": jobs, "problems": problems}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cmd_record(args) -> int:
    ticket = json.loads(Path(args.source).read_text())
    path = Path(args.file) if args.file else None
    state = load(path)
    result = record(state, ticket)
    save(state, path)

    job = result["job"]
    if result["problem"]:
        print(f"::error::{result['problem']}")
        # The state file is already saved above, so a halted or stalled chain
        # still records the slice it did. Only the requeue is skipped.
        return 2
    if result["complete"]:
        print(f"{ticket['job_id']} COMPLETE after {job['slices']} slice(s): "
              + ", ".join(f"{k}={v}" for k, v in sorted(job["totals"].items())))
        return 0

    print(f"{ticket['job_id']} at {job['cursor']} of {job['end']} "
          f"after {job['slices']} slice(s)")
    if not args.queue:
        return 0

    inputs = next_inputs(job)
    if inputs is None:
        return 0
    queue_path = Path(args.queue_file) if args.queue_file else None
    queue = writer_queue.load(queue_path)
    # Same lock, same queue, same priority rule: a backfill ticket still sorts
    # behind every correction, so slicing shortens the wait without ever
    # letting a backfill jump it.
    ticket_row = writer_queue.enqueue(
        queue, job["workflow"], inputs,
        reason=f"next slice of {ticket['job_id']} (resumes at {job['cursor']})",
        requested_by="backfill-slices")
    writer_queue.prune(queue)
    writer_queue.save(queue, queue_path)
    print(f"queued {ticket_row['id']} for the next slice")
    return 0


def _cmd_status(args) -> int:
    state = summary(load(Path(args.file) if args.file else None))
    print(json.dumps(state, indent=2, default=str))
    return 2 if state["problems"] else 0


def _cmd_reset(args) -> int:
    """Forget a job so the next dispatch starts it again from `start`."""
    path = Path(args.file) if args.file else None
    state = load(path)
    if args.job_id not in state.get("jobs", {}):
        print(f"::error::no backfill job {args.job_id!r}")
        return 2
    del state["jobs"][args.job_id]
    save(state, path)
    print(f"forgot {args.job_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="state file (default data/backfill_state.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="apply one finished slice, and queue the next")
    rec.add_argument("--from", dest="source", required=True,
                     help="the slice ticket a backfill script emitted")
    rec.add_argument("--queue", action="store_true",
                     help="append the next slice to the writer queue")
    rec.add_argument("--queue-file", help="writer queue file, for tests")
    rec.set_defaults(func=_cmd_record)

    show = sub.add_parser("status", help="every backfill's progress, as JSON")
    show.set_defaults(func=_cmd_status)

    forget = sub.add_parser("reset", help="forget a job so it starts over")
    forget.add_argument("job_id")
    forget.set_defaults(func=_cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
