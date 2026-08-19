"""One budget event must produce one email, not two red workflows.

MEASURED 2026-08-06
-------------------
`tripwire` run 31088398613 exited non-zero on
"ACTION NEEDED: this month's spend $10.08 is at or past 90% of the $10
allowance" — the spend guard binding exactly as the owner set it. Its writer
ticket `20260806T075314Z-tripwire` was then filed `state=failed`, so
drain-writers reported "the writer queue has NEW items that need a human" and
went red too (run 31088429711). Two red workflows, two failure emails, for one
expected, recurring, correct budget stop.

The principle was already settled here for the collectors and the backfills —
`spend.py --degrade` exits 0 and the free work carries on — and had simply never
been extended to (a) the tripwire's own exit code and (b) how a budget stop is
recorded in the queue. These pin both halves, and pin the line on the other
side of them: a GENUINE tripwire fault is still loudly red, and nothing here
raises a cap or weakens the guard.
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import spend
import writer_queue as wq

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _uncommented(path: Path) -> str:
    """The file with comment lines stripped.

    Every assertion about a workflow reads THIS. A `#` line that quotes the old
    behaviour while explaining why it changed would otherwise satisfy a
    substring check on its own — roughly ten tests in this repo have passed
    against defective code for exactly that reason.
    """
    return "\n".join(line for line in path.read_text().splitlines()
                     if not line.lstrip().startswith("#"))


def _ticket(state="dispatched", run_id="99", workflow="tripwire.yml",
            inputs=None, **extra):
    base = {
        "id": f"20260806T075314Z-{workflow.removesuffix('.yml')}",
        "workflow": workflow,
        "inputs": inputs if inputs is not None else {"dry_run": "false"},
        "reason": "scheduled link hygiene (0 7 * * 1,4)",
        "priority": 0,
        "state": state,
        "attempts": 0,
        "requested_at": "2026-08-06T07:53:14Z",
        "run_id": run_id,
        "dispatched_at": "2026-08-06T09:16:26Z",
        "history": [],
    }
    base.update(extra)
    return base


def _run(run_id="99", conclusion="success", name="tripwire"):
    return {"databaseId": run_id, "workflowName": name, "status": "completed",
            "conclusion": conclusion, "createdAt": "2026-08-06T09:16:30Z",
            "event": "workflow_dispatch", "job_count": 1}


MEMBERS = {"tripwire.yml": "tripwire"}
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# half one: a budget stop is recorded as a DEFERRAL
# ---------------------------------------------------------------------------

def test_a_deferring_run_is_filed_deferred_and_not_landed():
    """`landed` would claim work that was never done.

    The run is green and bought nothing. Calling that a landing is how a month
    of closed gates would read as a month of successful discovery.
    """
    ticket = _ticket()
    queue = {"tickets": [ticket]}
    wq.tick(queue, [_run()], members=MEMBERS, now=NOW,
            deferrals={"99": {"run_id": "99", "reason": "allowance exhausted"}})

    assert ticket["state"] == wq.DEFERRED
    assert ticket["deferred_reason"] == "allowance exhausted"
    assert ticket["deferred_at"]


def test_a_deferred_ticket_does_not_need_a_human():
    """THE defect. This is the set drain-writers reddens on."""
    queue = {"tickets": [_ticket(state=wq.DEFERRED,
                                 deferred_at="2026-08-06T09:20:00Z",
                                 deferred_reason="allowance exhausted")]}
    state = wq.summary(queue, now=NOW)
    assert state["problems"] == [], state["problems"]


def test_the_same_ticket_filed_failed_DOES_need_a_human():
    """Guard the guard: if `failed` had also stopped reddening, the test above
    would pass for the wrong reason and a real failure would be silent."""
    queue = {"tickets": [_ticket(state="failed")]}
    assert wq.summary(queue, now=NOW)["problems"]


def test_a_deferred_ticket_is_still_visible_and_countable():
    """Excluded from RED, not from sight. Deferred work nobody can see is the
    silent delete this state exists to prevent."""
    queue = {"tickets": [_ticket(state=wq.DEFERRED,
                                 deferred_at="2026-08-06T09:20:00Z",
                                 deferred_reason="allowance exhausted")]}
    state = wq.summary(queue, now=NOW)
    assert state["counts"][wq.DEFERRED] == 1
    assert len(state["deferred"]) == 1
    assert state["deferred"][0]["reason"] == "allowance exhausted"
    assert state["deferred"][0]["needs_a_human_after"]


def test_a_deferred_ticket_never_occupies_the_writer_slot():
    """It is terminal: it is not dispatched, and it does not count as waiting,
    so a deferred tripwire cannot hold up the next correction."""
    assert wq.DEFERRED in wq.TERMINAL_STATES
    ticket = _ticket(state=wq.DEFERRED)
    queue = {"tickets": [ticket]}
    report = wq.tick(queue, [], members=MEMBERS, now=NOW)
    assert report["dispatch"] is None
    assert wq.summary(queue, now=NOW)["waiting"] == []


# ---------------------------------------------------------------------------
# the escalation window: a deferral is not a delete
# ---------------------------------------------------------------------------

def test_the_window_is_the_next_allowance_month_plus_a_grace():
    """The money comes back at 00:00Z on the 1st, so nothing shorter is honest:
    a ticket waiting for the reset is waiting exactly as designed."""
    ticket = _ticket(state=wq.DEFERRED, deferred_at="2026-08-06T09:20:00Z")
    assert wq.deferral_expires_at(ticket) == (
        datetime(2026, 9, 1, tzinfo=timezone.utc)
        + timedelta(days=wq.DEFERRAL_GRACE_DAYS))


def test_the_grace_outlasts_the_widest_gap_between_two_chances_to_resume():
    """The deferring job runs Monday and Thursday, so the widest gap between
    runs is four days. A grace inside that would escalate a ticket that has not
    yet had a single chance since the reset."""
    assert wq.DEFERRAL_GRACE_DAYS > 4


def test_december_rolls_into_january():
    ticket = _ticket(state=wq.DEFERRED, deferred_at="2026-12-30T09:20:00Z")
    assert wq.deferral_expires_at(ticket).year == 2027
    assert wq.deferral_expires_at(ticket).month == 1


def test_a_deferral_that_outlives_its_window_needs_a_human():
    """The allowance reset and the work still never resumed. That is deferred
    work nobody came back to, which is its own failure mode."""
    queue = {"tickets": [_ticket(state=wq.DEFERRED,
                                 deferred_at="2026-08-06T09:20:00Z",
                                 deferred_reason="allowance exhausted")]}
    late = datetime(2026, 9, 20, tzinfo=timezone.utc)
    problems = wq.summary(queue, now=late)["problems"]
    assert len(problems) == 1
    assert "STILL not done" in problems[0]


def test_an_expired_deferral_the_chain_recovered_from_is_not_a_problem():
    """Prior art, reused rather than duplicated: a later LANDED ticket of the
    same chain is proof the work went through, so the old one stops being a
    reason to go red. Without this every twice-weekly deferral would escalate
    next month even though discovery had long since resumed."""
    deferred = _ticket(state=wq.DEFERRED, deferred_at="2026-08-06T09:20:00Z")
    later = _ticket(state="landed", run_id="100")
    later["id"] = "20260910T070000Z-tripwire"
    later["requested_at"] = "2026-09-10T07:00:00Z"
    queue = {"tickets": [deferred, later]}
    late = datetime(2026, 9, 20, tzinfo=timezone.utc)
    state = wq.summary(queue, now=late)
    assert state["problems"] == []
    assert state["recovered"] and "deferred" in state["recovered"][0]


def test_a_deferral_with_no_usable_date_is_reported_rather_than_forgiven():
    """PASS / FAIL / UNKNOWN are three states. A deferral nothing can date must
    not silently receive an infinite deadline."""
    ticket = _ticket(state=wq.DEFERRED)
    ticket.pop("requested_at")
    queue = {"tickets": [ticket]}
    problems = wq.summary(queue, now=NOW)["problems"]
    assert len(problems) == 1
    assert "no usable date" in problems[0]


def test_an_operator_can_close_a_deferral_by_hand():
    """Deciding the work is no longer wanted is a legitimate outcome, and it
    has to be recordable or the escalation becomes the permanently-red job the
    queue already learned about."""
    ticket = _ticket(state=wq.DEFERRED, deferred_at="2026-08-06T09:20:00Z")
    queue = {"tickets": [ticket]}
    ticket["acknowledged"] = "2026-09-20T00:00:00Z"
    late = datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert wq.summary(queue, now=late)["problems"] == []


# ---------------------------------------------------------------------------
# the channel: how a deferring run tells the drainer
# ---------------------------------------------------------------------------

def test_the_marker_is_one_file_per_run_so_two_runs_cannot_conflict(tmp_path):
    """Deliberately not one shared JSON document. drain-writers pushes the queue
    file every tick, and a second writer rebasing onto it is the lost-write
    shape this repo has already paid for twice."""
    wq.write_deferral("111", "a", directory=tmp_path)
    wq.write_deferral("222", "b", directory=tmp_path)
    found = wq.load_deferrals(tmp_path)
    assert set(found) == {"111", "222"}
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_a_run_id_cannot_escape_the_marker_directory(tmp_path):
    with pytest.raises(ValueError):
        wq.write_deferral("../../etc/passwd", "x", directory=tmp_path)


def test_an_unreadable_marker_is_ignored_rather_than_fatal(tmp_path):
    """A parser that guesses wrong must fail open: the worst case of ignoring a
    marker is the old behaviour, and the worst case of raising is a drainer
    that reconciles nothing at all."""
    (tmp_path / "333.json").write_text("{not json")
    wq.write_deferral("444", "b", directory=tmp_path)
    assert set(wq.load_deferrals(tmp_path)) == {"444"}


def test_the_tick_consumes_the_marker_it_applied(tmp_path):
    """Left behind it would re-apply on every tick forever."""
    queue_file = tmp_path / "queue.json"
    markers = tmp_path / "markers"
    runs_file = tmp_path / "runs.json"
    wq.save({"version": 1, "tickets": [_ticket()], "last_tick": None}, queue_file)
    wq.write_deferral("99", "allowance exhausted", directory=markers)
    runs_file.write_text(json.dumps([_run()]))

    rc = wq.main(["--file", str(queue_file), "tick", "--runs", str(runs_file),
                  "--deferrals", str(markers)])
    assert rc == 0, "a budget deferral must not redden the drain tick"
    assert wq.load(queue_file)["tickets"][0]["state"] == wq.DEFERRED
    assert list(markers.glob("*.json")) == []


def test_a_marker_nobody_claims_is_swept_out_loud(tmp_path, capsys):
    """Swept, so the directory is not a landfill; out loud, because a file
    deleted in silence is the delete this whole state exists to prevent."""
    queue_file = tmp_path / "queue.json"
    markers = tmp_path / "markers"
    wq.save({"version": 1, "tickets": [], "last_tick": None}, queue_file)
    old = wq._now() - timedelta(days=wq.DEFERRAL_MARKER_MAX_AGE_DAYS + 1)
    wq.write_deferral("777", "nobody's", directory=markers, now=old)

    wq.main(["--file", str(queue_file), "tick", "--deferrals", str(markers)])
    out = capsys.readouterr().out
    assert "sweeping the budget-deferral marker" in out
    assert list(markers.glob("*.json")) == []


def test_the_cli_writes_a_marker_the_tick_can_read(tmp_path):
    rc = wq.main(["mark-deferred", "--run-id", "31088398613",
                  "--reason", "allowance exhausted",
                  "--workflow", "tripwire.yml", "--dir", str(tmp_path)])
    assert rc == 0
    assert wq.load_deferrals(tmp_path)["31088398613"]["reason"] == "allowance exhausted"


# ---------------------------------------------------------------------------
# half two: the tripwire's own exit code
# ---------------------------------------------------------------------------

def test_the_gate_exits_zero_over_the_allowance(tmp_path, monkeypatch, capsys):
    """Hitting the monthly allowance is expected, recurring and correct."""
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    spend.gate(True, 10.08)
    assert "::notice::" in capsys.readouterr().out
    assert "over=true" in (tmp_path / "out").read_text()


def test_the_gate_names_the_spend_and_the_allowance(capsys):
    """A stop that does not say how much or against what is a stop nobody can
    act on."""
    spend.gate(True, 10.08)
    out = capsys.readouterr().out
    assert "10.08" in out and f"{spend.MONTHLY_ALLOWANCE_USD:,.2f}" in out


def test_the_gate_says_open_when_inside_the_allowance(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    spend.gate(False, 2.00)
    assert "over=false" in (tmp_path / "out").read_text()


def test_the_gate_never_exits_non_zero(monkeypatch):
    """Whichever way the answer goes."""
    for used, limit in ((100.0, 200.0), (0.5, 200.0)):
        monkeypatch.setattr(spend, "fetch",
                            lambda u=used, l=limit: {"usage": u, "limit": l})
        monkeypatch.setattr(spend, "month_delta", lambda used: (used, "2026-08"))
        monkeypatch.setattr(sys, "argv", ["spend.py", "--gate"])
        assert spend.main() == 0


def test_the_tripwire_asks_the_ceiling_instead_of_being_halted_by_it():
    text = _uncommented(WORKFLOWS / "tripwire.yml")
    assert "python spend.py --gate" in text
    assert "--enforce" not in text, (
        "an --enforce here is the 2026-08-06 double alert again: the run goes "
        "red for a budget stop and its queue ticket is filed as a failure")


def test_the_paid_step_skips_itself_when_the_gate_is_closed():
    """The gate has to actually gate. Exiting 0 and then spending anyway would
    be the guard weakened, which is the one thing this change may not do."""
    text = _uncommented(WORKFLOWS / "tripwire.yml")
    gate_at = text.index("python spend.py --gate")
    run_at = text.index("python run_tripwire.py")
    assert gate_at < run_at, "the guard must run before the money"
    between = text[gate_at:run_at]
    assert "steps.spend.outputs.over != 'true'" in between


def test_a_genuine_tripwire_fault_is_still_loudly_red():
    """The distinction that makes the rest of this safe. run_tripwire.py's own
    non-zero exits — a silent zero from the outside view, a crash — are
    untouched by the gate, and the step that runs it has no `|| true` and no
    `continue-on-error`."""
    src = inspect.getsource(sys.modules["run_tripwire"]) if \
        "run_tripwire" in sys.modules else (ROOT / "run_tripwire.py").read_text()
    assert "return 1" in src

    text = _uncommented(WORKFLOWS / "tripwire.yml")
    run_step = text[text.index("python run_tripwire.py"):][:400]
    assert "continue-on-error" not in run_step
    assert "|| true" not in run_step


def test_a_deferring_run_records_the_deferral_before_it_ends_green():
    """Green and silent would file the ticket as landed."""
    text = _uncommented(WORKFLOWS / "tripwire.yml")
    assert "writer_queue.py mark-deferred" in text
    assert "steps.spend.outputs.over == 'true'" in text


def test_the_owner_still_hears_about_it_exactly_once():
    """The red run used to be the signal. Now that stopping is green, one
    deduped email is — on the same endpoint, dedupe and held-not-lost outbox as
    every other alert, keyed on the allowance MONTH so the remaining runs of a
    closed month add nothing to the inbox."""
    text = _uncommented(WORKFLOWS / "tripwire.yml")
    assert "ci_alert.py" in text
    assert "--notice-key" in text
    assert re.search(r'spend-ceiling:\$\(date -u \+%Y-%m\)', text), (
        "the dedupe key must be per allowance month, or the owner gets one "
        "email per run for the rest of a capped month")
    assert "alert_outbox.py enqueue" in text, (
        "an undeliverable alert is HELD, not lost")


def test_the_notice_key_is_one_the_endpoint_will_accept():
    """A key that fails KEY_SAFE earns a settled 400 on every attempt forever."""
    import ci_alert
    assert ci_alert.KEY_SAFE.match("spend-ceiling:2026-08")


def test_the_notice_path_needs_no_run_conclusion():
    """It is not reporting a run. Requiring --run-id/--conclusion would mean
    inventing a fake failure to describe something that did not fail."""
    import ci_alert
    rc = ci_alert.main(["--notice-key", "spend-ceiling:2026-08",
                        "--notice-subject", "s", "--notice-body", "b",
                        "--dry-run"])
    assert rc == 0


def test_the_run_conclusion_path_still_demands_its_arguments():
    """Guard the guard: making three arguments optional must not let a real CI
    alert be sent with none of them."""
    import ci_alert
    with pytest.raises(SystemExit):
        ci_alert.main(["--dry-run"])


# ---------------------------------------------------------------------------
# the same shape anywhere else
# ---------------------------------------------------------------------------

def test_no_workflow_is_hard_stopped_by_the_spend_guard():
    """The audit, kept as a test rather than as a claim in a report. Every other
    workflow already used --degrade; tripwire.yml was the only --enforce, and it
    is now --gate. A new one would be the same defect with a new name."""
    offenders = sorted(p.name for p in WORKFLOWS.glob("*.yml")
                       if "spend.py --enforce" in _uncommented(p))
    assert offenders == [], (
        f"{offenders} exit non-zero when the monthly allowance binds, which "
        "turns an expected budget stop into a red run — and, for any workflow "
        "dispatched through the writer queue, into a second red run from "
        "drain-writers filing its ticket as failed")


def test_the_guard_itself_is_untouched():
    """Nothing in THIS file's subject may soften the ceiling. The allowance is
    whatever the owner last set (see test_the_allowance_is_the_number_the_owner_set,
    which is the one place that pins the value), the stop is at the same 90%,
    and --degrade still exits 0 with paid reads off.

    The allowance moved 10.0 -> 18.0 on 2026-08-12 by the owner's decision. This
    test used to pin the literal too, which meant a legitimate budget change had
    to edit three files that each claimed to be the single source of the number.
    It now asserts the SHAPE it actually cares about — a real ceiling, stopping
    short of it, below the provider's hard cap — so it goes red for a softened
    guard and not for a decision the owner took.
    """
    assert 0 < spend.MONTHLY_ALLOWANCE_USD < 20.0, (
        "the allowance must be a real ceiling and must stay under the $20 "
        "provider cap on the key, so that spend.py degrades before the "
        "provider fails a call mid-run")
    assert spend.STOP_AT_FRACTION == 0.9


def test_a_gated_run_buys_nothing():
    """`--gate` reports; it must not be a way to spend past the ceiling."""
    src = inspect.getsource(spend.gate)
    assert "GITHUB_OUTPUT" in src
    assert "MONTHLY_ALLOWANCE_USD" not in src.split("def gate")[0]


def test_the_drainer_commits_the_markers_it_consumes():
    """A deletion that is never pushed is a marker that re-applies forever."""
    text = _uncommented(WORKFLOWS / "drain-writers.yml")
    assert "git add -A data/writer_deferrals" in text


def test_the_deferring_run_commits_the_marker_it_wrote():
    """A marker that stays on the runner is a marker the drainer never sees."""
    text = _uncommented(WORKFLOWS / "tripwire.yml")
    assert text.count("data/writer_deferrals") >= 2, (
        "the marker must be staged both before and after the reset-to-origin, "
        "or the retry loop drops it")


def test_writer_queue_still_imports_with_no_dependencies():
    """ops_status.py imports it and must never need a network or a key."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import writer_queue" % str(ROOT)],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


