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
#:
#: MEASURED AND DELIBERATELY NOT LOWERED, 2026-08-02. With five chains live, the
#: obvious answer to "long slices starve short chains" is to shorten the slice.
#: The queue's own history refuses it: exactly ONE chain of the five reaches
#: this budget (gdelt, 56 minutes wall), and 56 is already inside
#: `writer_queue.LONG_HOLD_MINUTES` (120), the line that file draws at "the
#: queue is starved". gnews lands at 23, companies_house at 21 and bse_india at
#: 3, so a lower ceiling would not bind on four of five chains at all. Halving
#: it shortens a full round from ~135 minutes to ~115 while costing the chain
#: doing the most work about 40% more runs, each paying the fixed 3-6 minutes of
#: checkout, install, merge and push over again — and it would leave the actual
#: defect untouched, because a three-minute chain waiting behind a 25-minute one
#: is the same shape of unfair as waiting behind a 50-minute one. The fix went
#: into the dispatch ORDER instead: `writer_queue.dispatch_key`.
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

#: How long a `running` chain may go with nothing behind it in the writer queue
#: before it is called stalled rather than busy.
#:
#: A healthy chain is never in that position for long: the requeue is committed
#: in the SAME commit as the slice, so the successor ticket exists from the
#: moment a run ends, and while a slice is executing its own ticket is
#: `dispatched`. Either way something is live. The gap only opens when a slice
#: ends without running its commit step, which is what cancellation does.
#:
#: Three hours, then, is not a cadence estimate — it is slack. One slice is at
#: most SLICE_TIMEOUT_MINUTES (90) and a drain tick is throttled by GitHub to
#: roughly an hour, so three hours clears a full worst-case cycle twice over
#: while still being two days shorter than how long the 2026-07-31 stall went
#: unnoticed.
CHAIN_IDLE_HOURS = 3

#: `days` and `quarters` walk a calendar. `slices` walks a POPULATION: its
#: cursor is an integer index into a deterministic partition of a roster, and
#: the date window is a fixed input carried on the job rather than the thing
#: being advanced. That exists because `companies_house` costs one request per
#: COMPANY and nothing per day — its window is a filter over data the endpoint
#: returns anyway, so widening it is free and sweeping the roster is not. A
#: date cursor there would advance over work that had never been done.
UNITS = ("days", "quarters", "slices")


# --------------------------------------------------------------------------
# A WINDOW HAS THREE OUTCOMES, AND TWO OF THEM USED TO BE ONE
# --------------------------------------------------------------------------
#
# Measured on `backfill-gnews-2026` run 30662474194 (2026-07-31). Google News
# refused every request for the whole 74-minute slice — `queries sent 576 (576
# failed)`, `windows 3 (3 empty)` — and the run correctly went RED on its own
# fail-loud check. The chain advanced anyway, 2026-01-22 to 2026-01-25, and
# 2026-01-24 holds zero google_news rows to this day with nothing left that
# would ever go back for it.
#
# Nothing was missing. The guard existed, fired, and did not matter:
#
#   * the slice ticket carrying the new cursor is emitted BEFORE the fail-loud
#     check, deliberately, so that rows already collected are never the price
#     of how a run ended;
#   * the commit step runs `if: !cancelled()`, deliberately, for the same
#     reason. So a RED run still records and still requeues.
#
# **Red is not the same as unadvanced.** The one thing that decides whether a
# day is skipped forever is the cursor in that ticket, and it was computed from
# `done_through`, which every walker set at the bottom of its loop body whether
# or not the window had produced anything. The comment above the emit says "a
# run that finished NOTHING emits a cursor that has not moved" — true, but
# "finished" was measured as "completed a loop iteration", and three days that
# were walked and could not be fetched are three iterations.
#
# So a window ends in one of THREE states, never two, for exactly the reason
# ops_status keeps PASS / FAIL / UNKNOWN apart: the absence of an article and
# the absence of an answer are different facts, and only one of them is
# progress.
#
#: The fetch worked and returned something. The cursor may pass this window.
COLLECTED = "collected"
#: The fetch worked and there was nothing there. A real answer, so the cursor
#: may pass — an empty day is collected history, not a hole.
EMPTY = "empty"
#: The fetch itself failed. We do not know what was there and nothing gathered
#: it, so the cursor MUST NOT pass: a chain only ever moves forwards, and a day
#: it steps over is a day no run will be asked for again.
UNREACHED = "unreached"


