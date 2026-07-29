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
         created=None, job_count=1):
    return {
        "databaseId": run_id,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "createdAt": (created or NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job_count": job_count,
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