# --- a run that declined to spend must not look like a run that died -------
#
# MEASURED 2026-08-18. ops_status.py reported, in one report:
#
#     [3b] DISCOVERY TRIPWIRE  DORMANT — dispatch only
#     -> tripwire last ran 406h ago — stale (its schedule expects a run
#        within 336h)
#
# Both false. The tripwire is armed Mon+Thu via schedule-link-hygiene.yml and
# had run green on 08-10, 08-13 and 08-17. It looked dead because the spend
# gate is read BEFORE the database is opened, so a declining run filed no
# source_health row at all and the ledger's newest entry stayed at 2026-08-02 —
# the last run that spent money.
#
# The tests above pin "a budget stop is green and is recorded in the QUEUE".
# These pin the other ledger: it must also be recorded in source_health, or the
# staleness leash cannot tell a binding budget from a dead collector. Three
# false alarms in one report is how a session learns to skim the report.

def test_a_declining_tripwire_run_files_a_health_row():
    """Green and SILENT is what made a working budget look like a dead job."""
    import run_tripwire

    assert hasattr(run_tripwire, "report_declined"), (
        "a run that declines to spend must still file its run in the health "
        "ledger; without a row it is indistinguishable from a run that died")

    src = inspect.getsource(run_tripwire.report_declined)
    assert 'status="skipped"' in src, (
        "the declining run must file `skipped`: not ok (nothing was "
        "collected, and report_health rewrites a zero-item ok to degraded) "
        "and not degraded/error (nothing broke)")

    # The gate's own decline path has to CALL it, not merely have it available.
    main_src = inspect.getsource(run_tripwire.main)
    gate = main_src.split("NOT SPENDING")[1]
    assert "report_declined" in gate.split("return 0")[0], (
        "run_tripwire.main returns 0 at the spend gate without filing the "
        "row, which is the 406-hour false alarm of 2026-08-18")


