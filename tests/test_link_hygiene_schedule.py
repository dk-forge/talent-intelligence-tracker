"""The link-hygiene jobs run on a timer, and the timer is not a cron in them.

Both `archive-sources` and `link-check` write the database, so both sit in the
single `talent-collect` concurrency group, and GitHub keeps exactly ONE pending
run per group. A `schedule:` in either file would make it an uncoordinated third
body entering that group on a timer, with two outcomes and no third:

  * it evicts whatever was pending, or
  * it IS evicted — ending `cancelled` with zero jobs, no steps, no logs and no
    annotation, and unreplayable, because GitHub does not expose a dispatched
    run's inputs. data/writer_queue.json still carries 15 of those from
    2026-07-29, all closed with one hand-written triage note.

So the schedule lives in `schedule-link-hygiene.yml`, which is not a writer, and
which writes a TICKET that drain-writers dispatches into an empty group. A ticket
cannot be evicted, and one that somehow is gets re-dispatched with its inputs
intact.

These tests assert that shape, and they exist because "put the cron back in the
obvious place" is a one-line change that looks like tidying up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# tripwire.yml joined on 2026-07-30 when it was armed. It is the same shape of
# thing as the other two -- a database writer whose schedule must not be a cron
# in its own file -- and it is the one that spends, so it is the one where an
# eviction is most expensive: a run that dies with zero jobs still cost nothing,
# but a slot that silently skips is a week of discovery lost with no red run.
# enrich.yml joined this list on 2026-07-30. It is what carries a captured
# snapshot to the live rows, and it had no schedule of its own, so an
# archiver running eight times a day filled the local ledger while every
# reader went on seeing the publisher's link alone.
# benchmark-diff.yml joined on 2026-08-02, the day it was built: a database
# writer when armed, dormant (one line, exit 0) until a BENCHMARK_* secret
# exists, and scheduled weekly through the same ticket path either way.
WRITERS = ("archive-sources.yml", "link-check.yml", "tripwire.yml",
           "enrich.yml", "benchmark-diff.yml")
SCHEDULER = "schedule-link-hygiene.yml"


def _parsed(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _triggers(name: str) -> dict:
    parsed = _parsed(name)
    # `on:` is YAML's boolean True, which is why it is looked up as True here.
    return parsed.get("on") or parsed.get(True)


def _steps(name: str) -> list[dict]:
    return [s for job in _parsed(name)["jobs"].values()
            for s in job.get("steps", [])]


# --- the two writers must never schedule themselves ------------------------

@pytest.mark.parametrize("name", WRITERS)
def test_a_lock_group_writer_has_no_cron_of_its_own(name):
    triggers = _triggers(name)
    assert "schedule" not in triggers, (
        f"{name} carries its own cron. It is a database writer in the "
        f"talent-collect group, so a scheduled run either evicts the pending "
        f"run or is evicted itself, and an evicted run cannot be replayed. Put "
        f"the slot in {SCHEDULER}, which writes a ticket instead.")
    assert "workflow_dispatch" in triggers


@pytest.mark.parametrize("name", WRITERS)
def test_the_two_writers_still_share_the_writer_lock(name):
    parsed = _parsed(name)
    assert parsed["concurrency"]["group"] == "talent-collect", name
    assert parsed["concurrency"]["cancel-in-progress"] is False, name


@pytest.mark.parametrize("name", WRITERS)
def test_no_commented_out_cron_is_left_lying_around(name):
    """A `# schedule:` block is an invitation to uncomment it.

    Which is the wrong fix, arrived at by the most natural route available. The
    file has to explain instead of tempt.
    """
    text = (WORKFLOWS / name).read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and "- cron:" in stripped:
            pytest.fail(
                f"{name} still has a commented-out cron ({stripped!r}). Someone "
                f"will uncomment it; the header should say why not instead.")


# --- the scheduler ---------------------------------------------------------

def test_the_scheduler_exists_and_is_armed():
    triggers = _triggers(SCHEDULER)
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert "20 */8 * * *" in crons, "the eight-hourly Wayback slot is gone"
    assert "30 5 * * *" in crons, "the daily rot sweep slot is gone"


def test_the_scheduler_is_not_in_the_writer_lock():
    """A scheduler queued behind the lock could never write a ticket for it —
    the same reason drain-writers keeps its own group."""
    concurrency = _parsed(SCHEDULER).get("concurrency") or {}
    assert concurrency.get("group") not in (None, "talent-collect")


def test_the_scheduler_is_not_mistaken_for_a_database_writer():
    """tests/test_workflows.py finds writers by searching the RAW TEXT for the
    database filename, and then requires every match to be in the writer lock.

    So naming the file in this workflow — even in a comment — would make that
    test demand this workflow join the group it must stay out of, and the
    obvious way to satisfy it would be to put it there.
    """
    text = (WORKFLOWS / SCHEDULER).read_text()
    assert "talent_intel.db" not in text


def test_the_scheduler_queues_both_writers_in_recording_mode():
    """The trap this repo keeps re-finding: a green run that recorded nothing.

    Both workflows default dry_run to true, so a ticket that omits it produces a
    perfectly successful run that captures nothing and writes nothing.
    """
    body = "\n".join(s.get("run") or "" for s in _steps(SCHEDULER))
    for name in WRITERS:
        assert name in body, f"{SCHEDULER} never names {name}"
    assert '"dry_run":"false"' in body.replace(" ", ""), (
        "the ticket does not set dry_run=false, so every scheduled run would be "
        "a dry run that records nothing and still goes green")
    assert "--if-absent" in body, (
        "without --if-absent a nightly slot behind a long backfill leaves one "
        "ticket per night, each aging separately into 'the lock is starved'")


def test_the_scheduler_only_ever_enqueues_lock_group_members():
    """`writer_queue.enqueue` refuses a workflow outside the group, so a typo
    here would be a nightly red run rather than a silent one — but it would
    still be a nightly red run."""
    import writer_queue

    members = writer_queue.lock_group_workflows()
    for name in WRITERS:
        assert name in members, f"{name} is no longer in the lock group"


def test_the_scheduler_re_derives_its_ticket_after_a_reset():
    """Same lesson as merge_db.py, one file along.

    A rejected push is answered by fetching main, resetting onto it and writing
    the ticket AGAIN, not by rebasing a diff of a hand-edited JSON file. Writing
    it before the reset means the reset throws it away and the slot silently
    does nothing.
    """
    step = next(s for s in _steps(SCHEDULER)
                if "writer_queue.py enqueue" in (s.get("run") or ""))
    run = "\n".join(line for line in step["run"].splitlines()
                    if not line.lstrip().startswith("#"))
    assert "git reset --hard origin/main" in run
    assert run.index("git reset --hard") < run.index("writer_queue.py enqueue"), (
        "the ticket is written before the reset, which discards it")
    assert run.index("writer_queue.py enqueue") < run.index("git commit"), (
        "the commit happens before the ticket is written")
    assert "for attempt in" in run, "a single push attempt loses the ticket on a race"
    assert "::error::" in run and run.rstrip().endswith("exit 1"), (
        "a slot that cannot commit its ticket exits green, so a night of "
        "archiving is skipped and nothing says so")


def test_an_unmapped_schedule_slot_goes_red_rather_than_quiet():
    """Editing a cron above without editing the mapping below would otherwise
    give a slot that fires forever and queues nothing."""
    step = next(s for s in _steps(SCHEDULER)
                if "github.event.schedule" in (s.get("run") or "")
                or "SLOT" in str(s.get("env") or {}))
    run = step["run"]
    assert "::error::" in run and "exit 1" in run


def test_the_scheduler_does_not_wake_the_drainer_because_it_is_not_a_writer():
    """drain-writers' workflow_run list is asserted elsewhere to be EXACTLY the
    lock group's membership, so listing a non-writer there fails that test. This
    says the same thing from this side, where the mistake would be made."""
    triggers = _triggers("drain-writers.yml")
    listed = triggers["workflow_run"]["workflows"]
    assert _parsed(SCHEDULER)["name"] not in listed


# --- the leashes have to agree with the cadence ----------------------------

def test_the_staleness_leash_matches_the_new_cadence():
    """A checker that stops running looks exactly like a checker with nothing to
    report, so the leash has to be derived from the cron rather than left where
    the last cadence put it.

    Both bounds matter and they fail in opposite directions. Too LONG and a dead
    job stays invisible for days while the digest reads its silence as good
    news; that is what the 180-hour weekly leash did. Too SHORT and the queue
    working correctly becomes an alarm: a ticket written while a 350-minute
    backfill holds the writer lock is entitled to those 350 minutes.
    """
    import staleness

    # tripwire joined this loop on 2026-08-18. It was excluded while it filed a
    # health row ONLY on the runs that spent money: its leash was then carrying
    # "a whole binding month of budget stops" as well as its cadence, so no
    # cron-derived bound could have been honest about it, and the 336h that
    # bought that silence still reported a live twice-weekly collector STALE.
    # Now that a declining run files `skipped` (run_tripwire.report_declined),
    # every scheduled slot leaves evidence and the leash is answerable to the
    # cron like the other two.
    for collector, workflow in (("archive_sources", "archive-sources.yml"),
                                ("link_check", "link-check.yml"),
                                ("tripwire", "tripwire.yml")):
        cadence = min(24.0 / _runs_per_day(cron)
                      for cron in _crons_that_queue(workflow))
        leash = staleness.MAX_AGE_HOURS[collector]
        assert leash >= cadence + 6, (
            f"{collector}'s leash ({leash}h) leaves no room for a ticket to "
            f"wait behind a long backfill, so the queue working as designed "
            f"would page")
        assert leash <= max(cadence * 2, 24) + 12, (
            f"{collector} runs every {cadence:.0f}h but is allowed to go "
            f"silent for {leash}h. A leash that long is how a stopped job goes "
            f"on looking like a healthy one")


def _runs_per_day(cron: str) -> float:
    """Enough of a cron parser for the shapes this scheduler actually uses."""
    minute, hour = cron.split()[0], cron.split()[1]
    day_of_week = cron.split()[4]
    per_hour = len(minute.split(",")) if not minute.startswith("*") else 1
    if hour == "*":
        hours = 24
    elif hour.startswith("*/"):
        hours = len(range(0, 24, int(hour[2:])))
    else:
        hours = len(hour.split(","))
    days = 7 if day_of_week == "*" else len(day_of_week.split(","))
    return per_hour * hours * days / 7


def _crons_that_queue(workflow: str) -> list[str]:
    """The scheduler's slots that ask for `workflow`, read from the mapping.

    Read from the shell `case` rather than from the cron comments, because the
    mapping is what actually decides, and a slot the mapping does not know is
    already a hard failure in that step.
    """
    body = "\n".join(s.get("run") or "" for s in _steps(SCHEDULER))
    found = [line.split("'")[1] for line in body.splitlines()
             if line.strip().startswith("'") and workflow in line]
    assert found, f"no schedule slot in {SCHEDULER} asks for {workflow}"
    crons = [entry["cron"] for entry in _triggers(SCHEDULER)["schedule"]]
    for cron in found:
        assert cron in crons, (
            f"{SCHEDULER} maps the slot {cron!r} to {workflow} but no such cron "
            f"is armed, so {workflow} is scheduled only in prose")
    return found
