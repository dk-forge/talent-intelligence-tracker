"""The queue that stops a database writer vanishing without anyone knowing.

Every workflow that writes the database shares the `talent-collect` lock, and
GitHub keeps exactly ONE pending run per lock. While one run executes and a
second waits, dispatching a THIRD silently evicts the waiting one: it ends
`cancelled` having created no jobs, with no error, no annotation and nothing in
any log saying work was lost.

Measured 2026-07-29: seven writer runs were thrown away that way in two hours
behind a GDELT backfill, and each was reported to the owner as "queued". These
tests pin the properties that make that impossible to repeat quietly.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import writer_queue as wq

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
DRAINER = WORKFLOWS / "drain-writers.yml"

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _run(run_id, workflow, status="completed", conclusion="success",
         created=None, job_count=1, event="workflow_dispatch"):
    return {
        "databaseId": run_id,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "createdAt": (created or NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job_count": job_count,
        "event": event,
    }


@pytest.fixture
def members():
    return wq.lock_group_workflows()


# --------------------------------------------------------------------------
# the drainer must be able to run while the lock is held
# --------------------------------------------------------------------------

def test_the_drainer_is_not_itself_in_the_writers_lock():
    """A drainer that queues behind the lock could never drain it — and would
    be evicted from the pending slot by the very writers it is watching."""
    parsed = yaml.safe_load(DRAINER.read_text())
    group = (parsed.get("concurrency") or {}).get("group")
    assert group and group != wq.LOCK_GROUP, (
        f"drain-writers.yml sits in {group!r}, the writers' own lock")


def test_the_drainer_never_touches_the_database():
    """It must not become a writer by accident. tests/test_workflows.py would
    then demand it join the lock, which would break the previous property."""
    assert "talent_intel.db" not in DRAINER.read_text()


def test_the_drainer_watches_every_member_of_the_lock(members):
    """A fourteenth writer added without a line here would drain slowly (cron
    only) and, worse, would never wake the drainer when it finished."""
    parsed = yaml.safe_load(DRAINER.read_text())
    trigger = parsed.get("on") or parsed.get(True)
    watched = set(trigger["workflow_run"]["workflows"])
    missing = set(members.values()) - watched
    assert not missing, f"drain-writers.yml does not watch: {sorted(missing)}"


def test_enrich_is_a_lock_member_even_though_it_never_names_the_file(members):
    """tests/test_workflows.py finds writers by searching for the database
    filename. enrich.yml writes through pipeline.publish and never mentions it,
    so that heuristic misses it — yet it holds the lock and was evicted twice on
    2026-07-29. Membership of the group is the honest definition, and this
    module uses it."""
    assert "enrich.yml" in members


# --------------------------------------------------------------------------
# the invariant: never create a second pending run
# --------------------------------------------------------------------------

def test_nothing_is_dispatched_while_the_group_is_busy(members):
    """This is the whole fix. Eviction is only possible when something is
    already waiting, so the drainer never puts a second run in the queue."""
    queue = wq.empty_queue()
    wq.enqueue(queue, "correct-form-d.yml", {"dry_run": False}, members=members, now=NOW)
    runs = [_run(1, "backfill-gdelt-2026", status="in_progress", conclusion=None)]

    report = wq.tick(queue, runs, members, now=NOW)

    assert report["dispatch"] is None
    assert report["busy"]["workflowName"] == "backfill-gdelt-2026"
    assert queue["tickets"][0]["state"] == "queued"


@pytest.mark.parametrize("status", sorted(wq.BUSY_STATUSES))
def test_every_waiting_status_counts_as_busy(members, status):
    """The run that displaced the others was `pending`, not `in_progress`. A
    definition of "busy" that only knew about running jobs would reproduce the
    bug exactly."""
    queue = wq.empty_queue()
    wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    runs = [_run(1, "collect", status=status, conclusion=None)]
    assert wq.tick(queue, runs, members, now=NOW)["dispatch"] is None


def test_an_empty_group_dispatches_exactly_one(members):
    queue = wq.empty_queue()
    wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    wq.enqueue(queue, "recall.yml", members=members, now=NOW + timedelta(seconds=1))

    report = wq.tick(queue, [], members, now=NOW)

    assert report["dispatch"]["workflow"] == "enrich.yml"
    assert [t["state"] for t in queue["tickets"]] == ["queued", "queued"]


def test_short_jobs_leave_before_long_backfills(members):
    """A three-hour GDELT backfill starving a thirty-second correction is real.
    They must share the lock — one writer at a time is not negotiable — but they
    need not share a place in the line."""
    queue = wq.empty_queue()
    wq.enqueue(queue, "backfill-gdelt-2026.yml", {"start": "2026-01-01", "end": "2026-01-31"},
               members=members, now=NOW)
    wq.enqueue(queue, "correct-form-d.yml", {"dry_run": False},
               members=members, now=NOW + timedelta(minutes=1))

    report = wq.tick(queue, [], members, now=NOW)
    assert report["dispatch"]["workflow"] == "correct-form-d.yml"


def test_the_holder_reported_is_the_one_actually_running(members):
    """The pending run is the victim-in-waiting, not the lock holder. Naming it
    would make a three-hour backfill read as an eleven-minute one, and hide
    exactly the starvation worth seeing."""
    runs = [
        _run(2, "collect", status="pending", conclusion=None, created=NOW - timedelta(minutes=11)),
        _run(1, "backfill-gdelt-2026", status="in_progress", conclusion=None,
             created=NOW - timedelta(minutes=138)),
    ]
    assert wq.group_busy(runs, members)["workflowName"] == "backfill-gdelt-2026"


def test_a_long_hold_with_work_waiting_is_called_starvation(members):
    """Priority decides who goes next; it cannot preempt what is already
    running. A backfill entitled to 350 minutes will starve a thirty-second
    correction, and that should be said rather than discovered."""
    queue = wq.empty_queue()
    wq.enqueue(queue, "correct-form-d.yml", {"dry_run": False}, members=members, now=NOW)
    runs = [_run(1, "backfill-gdelt-2026", status="in_progress", conclusion=None,
                 created=NOW - timedelta(minutes=wq.LONG_HOLD_MINUTES + 18))]

    report = wq.tick(queue, runs, members, now=NOW)

    assert report["starving"]["holder"] == "backfill-gdelt-2026"
    assert report["starving"]["waiting"] == 1


def test_a_short_hold_is_not_called_starvation(members):
    queue = wq.empty_queue()
    wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    runs = [_run(1, "collect", status="in_progress", conclusion=None,
                 created=NOW - timedelta(minutes=5))]
    assert not wq.tick(queue, runs, members, now=NOW).get("starving")


# --------------------------------------------------------------------------
# eviction must never be silent
# --------------------------------------------------------------------------

def test_a_run_that_created_no_jobs_is_the_eviction_fingerprint():
    """All seven runs lost on 2026-07-29 had zero jobs. A run a human cancels
    mid-flight has jobs, and must not be confused with this."""
    assert wq.never_started({"job_count": 0})
    assert not wq.never_started({"job_count": 3})
    assert wq.never_started({"jobs": []})
    assert not wq.never_started({"jobs": [{"name": "collect"}]})


def test_an_evicted_ticket_is_requeued_with_its_inputs_intact(members):
    """The inputs are the point. GitHub does not expose a dispatched run's
    inputs, so a re-dispatch that guessed would turn `dry_run=false` back into
    the default `true` and apply nothing — silently."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": False},
                        members=members, now=NOW)
    ticket["state"] = "dispatched"
    ticket["run_id"] = "999"
    ticket["dispatched_at"] = wq._iso(NOW)

    runs = [_run("999", "correct-form-d", conclusion="cancelled", job_count=0)]
    report = wq.tick(queue, runs, members, now=NOW + timedelta(minutes=1))

    assert report["displaced"] == [ticket]
    assert ticket["state"] == "queued"
    assert ticket["attempts"] == 1
    assert ticket["inputs"] == {"dry_run": "false"}
    assert ticket["run_id"] is None