def test_a_declining_run_is_benign_in_both_judges_and_still_ages():
    """`skipped` is not an incident — and it is not an exemption either.

    It must be benign (a budget stop is UNDECIDED, never a verdict) while its
    timestamp keeps ticking the staleness clock. Putting it alongside
    retired/disabled would exempt a genuinely dead collector from the age check
    for ever, which is the failure the status exists to close.
    """
    import health_digest
    import ops_status

    assert "skipped" in health_digest.BENIGN_STATUSES
    assert "skipped" in ops_status.BENIGN_STATUSES
    assert "skipped" not in health_digest.DELIBERATELY_STOPPED, (
        "a skipped run is FRESH evidence, not a deliberate stop: exempting it "
        "from the age check would hide a dead job for ever")


def test_the_tripwire_arming_report_reads_the_scheduler_not_its_own_file():
    """A cron in tripwire.yml is the BUG, so it cannot be the armed signal.

    ops_status [3b] asked whether tripwire.yml carried a cron and printed
    DORMANT when it did not. Arming the tripwire meant DELETING that cron and
    moving the slot to the scheduler, so the check was guaranteed to print
    DORMANT for ever — and would have printed ARMED in precisely the miswired
    state `_report_link_schedule` exists to catch.
    """
    import ops_status

    src = inspect.getsource(ops_status._report_discovery)
    assert "LINK_SCHEDULER" in src, (
        "[3b] must decide arming from the workflow that writes the ticket, "
        "not from the writer's own file")
    assert "MISWIRED" in src, (
        "a cron in tripwire.yml is an eviction bug and must be reported as "
        "one, never as the armed state")


def test_every_collector_that_reports_health_has_a_chosen_leash():
    """DEFAULT_MAX_AGE_HOURS is a backstop, never a verdict.

    primary_chase had no entry, so it silently inherited 336h and was reported
    "stale (its schedule expects a run within 336h)" — about a job that is
    dispatch-only by explicit design and has no schedule to expect anything of.
    A leash nobody chose must be loud rather than quietly applied.
    """
    import sqlite3

    import staleness

    db = ROOT / "data" / "talent_intel.db"
    if not db.exists():                            # pragma: no cover
        pytest.skip("no committed database in this checkout")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        seen = {r[0] for r in conn.execute(
            "SELECT DISTINCT collector FROM source_health")}
    finally:
        conn.close()

    missing = sorted(seen - set(staleness.MAX_AGE_HOURS))
    assert not missing, (
        f"{missing} file health rows but have no leash in staleness.py, so "
        f"each silently wears DEFAULT_MAX_AGE_HOURS ({staleness.DEFAULT_MAX_AGE_HOURS}h). "
        f"Give each one a number derived from its REAL cadence — including "
        f"'this is dispatch-only', which is a cadence too")