def sampled_window(items: int, fetch_errors: int) -> str:
    """The state of a window for a walker that SAMPLES its window.

    `backfill_gnews_2026` and `backfill_gdelt_2026` fetch a day across dozens
    of editions or queries and then ration what reaches the model, so partial
    coverage of a day is the DESIGNED outcome and a re-walk of the same range
    deliberately reads different rows. One flaky edition out of 52 is therefore
    not a hole, and treating it as one would stop the chain on ordinary
    weather.

    What is a hole is a window that produced NOTHING while the fetch was
    erroring, which is the measured incident exactly: 576 of 576 queries
    failed, so "there was no news on 2026-01-24 anywhere on earth in any
    language" was recorded as fact.
    """
    if items:
        return COLLECTED
    return UNREACHED if fetch_errors else EMPTY


def enumerated_window(items: int, fetch_errors: int) -> str:
    """The state of a window for a walker that ENUMERATES its window.

    `backfill_sec_2026` and `backfill_form_d_2026` page through a filing index
    and the contract is completeness: every 8-K 5.02 in the week, every Form D
    in the month. A search that dies on page three has enumerated part of a
    window, and the pages it never asked for are indistinguishable from pages
    that held nothing. So ANY fetch failure leaves the window unreached,
    however many filings the earlier pages returned.

    Re-walking is close to free here and a hole is not: `store.already_seen`
    skips every URL a previous pass stored, before any model call.
    """
    if fetch_errors:
        return UNREACHED
    return COLLECTED if items else EMPTY


def unreached_reason(window: str, detail: str = "") -> str:
    """The one sentence a walker prints when it refuses to pass a window."""
    return (f"{window} could not be FETCHED ({detail or 'the fetch failed'}). "
            "Stopping here: the cursor does not move past a window nobody "
            "collected, because nothing would ever come back for it.")


# --------------------------------------------------------------------------
# THE CURSOR ADVANCES PER RUN, NOT PER DAY, AND THAT IS A PROPERTY NOT A DETAIL
# --------------------------------------------------------------------------
#
# `record()` moves the cursor from the TICKET a run emitted — `next_cursor`,
# derived from the last window that run actually finished. It reads no clock. Two
# runs in one hour therefore advance twice, and a run that does nothing advances
# not at all (which `record` catches and refuses to requeue).
#
# The sibling tracker got this wrong in the most expensive way available. Its
# `edgar-history-sweep` keyed its cursor on `now.toordinal()` — a DATE ordinal —
# and ran HOURLY. Every run in a day therefore computed the identical window,
# re-fetched the identical filings and re-extracted them at full prompt: about
# $3.80 a day of pure waste for six days, and every one of those runs was green,
# because from outside a run that re-does yesterday's work looks exactly like a
# run that did work.
#
# So the pairing is what is dangerous, not either half. A date-keyed cursor with a
# daily cron is fine. A run-keyed cursor with an hourly cron is fine. A date-keyed
# cursor with a sub-daily cron silently multiplies spend by the runs per day.
# `tests/test_backfill_pace.py` asserts the property directly — that a second
# `record` in the same clock second still advances — rather than asserting the
# symptom, and it also refuses to let a sliced workflow grow a cron faster than
# daily while any cursor in this module is date-shaped.
#
#: Runs per day above which a date-keyed cursor becomes a spend multiplier. One:
#: any second run in the same day repeats the first.
DATE_CURSOR_SAFE_RUNS_PER_DAY = 1


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


