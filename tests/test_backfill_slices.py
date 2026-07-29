"""A backfill has to be a chain of short runs, and the chain has to be durable.

The incident: `backfill-gdelt-2026` held the single `talent-collect` writer
lock from 04:59 to 10:49 UTC on 2026-07-29, hit its own 350-minute timeout, was
CANCELLED, and its commit step — guarded by `if: !cancelled()` — was SKIPPED.
Six hours of collection was lost AND every correction queued behind it waited
the entire time.

Every test here is one of the ways that comes back.
"""

from datetime import datetime, timedelta, timezone

import pytest

import backfill_slices as bs
import writer_queue


GDELT = "backfill-gdelt-2026.yml"
BULK = "backfill-funding-bulk.yml"


def _job(**over):
    state = bs.empty_state()
    kwargs = dict(workflow=GDELT, unit="days", start="2026-01-01",
                  end="2026-01-10", slice_size=4)
    kwargs.update(over)
    return state, bs.open_job(state, **kwargs)


# --- slice arithmetic ------------------------------------------------------

def test_a_slice_is_inclusive_at_both_ends():
    """Because that is what every backfill script's --start/--end already mean.

    A half-open window that looks like the inclusive one it replaced is a
    silently skipped day, and nothing about the run would say so.
    """
    assert bs.next_slice("2026-01-01", "2026-01-31", "days", 4) == \
        ("2026-01-01", "2026-01-04")
    assert bs.advance("2026-01-04", "days") == "2026-01-05"


def test_the_last_slice_is_clamped_to_the_end_rather_than_overshooting():
    assert bs.next_slice("2026-01-09", "2026-01-10", "days", 4) == \
        ("2026-01-09", "2026-01-10")


def test_a_cursor_past_the_end_is_the_job_being_finished():
    assert bs.next_slice("2026-02-01", "2026-01-31", "days", 4) is None
    assert bs.next_slice(None, "2026-01-31", "days", 4) is None


def test_quarters_slice_and_roll_over_the_year():
    assert bs.next_slice("2025q4", "2026q2", "quarters", 1) == ("2025q4", "2025q4")
    assert bs.advance("2025q4", "quarters") == "2026q1"
    assert bs.next_slice("2026q1", "2026q2", "quarters", 2) == ("2026q1", "2026q2")
    assert bs.slice_members("2025q4", "2026q2", "quarters") == \
        ["2025q4", "2026q1", "2026q2"]


def test_an_unknown_unit_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        bs.next_slice("2026-01-01", "2026-01-31", "fortnights", 1)
    with pytest.raises(ValueError):
        bs.open_job(bs.empty_state(), workflow=GDELT, unit="fortnights",
                    start="a", end="b", slice_size=1)


# --- the chain -------------------------------------------------------------

def _run_one(state, job, *, stored=1):
    """One whole run: take a slice, do it, record it."""
    window = bs.next_slice(job["cursor"], job["end"], job["unit"], job["slice_size"])
    assert window is not None
    ticket = bs.slice_ticket(job, window[0], window[1], totals={"stored": stored})
    return window, bs.record(state, ticket)


def test_a_backfill_walks_its_whole_window_one_slice_at_a_time():
    state, job = _job()
    windows = []
    while job["state"] == "running":
        window, result = _run_one(state, job)
        windows.append(window)
        job = result["job"]

    assert windows == [("2026-01-01", "2026-01-04"),
                       ("2026-01-05", "2026-01-08"),
                       ("2026-01-09", "2026-01-10")]
    assert job["state"] == "done"
    assert job["cursor"] is None
    assert job["totals"]["stored"] == 3


def test_the_next_run_resumes_from_the_committed_cursor_and_not_from_its_inputs():
    """The whole point: nobody reads a log to find out where a dead run got to.

    A ticket can wait hours behind other work, so the dispatch inputs are a
    label and the committed cursor is the authority.
    """
    state, job = _job()
    _run_one(state, job)

    # A completely fresh process, loading the same committed state.
    reloaded, resumed = bs.plan(state, workflow=GDELT, unit="days",
                                start="2026-01-01", end="2026-01-10",
                                slice_size=4)
    assert resumed == ("2026-01-05", "2026-01-08")
    assert reloaded["slices"] == 1


def test_a_run_that_dies_mid_slice_keeps_every_slice_before_it():
    state, job = _job()
    _run_one(state, job)
    _run_one(state, state["jobs"][bs.job_id(GDELT, "2026-01-01", "2026-01-10")])

    # ...and now a run simply never records anything, because it was cancelled.
    _, resumed = bs.plan(state, workflow=GDELT, unit="days", start="2026-01-01",
                         end="2026-01-10", slice_size=4)
    assert resumed == ("2026-01-09", "2026-01-10"), (
        "a run that died lost more than its own slice")