def test_an_evicted_run_nobody_queued_becomes_a_loud_orphan(members):
    """Dispatched directly rather than queued. We cannot replay it, because the
    API will not tell us what inputs it was given — so it goes red and names
    itself rather than being quietly forgotten."""
    queue = wq.empty_queue()
    runs = [_run("555", "correct-sec-pillar", conclusion="cancelled", job_count=0)]

    report = wq.tick(queue, runs, members, now=NOW)

    assert len(report["orphans"]) == 1
    assert report["orphans"][0]["run_id"] == "555"

    problems = wq.summary(queue, now=NOW)["problems"]
    assert any("555" in p for p in problems)


def test_an_orphan_stays_loud_until_a_human_decides(tmp_path, members):
    """Including the decision NOT to re-run it — several of the runs lost on
    2026-07-29 were duplicate dispatches of the same backfill. What must never
    happen is the decision never being made."""
    path = tmp_path / "writer_queue.json"
    runs = tmp_path / "runs.json"
    queue = wq.empty_queue()
    runs.write_text(json.dumps([
        _run("555", "recall", conclusion="cancelled", job_count=0),
        _run("556", "enrich", conclusion="cancelled", job_count=0),
    ]))
    wq.save(queue, path)

    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 2
    assert len(wq.summary(wq.load(path))["problems"]) == 2

    assert wq.main(["--file", str(path), "resolve", "555", "--note", "rerun"]) == 0
    assert len(wq.summary(wq.load(path))["problems"]) == 1

    assert wq.main(["--file", str(path), "resolve", "all", "--note", "duplicates"]) == 0
    assert wq.summary(wq.load(path))["problems"] == []
    # Resolved is not forgotten: the record of the loss survives.
    assert len(wq.load(path)["orphans"]) == 2


def test_resolving_something_that_is_not_waiting_is_an_error(tmp_path):
    path = tmp_path / "writer_queue.json"
    wq.save(wq.empty_queue(), path)
    assert wq.main(["--file", str(path), "resolve", "nope"]) == 2


def test_an_orphan_is_only_noticed_once(members):
    queue = wq.empty_queue()
    runs = [_run("555", "recall", conclusion="cancelled", job_count=0)]
    wq.tick(queue, runs, members, now=NOW)
    second = wq.tick(queue, runs, members, now=NOW + timedelta(minutes=15))
    assert second["orphans"] == []
    assert len(queue["orphans"]) == 1


def test_a_cancel_after_the_run_started_is_not_treated_as_eviction(members):
    """A human cancelling a half-applied correction must not be undone by an
    automatic re-dispatch — that could double-apply it."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": False},
                        members=members, now=NOW)
    ticket.update(state="dispatched", run_id="777", dispatched_at=wq._iso(NOW))

    runs = [_run("777", "correct-form-d", conclusion="cancelled", job_count=2)]
    wq.tick(queue, runs, members, now=NOW + timedelta(minutes=1))

    assert ticket["state"] == "failed"
    assert wq.summary(queue, now=NOW)["problems"]


def test_a_failing_run_is_not_retried_forever(members):
    """A job that fails on its own merits is a bug, not a lock problem. Looping
    it would burn the LLM budget rediscovering the same failure."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    ticket.update(state="dispatched", run_id="888", dispatched_at=wq._iso(NOW))

    wq.tick(queue, [_run("888", "enrich", conclusion="failure")], members,
            now=NOW + timedelta(minutes=1))

    assert ticket["state"] == "failed"
    assert any("FAILED" in p for p in wq.summary(queue, now=NOW)["problems"])


def test_endless_eviction_eventually_gives_up_loudly(members):
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "recall.yml", members=members, now=NOW)
    ticket["attempts"] = wq.MAX_ATTEMPTS - 1
    ticket.update(state="dispatched", run_id="100", dispatched_at=wq._iso(NOW))

    wq.tick(queue, [_run("100", "recall", conclusion="cancelled", job_count=0)],
            members, now=NOW + timedelta(minutes=1))

    assert ticket["state"] == "abandoned"
    assert wq.summary(queue, now=NOW)["problems"]


def test_a_success_lands_the_ticket(members):
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    ticket.update(state="dispatched", run_id="200", dispatched_at=wq._iso(NOW))

    wq.tick(queue, [_run("200", "enrich", conclusion="success")], members,
            now=NOW + timedelta(minutes=1))

    assert ticket["state"] == "landed"
    assert wq.summary(queue, now=NOW)["problems"] == []


# --------------------------------------------------------------------------
# stalls
# --------------------------------------------------------------------------