def _slice_index(value: str) -> int:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"not a roster slice index: {value!r} (expected e.g. 0)")
    return int(text)


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
    if unit == "slices":
        lo = _slice_index(cursor)
        stop = _slice_index(end)
        if lo > stop:
            return None
        return str(lo), str(min(lo + size - 1, stop))
    raise ValueError(f"unknown slice unit {unit!r}")


def advance(hi: str, unit: str) -> str:
    """The cursor value that follows a slice ending at `hi`."""
    if unit == "days":
        return (date.fromisoformat(hi) + timedelta(days=1)).isoformat()
    if unit == "quarters":
        return _next_quarter(hi)
    if unit == "slices":
        return str(_slice_index(hi) + 1)
    raise ValueError(f"unknown slice unit {unit!r}")


def past_end(cursor: str | None, end: str, unit: str) -> bool:
    if not cursor:
        return True
    if unit == "days":
        return date.fromisoformat(cursor) > date.fromisoformat(end)
    if unit == "slices":
        return _slice_index(cursor) > _slice_index(end)
    return _quarter_index(cursor) > _quarter_index(end)


def slice_members(lo: str, hi: str, unit: str) -> list[str]:
    """Every unit in an inclusive slice — what a quarter-shaped script needs."""
    if unit == "quarters":
        out, current = [], lo
        while _quarter_index(current) <= _quarter_index(hi):
            out.append(current)
            current = _next_quarter(current)
        return out
    if unit == "slices":
        return [str(i) for i in range(_slice_index(lo), _slice_index(hi) + 1)]
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


def job_id(workflow: str, start: str, end: str, label: str = "") -> str:
    """Stable across every slice of one backfill, so the chain finds itself.

    `label` exists because one workflow may walk more than one population.
    `backfill-structured-2026.yml` takes a `source` input and runs three
    registry collectors over the SAME 2026 window, so without it India's cursor
    and Korea's would be the same key and each would resume where the other
    stopped — a hole and a double-collection in one. It defaults to empty, so
    every job written before it existed keeps its id.
    """
    stem = workflow.removesuffix(".yml")
    return f"{stem}:{label}:{start}..{end}" if label else f"{stem}:{start}..{end}"


def open_job(state: dict, *, workflow: str, unit: str, start: str, end: str,
             slice_size: int, inputs: dict | None = None, label: str = "") -> dict:
    """Find this backfill's record, creating it at `start` the first time.

    Creating it here rather than at dispatch is what makes the FIRST run of a
    chain identical to every later one: there is no special case to get wrong,
    and re-dispatching a finished job resumes as complete instead of silently
    starting over.
    """
    if unit not in UNITS:
        raise ValueError(f"unknown slice unit {unit!r}, expected one of {UNITS}")
    key = job_id(workflow, start, end, label)
    job = state.setdefault("jobs", {}).get(key)
    if job is None:
        job = {
            "workflow": workflow,
            "label": label,
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
         slice_size: int, inputs: dict | None = None,
         label: str = "") -> tuple[dict, tuple[str, str] | None]:
    """(job, next slice) — the whole "where do I resume" question, answered."""
    job = open_job(state, workflow=workflow, unit=unit, start=start, end=end,
                   slice_size=slice_size, inputs=inputs, label=label)
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
                       slice_size=ticket["slice_size"], inputs=ticket.get("inputs"),
                       label=ticket.get("label", ""))

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
        # NAME WHAT DID NOT ADVANCE, not just that something did not. The
        # 2026-08-05 press run said "the cursor is still 0" and stopped there,
        # so the actual cause (the month's allowance was spent, the walk broke
        # out after 2 of 40 publishers) was only in the previous step's log and
        # only if you knew to read it. The ticket carries the reason; print it.
        why = ticket.get("stopped_early") or ticket.get("halt") or ""
        return {"job": job, "advanced": False, "complete": False,
                "problem": (
                    f"{key} made no progress: it walked "
                    f"{ticket.get('slice') or '?'} and the cursor is still "
                    f"{was}, so the chain STOPS here and will not restart "
                    f"itself. Not requeueing — a chain that cannot advance "
                    f"would run forever without ever going red. "
                    + (f"The run says it ended because: {why}" if why else
                       "The run recorded no reason for ending, which is itself "
                       "the thing to investigate: read its walk step."))}

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
    if job.get("unit") == "slices":
        # The job's start/end are ROSTER SLICE INDICES, not dates. The date
        # window is a fixed dispatch input carried on `inputs`, so injecting
        # start/end here would overwrite "2026-01-01".."2026-07-30" with "0"
        # and "7" and the next run would read a one-day window.
        return inputs
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
               inputs: dict | None = None, state_path: str | Path | None = None,
               label: str = ""):
    """(job, (lo, hi) or None) for the run that is starting right now."""
    path = Path(state_path) if state_path else None
    state = load(path)
    return plan(state, workflow=workflow, unit=unit, start=start, end=end,
                slice_size=slice_size, inputs=inputs, label=label)


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
        "job_id": job_id(job["workflow"], job["start"], job["end"],
                         job.get("label", "")),
        "workflow": job["workflow"],
        "label": job.get("label", ""),
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