def test_a_run_stopped_by_its_budget_resumes_on_the_exact_next_window():
    """Sizing a slice is an estimate; the wall clock is the promise.

    A run that stops half way through its slice records the last window it
    FINISHED, so nothing is collected twice and nothing is skipped.
    """
    state, job = _job()
    # Slice is 01-01..01-04; the run got through 01-01 and 01-02 only.
    ticket = bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                             next_cursor="2026-01-03",
                             totals={"stored": 2},
                             stopped_early="slice budget reached")
    result = bs.record(state, ticket)
    assert result["job"]["cursor"] == "2026-01-03"
    assert result["job"]["history"][-1]["stopped_early"] == "slice budget reached"
    assert bs.next_slice(result["job"]["cursor"], "2026-01-10", "days", 4) == \
        ("2026-01-03", "2026-01-06")


def test_a_finished_job_is_not_silently_started_again():
    state, job = _job(end="2026-01-04")
    _run_one(state, job)
    reopened, window = bs.plan(state, workflow=GDELT, unit="days",
                               start="2026-01-01", end="2026-01-04", slice_size=4)
    assert window is None
    assert reopened["state"] == "done"


# --- the guards ------------------------------------------------------------

def test_a_slice_that_made_no_progress_is_never_requeued():
    """A chain of green runs collecting nothing is the quietest failure there
    is, and this repo's incident log is mostly that shape."""
    state, job = _job()
    ticket = bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                             next_cursor=job["cursor"])
    result = bs.record(state, ticket)

    assert result["advanced"] is False
    assert result["problem"] and "no progress" in result["problem"]
    assert result["job"]["state"] == "stalled"
    assert bs.next_inputs(result["job"]) is None, "a stalled job must not requeue"
    assert bs.summary(state)["problems"], "a stalled job must reach ops_status"


def test_a_chain_that_will_not_end_stops_itself():
    state, job = _job(end="2036-01-01")
    job["slices"] = bs.MAX_SLICES_PER_JOB - 1
    _, result = _run_one(state, job)
    assert result["job"]["state"] == "stalled"
    assert "ceiling" in result["problem"]
    assert bs.next_inputs(result["job"]) is None


def test_the_ceiling_is_far_above_any_real_backfill():
    """A guard that real work trips is a guard that gets raised until it is
    useless. A year of GDELT at four days a slice is 92."""
    assert bs.MAX_SLICES_PER_JOB >= 92


# --- it merges, it does not overwrite --------------------------------------

def test_recording_a_slice_leaves_every_other_job_alone():
    """`record` runs AFTER `git reset --hard origin/main`, against whatever
    main holds. Copying our whole file back is the bug that cost 9,572 rows
    one file along; this is the same rule for the cursor."""
    state, job = _job()
    other = bs.open_job(state, workflow=BULK, unit="quarters", start="2026q1",
                        end="2026q4", slice_size=1)
    other["cursor"] = "2026q3"
    other["slices"] = 2

    _run_one(state, job)

    assert state["jobs"][bs.job_id(BULK, "2026q1", "2026q4")]["cursor"] == "2026q3"
    assert state["jobs"][bs.job_id(BULK, "2026q1", "2026q4")]["slices"] == 2


def test_a_ticket_for_an_unknown_job_opens_it_rather_than_being_dropped():
    """The state file is committed, so a run can legitimately be the first to
    see a job — including after someone reset it."""
    state = bs.empty_state()
    _, job = _job()
    result = bs.record(state, bs.slice_ticket(job, "2026-01-01", "2026-01-04"))
    assert result["advanced"] and result["job"]["cursor"] == "2026-01-05"


# --- the successor ticket --------------------------------------------------

def test_the_successor_goes_into_the_real_queue_behind_every_correction():
    """Slicing shortens the wait; it must not let a backfill jump the line."""
    state, job = _job()
    _, result = _run_one(state, job)
    inputs = bs.next_inputs(result["job"])
    assert inputs["start"] == "2026-01-01" and inputs["end"] == "2026-01-10"

    queue = writer_queue.empty_queue()
    ticket = writer_queue.enqueue(queue, GDELT, inputs, reason="next slice")
    assert ticket["priority"] == writer_queue.BACKFILL_PRIORITY

    correction = writer_queue.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"})
    waiting = sorted(queue["tickets"],
                     key=lambda t: (t["priority"], t["requested_at"]))
    assert waiting[0]["id"] == correction["id"], (
        "the backfill slice queued ahead of a correction")


def test_every_sliced_backfill_is_a_member_of_the_writer_lock_group():
    """`writer_queue.enqueue` refuses a workflow that does not hold the lock,
    so this is also what proves the successor can be queued at all."""
    members = writer_queue.lock_group_workflows()
    backfills = [name for name in members if name.startswith("backfill-")]
    assert len(backfills) >= 4, backfills
    for name in backfills:
        writer_queue.enqueue(writer_queue.empty_queue(), name, {"dry_run": "false"})


def test_quarter_inputs_are_a_range_not_a_list():
    """A comma list would give every dispatch a different job id, and the chain
    would never find its own cursor."""
    state = bs.empty_state()
    job = bs.open_job(state, workflow=BULK, unit="quarters", start="2008q1",
                      end="2026q2", slice_size=1)
    bs.record(state, bs.slice_ticket(job, "2008q1", "2008q1"))
    assert bs.next_inputs(state["jobs"][bs.job_id(BULK, "2008q1", "2026q2")]) == \
        {"quarters": "2008q1..2026q2"}


