#!/usr/bin/env python3
"""A durable queue for the one workflow slot that may write the database.

THE BUG THIS EXISTS TO KILL
---------------------------
Every workflow that writes the database shares one concurrency group,
`talent-collect`, with `cancel-in-progress: false`. That lock is correct and
must stay: two writers both end with reset-to-origin-then-put-our-rows-back, so
whichever pushes second destroys the other's work.

But GitHub keeps exactly ONE pending run per concurrency group. While a run is
executing and a second is waiting, dispatching a THIRD silently replaces the
waiting one. The displaced run ends `cancelled` having created NO jobs at all:
no steps, no logs, no annotation, nothing anywhere that says work was lost.

Measured on 2026-07-29, seven writer runs were thrown away this way while a
GDELT backfill held the lock from 04:59 onward — 1x correct-form-d, 2x
correct-sec-pillar, 2x enrich, 1x recall, 1x collect-press. Each one's
cancellation timestamp lands two to three seconds after the NEXT dispatch, and
each has zero jobs. Every one of them was reported to the owner as "queued".

THE FIX
-------
Displacement is only possible when a second run is already pending. So the
drainer maintains the invariant:

    AT MOST ONE WRITER RUN IS PENDING AT ANY TIME

It dispatches the next ticket only when the group holds nothing that is running
or waiting. Work that has not been dispatched yet sits in this file — committed
to the repo, so it survives everything — rather than in GitHub's single, silently
lossy pending slot. Queued work therefore cannot be displaced, because it is
never in a position to be.

That covers work that goes through the queue. Anything dispatched directly
still can be displaced, so the drainer ALSO reads the run list every tick and
flags any writer run that ended `cancelled` without ever creating a job. If it
matches one of our tickets we re-dispatch it with the inputs we recorded. If it
does not, we cannot know what inputs it was given — GitHub does not expose the
inputs of a dispatched run through its API — so it is recorded as an orphan and
the run goes RED. A dropped run that shouts is the point.

Nothing here writes the database, which is why the drainer is deliberately
NOT a member of `talent-collect`: it must be able to run while the lock is held.

AND ONE LINE IS NOT AUTOMATICALLY A FAIR LINE
---------------------------------------------
With five backfill chains live, a chain whose slice takes three minutes was
waiting two hours for it, because dispatch ordered on a timestamp that knows
nothing about what a ticket costs. `dispatch_key` fixes that from this file's
own measured history, under a starvation ceiling, without touching the stored
priority. The measurement, and the two other fixes it rules out, are written
out above FAST_SLICE_MINUTES.

Stdlib only, on purpose — ops_status.py imports it and takes no dependencies.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_PATH = ROOT / "data" / "writer_queue.json"
WORKFLOW_DIR = ROOT / ".github" / "workflows"

#: The lock every database writer shares. Read from the workflows themselves so
#: this cannot drift from what is actually deployed.
LOCK_GROUP = "talent-collect"

#: A run in any of these states occupies the group — either executing or
#: waiting to. Dispatching anything while one of these exists is what creates a
#: second pending run, which is what loses work.
BUSY_STATUSES = frozenset({
    "queued", "in_progress", "pending", "waiting", "requested", "action_required",
})

#: How many times a displaced ticket is re-dispatched before we stop and shout.
#: Displacement is not the ticket's fault, so this is generous; it exists only
#: to stop an infinite loop if something is systematically eating dispatches.
MAX_ATTEMPTS = 8

#: A ticket marked `dispatched` that never binds to a run is a stall: the
#: drainer died between committing the state and calling the API, or the token
#: could not create the run. After this long we put it back in the line rather
#: than let it sit forever. Kept well inside the window the run list covers, so
#: a run that DID start is always visible by now and never double-dispatched.
UNBOUND_AFTER_MINUTES = 45

#: A ticket that has waited longer than this has stopped being "queued" and
#: started being "stuck". A 350-minute backfill can legitimately hold the lock
#: for most of a day, so this is set past that.
STUCK_AFTER_HOURS = 14

#: Two dispatches in a row that produced no run is not bad luck, it is broken.
#: One is re-dispatched with a warning; the second makes the drain run red.
UNBOUND_ALARM_COUNT = 2

#: The queue has work waiting, the lock group is EMPTY, and still nothing has
#: been dispatched. That combination is never legitimate for long, and on
#: 2026-07-30 it persisted for hours behind a string of GREEN drain ticks.
#:
#: Sized against the tick interval that actually happens rather than the one
#: the cron asks for: `*/15` is throttled by GitHub to roughly 45-60 minutes on
#: this repo (measured across 2026-07-30 10:17Z-17:31Z, gaps 34-60 min). So a
#: single tick can straddle an hour, and anything under that would fire on a
#: transient. Two real intervals is the smallest honest threshold.
IDLE_STALL_MINUTES = 90

#: Backfills are hours; corrections are seconds. They share the lock (they must
#: — one writer at a time is not negotiable), but they do not have to share a
#: place in the line. Lower number leaves first.
DEFAULT_PRIORITY = 0
BACKFILL_PRIORITY = 10

#: How long one run may hold the lock before a waiting queue is called starved.
#: Not an error — the backfills carry `timeout-minutes: 350` and are entitled to
#: it — but a fact that should be said out loud rather than discovered later.
LONG_HOLD_MINUTES = 120

TERMINAL_STATES = frozenset({"landed", "abandoned", "failed", "orphan"})


# --------------------------------------------------------------------------
# FAIRNESS: WHY A THREE-MINUTE CHAIN WAITED TWO HOURS
# --------------------------------------------------------------------------
#
# Measured from this file's own history, the 19 hours to 2026-08-02T03:00Z,
# with five backfill chains live. `wait` is requested -> dispatched, `slice` is
# dispatched -> landed, both in minutes, both medians:
#
#     chain                             slice   wait   wait/slice
#     backfill-gdelt-2026                  56     92         1.6
#     backfill-gnews-2026                  23    120         5.2
#     backfill-structured:companies_house  21    105         5.0
#     backfill-structured:bse_india         3    123        41.0
#     (archive-sources, priority 0)        26     17         0.7
#
# Three candidate fixes were on the table. The measurement decides between them
# and two of them are refused here, on the numbers, so nobody re-proposes them
# from intuition:
#
# **NOT a smaller slice budget.** Only ONE chain of five reaches
# `backfill_slices.SLICE_BUDGET_MINUTES` at all — gdelt, at 56 minutes wall
# against a 50-minute collection budget. That 56 is already inside
# LONG_HOLD_MINUTES (120), the line this file itself draws at "the queue is
# starved", so the ceiling is not the thing that is broken. Halving the budget
# would shorten one full round from ~135 minutes to ~115 (15%) and cost the
# chain doing the most work ~40% more runs, each paying the fixed ~3-6 minutes
# of checkout, install, merge and push again. And it would not touch the row
# that is actually wrong: a three-minute chain would still wait for everyone
# ahead of it, just slightly less.
#
# **NOT interleaving by chain.** It already interleaves, and the log above is
# what that looks like: a chain requeues its next slice at the END of its own
# run, so it re-enters the line with a fresh `requested_at` and sorts behind
# every chain that has been waiting — a clean round robin, for free, out of the
# FIFO tiebreak. Building a second scheduler to produce the order we already
# have would add a mechanism and change nothing.
#
# **IT IS THE DISPATCH ORDER, because priority carries no information.**
# Dispatch does honour `priority` — step 5 has sorted on it since the queue was
# built, and it works. The defect is upstream of that: `default_priority()`
# answers one question, "does the name start with backfill-", so all five
# chains are 10 and dispatch falls through to a timestamp that knows nothing
# about what a ticket costs. Round robin then gives every chain ONE TURN per
# round, which is fair in turns and indefensible in latency: bse_india pays 123
# minutes of queue for 3 minutes of work while gdelt pays 92 for 56.
#
# So the tiebreak becomes MEASURED, from this file's own history rather than a
# hand-kept list of which chain is meant to be quick. See `dispatch_key`.

#: A chain whose whole slice fits inside what it costs to START a run at all.
#: The fixed overhead of a slice — checkout, pip install, the database merge,
#: publish, up to five push attempts — measured at 3-6 minutes (bse_india's
#: ENTIRE run is 3 minutes; gdelt lands at 56 against a 50-minute budget). So a
#: chain under this bar is not "a shorter job", it is a job indistinguishable
#: from the noise of scheduling one, and letting it go first costs the chain it
#: overtakes less than that chain's own startup. Above the bar, reordering
#: transfers real lock time between chains, which is a policy decision and
#: belongs to an operator's `--priority` — which now survives a requeue (see
#: `chain_priority`) instead of lasting exactly one slice.
FAST_SLICE_MINUTES = 8

#: The ceiling on the fast lane. A ticket that has waited this long sorts ahead
#: of every cheap chain regardless, longest wait first, so no amount of cheap
#: work can hold a long chain out of the line forever.
#:
#: Deliberately the SAME number as LONG_HOLD_MINUTES rather than a second tuned
#: constant: this file already calls 120 minutes the point at which a waiting
#: queue is starved, and it would be incoherent to let the scheduler reorder a
#: ticket past the threshold at which it reports that ticket as a problem. It is
#: also inside the wait the FIFO it replaces already delivers (measured 46-173
#: minutes), so no chain is asked to tolerate more than it does today.
#:
#: It promotes; it does not preempt. Nothing here can shorten a slice that is
#: already running, so this bounds a ticket's PLACE IN THE LINE and not its
#: wait. That distinction is the whole reason backfill_slices.py exists.
FAIR_SHARE_AGE_MINUTES = LONG_HOLD_MINUTES

#: How many of a chain's most recent landed slices decide its measured hold.
#: A median over five, so one slice lost to a host outage does not exile a cheap
#: chain from the fast lane and one lucky short slice does not smuggle a heavy
#: one into it. Fewer than this is still measured — a chain with one landed
#: slice knows more about itself than a constant does.
HOLD_SAMPLE = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# which workflows the lock actually covers
# --------------------------------------------------------------------------

def lock_group_workflows(workflow_dir: Path | None = None) -> dict[str, str]:
    """Map workflow filename -> `name:` for every member of the lock group.

    Derived by reading the workflow files rather than from a hand-kept list,
    because a list is exactly the thing that goes stale the day someone adds a
    fourteenth writer.

    Note this deliberately keys off the concurrency group and NOT off the
    string `talent_intel.db`. enrich.yml writes through pipeline.publish and
    never names the file, so the textual test in tests/test_workflows.py does
    not see it as a writer — but it holds the lock, and it was displaced twice
    on 2026-07-29. Membership of the group is the honest definition.
    """
    directory = workflow_dir or WORKFLOW_DIR
    members: dict[str, str] = {}
    for path in sorted(directory.glob("*.yml")):
        text = path.read_text()
        if not re.search(rf"^\s*group:\s*{re.escape(LOCK_GROUP)}\s*(#.*)?$",
                         text, re.MULTILINE):
            continue
        match = re.search(r"^name:\s*(.+?)\s*$", text, re.MULTILINE)
        members[path.name] = match.group(1).strip() if match else path.stem
    return members


def _block(lines: list[str], start: int, indent: int) -> list[str]:
    """Lines belonging to the mapping that opened at `start` with `indent`.

    Blank and comment lines carry no indentation meaning in YAML, so they never
    end a block.
    """
    out: list[str] = []
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            out.append(line)
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return out


def workflow_dispatch_inputs(workflow: str,
                             workflow_dir: Path | None = None
                             ) -> tuple[set[str], set[str]] | None:
    """(declared, required) input names for a workflow, or None if unreadable.

    THE BUG THIS EXISTS TO KILL
    ---------------------------
    `enqueue` checked that the workflow holds the lock and checked nothing about
    the inputs, so a ticket could be accepted that the dispatch API will always
    refuse. On 2026-07-30 a GDELT backfill was queued with `slice: "true"`, an
    input backfill-gdelt-2026.yml does not declare; every dispatch answered
    `Unexpected inputs provided: ["slice"] (HTTP 422)` and the ticket could
    never leave the queue. The API is the only thing that knew, and it only
    knew at dispatch time, which is minutes-to-hours after the human who
    mistyped it has gone. So the check moves to the moment of typing.

    Deliberately regex-and-indentation rather than yaml: ops_status.py imports
    this module and must take no dependencies. Returning None means "could not
    determine", which skips validation rather than blocking a legitimate
    ticket — a parser that guesses wrong must fail open. tests/test_writer_queue
    pins the parse of every current lock member so a formatting change is
    caught by the suite instead of in production.
    """
    path = (workflow_dir or WORKFLOW_DIR) / workflow
    if not path.exists():
        return None
    lines = path.read_text().splitlines()

    opened = None
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)workflow_dispatch:\s*(\S*)\s*$", line)
        if match:
            opened = (index, len(match.group(1)), match.group(2))
            break
    if opened is None:
        return None
    at, indent, inline = opened
    if inline and inline != "{}":
        return None            # some inline form we are not going to guess at
    body = _block(lines, at, indent)
    if inline == "{}" or not body:
        return set(), set()    # dispatchable, and it takes no inputs

    inputs_at = None
    for index, line in enumerate(body):
        match = re.match(r"^(\s*)inputs:\s*$", line)
        if match:
            inputs_at = (index, len(match.group(1)))
            break
    if inputs_at is None:
        return set(), set()
    at, indent = inputs_at
    body = _block(body, at, indent)

    real = [line for line in body if line.strip() and not line.lstrip().startswith("#")]
    if not real:
        return set(), set()
    child = min(len(line) - len(line.lstrip()) for line in real)

    declared: set[str] = set()
    required: set[str] = set()
    name: str | None = None
    for line in body:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        depth = len(line) - len(line.lstrip())
        match = re.match(r"^\s*([A-Za-z_][\w-]*):\s*(.*)$", line)
        if depth == child and match:
            name = match.group(1)
            declared.add(name)
        elif depth > child and name and re.match(r"^\s*required:\s*true\s*(#.*)?$", line):
            required.add(name)
    return declared, required


def missing_required_inputs(workflow: str, inputs: dict,
                            workflow_dir: Path | None = None) -> list[str]:
    """Declared-required inputs this ticket does not carry.

    A warning rather than a refusal, unlike an UNDECLARED input. The two are
    not symmetrical: an input the workflow does not declare is always a typo
    and the API will always refuse it, whereas `enqueue` is also used as the
    canonical "can this workflow be queued at all" assertion (see
    tests/test_backfill_slices.py) with a token input and no intent to
    dispatch. So this is surfaced where a human is reading — the CLI — and the
    dispatch step catches the 422 for real if one ever gets through.
    """
    schema = workflow_dispatch_inputs(workflow, workflow_dir)
    if schema is None:
        return []
    return sorted(schema[1] - set(inputs or {}))


# --------------------------------------------------------------------------
# the queue file
# --------------------------------------------------------------------------

def empty_queue() -> dict:
    return {"version": 1, "tickets": [], "last_tick": None}


def load(path: Path | None = None) -> dict:
    target = path or QUEUE_PATH
    if not target.exists():
        return empty_queue()
    try:
        data = json.loads(target.read_text())
    except ValueError:
        return empty_queue()
    if not isinstance(data, dict) or "tickets" not in data:
        return empty_queue()
    return data


def save(queue: dict, path: Path | None = None) -> None:
    target = path or QUEUE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(queue, indent=2, sort_keys=True) + "\n")


def _log(ticket: dict, event: str, detail: str = "") -> None:
    ticket.setdefault("history", []).append(
        {"at": _iso(_now()), "event": event, "detail": detail})


def default_priority(workflow: str) -> int:
    return BACKFILL_PRIORITY if workflow.startswith("backfill-") else DEFAULT_PRIORITY


def chain_inputs(inputs: dict | None) -> dict[str, str]:
    """A ticket's inputs as the dispatch API will see them.

    The one definition of a chain's identity, which is workflow + these. It was
    written out four times — `chain_priority`, `superseded_by`,
    `backfill_slices._live_ticket` and now the fairness measurement — and four
    copies of an equality rule is three chances for one of them to drift into
    comparing something subtly different.
    """
    return {str(key): str(value) for key, value in (inputs or {}).items()}


def same_chain(ticket: dict, workflow: str, inputs: dict | None) -> bool:
    """Is this ticket a slice of that chain?

    `backfill_slices.next_inputs` re-emits a job's own inputs unchanged on every
    slice, so exact equality IS the chain's identity. One workflow drives
    several independent chains — `backfill-structured-2026` walks bse_india,
    companies_house and opendart_korea over the same window — and a ticket for
    one says nothing at all about the pace or the cost of the other two.
    """
    return (ticket.get("workflow") == workflow
            and chain_inputs(ticket.get("inputs")) == chain_inputs(inputs))


def chain_priority(queue: dict, workflow: str,
                   inputs: dict | None = None) -> int | None:
    """The priority the previous slice of this same chain ran at, or None.

    `default_priority()` is a property of the WORKFLOW, so it reapplies to
    every ticket, so an operator's `--priority` override lasted exactly one
    slice. A chain then quietly reverted to `BACKFILL_PRIORITY` on its own
    first requeue: `backfill-structured-2026` for bse_india was queued at 5 so
    that the free, no-model walkers would drain ahead of the paid ones, and its
    self-requeued ticket came back at 10, behind the very work it was meant to
    overtake. The parameter looked effective and was not, which is worse than
    not having it.

    So priority is STICKY ALONG A CHAIN rather than recomputed per ticket. It
    is matched on the workflow AND the exact inputs, because one workflow drives
    several independent chains — bse_india's pace is not companies_house's —
    and because `backfill_slices.next_inputs` re-emits the job's own inputs
    unchanged every slice, so equality is the chain's identity.

    Returns None when this is the first ticket of a chain, which leaves
    `enqueue` on `default_priority` exactly as before.
    """
    best, seen_at = None, ""
    for ticket in queue.get("tickets", []):
        if not same_chain(ticket, workflow, inputs):
            continue
        stamp = str(ticket.get("requested_at") or "")
        if best is None or stamp >= seen_at:
            best, seen_at = ticket.get("priority"), stamp
    return None if best is None else int(best)


# --------------------------------------------------------------------------
# what a chain actually costs, measured rather than declared
# --------------------------------------------------------------------------

def ticket_hold_minutes(ticket: dict) -> float | None:
    """How long one landed ticket occupied the writer slot, or None.

    Dispatch to landing, from the ticket's own history. That is deliberately a
    little WIDER than the run: `landed` is stamped by the drain tick that
    noticed the run finish, so it includes the seconds between the run ending
    and the queue being told. Those seconds are part of what the next ticket
    waits for, so counting them is the honest measure of a chain's share of the
    line rather than of its billing.

    The last `dispatched` before the `landed` is the one that counts — a ticket
    evicted from the pending slot and re-dispatched has two, and the first one
    held nothing.
    """
    sent = None
    for event in ticket.get("history", []):
        name = event.get("event")
        if name == "dispatched":
            sent = _parse(event.get("at"))
        elif name == "landed" and sent is not None:
            done = _parse(event.get("at"))
            if done is not None:
                return max(0.0, (done - sent).total_seconds() / 60)
    # Older tickets predate the history log; the two fields say the same thing.
    sent = sent or _parse(ticket.get("dispatched_at"))
    done = _parse(ticket.get("landed_at"))
    if sent is None or done is None:
        return None
    return max(0.0, (done - sent).total_seconds() / 60)


def measured_hold_minutes(queue: dict, workflow: str,
                          inputs: dict | None = None) -> float | None:
    """The median slice this chain has actually taken, or None if never timed.

    None is a THIRD state and not a zero. A chain nobody has timed is unknown,
    not cheap: reading an absent measurement as "fast" would put every brand
    new chain in the fast lane on its first ticket, which is exactly how a heavy
    one would get in. It costs the chain one ordinary turn to become measured.
    """
    holds = []
    for ticket in queue.get("tickets", []):
        if ticket.get("state") != "landed" or not same_chain(ticket, workflow, inputs):
            continue
        held = ticket_hold_minutes(ticket)
        if held is not None:
            holds.append((str(ticket.get("requested_at") or ""), held))
    if not holds:
        return None
    holds.sort()
    return statistics.median(value for _, value in holds[-HOLD_SAMPLE:])


def _waited_minutes(ticket: dict, moment: datetime) -> float:
    asked = _parse(ticket.get("requested_at"))
    return 0.0 if asked is None else max(0.0, (moment - asked).total_seconds() / 60)


def dispatch_key(queue: dict, ticket: dict, now: datetime | None = None) -> tuple:
    """Where this ticket sits in the line. Lower leaves first.

    Four terms, in the order they are allowed to matter:

      1. **the operator's priority**, untouched. It decides everything else and
         nothing below can overturn it, which is what makes `--priority` a real
         control rather than a suggestion.
      2. **age**, so nothing starves: past FAIR_SHARE_AGE_MINUTES a ticket sorts
         ahead of every cheap chain, longest wait first.
      3. **measured cost**, so a chain whose whole slice is under
         FAST_SLICE_MINUTES goes ahead of one that is not. This is the fix: it
         is what stops three minutes of work paying two hours of queue.
      4. **FIFO**, which is the round robin that already worked and is left
         exactly as it was.

    THIS IS COMPUTED AT DISPATCH AND NEVER WRITTEN BACK, and that is not a
    detail. `default_priority()` used to be reapplied to a ticket on requeue and
    silently overwrote an operator's override, so the override looked effective
    for one slice and then quietly was not — the shape `chain_priority` was
    written to kill. An ordering that edited `ticket["priority"]` would be the
    same bug with a new name: the stored number must keep meaning "what a human
    asked for", and everything derived must be derived again next tick, from
    facts, where it can be seen changing.
    """
    moment = now or _now()
    priority = int(ticket.get("priority", 0) or 0)
    waited = _waited_minutes(ticket, moment)
    requested = str(ticket.get("requested_at") or "")

    if waited >= FAIR_SHARE_AGE_MINUTES:
        return (priority, 0, -waited, requested)

    hold = measured_hold_minutes(queue, ticket.get("workflow", ""),
                                 ticket.get("inputs"))
    if hold is not None and hold <= FAST_SLICE_MINUTES:
        return (priority, 1, hold, requested)
    return (priority, 2, 0.0, requested)


def dispatch_reason(queue: dict, ticket: dict, now: datetime | None = None) -> str:
    """One line saying why this ticket is where it is, for the drain log.

    A scheduler that reorders silently is a scheduler nobody can audit, and this
    repo has already paid for one queue that moved (or did not) without saying
    anything in eleven consecutive green runs.
    """
    moment = now or _now()
    _, band, _, _ = dispatch_key(queue, ticket, moment)
    waited = _waited_minutes(ticket, moment)
    if band == 0:
        return (f"waited {waited:.0f} min, past the {FAIR_SHARE_AGE_MINUTES}-minute "
                "fair-share ceiling, so it goes ahead of the cheap chains")
    hold = measured_hold_minutes(queue, ticket.get("workflow", ""),
                                 ticket.get("inputs"))
    if band == 1:
        return (f"its measured slice is {hold:.0f} min, inside the "
                f"{FAST_SLICE_MINUTES}-minute fast lane")
    if hold is None:
        return "never timed, so it takes an ordinary turn until it is"
    return f"its measured slice is {hold:.0f} min, so it takes an ordinary turn"


def enqueue(queue: dict, workflow: str, inputs: dict | None = None,
            reason: str = "", requested_by: str = "", priority: int | None = None,
            members: dict[str, str] | None = None, now: datetime | None = None,
            workflow_dir: Path | None = None) -> dict:
    """Add a ticket. Raises on a workflow that does not hold the lock, and on
    inputs the workflow does not declare.

    Inputs are coerced to strings because that is what the dispatch API takes,
    and a boolean `False` silently becoming the string "False" (which is truthy
    to a shell `[ "$x" = "true" ]` test... and to nothing else) is the kind of
    detail that turns a correction into a no-op.

    The input NAMES are checked here rather than left to the dispatch API,
    because the API's refusal arrives at dispatch time — which on 2026-07-30
    was twenty minutes after the human typed the ticket, in a step whose
    failure was then read as a token problem. A ticket the API will always
    refuse is a typo, and a typo belongs in front of the person who made it.
    """
    known = members if members is not None else lock_group_workflows()
    if workflow not in known:
        raise ValueError(
            f"{workflow} is not a member of the {LOCK_GROUP} lock group. "
            f"Members: {', '.join(sorted(known))}")

    clean: dict[str, str] = {}
    for key, value in (inputs or {}).items():
        if isinstance(value, bool):
            clean[key] = "true" if value else "false"
        else:
            clean[key] = str(value)

    schema = workflow_dispatch_inputs(workflow, workflow_dir)
    if schema is not None:
        unknown = sorted(set(clean) - schema[0])
        if unknown:
            raise ValueError(
                f"{workflow} does not declare the input(s) "
                f"{', '.join(unknown)}. The dispatch API answers 422 "
                f"'Unexpected inputs provided' and the ticket could never "
                f"leave the queue. It declares: "
                f"{', '.join(sorted(schema[0])) or '(none)'}")

    moment = now or _now()
    ticket = {
        "id": f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{workflow.removesuffix('.yml')}",
        "workflow": workflow,
        "inputs": clean,
        "reason": reason,
        "requested_by": requested_by,
        "priority": default_priority(workflow) if priority is None else int(priority),
        "state": "queued",
        "attempts": 0,
        "requested_at": _iso(moment),
        "run_id": None,
        "dispatched_at": None,
        "history": [],
    }
    _log(ticket, "queued", reason)
    queue.setdefault("tickets", []).append(ticket)
    return ticket


# --------------------------------------------------------------------------
# reading the world
# --------------------------------------------------------------------------

def never_started(run: dict) -> bool:
    """True when a run produced no jobs at all — the displacement fingerprint.

    A run that GitHub evicts from the pending slot is cancelled before any job
    is created, so `jobs` is empty and `job_count` is 0. A run a human cancels
    mid-flight has jobs. Verified against the seven runs lost on 2026-07-29:
    every one had zero jobs and a cancellation two to three seconds after the
    dispatch that displaced it.
    """
    if run.get("job_count") is not None:
        return int(run["job_count"]) == 0
    return not (run.get("jobs") or [])


def group_busy(runs: list[dict], members: dict[str, str]) -> dict | None:
    """The run currently occupying the lock group, or None.

    Any run that is executing OR waiting counts. Dispatching past a waiting run
    is precisely what displaces it.
    """
    wanted = set(members) | set(members.values())
    busy = [run for run in runs
            if run.get("status") in BUSY_STATUSES
            and (run.get("workflow") in wanted or run.get("workflowName") in wanted)]
    if not busy:
        return None
    # Any of them blocks a dispatch, but report the one actually EXECUTING and
    # holding the lock rather than whatever happens to be first in the list. The
    # pending run is the victim-in-waiting, not the holder, and naming it would
    # make a three-hour backfill look like an eleven-minute one.
    running = [run for run in busy if run.get("status") == "in_progress"]
    pool = running or busy
    pool.sort(key=lambda r: r.get("createdAt") or r.get("created_at") or "")
    return pool[0]


def _matches(ticket: dict, run: dict, members: dict[str, str]) -> bool:
    names = {ticket["workflow"], members.get(ticket["workflow"], "")}
    return bool(names & {run.get("workflow"), run.get("workflowName")})


# --------------------------------------------------------------------------
# the tick
# --------------------------------------------------------------------------

def tick(queue: dict, runs: list[dict], members: dict[str, str] | None = None,
         now: datetime | None = None) -> dict:
    """Reconcile the queue against reality, then decide what to dispatch.

    Pure: takes the run list, returns a plan. Everything that touches the
    network lives in the CLI below, so all of this is testable offline.
    """
    known = members if members is not None else lock_group_workflows()
    moment = now or _now()
    by_id = {str(run.get("databaseId") or run.get("id")): run for run in runs}

    report: dict = {"landed": [], "displaced": [], "failed": [],
                    "abandoned": [], "orphans": [], "stuck": [], "unbound": [],
                    "dispatch": None, "busy": None}

    tickets = queue.setdefault("tickets", [])

    # 1. Bind dispatched tickets that do not yet know their run id. The dispatch
    #    API answers 204 with no body, so the run is found afterwards by
    #    matching workflow and creation time.
    claimed = {t.get("run_id") for t in tickets if t.get("run_id")}
    for ticket in tickets:
        if ticket["state"] != "dispatched" or ticket.get("run_id"):
            continue
        sent = _parse(ticket.get("dispatched_at"))
        if not sent:
            continue
        candidates = [
            run for run in runs
            if _matches(ticket, run, known)
            and str(run.get("databaseId") or run.get("id")) not in claimed
            and (_parse(run.get("createdAt") or run.get("created_at")) or moment)
            >= sent - timedelta(seconds=90)
        ]
        if candidates:
            candidates.sort(key=lambda r: r.get("createdAt") or r.get("created_at") or "")
            run_id = str(candidates[0].get("databaseId") or candidates[0].get("id"))
            ticket["run_id"] = run_id
            claimed.add(run_id)
            _log(ticket, "bound", f"run {run_id}")
        elif (moment - sent) > timedelta(minutes=UNBOUND_AFTER_MINUTES):
            # We recorded a dispatch that produced no run. Waiting on it forever
            # is the silent stall this whole file exists to prevent — but so is
            # requeueing it forever without saying anything, which is what this
            # branch used to do: it was not counted, not reported by _cmd_tick,
            # and not a `problem`, so a ticket whose dispatch the API refuses
            # every single time went round this loop in complete silence.
            # Counted separately from `attempts` because displacement (someone
            # else's dispatch evicted us) and a dispatch that vanished are
            # different faults with different tolerances.
            ticket["state"] = "queued"
            ticket["dispatched_at"] = None
            ticket["unbound_count"] = int(ticket.get("unbound_count", 0)) + 1
            _log(ticket, "unbound",
                 f"dispatch produced no run (x{ticket['unbound_count']}); "
                 "back in the line")
            report.setdefault("unbound", []).append(ticket)

    # 2. Resolve dispatched tickets whose run has finished.
    for ticket in tickets:
        if ticket["state"] != "dispatched":
            continue
        run = by_id.get(str(ticket.get("run_id") or ""))
        if not run or run.get("status") != "completed":
            continue
        conclusion = run.get("conclusion")
        if conclusion == "success":
            ticket["state"] = "landed"
            ticket["landed_at"] = _iso(moment)
            _log(ticket, "landed", f"run {ticket['run_id']}")
            report["landed"].append(ticket)
        elif conclusion == "cancelled" and never_started(run):
            # Displaced out of the pending slot. Not the ticket's fault, and the
            # whole point of the queue is that we still hold its inputs.
            ticket["attempts"] += 1
            ticket["run_id"] = None
            ticket["dispatched_at"] = None
            if ticket["attempts"] >= MAX_ATTEMPTS:
                ticket["state"] = "abandoned"
                _log(ticket, "abandoned", f"displaced {ticket['attempts']}x")
                report["abandoned"].append(ticket)
            else:
                ticket["state"] = "queued"
                _log(ticket, "displaced",
                     f"run {run.get('databaseId') or run.get('id')} created no jobs; re-queued")
                report["displaced"].append(ticket)
        elif conclusion == "cancelled":
            # Cancelled after it started running. That is a human, or a
            # timeout. Re-running it automatically could double-apply a
            # correction, so this stops and asks.
            ticket["state"] = "failed"
            _log(ticket, "cancelled-mid-run", "cancelled after starting; needs a human")
            report["failed"].append(ticket)
        else:
            ticket["state"] = "failed"
            _log(ticket, "failed", f"conclusion {conclusion}")
            report["failed"].append(ticket)

    # 3. Writer runs that were displaced but belong to no ticket: someone
    #    dispatched directly. We cannot replay them, because GitHub does not
    #    expose a dispatched run's inputs. Record loudly and stop.
    tracked = {t.get("run_id") for t in tickets if t.get("run_id")}
    tracked |= {o["run_id"] for o in queue.get("orphans", [])}
    for run in runs:
        name = run.get("workflowName") or run.get("workflow")
        if name not in set(known.values()) | set(known):
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "cancelled":
            continue
        if not never_started(run):
            continue
        run_id = str(run.get("databaseId") or run.get("id"))
        if run_id in tracked:
            continue
        orphan = {
            "run_id": run_id,
            "workflow": name,
            "created_at": run.get("createdAt") or run.get("created_at"),
            "noticed_at": _iso(moment),
            "detail": "displaced from the pending slot; inputs unknowable, "
                      "so it cannot be replayed automatically",
        }
        queue.setdefault("orphans", []).append(orphan)
        report["orphans"].append(orphan)

    # 4. Anything that has waited far too long.
    for ticket in tickets:
        if ticket["state"] not in ("queued", "dispatched"):
            continue
        asked = _parse(ticket.get("requested_at"))
        if asked and (moment - asked) > timedelta(hours=STUCK_AFTER_HOURS):
            report["stuck"].append(ticket)

    # 5. Dispatch, but only into an empty group. This is the invariant that
    #    makes queued work undisplaceable.
    busy = group_busy(runs, known)
    if busy:
        report["busy"] = busy
        # A 350-minute backfill legitimately holds the lock for hours, and a
        # thirty-second correction behind it waits every one of them. Priority
        # decides who goes NEXT; it cannot preempt what is already running. So
        # say plainly when the lock is being held long enough to starve the
        # queue — that is a design question for a human, not an error.
        started = _parse(busy.get("createdAt") or busy.get("created_at"))
        waiting = [t for t in tickets if t["state"] == "queued"]
        if started and waiting:
            held = (moment - started).total_seconds() / 60
            if held > LONG_HOLD_MINUTES:
                report["starving"] = {
                    "holder": busy.get("workflowName") or busy.get("workflow"),
                    "held_minutes": round(held),
                    "waiting": len(waiting),
                }
    else:
        waiting = [t for t in tickets if t["state"] == "queued"]
        # Operator priority first, then age, then measured cost, then FIFO. The
        # sort key is derived fresh every tick and never stored on the ticket —
        # see dispatch_key for why that is load-bearing.
        waiting.sort(key=lambda t: dispatch_key(queue, t, moment))
        if waiting:
            report["dispatch"] = waiting[0]
            report["dispatch_reason"] = dispatch_reason(queue, waiting[0], moment)
            # Only worth saying when the new terms actually moved something. A
            # scheduler that narrates every ordinary FIFO turn is a log nobody
            # reads, which is how the useful line gets missed.
            fifo = sorted(waiting, key=lambda t: (t.get("priority", 0),
                                                  t.get("requested_at") or ""))
            if fifo and fifo[0] is not waiting[0]:
                report["overtook"] = {
                    "chosen": waiting[0]["id"],
                    "ahead_of": fifo[0]["id"],
                    "why": report["dispatch_reason"],
                }

    # 6. The silence detector.
    #
    # On 2026-07-30 one ticket sat in state `dispatched` with no run behind it
    # while the lock group stood empty and eleven consecutive drain ticks
    # reported SUCCESS, dispatched nothing, and printed not one line about it.
    # Every existing alarm was satisfied: the drainer was alive (last_tick
    # fresh), the ticket was young (STUCK_AFTER_HOURS is 14), the orphans were
    # all resolved. Nothing in the system was watching for "there is work, the
    # lock is free, and still nothing left".
    #
    # So that exact conjunction is recorded here, in the committed file, with
    # the moment it began. It is RECOMPUTED FROM LIVE FACTS on every tick —
    # cleared only by a real dispatch, a busy group or an empty queue — so
    # editing it out of the file buys silence until the next tick and no
    # longer. That is what stops the alarm being quietly satisfiable.
    live = [t for t in tickets if t["state"] in ("queued", "dispatched")]
    stalled = bool(live) and not report["busy"] and report["dispatch"] is None
    if stalled:
        if not queue.get("idle_since"):
            queue["idle_since"] = _iso(moment)
    elif queue.get("idle_since"):
        queue["idle_since"] = None
    report["idle_since"] = queue.get("idle_since")

    if report["dispatch"] is not None:
        queue["last_dispatch"] = _iso(moment)

    queue["last_tick"] = _iso(moment)
    return report


def mark_dispatched(ticket: dict, now: datetime | None = None) -> None:
    moment = now or _now()
    ticket["state"] = "dispatched"
    ticket["dispatched_at"] = _iso(moment)
    _log(ticket, "dispatched", "")


def prune(queue: dict, keep_terminal: int = 60) -> dict:
    """Keep the file readable. Terminal tickets are history, not state."""
    tickets = queue.get("tickets", [])
    live = [t for t in tickets if t["state"] not in TERMINAL_STATES]
    done = [t for t in tickets if t["state"] in TERMINAL_STATES]
    done.sort(key=lambda t: t.get("requested_at") or "")
    queue["tickets"] = live + done[-keep_terminal:]
    return queue


# --------------------------------------------------------------------------
# what ops_status.py and the health digest read
# --------------------------------------------------------------------------

def superseded_by(queue: dict, ticket: dict) -> str | None:
    """The id of a LATER ticket in the same chain that landed, or None.

    A transient upstream failure used to redden drain-writers on every tick
    until a human cleared it, long after the work had actually succeeded. On
    2026-08-01 opendart_korea hit a 45s read timeout at 20:17, the chain
    requeued itself, the 22:42 slice landed, and the 20:17 ticket kept the job
    red for five consecutive ticks in between. Nothing was wrong by then and
    every one of those runs said something was.

    That is the alarm-fatigue failure this repo keeps writing rules against: a
    channel that cries wolf about work that already completed is a channel that
    gets filtered, and the next real breakage goes unread.

    A chain is workflow + exact inputs, the same identity `chain_priority`
    uses, because `backfill_slices.next_inputs` re-emits a job's inputs
    unchanged every slice. So a later LANDED ticket of the same chain is proof
    the chain got past this failure on its own.

    This is NOT an auto-retry and it does not resurrect anything: the ticket
    stays failed, stays in the file, and stays visible in `status`. It simply
    stops being a reason to go red, on evidence rather than on a timer. A
    failure with no later success behind it still needs a human, which is the
    case the loud path exists for.
    """
    mine = str(ticket.get("requested_at") or "")
    for other in queue.get("tickets", []):
        if other is ticket or other.get("state") != "landed":
            continue
        if not same_chain(other, ticket.get("workflow"), ticket.get("inputs")):
            continue
        if str(other.get("requested_at") or "") > mine:
            return str(other.get("id"))
    return None


def summary(queue: dict | None = None, now: datetime | None = None) -> dict:
    """Queue state as plain data. Returns `problems` — anything in it means a
    human is needed, and both ops_status.py and the drainer exit non-zero."""
    data = queue if queue is not None else load()
    moment = now or _now()
    tickets = data.get("tickets", [])

    counts: dict[str, int] = {}
    for ticket in tickets:
        counts[ticket["state"]] = counts.get(ticket["state"], 0) + 1

    problems: list[str] = []
    recovered: list[str] = []
    for ticket in tickets:
        if ticket.get("acknowledged"):
            # A human read it and said so. It stays in the file as history; it
            # stops being a reason to go red, because a job that is red forever
            # is a job nobody reads.
            continue
        if ticket["state"] in ("failed", "abandoned"):
            later = superseded_by(queue, ticket)
            if later:
                # The chain recovered without us. Recorded, not red.
                recovered.append(
                    f"{ticket['workflow']} failed at {ticket['id']} and the "
                    f"chain landed anyway at {later}; no action needed")
                continue
        if ticket["state"] == "failed":
            problems.append(
                f"{ticket['workflow']} FAILED ({ticket['id']}) — it will not be "
                "retried automatically; read the run, then re-queue it")
        elif ticket["state"] == "abandoned":
            problems.append(
                f"{ticket['workflow']} was displaced {ticket['attempts']}x and "
                f"given up on ({ticket['id']})")
        elif ticket["state"] in ("queued", "dispatched"):
            asked = _parse(ticket.get("requested_at"))
            if asked and (moment - asked) > timedelta(hours=STUCK_AFTER_HOURS):
                hours = (moment - asked).total_seconds() / 3600
                problems.append(
                    f"{ticket['workflow']} has been waiting {hours:.0f}h "
                    f"({ticket['id']}) — the lock is starved")
            if int(ticket.get("unbound_count", 0)) >= UNBOUND_ALARM_COUNT:
                problems.append(
                    f"{ticket['workflow']} has been dispatched "
                    f"{ticket['unbound_count']}x and produced NO RUN either "
                    f"time ({ticket['id']}) — the dispatch itself is failing, "
                    "not the work. Read the 'Dispatch it' step of the last red "
                    "drain-writers run; check the ticket's inputs against the "
                    "workflow's declared ones and the WRITER_QUEUE_TOKEN secret")

    waiting = [t for t in tickets if t["state"] in ("queued", "dispatched")]
    idle = _parse(data.get("idle_since"))
    if idle and waiting and (moment - idle) > timedelta(minutes=IDLE_STALL_MINUTES):
        minutes = round((moment - idle).total_seconds() / 60)
        problems.append(
            f"the writer queue has {len(waiting)} ticket(s) waiting and the "
            f"{LOCK_GROUP} lock group has been EMPTY with nothing dispatched "
            f"for {minutes} minutes. The drainer is ticking and the queue is "
            "not moving — that is the shape of the 2026-07-30 stall, where "
            "eleven green ticks in a row said nothing")

    for orphan in data.get("orphans", []):
        if not orphan.get("resolved"):
            problems.append(
                f"{orphan['workflow']} run {orphan['run_id']} was displaced and "
                "was NOT queued, so its inputs are unknown — re-dispatch it by hand")

    return {
        "counts": counts,
        "waiting": waiting,
        "orphans": [o for o in data.get("orphans", []) if not o.get("resolved")],
        "last_tick": data.get("last_tick"),
        "last_dispatch": data.get("last_dispatch"),
        "idle_since": data.get("idle_since"),
        "problems": problems,
        # Failures the chain recovered from by itself. Reported, never red.
        "recovered": recovered,
    }


# --------------------------------------------------------------------------
# CLI — used by .github/workflows/drain-writers.yml
# --------------------------------------------------------------------------

def _cmd_enqueue(args) -> int:
    queue = load(Path(args.file) if args.file else None)
    try:
        inputs = json.loads(args.inputs) if args.inputs else {}
    except ValueError as exc:
        print(f"::error::--inputs is not valid JSON: {exc}")
        return 2
    if not isinstance(inputs, dict):
        print("::error::--inputs must be a JSON object")
        return 2

    # For a RECURRING request, "there is already one waiting" is the answer, not
    # a reason to write a second ticket. A nightly cron behind a long backfill
    # would otherwise leave a ticket per night, each one aging past
    # STUCK_AFTER_HOURS and reporting the lock as starved once per night for the
    # same single fact. Deliberately opt-in: two retractions of two different
    # rows are two pieces of work and must never collapse into one.
    if getattr(args, "if_absent", False):
        waiting = [t for t in queue.get("tickets", [])
                   if t["workflow"] == args.workflow
                   and t["state"] not in TERMINAL_STATES]
        if waiting:
            print(f"{args.workflow} is already in the queue as "
                  f"{waiting[0]['id']} ({waiting[0]['state']}) — not adding a "
                  f"second ticket for the same recurring pass.")
            return 0
    try:
        ticket = enqueue(queue, args.workflow, inputs, args.reason, args.by,
                         args.priority)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 2
    missing = missing_required_inputs(args.workflow, ticket["inputs"])
    if missing:
        print(f"::warning::{args.workflow} declares {', '.join(missing)} as "
              "required and this ticket does not carry them. The dispatch API "
              "will refuse it, and the drainer will mark it failed rather than "
              "retry it. Re-queue it with those inputs.")
    prune(queue)
    save(queue, Path(args.file) if args.file else None)
    print(f"queued {ticket['id']}  inputs={ticket['inputs']}")
    return 0


def _cmd_tick(args) -> int:
    path = Path(args.file) if args.file else None
    queue = load(path)
    # Snapshot before mutation so an idle tick can leave the file byte-identical.
    before = json.dumps(copy.deepcopy(queue), indent=2, sort_keys=True)
    prior_tick = queue.get("last_tick")
    runs = json.loads(Path(args.runs).read_text()) if args.runs else []
    report = tick(queue, runs)

    for ticket in report["landed"]:
        print(f"landed    {ticket['id']}")
    for ticket in report.get("unbound", []):
        print(f"::warning::{ticket['workflow']} ({ticket['id']}) was marked "
              f"dispatched {UNBOUND_AFTER_MINUTES}+ minutes ago and NO RUN ever "
              f"appeared for it (occurrence {ticket.get('unbound_count')}). Back "
              "in the line. A dispatch that creates nothing is the failure this "
              "queue exists to make audible, so read the 'Dispatch it' step of "
              "the run that sent it.")
    for ticket in report["displaced"]:
        print(f"::warning::DISPLACED and re-queued: {ticket['workflow']} "
              f"({ticket['id']}, attempt {ticket['attempts']}). It was evicted from "
              "the concurrency group's single pending slot without running.")
    for ticket in report["failed"]:
        print(f"::error::{ticket['workflow']} failed ({ticket['id']}) — not retried "
              "automatically, because re-running a half-applied correction can "
              "double-apply it. Read the run, then re-queue.")
    for ticket in report["abandoned"]:
        print(f"::error::{ticket['workflow']} displaced {ticket['attempts']}x and "
              f"abandoned ({ticket['id']}).")
    if report["orphans"]:
        print(f"::warning::noticed {len(report['orphans'])} writer run(s) that were "
              "evicted from the lock's single pending slot without ever running. "
              "They were dispatched directly rather than queued, so GitHub will not "
              "tell us what inputs they were given and they cannot be replayed "
              "automatically. Each is listed below.")

    if report["busy"]:
        busy = report["busy"]
        print(f"group busy: {busy.get('workflowName')} "
              f"({busy.get('status')}, since {busy.get('createdAt')}) — holding off")
    if report.get("starving"):
        starving = report["starving"]
        print(f"::warning::{starving['holder']} has held the writer lock for "
              f"{starving['held_minutes']} minutes with {starving['waiting']} "
              "ticket(s) waiting. Long backfills and short corrections share one "
              "lock by necessity; if this keeps happening, run the backfills in "
              "bounded slices that requeue themselves rather than as one run.")

    plan = report["dispatch"]
    if plan:
        mark_dispatched(plan)
        print(f"dispatching {plan['workflow']} ({plan['id']}) inputs={plan['inputs']}")
        if report.get("dispatch_reason"):
            print(f"  chosen because {report['dispatch_reason']}")
        if report.get("overtook"):
            over = report["overtook"]
            print(f"  it goes ahead of {over['ahead_of']}, which asked first. "
                  "Priority is unchanged on both — the order is derived from "
                  "measured slice length and is recomputed every tick.")
        if args.emit:
            Path(args.emit).write_text(json.dumps(
                {"workflow": plan["workflow"], "ticket": plan["id"],
                 "body": {"ref": args.ref, "inputs": plan["inputs"]}}, indent=2) + "\n")
    else:
        # Say why, every time. A tick that dispatches nothing used to print
        # NOTHING AT ALL unless the group was busy, so eleven consecutive runs
        # on 2026-07-30 each showed a green step with an empty log and a queue
        # that never moved. "Nothing to do" and "cannot do it" have to look
        # different from each other in the log.
        live = [t for t in queue.get("tickets", [])
                if t["state"] in ("queued", "dispatched")]
        if not live:
            print("nothing dispatched: the queue is empty.")
        elif report["busy"]:
            print(f"nothing dispatched: {len(live)} ticket(s) waiting on a busy group.")
        else:
            waiting = ", ".join(f"{t['id']} [{t['state']}]" for t in live)
            print(f"::warning::nothing dispatched, and the {LOCK_GROUP} group is "
                  f"EMPTY, yet {len(live)} ticket(s) are live: {waiting}. Nothing "
                  "is in state 'queued', so there is nothing this tick may send. "
                  f"Idle since {report.get('idle_since')}.")

    prune(queue)

    state = summary(queue)
    # The heartbeat is only worth recording while something is actually waiting.
    # Writing it on every idle tick would commit to main four times an hour
    # forever, and every writer rebases onto main. When there IS live work, a
    # stale last_tick is the signal that the drainer itself has died.
    if not (state["waiting"] or state["orphans"]):
        queue["last_tick"] = prior_tick
    after = json.dumps(copy.deepcopy(queue), indent=2, sort_keys=True)
    if after != before:
        save(queue, path)

    # Printed, deliberately not as ::error::. A chain that recovered on its own
    # is worth seeing in the log and is not worth a red run or an email.
    for note in state.get("recovered", []):
        print(f"recovered: {note}")
    for problem in state["problems"]:
        print(f"::error::{problem}")
    if state["problems"]:
        print(f"\n{len(state['problems'])} item(s) need a human.")
        return 2
    return 0


def _cmd_requeue(args) -> int:
    """Put a ticket back in the line.

    The drainer calls this when its own dispatch did not produce a run — if we
    left the ticket marked `dispatched` it would sit there forever waiting to be
    bound to a run that does not exist, which is a silent stall.
    """
    path = Path(args.file) if args.file else None
    queue = load(path)
    hits = [t for t in queue.get("tickets", []) if t["id"] == args.ticket]
    if not hits:
        print(f"::error::no ticket {args.ticket}")
        return 2
    for ticket in hits:
        ticket["state"] = "queued"
        ticket["run_id"] = None
        ticket["dispatched_at"] = None
        _log(ticket, "requeued", args.note)
    save(queue, path)
    print(f"requeued {args.ticket}")
    return 0


def _cmd_dispatch_failed(args) -> int:
    """The dispatch API refused, or produced nothing. Record which, then shout.

    `requeue` alone was the wrong tool for both halves of this. A dispatch the
    API REFUSES will be refused identically forever — putting the ticket back in
    the line just hides a permanent fault behind an infinite retry, which is
    exactly what happened to the GDELT ticket on 2026-07-30: every dispatch
    answered 422 and the queue reported success. `--permanent` marks it failed
    instead, which `summary()` reports as needing a human and which
    `resolve` is the way out of.

    A transient failure (a token that cannot create runs, a 5xx) does belong
    back in the line, but it must still be COUNTED, so the second occurrence is
    red rather than the eight-thousandth.
    """
    path = Path(args.file) if args.file else None
    queue = load(path)
    hits = [t for t in queue.get("tickets", []) if t["id"] == args.ticket]
    if not hits:
        print(f"::error::no ticket {args.ticket}")
        return 2
    for ticket in hits:
        ticket["run_id"] = None
        ticket["dispatched_at"] = None
        ticket["unbound_count"] = int(ticket.get("unbound_count", 0)) + 1
        if args.permanent:
            ticket["state"] = "failed"
            _log(ticket, "dispatch-refused", args.note)
        else:
            ticket["state"] = "queued"
            _log(ticket, "dispatch-failed", args.note)
    save(queue, path)
    verb = "refused permanently" if args.permanent else "failed; back in the line"
    print(f"::error::dispatch of {args.ticket} {verb}: {args.note}")
    return 0


def _cmd_drop(args) -> int:
    """Withdraw a ticket that is still WAITING, before it is ever dispatched.

    There was no way to do this and there needed to be, because the queue is
    append-only by design and hand-editing the file is how you desynchronise it
    from the runs it is tracking. Two identical `backfill-gdelt-2026` tickets
    were queued on 2026-08-01; both resume from the same committed cursor, so
    the second would have re-run the first's slice — no rows lost, no rows
    doubled, just one paid slice of history nobody asked for.

    Withdrawing is only ever legitimate BEFORE dispatch. A `dispatched` ticket
    is bound to a run that is already holding the lock, and forgetting it here
    would leave that run unaccounted for — which is the eviction bug's own
    fingerprint. Use `requeue` or `resolve` for those.

    The ticket is marked `abandoned` and acknowledged in one step rather than
    deleted: the file keeps landed and dead work as history, and a withdrawal
    with a note explaining it is history worth keeping. Acknowledging it is
    what stops `summary()` reporting a deliberate decision as a problem.
    """
    path = Path(args.file) if args.file else None
    queue = load(path)
    hits = [t for t in queue.get("tickets", []) if t["id"] == args.ticket]
    if not hits:
        print(f"::error::no ticket {args.ticket}")
        return 2
    for ticket in hits:
        if ticket["state"] != "queued":
            print(f"::error::{args.ticket} is {ticket['state']}, not queued. "
                  "Only a ticket that has never been dispatched can be "
                  "withdrawn; a dispatched one is bound to a run that holds "
                  "the lock, and dropping it here would lose the run.")
            return 2
        ticket["state"] = "abandoned"
        ticket["acknowledged"] = _iso(_now())
        _log(ticket, "dropped", args.note)
    save(queue, path)
    print(f"dropped {args.ticket}: {args.note}")
    return 0


def _cmd_status(args) -> int:
    state = summary(load(Path(args.file) if args.file else None))
    print(json.dumps(state, indent=2, default=str))
    return 2 if state["problems"] else 0


def _cmd_resolve(args) -> int:
    """Mark an orphan — or a failed ticket — handled, once a human has decided.

    Deciding NOT to re-run something is a legitimate outcome — several of the
    runs lost on 2026-07-29 were duplicate dispatches of the same backfill. What
    is not legitimate is the decision never being made, so it stays loud until
    someone says otherwise here, and the note records who said what.

    A FAILED ticket needs the same escape and did not have one. `summary()`
    reports every failed ticket as a problem, `prune` keeps terminal tickets,
    and the drainer exits non-zero on any problem — so one correction that went
    red left drain-writers red on every tick from then on, including after the
    correction had been read, fixed and re-queued. A permanently red job is the
    thing this repo can least afford: it is how a real failure becomes
    invisible, which is the whole theme of the day it was built.
    """
    path = Path(args.file) if args.file else None
    queue = load(path)
    target = str(args.run_id)

    orphans = queue.get("orphans", [])
    if target == "all":
        hits = [o for o in orphans if not o.get("resolved")]
    else:
        hits = [o for o in orphans if o["run_id"] == target]
    for orphan in hits:
        orphan["resolved"] = _iso(_now())
        orphan["resolved_note"] = args.note

    # "all" covers FAILED TICKETS too, not just orphans. It did not, and the
    # help text promised it did ("mark an orphan run, or a failed ticket,
    # handled"), so `resolve=all` looked like it had cleared the backlog and
    # left five failed tickets untouched. drain-writers then went red on every
    # tick for hours — the permanently-red job this function exists to prevent,
    # reintroduced by the escape hatch itself (2026-08-01).
    tickets = [t for t in queue.get("tickets", [])
               if (target == "all" or t["id"] == target)
               and t["state"] in ("failed", "abandoned")
               and not t.get("acknowledged")]
    for ticket in tickets:
        ticket["acknowledged"] = _iso(_now())
        _log(ticket, "acknowledged", args.note)

    if not (hits or tickets):
        print(f"::error::nothing unresolved matching {target!r} — expected an "
              "orphan run id, a failed ticket id, or 'all'")
        return 2
    save(queue, path)
    if hits:
        print(f"resolved {len(hits)} orphan(s): "
              + ", ".join(o["run_id"] for o in hits))
    if tickets:
        print(f"acknowledged {len(tickets)} failed ticket(s): "
              + ", ".join(t["id"] for t in tickets))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="queue file (default data/writer_queue.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("enqueue", help="ask for a writer workflow to be run")
    add.add_argument("workflow")
    add.add_argument("--inputs", default="", help="JSON object of workflow inputs")
    add.add_argument("--reason", default="")
    add.add_argument("--by", default="")
    add.add_argument("--priority", type=int, default=None)
    add.add_argument("--if-absent", action="store_true",
                     help="do nothing if a ticket for this workflow is already "
                          "waiting. For recurring passes only.")
    add.set_defaults(func=_cmd_enqueue)

    run = sub.add_parser("tick", help="reconcile, then pick at most one thing to dispatch")
    run.add_argument("--runs", help="JSON file of recent runs from the API")
    run.add_argument("--emit", help="write the dispatch plan here")
    run.add_argument("--ref", default="main")
    run.set_defaults(func=_cmd_tick)

    back = sub.add_parser("requeue", help="put a dispatched ticket back in the line")
    back.add_argument("ticket")
    back.add_argument("--note", default="")
    back.set_defaults(func=_cmd_requeue)

    dead = sub.add_parser(
        "dispatch-failed",
        help="the dispatch API refused this ticket, or created no run")
    dead.add_argument("ticket")
    dead.add_argument("--note", default="")
    dead.add_argument("--permanent", action="store_true",
                      help="the API refused it and always will (a 422 on the "
                           "inputs). Marks it failed rather than retrying it "
                           "forever.")
    dead.set_defaults(func=_cmd_dispatch_failed)

    gone = sub.add_parser(
        "drop", help="withdraw a ticket that is still queued (never dispatched)")
    gone.add_argument("ticket")
    gone.add_argument("--note", default="")
    gone.set_defaults(func=_cmd_drop)

    show = sub.add_parser("status", help="queue state as JSON")
    show.set_defaults(func=_cmd_status)

    fixed = sub.add_parser(
        "resolve",
        help="mark an orphan run, or a failed ticket, handled by a human")
    fixed.add_argument("run_id", metavar="RUN_ID_OR_TICKET_ID")
    fixed.add_argument("--note", default="")
    fixed.set_defaults(func=_cmd_resolve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