def _live_ticket(queue: dict, job: dict) -> dict | None:
    """The queued or dispatched ticket that is this chain's next slice.

    Matched on the workflow AND the inputs, because one workflow can drive
    several independent chains: `backfill-structured-2026.yml` walks bse_india,
    companies_house and opendart_korea over the same window, and a ticket for
    one of them says nothing about the other two. `next_inputs` re-emits the
    job's own inputs every slice, so the identity is an exact comparison.
    """
    wanted = next_inputs(job) or {}
    for ticket in queue.get("tickets", []):
        if ticket.get("state") not in ("queued", "dispatched"):
            continue
        if writer_queue.same_chain(ticket, job.get("workflow"), wanted):
            return ticket
    return None


def summary(state: dict | None = None, *, queue: dict | None = None,
            now: datetime | None = None,
            queue_path: Path | None = None) -> dict:
    """What ops_status.py prints. Problems mean a human is needed."""
    data = state if state is not None else load()
    moment = now or _now()

    # A CANCELLED slice leaves NOTHING behind, and that used to be silent.
    #
    # A failed slice requeues: the workflow's commit step runs `if:
    # !cancelled()`, records the ticket and appends the next one. A CANCELLED
    # slice skips that step entirely, so the chain simply ends — no ticket, no
    # run, no red anything. `backfill-structured-2026` run 30594795739 was
    # cancelled mid-run during the 2026-07-31 Bluehost outage and bse_india sat
    # at 2026-01-29 and companies_house at slice 1 of 7 for two days while this
    # function returned `problems: []`.
    #
    # The writer queue does mark a cancelled-mid-run ticket `failed`, and does
    # report it — but `writer_queue resolve` exists so a human can stop a red
    # drain tick, and acknowledging that ticket clears the queue's alarm
    # WITHOUT putting anything back in the line. The chain is then dead and
    # every dashboard is green. So the chain has to be able to notice its own
    # death, from its own state, rather than inheriting a signal from a queue
    # that has legitimately moved on.
    #
    # DELIBERATELY NOT AN AUTO-REQUEUE, and this is the part that is a
    # judgement rather than a bug fix:
    #
    #   * mid-run cancellation is what a HOST OUTAGE and what a TIMEOUT both
    #     look like from here. Requeueing into the first is the loop this repo
    #     already paid to break once (an alerter that posts to the host it is
    #     reporting as down); requeueing into the second is a chain that burns
    #     one paid slice per attempt forever and is green every time.
    #   * `writer_queue.tick` already draws this exact line for every other
    #     writer: cancelled-with-no-jobs is displacement and auto-requeues,
    #     cancelled-after-starting is `failed` and "needs a human". A backfill
    #     is not special enough to get a second policy.
    #
    # What was missing was never the requeue. It was somebody being told.
    unknown_queue = False
    if queue is None:
        target = queue_path or writer_queue.QUEUE_PATH
        if target.exists():
            queue = writer_queue.load(target)
        else:
            # Three states, not two. No queue file is "could not check", and a
            # check that could not run must never read as a pass.
            unknown_queue = True

    jobs, problems = [], []
    for key, job in sorted(data.get("jobs", {}).items()):
        done = job.get("slices", 0)
        row = {
            "id": key, "state": job.get("state"), "slices": done,
            "cursor": job.get("cursor"), "end": job.get("end"),
            "unit": job.get("unit"), "updated_at": job.get("updated_at"),
            "totals": job.get("totals", {}),
        }
        if job.get("state") in ("stalled", "halted"):
            problems.append(
                f"backfill {key} is {job['state']} at {job.get('cursor')} after "
                f"{done} slice(s) — it will NOT requeue itself; read the last run")
        elif job.get("state") == "running" and job.get("cursor"):
            row["waiting_on"] = "unknown" if unknown_queue else None
            if unknown_queue:
                row["idle_hours"] = None
            else:
                ticket = _live_ticket(queue or {}, job)
                row["waiting_on"] = ticket["id"] if ticket else None
                idle = _idle_hours(job.get("updated_at"), moment)
                row["idle_hours"] = idle
                if ticket is None and (idle is None or idle >= CHAIN_IDLE_HOURS):
                    problems.append(_stalled_chain_problem(key, job, idle))
        jobs.append(row)
    return {"jobs": jobs, "problems": problems}