def test_a_dispatch_that_produced_no_run_goes_back_in_the_line(members):
    """Otherwise the ticket waits forever to be bound to a run that does not
    exist — a silent stall, which is the defect wearing a different hat."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    ticket.update(state="dispatched", dispatched_at=wq._iso(NOW))

    wq.tick(queue, [], members,
            now=NOW + timedelta(minutes=wq.UNBOUND_AFTER_MINUTES + 1))

    assert ticket["state"] == "queued"


# --------------------------------------------------------------------------
# 2026-07-30: the queue stopped moving and eleven green ticks said nothing
#
# A GDELT ticket was queued carrying `slice: "true"`, an input
# backfill-gdelt-2026.yml does not declare. Every dispatch answered
#   Unexpected inputs provided: ["slice"] (HTTP 422)
# and `set -euo pipefail` killed the dispatch step on that line, before the
# requeue below it — so the ticket stayed in state `dispatched` with run_id
# None. Every later tick found ZERO tickets in state `queued`, emitted no plan,
# skipped the dispatch step, and exited 0. Measured: 11 drain runs between
# 10:17Z and 18:09Z, all green, queue unchanged apart from `last_tick`.
#
# These are the properties that make that impossible to repeat quietly.
# --------------------------------------------------------------------------

def test_eligible_tickets_and_an_empty_group_always_produce_a_plan(members, tmp_path):
    """THE property. Whatever else is in the file, if something is dispatchable
    and nothing holds the lock, a plan is emitted — and it reaches the file the
    workflow actually gates the dispatch step on."""
    path = tmp_path / "q.json"
    plan = tmp_path / "plan.json"
    runs = tmp_path / "runs.json"
    runs.write_text("[]")

    # The CLI reconciles against the real clock, so the ticket has to be one a
    # real tick would consider fresh.
    now = datetime.now(timezone.utc)

    queue = wq.empty_queue()
    # History, an acknowledged failure and a resolved orphan: everything that
    # was in the real file on the day, none of which may suppress a dispatch.
    done = wq.enqueue(queue, "enrich.yml", {"dry_run": "true"}, members=members, now=now)
    done["state"] = "landed"
    dead = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"},
                      members=members, now=now)
    dead.update(state="failed", acknowledged=wq._iso(now))
    queue["orphans"] = [{"run_id": "1", "workflow": "enrich", "resolved": wq._iso(now)}]
    live = wq.enqueue(queue, "recall.yml", {"publish": "false"},
                      members=members, now=now + timedelta(seconds=1))
    wq.save(queue, path)

    assert wq.main(["--file", str(path), "tick", "--runs", str(runs),
                    "--emit", str(plan), "--ref", "main"]) == 0

    assert plan.exists(), (
        "no plan.json means the workflow's 'Dispatch it' step is skipped and "
        "the queue never moves — with a green run and an empty log")
    assert json.loads(plan.read_text())["ticket"] == live["id"]
    stored = {t["id"]: t for t in wq.load(path)["tickets"]}
    assert stored[live["id"]]["state"] == "dispatched"


def test_a_ticket_stuck_in_dispatched_still_leaves_the_group_free(members):
    """The exact state main was left in: one ticket `dispatched`, no run behind
    it, nothing queued. The tick has nothing it may send — and must therefore
    record that it is stalled, because that is the only trace it leaves."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "backfill-gdelt-2026.yml",
                        {"start": "2026-01-01", "end": "2026-07-26"},
                        members=members, now=NOW)
    ticket.update(state="dispatched", dispatched_at=wq._iso(NOW), run_id=None)

    report = wq.tick(queue, [], members, now=NOW + timedelta(minutes=20))

    assert report["dispatch"] is None and report["busy"] is None
    assert queue["idle_since"] == wq._iso(NOW + timedelta(minutes=20))


def test_a_queue_that_stops_moving_with_a_free_lock_goes_red(members):
    """The alarm nothing in the system had. Every other check was satisfied on
    the day: the drainer was alive, the ticket was 20 minutes old against a
    14-hour threshold, and every orphan was resolved."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "backfill-gdelt-2026.yml",
                        {"start": "2026-01-01", "end": "2026-07-26"},
                        members=members, now=NOW)
    ticket.update(state="dispatched", dispatched_at=wq._iso(NOW), run_id=None)
    wq.tick(queue, [], members, now=NOW + timedelta(minutes=20))

    soon = wq.summary(queue, now=NOW + timedelta(minutes=25))["problems"]
    assert not soon, "one tick of quiet is not a stall"

    later = NOW + timedelta(minutes=25 + wq.IDLE_STALL_MINUTES)
    problems = wq.summary(queue, now=later)["problems"]
    assert any("EMPTY with nothing dispatched" in p for p in problems)


def test_the_stall_clock_is_recomputed_from_facts_not_trusted(members):
    """An alarm you can turn off by editing the file it lives in is not an
    alarm. `idle_since` is derived on every tick, so clearing it by hand buys
    exactly one tick of silence."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    ticket.update(state="dispatched", dispatched_at=wq._iso(NOW), run_id=None)
    wq.tick(queue, [], members, now=NOW + timedelta(minutes=5))
    assert queue["idle_since"]

    queue["idle_since"] = None                       # somebody "fixes" it
    wq.tick(queue, [], members, now=NOW + timedelta(minutes=10))
    assert queue["idle_since"] == wq._iso(NOW + timedelta(minutes=10))


def test_a_real_dispatch_clears_the_stall_clock(members):
    """And the only thing that clears it durably is the queue actually moving."""
    queue = wq.empty_queue()
    stuck = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    stuck.update(state="dispatched", dispatched_at=wq._iso(NOW), run_id=None)
    wq.tick(queue, [], members, now=NOW + timedelta(minutes=5))
    assert queue["idle_since"]

    wq.enqueue(queue, "recall.yml", {"publish": "false"}, members=members,
               now=NOW + timedelta(minutes=6))
    report = wq.tick(queue, [], members, now=NOW + timedelta(minutes=7))

    assert report["dispatch"]["workflow"] == "recall.yml"
    assert queue["idle_since"] is None
    assert queue["last_dispatch"] == wq._iso(NOW + timedelta(minutes=7))


def test_a_busy_group_is_not_a_stall(members):
    """A 90-minute backfill holding the lock is the system working. Calling that
    a stall would make the alarm noise, and a noisy alarm is an ignored one."""
    queue = wq.empty_queue()
    wq.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"},
               members=members, now=NOW)
    runs = [_run(1, "backfill-gdelt-2026", status="in_progress", conclusion=None)]

    wq.tick(queue, runs, members, now=NOW + timedelta(minutes=5))

    assert queue.get("idle_since") is None
    assert not wq.summary(
        queue, now=NOW + timedelta(minutes=5 + wq.IDLE_STALL_MINUTES))["problems"]


