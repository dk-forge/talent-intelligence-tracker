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

    The window is deliberately one where every search WORKED and returned
    nothing, so the run ends by FAILING its own fail-loud check — a historical
    month with no 8-K 5.02 in it is implausible enough to be worth a human. The
    ticket must still be emitted and the cursor must still advance: a run that
    collected four weeks and then hit a broken search has still done four
    weeks, and slicing exists so finished work is never the price of how a run
    ended.

    Note the `0` in the stub. That is the search-failure count, and it is the
    whole difference between this test and the one below it: an answer of "no
    filings" is collected history and the cursor may pass it. See
    `test_a_window_the_search_refused_does_not_move_the_cursor`.
    """
    import json
    import sys

    import backfill_sec_2026 as sec
    from pipeline import schema

    monkeypatch.setattr(sec, "collect_window", lambda lo, hi: ([], 0))
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


# --- a window nobody fetched is not a window with nothing in it ------------

def test_a_fetch_that_failed_and_a_day_that_was_quiet_are_different_states():
    """PASS / FAIL / UNKNOWN, applied to one window of one backfill.

    Measured on `backfill-gnews-2026` run 30662474194: 576 of 576 queries
    failed, three day-windows returned nothing, and "nothing came back" was
    recorded as "there was no news on earth that day".
    """
    assert bs.sampled_window(items=12, fetch_errors=0) == bs.COLLECTED
    assert bs.sampled_window(items=0, fetch_errors=0) == bs.EMPTY
    assert bs.sampled_window(items=0, fetch_errors=576) == bs.UNREACHED

    # A sampler tolerates partial failure by design: gnews rations what reaches
    # the model and leaves the rest unmarked, so a re-walk of the same day
    # deliberately reads different rows. One flaky edition of 52 is weather.
    assert bs.sampled_window(items=12, fetch_errors=3) == bs.COLLECTED

    # An enumerator does not. The contract is every 8-K 5.02 in the week, so a
    # search that dies on page three leaves pages nobody asked for, and they
    # are indistinguishable from pages that held nothing.
    assert bs.enumerated_window(items=40, fetch_errors=1) == bs.UNREACHED
    assert bs.enumerated_window(items=0, fetch_errors=0) == bs.EMPTY
    assert bs.enumerated_window(items=40, fetch_errors=0) == bs.COLLECTED


def test_a_window_the_search_refused_does_not_move_the_cursor(tmp_path, monkeypatch):
    """The defect, end to end, in the shape that cost three days of history.

    Run 30662474194 did everything the design asked of it. It printed the
    failure, it returned 1, and the chain advanced from 2026-01-22 to
    2026-01-25 anyway — because the ticket carrying the cursor is emitted
    BEFORE the fail-loud check and the workflow's commit step runs
    `if: !cancelled()`, both deliberately, so that rows already collected are
    never the price of how a run ended.

    RED IS NOT UNADVANCED. The only thing between a broken fetch and a day
    skipped forever is that the day never sets `done_through`, so the emitted
    cursor equals the one the run started from and `record` refuses it.
    """
    import json
    import sys

    import backfill_sec_2026 as sec
    from pipeline import schema

    # Every page of every window: the search itself refuses. Not "the month was
    # quiet" — the difference is the second element and nothing else.
    monkeypatch.setattr(sec, "collect_window", lambda lo, hi: ([], 1))
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(sec.publish, "publish", lambda *a, **k: {"sent": 0})

    state_path = tmp_path / "backfill_state.json"
    queue_path = tmp_path / "writer_queue.json"
    ticket_path = tmp_path / "slice.json"
    writer_queue.save(writer_queue.empty_queue(), queue_path)

    monkeypatch.setattr(sys, "argv", [
        "backfill_sec_2026.py", "--start", "2026-01-01", "--end", "2026-01-20",
        "--slice", "--state", str(state_path), "--emit-next", str(ticket_path)])
    assert sec.main() == 1, "a window nobody could enumerate must go red"

    ticket = json.loads(ticket_path.read_text())
    assert ticket["next_cursor"] == "2026-01-01", (
        f"the cursor moved to {ticket['next_cursor']} over a window whose "
        "search refused every page. Nothing collected those filings and no "
        "later run will ever be asked to: the chain only moves forwards.")

    # And the chain stops itself rather than spinning: `record` sees a cursor
    # that did not move, marks the job stalled, queues nothing, and exits 2.
    assert bs.main(["--file", str(state_path), "record", "--from", str(ticket_path),
                    "--queue", "--queue-file", str(queue_path)]) == 2
    job = bs.load(state_path)["jobs"][bs.job_id(sec.WORKFLOW, "2026-01-01", "2026-01-20")]
    assert job["state"] == "stalled" and job["cursor"] == "2026-01-01"
    assert json.loads(queue_path.read_text())["tickets"] == []


def test_a_publish_failure_records_the_slice_but_stops_the_chain(tmp_path):
    """Measured on the first live sliced run (30481065108).

    It collected its whole quarter and then died inside `publish.publish`,
    because the publish guardrails were holding eight open findings. The ticket
    was emitted after the publish call, so nothing was recorded, the cursor
    never moved, and the chain stopped having written nothing down.

    Collecting and publishing are separate gates. The slice's progress is kept
    either way; what a halt withholds is the REQUEUE, because whatever blocked
    this slice blocks the next one and a chain requeueing into a wall produces
    one red run per slice and buries the first, real one.
    """
    state, job = _job()
    ticket = bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                             totals={"stored": 9},
                             halt="publish refused: 8 open guardrail findings")
    result = bs.record(state, ticket)

    assert result["advanced"] is True
    assert result["job"]["cursor"] == "2026-01-05", "the collected slice was lost"
    assert result["job"]["totals"]["stored"] == 9
    assert result["job"]["state"] == "halted"
    assert bs.next_inputs(result["job"]) is None, "a halted chain must not requeue"
    assert "guardrail" in result["problem"]
    assert bs.summary(state)["problems"], "a halted chain must reach ops_status"


def test_a_halted_chain_resumes_where_it_stopped_once_a_human_clears_it(tmp_path):
    state, job = _job()
    bs.record(state, bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                                     halt="publish refused"))
    _, window = bs.plan(state, workflow=GDELT, unit="days", start="2026-01-01",
                        end="2026-01-10", slice_size=4)
    assert window == ("2026-01-05", "2026-01-08")


def test_the_cli_records_a_halt_and_goes_red_without_queueing(tmp_path):
    import json

    state, job = _job()
    state_path = tmp_path / "backfill_state.json"
    queue_path = tmp_path / "writer_queue.json"
    ticket_path = tmp_path / "slice.json"
    bs.save(state, state_path)
    writer_queue.save(writer_queue.empty_queue(), queue_path)
    bs.emit(ticket_path, bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                                         totals={"stored": 3},
                                         halt="publish refused"))

    assert bs.main(["--file", str(state_path), "record", "--from", str(ticket_path),
                    "--queue", "--queue-file", str(queue_path)]) == 2
    saved = json.loads(state_path.read_text())["jobs"][bs.job_id(
        GDELT, "2026-01-01", "2026-01-10")]
    assert saved["cursor"] == "2026-01-05", "a red run still records its slice"
    assert json.loads(queue_path.read_text())["tickets"] == []


def test_every_sliced_backfill_survives_publish_refusing(tmp_path):
    """The property, asserted against the scripts rather than described.

    `publish.publish` sits between the collecting and the ticket in all four,
    and a bare call there throws the slice away.
    """
    import inspect

    import backfill_form_d_2026
    import backfill_form_d_bulk
    import backfill_gdelt_2026
    import backfill_sec_2026

    for module in (backfill_gdelt_2026, backfill_sec_2026,
                   backfill_form_d_2026, backfill_form_d_bulk):
        source = inspect.getsource(module.main)
        assert "except publish.PublishError" in source, (
            f"{module.__name__} lets a publish failure discard the slice it "
            "already collected")
        assert source.index("except publish.PublishError") < \
            source.index("backfill_slices.emit("), (
                f"{module.__name__} emits its ticket before it knows whether "
                "publishing worked")
        assert "halt=" in source, (
            f"{module.__name__} does not tell `record` to stop the chain, so it "
            "requeues into the same wall one slice at a time")


# --- a cancelled chain leaves nothing behind, and used to say nothing ------
#
# `backfill-structured-2026` run 30594795739 was cancelled mid-run during the
# 2026-07-31 Bluehost outage. A FAILED slice requeues — its commit step is
# `if: !cancelled()`, so it records the ticket and appends the next one. A
# CANCELLED slice skips that step entirely, so bse_india sat at 2026-01-29 and
# companies_house at slice 1 of 7 for two days while `status` printed
# `problems: []`. A stalled chain reporting no problems is a silent pass, and
# this repo does not get to have those.

def _running(state_and_job=None, **over):
    """A chain mid-walk, with a controllable `updated_at`."""
    state, job = _job(**over)
    job["state"] = "running"
    job["cursor"] = "2026-01-05"
    job["slices"] = 1
    return state, job


def _stamp(hours_ago: float) -> str:
    return bs._iso(datetime.now(timezone.utc) - timedelta(hours=hours_ago))


def test_a_chain_with_nothing_queued_behind_it_is_a_problem_not_a_silence():
    state, job = _running()
    job["updated_at"] = _stamp(50)

    report = bs.summary(state, queue=writer_queue.empty_queue())

    assert report["problems"], (
        "a running chain whose cursor has not moved in two days, with no run "
        "and no ticket anywhere, reported clean. That is the exact state "
        "bse_india and companies_house sat in on 2026-07-31.")
    problem = report["problems"][0]
    assert "NOTHING in the writer queue" in problem
    # The report has to carry the way out. A session reading this should not
    # have to work out the resume incantation from the cursor.
    assert "drain-writers.yml" in problem and job["cursor"] in problem


def test_a_chain_whose_next_slice_is_waiting_its_turn_is_not_stalled():
    """The distinction the check turns on.

    A backfill ticket sorts behind every correction and can legitimately wait
    hours. Waiting in the line and having fallen out of it look identical from
    the cursor alone, which is why this reads the queue rather than a clock.
    """
    state, job = _running()
    job["updated_at"] = _stamp(50)

    queue = writer_queue.empty_queue()
    writer_queue.enqueue(queue, GDELT, bs.next_inputs(job),
                         members={GDELT: "backfill-gdelt-2026"})

    report = bs.summary(state, queue=queue)
    assert report["problems"] == []
    assert report["jobs"][0]["waiting_on"], "the live ticket was not reported"


def test_a_ticket_for_a_DIFFERENT_chain_of_the_same_workflow_does_not_count():
    """One workflow, several independent chains.

    `backfill-structured-2026.yml` walks bse_india, companies_house and
    opendart_korea over the same window. companies_house sat dead for two days
    while the other two were moving, so a check that matched on the workflow
    name alone would have called it healthy the entire time.
    """
    state, job = _job(workflow="backfill-structured-2026.yml", label="companies_house",
                      unit="slices", start="0", end="7", slice_size=1)
    job.update(state="running", cursor="1", slices=1, updated_at=_stamp(50),
               inputs={"source": "companies_house", "start": "2026-01-01"})

    queue = writer_queue.empty_queue()
    writer_queue.enqueue(queue, "backfill-structured-2026.yml",
                         {"source": "bse_india", "start": "2026-01-01"},
                         members={"backfill-structured-2026.yml": "x"})

    report = bs.summary(state, queue=queue)
    assert report["problems"], (
        "a ticket for bse_india was read as cover for companies_house")


def test_a_chain_that_moved_recently_is_busy_rather_than_stalled():
    """The grace period. A slice runs up to 90 minutes and a drain tick is
    throttled to roughly an hour, so a gap is not immediately a death."""
    state, job = _running()
    job["updated_at"] = _stamp(1)
    assert bs.summary(state, queue=writer_queue.empty_queue())["problems"] == []


def test_a_finished_chain_is_never_reported_as_stalled():
    state, job = _running()
    job["state"] = "done"
    job["cursor"] = None
    job["updated_at"] = _stamp(500)
    assert bs.summary(state, queue=writer_queue.empty_queue())["problems"] == []


def test_no_queue_file_is_UNKNOWN_and_never_a_stall(tmp_path):
    """Three states, not two. A check that could not run is not a pass — and
    it is not a failure either, so it must not manufacture a red run."""
    state, job = _running()
    job["updated_at"] = _stamp(50)

    report = bs.summary(state, queue_path=tmp_path / "absent.json")
    assert report["problems"] == []
    assert report["jobs"][0]["waiting_on"] == "unknown", (
        "an unreadable queue was reported as an empty one")


# --- priority has to survive the chain that carries it --------------------

def test_priority_is_sticky_along_a_chain(tmp_path):
    """`--priority 5` bought one slice and then silently expired.

    `default_priority()` is a property of the WORKFLOW, so it reapplied to
    every requeued ticket: the free, no-model structured walkers were queued
    at 5 so they would drain ahead of the paid ones, and each chain's own next
    slice came back at BACKFILL_PRIORITY behind the very work it was meant to
    overtake. A parameter that looks effective and is not is worse than one
    that is missing.
    """
    import json

    state, job = _job()
    state_path = tmp_path / "backfill_state.json"
    queue_path = tmp_path / "writer_queue.json"
    ticket_path = tmp_path / "slice.json"
    bs.save(state, state_path)

    queue = writer_queue.empty_queue()
    writer_queue.enqueue(queue, GDELT, bs.next_inputs(job), priority=5,
                         members={GDELT: "backfill-gdelt-2026"})
    writer_queue.save(queue, queue_path)

    bs.emit(ticket_path, bs.slice_ticket(job, "2026-01-01", "2026-01-04",
                                         totals={"stored": 3}))
    assert bs.main(["--file", str(state_path), "record", "--from", str(ticket_path),
                    "--queue", "--queue-file", str(queue_path)]) == 0

    tickets = json.loads(queue_path.read_text())["tickets"]
    assert [t["priority"] for t in tickets] == [5, 5], (
        "the chain's own next slice reverted to the workflow default, so the "
        "override the operator typed lasted exactly one slice")


def test_a_chain_with_no_override_still_gets_the_workflow_default(tmp_path):
    """Stickiness must not become a way to lose the default."""
    import json

    state, job = _job()
    state_path = tmp_path / "backfill_state.json"
    queue_path = tmp_path / "writer_queue.json"
    ticket_path = tmp_path / "slice.json"
    bs.save(state, state_path)
    writer_queue.save(writer_queue.empty_queue(), queue_path)
    bs.emit(ticket_path, bs.slice_ticket(job, "2026-01-01", "2026-01-04"))

    assert bs.main(["--file", str(state_path), "record", "--from", str(ticket_path),
                    "--queue", "--queue-file", str(queue_path)]) == 0
    tickets = json.loads(queue_path.read_text())["tickets"]
    assert tickets[0]["priority"] == writer_queue.BACKFILL_PRIORITY