def _idle_hours(updated_at: str | None, moment: datetime) -> float | None:
    if not updated_at:
        return None
    try:
        last = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - last).total_seconds() / 3600)


def _stalled_chain_problem(key: str, job: dict, idle: float | None) -> str:
    since = f"{idle:.0f}h" if idle is not None else "an unknown time"
    inputs = json.dumps(next_inputs(job) or {}, sort_keys=True)
    return (
        f"backfill {key} is RUNNING at {job.get('cursor')} of {job.get('end')} "
        f"but has not moved for {since} and has NOTHING in the writer queue "
        f"behind it. A cancelled slice skips its commit step, so it records no "
        f"progress and queues no successor and the chain just ends. It does not "
        f"restart itself on purpose — a mid-run cancellation is what a host "
        f"outage and a timeout both look like. Read the last run, then:\n"
        f"       gh workflow run drain-writers.yml "
        f"-f enqueue={job.get('workflow')} \\\n"
        f"            -f inputs_json='{inputs}' \\\n"
        f"            -f reason='resume {key} at {job.get('cursor')}'")


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
    #
    # Requeueing at the END of the run is also what interleaves the chains: the
    # new ticket carries a fresh `requested_at` and therefore re-enters the line
    # behind every chain that has been waiting, which is a round robin for free.
    # What that ordering could NOT see is what a slice costs, so a three-minute
    # chain took one turn per round exactly like a fifty-minute one and paid two
    # hours of queue for it. `writer_queue.dispatch_key` adds that, measured,
    # and leaves this FIFO underneath it untouched.
    #
    # Inherited rather than recomputed, though. `default_priority()` is a
    # property of the workflow, so it reapplied here on every requeue and an
    # operator's `--priority` survived exactly one slice — the override looked
    # effective and was not. See writer_queue.chain_priority.
    ticket_row = writer_queue.enqueue(
        queue, job["workflow"], inputs,
        reason=f"next slice of {ticket['job_id']} (resumes at {job['cursor']})",
        requested_by="backfill-slices",
        priority=writer_queue.chain_priority(queue, job["workflow"], inputs))
    writer_queue.prune(queue)
    writer_queue.save(queue, queue_path)
    print(f"queued {ticket_row['id']} for the next slice")
    return 0


def _cmd_status(args) -> int:
    state = summary(load(Path(args.file) if args.file else None),
                    queue_path=Path(args.queue_file) if args.queue_file else None)
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
    show.add_argument("--queue-file", help="writer queue file, for tests")
    show.set_defaults(func=_cmd_status)

    forget = sub.add_parser("reset", help="forget a job so it starts over")
    forget.add_argument("job_id")
    forget.set_defaults(func=_cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