def test_a_dispatch_that_keeps_vanishing_is_reported_not_retried_silently(members):
    """The unbound requeue was silent and uncounted, so a ticket the API refuses
    every time went round that loop forever with nothing said."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)

    moment = NOW
    for _ in range(wq.UNBOUND_ALARM_COUNT):
        ticket.update(state="dispatched", dispatched_at=wq._iso(moment))
        moment += timedelta(minutes=wq.UNBOUND_AFTER_MINUTES + 1)
        report = wq.tick(queue, [], members, now=moment)
        assert ticket in report["unbound"]

    assert ticket["unbound_count"] == wq.UNBOUND_ALARM_COUNT
    problems = wq.summary(queue, now=moment)["problems"]
    assert any("produced NO RUN" in p for p in problems)


def test_a_refused_dispatch_is_marked_failed_rather_than_retried_forever(tmp_path):
    """A 422 on the inputs is deterministic. Requeueing it is an infinite silent
    retry, which is what the drainer would have done for ever."""
    path = tmp_path / "q.json"
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", {"dry_run": "true"})
    ticket["state"] = "dispatched"
    wq.save(queue, path)

    assert wq.main(["--file", str(path), "dispatch-failed", ticket["id"],
                    "--permanent", "--note", "Unexpected inputs provided"]) == 0

    stored = wq.load(path)["tickets"][0]
    assert stored["state"] == "failed" and stored["dispatched_at"] is None
    assert any(ticket["id"] in p for p in wq.summary(wq.load(path))["problems"])


def test_a_transient_dispatch_failure_goes_back_in_the_line_but_counted(tmp_path):
    path = tmp_path / "q.json"
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", {"dry_run": "true"})
    ticket["state"] = "dispatched"
    wq.save(queue, path)

    assert wq.main(["--file", str(path), "dispatch-failed", ticket["id"],
                    "--note", "HTTP 503"]) == 0

    stored = wq.load(path)["tickets"][0]
    assert stored["state"] == "queued" and stored["unbound_count"] == 1


# --------------------------------------------------------------------------
# a ticket the dispatch API will always refuse must never be accepted
# --------------------------------------------------------------------------

def test_an_input_the_workflow_does_not_declare_is_refused_at_the_door(members):
    """`slice` is not a backfill-gdelt-2026.yml input. Accepting the ticket put
    a permanent 422 into a durable queue, where it stopped everything behind it
    and said nothing for hours. The API was the only thing that knew, and it
    only knew at dispatch time."""
    with pytest.raises(ValueError, match="slice"):
        wq.enqueue(wq.empty_queue(), "backfill-gdelt-2026.yml",
                   {"start": "2026-01-01", "end": "2026-07-26", "slice": "true"},
                   members=members, now=NOW)


def test_a_missing_required_input_is_named_where_a_human_will_read_it(tmp_path):
    """A warning rather than a refusal: unlike an undeclared input, a bare
    ticket is also how other modules assert a workflow CAN be queued."""
    assert wq.missing_required_inputs(
        "backfill-gdelt-2026.yml", {"start": "2026-01-01"}) == ["end"]
    assert wq.missing_required_inputs(
        "backfill-gdelt-2026.yml", {"start": "x", "end": "y"}) == []

    path = tmp_path / "q.json"
    wq.save(wq.empty_queue(), path)
    assert wq.main(["--file", str(path), "enqueue", "backfill-gdelt-2026.yml",
                    "--inputs", '{"start":"2026-01-01"}']) == 0


def test_the_real_ticket_that_stalled_the_queue_is_refused(tmp_path, members):
    """End to end through the CLI the owner actually types."""
    path = tmp_path / "q.json"
    wq.save(wq.empty_queue(), path)
    assert wq.main([
        "--file", str(path), "enqueue", "backfill-gdelt-2026.yml",
        "--inputs", '{"start":"2026-01-01","end":"2026-07-26","slice":"true"}',
        "--reason", "2026 history walk"]) == 2
    assert wq.load(path)["tickets"] == []


def test_the_declared_inputs_are_read_the_same_way_yaml_reads_them(members):
    """The parser is regex-and-indentation because ops_status.py imports this
    module and it must stay dependency-free. This is what stops it drifting from
    what GitHub will actually accept: every current lock member, checked against
    a real YAML parse."""
    for workflow in sorted(members):
        parsed = yaml.safe_load((WORKFLOWS / workflow).read_text())
        section = parsed.get("on", parsed.get(True)) or {}
        assert "workflow_dispatch" in section, (
            f"{workflow} holds the writer lock but cannot be dispatched, so the "
            "queue could never drain it")
        declared_yaml = (section["workflow_dispatch"] or {}).get("inputs") or {}
        expected = (
            set(declared_yaml),
            {name for name, spec in declared_yaml.items()
             if (spec or {}).get("required") is True},
        )
        assert wq.workflow_dispatch_inputs(workflow) == expected, workflow


# --------------------------------------------------------------------------
# the drainer's own dispatch step
# --------------------------------------------------------------------------

def _dispatch_step() -> str:
    steps = yaml.safe_load(DRAINER.read_text())["jobs"]["drain"]["steps"]
    hits = [s["run"] for s in steps if s.get("name") == "Dispatch it"]
    assert len(hits) == 1, "there is no longer exactly one 'Dispatch it' step"
    return hits[0]


def test_a_refused_dispatch_cannot_abort_the_step_before_it_is_recorded():
    """2026-07-30: `gh api` returned 422 under `set -euo pipefail`, so bash
    killed the step ON THAT LINE — the verification loop and the requeue
    underneath it never ran, and the ticket was left `dispatched` with no run
    behind it. The API call's exit code must be CAUGHT, not fatal."""
    body = _dispatch_step()
    call = body.index("gh api")
    guard = body.rfind("set +e", 0, call)
    assert guard != -1, (
        "the dispatch API call runs under `set -e`; a refusal will kill the "
        "step before the ticket's fate is recorded, which is exactly how the "
        "queue stalled silently")
    assert body.find("set -e\n", guard, call) == -1
    assert "RC=$?" in body, "the API's exit code is never inspected"


def test_every_dispatch_failure_path_records_the_ticket_and_goes_red():
    body = _dispatch_step()
    assert body.count("writer_queue.py dispatch-failed") >= 3, (
        "each failure shape — inputs refused, dispatch refused, no run ever "
        "created — must record what happened to the ticket")
    assert "--permanent" in body, (
        "a 422 on the inputs is refused identically forever; requeueing it is "
        "an infinite silent retry")
    assert "writer_queue.py requeue" not in body, (
        "bare `requeue` loses the count, and an uncounted retry is a silent one")


def test_the_record_of_a_failed_dispatch_is_pushed_not_hoped_for():
    """`git push || true` was how the requeue could vanish even when it ran."""
    body = _dispatch_step()
    assert "git push || true" not in body
    assert "record " in body and "could not push the queue" in body


@pytest.mark.parametrize("step", ["Read what is actually running", "Dispatch it"])
def test_the_pat_is_preferred_and_its_absence_is_said_out_loud(step):
    steps = yaml.safe_load(DRAINER.read_text())["jobs"]["drain"]["steps"]
    env = [s.get("env") or {} for s in steps if s.get("name") == step][0]
    assert "WRITER_QUEUE_TOKEN" in env["GH_TOKEN"]
    assert "GITHUB_TOKEN" in env["GH_TOKEN"], "no fallback if the PAT is absent"
    if step == "Dispatch it":
        assert "HAVE_PAT" in env, (
            "the step must be able to say which token it used; on 2026-07-30 a "
            "422 about the INPUTS was read as a missing token for twenty minutes")


def test_an_unparseable_workflow_fails_open_rather_than_blocking_work(tmp_path, members):
    """A parser that guesses wrong must never be the reason a correction cannot
    be queued. Unknown means unchecked, not rejected."""
    assert wq.workflow_dispatch_inputs("enrich.yml", workflow_dir=tmp_path) is None
    ticket = wq.enqueue(wq.empty_queue(), "enrich.yml", {"whatever": "1"},
                        members=members, now=NOW, workflow_dir=tmp_path)
    assert ticket["inputs"] == {"whatever": "1"}


def test_work_that_never_moves_is_reported_as_starvation(members):
    queue = wq.empty_queue()
    wq.enqueue(queue, "correct-form-d.yml", {"dry_run": False}, members=members, now=NOW)
    later = NOW + timedelta(hours=wq.STUCK_AFTER_HOURS + 1)

    problems = wq.summary(queue, now=later)["problems"]
    assert any("starved" in p for p in problems)


