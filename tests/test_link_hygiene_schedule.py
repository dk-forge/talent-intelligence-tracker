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

WRITERS = ("archive-sources.yml", "link-check.yml")
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
    assert "40 3 * * *" in crons, "the nightly Wayback slot is gone"
    assert "30 5 * * 1" in crons, "the weekly rot sweep slot is gone"


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
    report. The 2400-hour leash both jobs carried while dormant would have hidden
    a fortnight of silence from an armed weekly job."""
    import staleness

    assert staleness.MAX_AGE_HOURS["archive_sources"] < 24 * 4, (
        "a nightly job may not carry a multi-day leash")
    assert staleness.MAX_AGE_HOURS["archive_sources"] > 24, (
        "one skipped night is not an incident: the candidate list is the gap, "
        "so tomorrow's run picks up what last night's missed")
    assert 168 < staleness.MAX_AGE_HOURS["link_check"] < 168 * 2, (
        "a weekly job's leash should flag inside the second week, not after two "
        "full misses (a fortnight of unchecked citations)")