# --- the clock -------------------------------------------------------------

def test_the_budget_stops_a_run_before_the_runner_kills_it():
    clock = {"at": datetime(2026, 7, 29, 5, 0, tzinfo=timezone.utc)}
    budget = bs.Budget(50, now=lambda: clock["at"])

    clock["at"] += timedelta(minutes=30)
    assert budget.expired() is False
    clock["at"] += timedelta(minutes=21)
    assert budget.expired() is True
    assert "50 minutes" in budget.reason()
    assert "51 min elapsed" in budget.reason()


def test_the_budget_leaves_room_for_the_commit_that_the_incident_never_reached():
    assert bs.SLICE_TIMEOUT_MINUTES - bs.SLICE_BUDGET_MINUTES >= 20, (
        "a slice that finishes at its timeout is cancelled, and a cancelled "
        "run's commit step is skipped — which is the whole incident")


# --- the file --------------------------------------------------------------

def test_the_state_file_round_trips_and_survives_a_corrupt_one(tmp_path):
    path = tmp_path / "backfill_state.json"
    state, job = _job()
    _run_one(state, job)
    bs.save(state, path)
    assert bs.load(path)["jobs"] == state["jobs"]

    path.write_text("{ not json")
    assert bs.load(path) == bs.empty_state(), (
        "a corrupt state file must read as 'nothing known', never crash the run")


def test_record_from_the_cli_writes_the_cursor_and_queues_the_successor(tmp_path):
    import json

    state, job = _job()
    state_path = tmp_path / "backfill_state.json"
    queue_path = tmp_path / "writer_queue.json"
    bs.save(state, state_path)
    writer_queue.save(writer_queue.empty_queue(), queue_path)

    ticket_path = tmp_path / "slice.json"
    bs.emit(ticket_path, bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                                         totals={"stored": 7}))

    rc = bs.main(["--file", str(state_path), "record", "--from", str(ticket_path),
                  "--queue", "--queue-file", str(queue_path)])
    assert rc == 0

    saved = json.loads(state_path.read_text())["jobs"][bs.job_id(
        GDELT, "2026-01-01", "2026-01-10")]
    assert saved["cursor"] == "2026-01-05" and saved["totals"]["stored"] == 7

    queued = json.loads(queue_path.read_text())["tickets"]
    assert len(queued) == 1 and queued[0]["workflow"] == GDELT
    assert queued[0]["state"] == "queued"


def test_a_stalled_chain_exits_non_zero_from_the_cli(tmp_path):
    state, job = _job()
    state_path = tmp_path / "backfill_state.json"
    bs.save(state, state_path)
    ticket_path = tmp_path / "slice.json"
    bs.emit(ticket_path, bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                                         next_cursor="2026-01-01"))
    assert bs.main(["--file", str(state_path), "record",
                    "--from", str(ticket_path), "--queue"]) == 2


# --- end to end, minus the network ----------------------------------------

def test_a_real_backfill_run_slices_emits_and_the_chain_advances(tmp_path, monkeypatch):
    """The whole loop through a real script: take a slice, emit, record, queue.

    The window is deliberately one where every search comes back empty, so the
    run ends by FAILING its own fail-loud check. The ticket must still be
    emitted and the cursor must still advance: a run that collected four weeks
    and then hit a broken search has still done four weeks, and slicing exists
    so finished work is never the price of how a run ended.
    """
    import json
    import sys

    import backfill_sec_2026 as sec
    from pipeline import schema

    monkeypatch.setattr(sec, "collect_window", lambda lo, hi: [])
    # The path, not the function: patching `connect` here would patch the real
    # pipeline.schema for everything loaded after it, and a lambda that calls
    # the name it just replaced recurses forever. See the test gotcha in
    # CLAUDE.md — stub data, never a module.
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(sec.publish, "publish", lambda *a, **k: {"sent": 0})

    state_path = tmp_path / "backfill_state.json"
    queue_path = tmp_path / "writer_queue.json"
    ticket_path = tmp_path / "slice.json"
    writer_queue.save(writer_queue.empty_queue(), queue_path)

    monkeypatch.setattr(sys, "argv", [
        "backfill_sec_2026.py", "--start", "2026-01-01", "--end", "2026-01-20",
        "--slice", "--state", str(state_path), "--emit-next", str(ticket_path)])
    assert sec.main() == 1, "an all-empty sweep must still go red"

    ticket = json.loads(ticket_path.read_text())
    assert ticket["slice"] == "2026-01-01..2026-01-07", (
        f"the run did not take a {sec.SLICE_DAYS}-day slice: {ticket['slice']}")
    assert ticket["next_cursor"] == "2026-01-08"

    assert bs.main(["--file", str(state_path), "record", "--from", str(ticket_path),
                    "--queue", "--queue-file", str(queue_path)]) == 0
    job = bs.load(state_path)["jobs"][bs.job_id(sec.WORKFLOW, "2026-01-01", "2026-01-20")]
    assert job["cursor"] == "2026-01-08" and job["state"] == "running"
    assert json.loads(queue_path.read_text())["tickets"][0]["workflow"] == sec.WORKFLOW