# --------------------------------------------------------------------------
# the ticket itself
# --------------------------------------------------------------------------

def test_a_workflow_outside_the_lock_cannot_be_queued(members):
    with pytest.raises(ValueError):
        wq.enqueue(wq.empty_queue(), "deploy-plugin.yml", members=members, now=NOW)


def test_booleans_become_the_strings_the_dispatch_api_wants(members):
    """`False` stringified naively is "False", which no shell `= "true"` test
    matches and which the API rejects. That is how a correction becomes a
    no-op."""
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "correct-form-d.yml",
                        {"dry_run": False, "force": True}, members=members, now=NOW)
    assert ticket["inputs"] == {"dry_run": "false", "force": "true"}


def test_the_queue_survives_a_round_trip(tmp_path, members):
    path = tmp_path / "writer_queue.json"
    queue = wq.empty_queue()
    wq.enqueue(queue, "correct-form-d.yml", {"dry_run": False}, members=members, now=NOW)
    wq.save(queue, path)
    assert wq.load(path)["tickets"][0]["inputs"] == {"dry_run": "false"}


def test_an_unreadable_queue_reads_as_empty_rather_than_crashing(tmp_path):
    path = tmp_path / "writer_queue.json"
    path.write_text("{ not json")
    assert wq.load(path) == wq.empty_queue()


def test_terminal_tickets_are_pruned_but_live_ones_never_are(members):
    queue = wq.empty_queue()
    for index in range(80):
        ticket = wq.enqueue(queue, "enrich.yml", members=members,
                            now=NOW + timedelta(seconds=index))
        ticket["state"] = "landed"
    live = wq.enqueue(queue, "recall.yml", members=members, now=NOW + timedelta(hours=1))

    wq.prune(queue, keep_terminal=10)

    assert live in queue["tickets"]
    assert len(queue["tickets"]) == 11


def test_an_idle_tick_does_not_rewrite_the_file(tmp_path, members, monkeypatch):
    """The drainer runs four times an hour forever. A heartbeat written on every
    idle tick would commit to main 96 times a day, and every writer rebases onto
    main."""
    path = tmp_path / "writer_queue.json"
    runs = tmp_path / "runs.json"
    runs.write_text("[]")

    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    ticket["state"] = "landed"
    wq.save(queue, path)
    before = path.read_text()

    wq.main(["--file", str(path), "tick", "--runs", str(runs)])

    assert path.read_text() == before


# --- a job that is red forever is a job nobody reads ------------------------

def test_acknowledging_a_failed_ticket_silences_only_that_one(tmp_path):
    path = tmp_path / "q.json"
    queue = wq.empty_queue()
    first = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"})
    second = wq.enqueue(queue, "correct-sec-pillar.yml", {"dry_run": "false"})
    first["state"] = second["state"] = "failed"
    wq.save(queue, path)

    assert wq.main(["--file", str(path), "resolve", first["id"],
                              "--note", "read, fixed and re-queued"]) == 0

    state = wq.summary(wq.load(path))
    assert len(state["problems"]) == 1
    assert second["id"] in state["problems"][0]
    assert wq.load(path)["tickets"][0]["acknowledged"], (
        "the ticket stays in the file as history, it just stops going red")


def test_acknowledging_something_that_does_not_exist_is_an_error(tmp_path):
    path = tmp_path / "q.json"
    wq.save(wq.empty_queue(), path)
    assert wq.main(["--file", str(path), "resolve", "nope"]) == 2


def test_a_queued_ticket_cannot_be_silenced_by_acknowledging_it(tmp_path):
    """Only a TERMINAL ticket is a decision to record. Silencing live work
    would hide the thing the queue exists to show."""
    path = tmp_path / "q.json"
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"})
    wq.save(queue, path)
    assert wq.main(["--file", str(path), "resolve", ticket["id"]]) == 2


def test_resolve_all_acknowledges_failed_tickets_not_only_orphans():
    """`resolve=all` promised "an orphan run, or a failed ticket" and delivered
    only orphans.

    On 2026-08-01 a `resolve=all` looked like it had cleared the backlog and
    left five failed tickets untouched, so drain-writers went red on every tick
    for hours. That is the permanently-red job `_cmd_resolve` exists to
    prevent, reintroduced by the escape hatch itself: an operator who runs the
    documented command and still sees red learns to stop reading the red.
    """
    import types

    import writer_queue as wq

    queue = wq.empty_queue()
    queue["orphans"] = [{"run_id": "111", "workflow": "collect"}]
    queue["tickets"] = [
        {"id": "t-failed", "workflow": "collect.yml", "state": "failed",
         "history": []},
        {"id": "t-abandoned", "workflow": "enrich.yml", "state": "abandoned",
         "history": []},
        {"id": "t-landed", "workflow": "recall.yml", "state": "landed",
         "history": []},
    ]

    import json
    import tempfile
    import pathlib

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "q.json"
        path.write_text(json.dumps(queue))
        rc = wq._cmd_resolve(types.SimpleNamespace(
            file=str(path), run_id="all", note="cleared after an outage"))
        assert rc == 0
        after = json.loads(path.read_text())

    assert after["orphans"][0]["resolved"], "the orphan still resolves"
    by_id = {t["id"]: t for t in after["tickets"]}
    assert by_id["t-failed"].get("acknowledged"), "a failed ticket must clear"
    assert by_id["t-abandoned"].get("acknowledged"), "abandoned clears too"
    # A landed ticket was never a problem and must not be touched.
    assert not by_id["t-landed"].get("acknowledged")


# --------------------------------------------------------------------------
# withdrawing work that has not started
# --------------------------------------------------------------------------

def test_a_queued_ticket_can_be_withdrawn_before_it_is_ever_dispatched(
        tmp_path, members):
    """Two identical `backfill-gdelt-2026` tickets were queued on 2026-08-01.

    Both resume from the same committed cursor, so the second would not have
    redone or doubled anything — it would simply have bought one paid slice of
    history nobody asked for. There was no way to withdraw it, and hand-editing
    the queue file is how it stops agreeing with the runs it tracks.
    """
    path = tmp_path / "q.json"
    queue = wq.empty_queue()
    keep = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    spare = wq.enqueue(queue, "enrich.yml", members=members,
                       now=NOW + timedelta(minutes=1))
    wq.save(queue, path)

    assert wq.main(["--file", str(path), "drop", spare["id"],
                    "--note", "duplicate of " + keep["id"]]) == 0

    saved = {t["id"]: t for t in json.loads(path.read_text())["tickets"]}
    assert saved[spare["id"]]["state"] == "abandoned"
    assert saved[keep["id"]]["state"] == "queued", "the wrong ticket was dropped"

    # A deliberate withdrawal is a decision, not an incident. Left unacknowledged
    # it would report as a problem for ever and turn the drain tick permanently
    # red, which is how a real failure becomes invisible.
    assert wq.summary(json.loads(path.read_text()), now=NOW)["problems"] == []
    assert saved[spare["id"]]["history"][-1]["event"] == "dropped"


def test_a_dispatched_ticket_cannot_be_withdrawn(tmp_path, members):
    """It is bound to a run that is already holding the lock. Forgetting it
    here would leave that run unaccounted for, which is the eviction bug's own
    fingerprint wearing a helpful command as a hat."""
    path = tmp_path / "q.json"
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "enrich.yml", members=members, now=NOW)
    ticket["state"] = "dispatched"
    ticket["run_id"] = "123"
    wq.save(queue, path)

    assert wq.main(["--file", str(path), "drop", ticket["id"]]) == 2
    saved = json.loads(path.read_text())["tickets"][0]
    assert saved["state"] == "dispatched" and saved["run_id"] == "123"


def test_chain_priority_reads_the_last_ticket_of_the_SAME_chain(members):
    """One workflow drives several independent chains, and they do not share a
    pace: `backfill-structured-2026.yml` walks bse_india, companies_house and
    opendart_korea over the same window."""
    queue = wq.empty_queue()
    wq.enqueue(queue, "backfill-structured-2026.yml", {"source": "bse_india"},
               priority=5, members=members, now=NOW)
    wq.enqueue(queue, "backfill-structured-2026.yml", {"source": "opendart_korea"},
               members=members, now=NOW + timedelta(minutes=1))

    assert wq.chain_priority(queue, "backfill-structured-2026.yml",
                             {"source": "bse_india"}) == 5
    assert wq.chain_priority(queue, "backfill-structured-2026.yml",
                             {"source": "opendart_korea"}) == \
        wq.BACKFILL_PRIORITY
    assert wq.chain_priority(queue, "backfill-structured-2026.yml",
                             {"source": "companies_house"}) is None, (
        "a chain with no history must fall back to the workflow default rather "
        "than inherit a stranger's priority")


def _chain_ticket(tid, state, requested_at, source="opendart_korea"):
    return {"id": tid, "workflow": "backfill-structured-2026.yml", "state": state,
            "requested_at": requested_at, "attempts": 1, "history": [],
            "inputs": {"source": source, "start": "2026-01-01", "end": "2026-07-30"}}


def test_a_failure_the_chain_recovered_from_is_not_red():
    """A transient upstream timeout must not redden the drainer forever.

    On 2026-08-01 opendart_korea hit a 45s read timeout at 20:17, the chain
    requeued itself, and the 22:42 slice landed. The 20:17 ticket kept
    drain-writers red for five consecutive ticks in between, every one of them
    reporting a problem that no longer existed. A channel that cries wolf about
    finished work is a channel that gets filtered.
    """
    import writer_queue as wq

    queue = wq.empty_queue()
    queue["tickets"] = [
        _chain_ticket("t-failed", "failed", "2026-08-01T20:17:00Z"),
        _chain_ticket("t-landed", "landed", "2026-08-01T22:42:00Z"),
    ]
    state = wq.summary(queue)
    assert state["problems"] == [], "the chain recovered; nothing needs a human"
    assert any("t-landed" in n for n in state["recovered"]), \
        "and the recovery is still reported, not silently dropped"


def test_a_failure_with_no_later_success_still_needs_a_human():
    """The loud path is the point; only EVIDENCE of recovery quiets it."""
    import writer_queue as wq

    queue = wq.empty_queue()
    queue["tickets"] = [_chain_ticket("t-failed", "failed", "2026-08-01T20:17:00Z")]
    assert len(wq.summary(queue)["problems"]) == 1


def test_another_chains_success_does_not_clear_this_ones_failure():
    """bse_india landing says nothing about opendart_korea.

    One workflow drives several independent chains, so chain identity is the
    workflow AND its exact inputs.
    """
    import writer_queue as wq

    queue = wq.empty_queue()
    queue["tickets"] = [
        _chain_ticket("t-failed", "failed", "2026-08-01T20:17:00Z"),
        _chain_ticket("t-other", "landed", "2026-08-01T22:42:00Z", source="bse_india"),
    ]
    assert len(wq.summary(queue)["problems"]) == 1


def test_an_earlier_success_does_not_excuse_a_later_failure():
    """Recovery has to come AFTER the failure, or it is not recovery."""
    import writer_queue as wq

    queue = wq.empty_queue()
    queue["tickets"] = [
        _chain_ticket("t-landed", "landed", "2026-08-01T18:00:00Z"),
        _chain_ticket("t-failed", "failed", "2026-08-01T20:17:00Z"),
    ]
    assert len(wq.summary(queue)["problems"]) == 1


# --------------------------------------------------------------------------
# fairness: one line is not automatically a fair line
# --------------------------------------------------------------------------
#
# Measured over the 19 hours to 2026-08-02T03:00Z with five chains live:
# bse_india's slice took 3 minutes and waited 123 for it, while gdelt's took 56
# and waited 92. Every chain got one turn per round, which is fair in turns and
# indefensible in latency, because the order fell through to a timestamp that
# knows nothing about what a ticket costs.


def _slice(tid, workflow, inputs, requested, dispatched=None, landed=None,
           state="landed", priority=None):
    """A ticket with the history the fairness measurement actually reads."""
    history = []
    if dispatched:
        history.append({"at": dispatched, "event": "dispatched", "detail": ""})
    if landed:
        history.append({"at": landed, "event": "landed", "detail": ""})
    return {
        "id": tid, "workflow": workflow, "inputs": dict(inputs), "state": state,
        "requested_at": requested, "attempts": 0, "history": history,
        "priority": wq.BACKFILL_PRIORITY if priority is None else priority,
        "dispatched_at": dispatched, "landed_at": landed,
    }


def _chain_history(workflow, inputs, minutes, count=3, priority=None):
    """`count` landed slices of one chain, each holding the lock `minutes`."""
    out = []
    for index in range(count):
        start = NOW - timedelta(hours=6 - index)
        out.append(_slice(
            f"{workflow}-{inputs.get('source', 'x')}-{index}", workflow, inputs,
            requested=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            dispatched=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            landed=(start + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            priority=priority))
    return out


def test_a_three_minute_chain_is_measured_at_three_minutes():
    """The cost of a chain is read from what it has actually done, not from a
    hand-kept list of which chain is supposed to be quick. A list is the thing
    that goes stale the day a walker starts calling the model."""
    queue = wq.empty_queue()
    queue["tickets"] = (
        _chain_history("backfill-structured-2026.yml", {"source": "bse_india"}, 3)
        + _chain_history("backfill-gdelt-2026.yml", {}, 56))

    assert wq.measured_hold_minutes(
        queue, "backfill-structured-2026.yml", {"source": "bse_india"}) == 3
    assert wq.measured_hold_minutes(queue, "backfill-gdelt-2026.yml", {}) == 56


def test_a_chain_nobody_has_timed_is_UNKNOWN_and_not_cheap():
    """Three states, not two. Reading an absent measurement as "fast" would put
    every brand new chain in the fast lane on its first ticket, which is exactly
    how a heavy one would get in."""
    queue = wq.empty_queue()
    assert wq.measured_hold_minutes(queue, "backfill-press-2026.yml", {}) is None

    ticket = _slice("t-new", "backfill-press-2026.yml", {},
                    requested=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), state="queued")
    _, band, _, _ = wq.dispatch_key(queue, ticket, NOW)
    assert band == 2, "an untimed chain takes an ordinary turn, not a fast one"


def test_the_cheap_chain_stops_paying_two_hours_for_three_minutes(members):
    """The measured defect, as a test: bse_india asks LAST and still goes first,
    because its whole slice is inside what it costs to start a run at all."""
    queue = wq.empty_queue()
    queue["tickets"] = (
        _chain_history("backfill-gdelt-2026.yml", {}, 56)
        + _chain_history("backfill-gnews-2026.yml", {}, 23)
        + _chain_history("backfill-structured-2026.yml", {"source": "bse_india"}, 3))
    for workflow, inputs in (("backfill-gdelt-2026.yml", {}),
                             ("backfill-gnews-2026.yml", {}),
                             ("backfill-structured-2026.yml", {"source": "bse_india"})):
        wq.enqueue(queue, workflow, inputs, members=members,
                   now=NOW + timedelta(minutes=len(queue["tickets"])))

    report = wq.tick(queue, [], members=members, now=NOW + timedelta(minutes=30))
    chosen = report["dispatch"]
    assert chosen["workflow"] == "backfill-structured-2026.yml"
    assert chosen["inputs"]["source"] == "bse_india"
    assert report["overtook"], "the log has to say it jumped, or nobody can audit it"


def test_a_long_chain_cannot_be_held_out_of_the_line_forever(members):
    """The fast lane has a ceiling, and it is the same 120 minutes this file
    already calls a starved queue. Letting the scheduler reorder a ticket past
    the threshold at which it REPORTS that ticket as a problem would be
    incoherent."""
    queue = wq.empty_queue()
    queue["tickets"] = (
        _chain_history("backfill-gdelt-2026.yml", {}, 56)
        + _chain_history("backfill-structured-2026.yml", {"source": "bse_india"}, 3))
    wq.enqueue(queue, "backfill-gdelt-2026.yml", {}, members=members, now=NOW)
    wq.enqueue(queue, "backfill-structured-2026.yml", {"source": "bse_india"},
               members=members, now=NOW + timedelta(minutes=110))

    later = NOW + timedelta(minutes=wq.FAIR_SHARE_AGE_MINUTES + 1)
    report = wq.tick(queue, [], members=members, now=later)
    assert report["dispatch"]["workflow"] == "backfill-gdelt-2026.yml", (
        "past the fair-share ceiling the long chain goes ahead of the cheap one")


def test_an_operators_priority_still_decides_everything_above_it(members):
    """Priority is the outer term and nothing derived may overturn it. A
    correction at DEFAULT_PRIORITY goes before a three-minute backfill, and an
    operator who puts a heavy chain at 1 gets a heavy chain at 1."""
    queue = wq.empty_queue()
    queue["tickets"] = (
        _chain_history("backfill-structured-2026.yml", {"source": "bse_india"}, 3)
        + _chain_history("backfill-gdelt-2026.yml", {}, 56))
    wq.enqueue(queue, "backfill-structured-2026.yml", {"source": "bse_india"},
               members=members, now=NOW)
    wq.enqueue(queue, "backfill-gdelt-2026.yml", {}, priority=1, members=members,
               now=NOW + timedelta(minutes=1))

    report = wq.tick(queue, [], members=members, now=NOW + timedelta(minutes=5))
    assert report["dispatch"]["workflow"] == "backfill-gdelt-2026.yml"


def test_the_dispatch_order_is_never_written_back_onto_the_ticket(members):
    """`default_priority()` used to be reapplied on requeue and silently undid
    an operator's override, so the override looked effective for one slice and
    then was not. An ordering that edited `priority` would be that bug in a new
    hat: the stored number keeps meaning "what a human asked for", and anything
    derived is derived again next tick where it can be seen changing."""
    queue = wq.empty_queue()
    queue["tickets"] = _chain_history(
        "backfill-structured-2026.yml", {"source": "bse_india"}, 3)
    ticket = wq.enqueue(queue, "backfill-structured-2026.yml",
                        {"source": "bse_india"}, priority=7, members=members,
                        now=NOW)

    wq.tick(queue, [], members=members, now=NOW + timedelta(minutes=5))
    assert ticket["priority"] == 7
    assert "fast_lane" not in ticket and "effective_priority" not in ticket


def test_a_re_dispatched_ticket_is_timed_from_the_dispatch_that_stuck():
    """A ticket evicted from the pending slot and re-dispatched has two
    `dispatched` events, and the first one held the lock for exactly no time.
    Timing from it would report an eviction as a very slow chain."""
    queue = wq.empty_queue()
    start = NOW - timedelta(hours=2)
    ticket = _slice("t-evicted", "backfill-structured-2026.yml",
                    {"source": "bse_india"},
                    requested=start.strftime("%Y-%m-%dT%H:%M:%SZ"))
    ticket["history"] = [
        {"at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "dispatched"},
        {"at": (start + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "event": "displaced"},
        {"at": (start + timedelta(minutes=95)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "event": "dispatched"},
        {"at": (start + timedelta(minutes=98)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "event": "landed"},
    ]
    queue["tickets"] = [ticket]
    assert wq.measured_hold_minutes(
        queue, "backfill-structured-2026.yml", {"source": "bse_india"}) == 3


def test_the_fast_lane_bar_is_the_cost_of_starting_a_run_at_all():
    """The threshold is not a taste. Below it, letting a chain go first costs
    the chain it overtakes less than that chain's own checkout, install, merge
    and push; above it, reordering transfers real lock time between chains,
    which is a policy decision and belongs to an operator's --priority."""
    assert wq.FAST_SLICE_MINUTES <= 10
    assert wq.FAIR_SHARE_AGE_MINUTES == wq.LONG_HOLD_MINUTES
    import backfill_slices
    assert wq.FAST_SLICE_MINUTES < backfill_slices.SLICE_BUDGET_MINUTES / 4, (
        "a 'fast' chain has to be far below the slice budget or the lane is "
        "just a second, quieter priority scheme")


# --------------------------------------------------------------------------
# red once per ITEM, not once per tick
#
# Measured 2026-07-26..08-02: 180 red drain-writers runs for a handful of
# needs-human items, one GitHub failure email each, because every 15-minute
# tick re-reddened the same already-reported facts until a human acknowledged
# them. These tests pin the replacement: the tick that FIRST reports an item
# goes red; later ticks keep listing it but stay green; a NEW item always
# reds; an ignored item earns one more red every RE_RED_HOURS.
# --------------------------------------------------------------------------

def _failed_ticket_queue(path, workflow="correct-form-d.yml"):
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, workflow, {"dry_run": "false"})
    ticket["state"] = "failed"
    wq.save(queue, path)
    return ticket


def test_a_known_needs_human_item_reddens_once_not_every_tick(tmp_path):
    """THE 180-red-runs defect, in one assertion: the second tick over the
    same unhandled failure must not be a second red run."""
    path = tmp_path / "q.json"
    runs = tmp_path / "runs.json"
    runs.write_text("[]")
    _failed_ticket_queue(path)

    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 2, (
        "the first tick to report the failure is the red one")
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 0, (
        "the second tick reports the SAME fact; a red run here is only "
        "another email about it")
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 0


def test_a_muted_item_is_still_listed_as_a_problem_everywhere(tmp_path, capsys):
    """Muted is not silenced: status and ops_status still see the problem, and
    the tick log still prints it. Only the run's conclusion changes."""
    path = tmp_path / "q.json"
    runs = tmp_path / "runs.json"
    runs.write_text("[]")
    ticket = _failed_ticket_queue(path)

    wq.main(["--file", str(path), "tick", "--runs", str(runs)])
    capsys.readouterr()
    wq.main(["--file", str(path), "tick", "--runs", str(runs)])
    out = capsys.readouterr().out
    assert ticket["id"] in out, "the muted item must stay visible in the log"
    assert "::warning::" in out and "FAILED" in out

    state = wq.summary(wq.load(path))
    assert any(ticket["id"] in p for p in state["problems"]), (
        "status/ops_status keep reporting it until a human acts")
    assert wq.main(["--file", str(path), "status"]) == 2


def test_a_new_item_reds_even_while_an_old_one_is_muted(tmp_path):
    path = tmp_path / "q.json"
    runs = tmp_path / "runs.json"
    runs.write_text("[]")
    _failed_ticket_queue(path)
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 2

    queue = wq.load(path)
    second = wq.enqueue(queue, "correct-sec-pillar.yml", {"dry_run": "false"})
    second["state"] = "failed"
    wq.save(queue, path)
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 2, (
        "a new failure is a new fact and must red, muted neighbours or not")
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 0


def test_an_ignored_item_earns_one_more_red_every_re_red_period():
    queue = wq.empty_queue()
    ticket = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"})
    ticket["state"] = "failed"

    state = wq.summary(queue, now=NOW)
    fresh, muted = wq.select_red(queue, state["problem_keys"], now=NOW)
    assert fresh and not muted

    later = NOW + timedelta(hours=wq.RE_RED_HOURS - 1)
    fresh, muted = wq.select_red(queue, wq.summary(queue, now=later)["problem_keys"],
                                 now=later)
    assert not fresh and muted, "inside the period it stays muted"

    much_later = NOW + timedelta(hours=wq.RE_RED_HOURS, minutes=1)
    fresh, muted = wq.select_red(queue,
                                 wq.summary(queue, now=much_later)["problem_keys"],
                                 now=much_later)
    assert fresh and not muted, (
        "an item ignored for RE_RED_HOURS is re-reported once, so it cannot "
        "fade into a warning nobody reads")


def test_acknowledging_clears_the_mark_so_a_refailure_reds_again(tmp_path):
    """The mark must die with the problem. If it survived an acknowledge, a
    RE-failure of the re-queued work inside RE_RED_HOURS would arrive
    pre-muted — a genuinely new failure that never goes red."""
    path = tmp_path / "q.json"
    runs = tmp_path / "runs.json"
    runs.write_text("[]")
    ticket = _failed_ticket_queue(path)
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 2
    assert wq.main(["--file", str(path), "resolve", ticket["id"],
                    "--note", "read; re-queued"]) == 0

    queue = wq.load(path)
    assert not queue.get("red_marks"), (
        "no live problems means no marks — an acknowledged item's mark is "
        "pruned, and an all-clear file carries no red_marks key at all")
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 0

    # The re-queued work fails again: a new ticket, a new red.
    retry = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"})
    retry["state"] = "failed"
    wq.save(queue, path)
    assert wq.main(["--file", str(path), "tick", "--runs", str(runs)]) == 2


def test_the_red_marks_survive_in_the_committed_file(tmp_path):
    """The drainer runs on a fresh runner every tick; 'already reported' only
    works if it is persisted in the queue file the workflow commits."""
    path = tmp_path / "q.json"
    runs = tmp_path / "runs.json"
    runs.write_text("[]")
    ticket = _failed_ticket_queue(path)
    wq.main(["--file", str(path), "tick", "--runs", str(runs)])
    saved = json.loads(path.read_text())
    assert saved.get("red_marks"), "the mark must be in the file, not in memory"
    assert f"ticket:{ticket['id']}" in saved["red_marks"]


# --------------------------------------------------------------------------
# a displaced SCHEDULED run resolves itself; a dispatched one still shouts
# --------------------------------------------------------------------------

def test_an_evicted_scheduled_run_is_recorded_and_resolved_automatically(members):
    """A scheduled run carries no inputs, so nothing about it is unknowable and
    its own next cron repeats the pass. 2026-08-02: one evicted scheduled
    press run generated red drain ticks for hours before a human typed the
    same conclusion by hand. Recorded, never red."""
    queue = wq.empty_queue()
    runs = [_run("777", "collect national press", conclusion="cancelled",
                 job_count=0, event="schedule")]
    report = wq.tick(queue, runs, members, now=NOW)

    assert report["orphans"] == [], "not a needs-human orphan"
    assert len(report["schedule_orphans"]) == 1
    assert len(queue["orphans"]) == 1, "still recorded — resolved is not forgotten"
    assert queue["orphans"][0]["resolved"]
    assert wq.summary(queue, now=NOW)["problems"] == []


def test_an_evicted_scheduled_run_is_still_noticed_only_once(members):
    queue = wq.empty_queue()
    runs = [_run("777", "collect", conclusion="cancelled",
                 job_count=0, event="schedule")]
    wq.tick(queue, runs, members, now=NOW)
    second = wq.tick(queue, runs, members, now=NOW + timedelta(minutes=15))
    assert second["schedule_orphans"] == []
    assert len(queue["orphans"]) == 1


def test_an_evicted_dispatched_run_still_needs_a_human(members):
    """The auto-resolve is ONLY for schedule events; a workflow_dispatch run
    really does have inputs GitHub will not reveal."""
    queue = wq.empty_queue()
    runs = [_run("888", "correct-sec-pillar", conclusion="cancelled",
                 job_count=0, event="workflow_dispatch")]
    report = wq.tick(queue, runs, members, now=NOW)
    assert len(report["orphans"]) == 1
    assert any("888" in p for p in wq.summary(queue, now=NOW)["problems"])


def test_an_eviction_with_no_event_field_is_treated_as_unknowable(members):
    """Absence of a signal is never a pass: a run list that lost the event
    field must fall back to the loud path, not the quiet one."""
    queue = wq.empty_queue()
    run = _run("999", "enrich", conclusion="cancelled", job_count=0)
    del run["event"]
    report = wq.tick(queue, [run], members, now=NOW)
    assert len(report["orphans"]) == 1
    assert any("999" in p for p in wq.summary(queue, now=NOW)["problems"])
