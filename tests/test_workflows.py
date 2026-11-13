"""Workflow files must parse.

A shell ternary in a plain YAML scalar (`? 1 : 0`) reads as a mapping key and
makes GitHub reject the whole file. It fails before any job is created, so
there are no logs and no annotations — just a red X with no explanation.
Catching it here costs nothing.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = sorted((Path(__file__).parent.parent / ".github" / "workflows").glob("*.yml"))


def test_there_are_workflows():
    assert WORKFLOWS, "no workflow files found"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_parses(path):
    parsed = yaml.safe_load(path.read_text())
    assert isinstance(parsed, dict)
    # `on:` is YAML's boolean True, which is why it is looked up as True here.
    assert parsed.get("on") or parsed.get(True), f"{path.name} has no trigger"
    assert parsed.get("jobs"), f"{path.name} defines no jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_step_has_a_run_or_uses(path):
    parsed = yaml.safe_load(path.read_text())
    for job_name, job in parsed["jobs"].items():
        for step in job.get("steps", []):
            assert "run" in step or "uses" in step, (
                f"{path.name}:{job_name} has a step with neither run nor uses: {step}"
            )


def _code(run):
    """The step's script with comments stripped.

    Every assertion about ORDER has to run against this. The comments in these
    steps talk about `merge_db.py` and `git reset --hard` by name, so matching
    against the raw text finds the explanation rather than the command.
    """
    return "\n".join(line for line in run.splitlines()
                     if not line.lstrip().startswith("#"))


def _database_writers():
    """Every workflow that writes data/talent_intel.db, as (name, commit steps)."""
    import yaml

    for path in WORKFLOWS:
        text = path.read_text()
        if "talent_intel.db" not in text:
            continue
        parsed = yaml.safe_load(text)
        steps = [s for job in parsed["jobs"].values() for s in job.get("steps", [])
                 if "talent_intel.db" in _code(s.get("run") or "")]
        if steps:
            yield path.name, steps


def test_the_collect_commit_survives_a_racing_push():
    """The first armed run collected 28 records and lost all of them.

    A run checks out main, collects for several minutes, then pushes. A plugin
    deploy landed in that window, the push was rejected, and the records existed
    only on the runner. Six dry runs could never have found this, because a dry
    run writes nothing.
    """
    from pathlib import Path

    import yaml

    wf = yaml.safe_load(
        (Path(__file__).parent.parent / ".github/workflows/collect.yml").read_text())
    step = next(s for s in wf["jobs"]["collect"]["steps"]
                if s.get("name") == "Commit the database")
    run = _code(step["run"])

    assert "for attempt in" in run, "a single push attempt loses the data on any race"
    assert "git fetch origin main" in run and "git reset --hard origin/main" in run
    # Our rows have to survive the reset. They used to be restored by copying our
    # whole file back, which is the bug below; now they are merged back in.
    assert run.index("git reset --hard") < run.index("merge_db.py",
                                                     run.index("git reset --hard"))
    # Failure must be loud. Silently exiting 0 here is how a month of runs
    # quietly stores nothing.
    assert "::error::" in run and run.rstrip().endswith("exit 1")


def test_no_writer_copies_its_database_over_the_reset():
    """The reset-and-copy that cost 9,572 signal rows and the identity cache.

    Every writer used to end the same way: on a rejected push, `git reset --hard
    origin/main` and then `cp our.db data/talent_intel.db`. That replaces the
    WHOLE file, so every row anyone else committed while this run was collecting
    is destroyed, and nothing reports an error.

    The comment defending it argued the concurrency group made the run the only
    writer. That was wrong in two directions, and both were paid for on
    2026-07-28/29:

      * the group only excludes other workflow RUNS. A human or an agent
        committing the database from a laptop is invisible to it, which is how
        the 3,604-row employer_identity cache went to zero (9991861 -> e1bfb03).
      * a run the group serialises CORRECTLY is stale by construction.
        actions/checkout gives it the SHA pinned when it was dispatched, so the
        longer it waits for the lock the more it is about to overwrite. Run
        30413051586 was dispatched at 01:05, started at 03:21, and discarded the
        311 rows pushed at 03:21:28 (d46fb10 -> 4d604f3).

    So the lock is necessary and nowhere near sufficient, and this asserts the
    thing that actually is: nobody restores the database by copying a file onto
    it. Merging is the only safe way back, and merge_db.py is where it lives.
    """
    offenders = {}
    for name, steps in _database_writers():
        for step in steps:
            for line in _code(step["run"]).splitlines():
                stripped = line.strip()
                if not stripped.startswith(("cp ", "mv ", "install ")):
                    continue
                # The destination is the last word. Saving the database TO
                # $RUNNER_TEMP is fine and necessary; restoring FROM it is the bug.
                destination = stripped.rstrip("\\").split()[-1].strip("'\"")
                if destination.endswith("data/talent_intel.db"):
                    offenders.setdefault(name, []).append(stripped)

    assert not offenders, (
        "these steps restore the database by copying a file over it, which "
        f"destroys every row another writer pushed in the meantime: {offenders}"
    )


def test_every_writer_that_resets_merges_its_rows_back():
    """`git reset --hard origin/main` throws this run's rows away.

    Whatever puts them back has to be a merge. The one place this must NOT be
    applied is correct-form-d.yml, which edits rows in place rather than
    appending a revision — a merge has no new (content_hash, revision) to carry,
    so it would turn a loud rerun into a silent no-op. It rebases instead, and
    so never reaches this assertion.
    """
    for name, steps in _database_writers():
        for step in steps:
            run = _code(step["run"])
            if "git reset --hard" not in run:
                continue
            assert "merge_db.py" in run, (
                f"{name} resets to origin/main without merging its rows back, "
                "so everything this run collected is discarded"
            )
            assert run.index("git reset --hard") < run.index("merge_db.py"), (
                f"{name} merges before the reset, which then discards the merge"
            )
            # The merge has to be what gets committed, not an afterthought.
            assert run.index("merge_db.py") < run.index("git commit"), (
                f"{name} commits before merging, so it commits the unmerged file"
            )


def test_a_failed_push_is_loud():
    """A writer that gives up quietly leaves rows on a runner that gets deleted,
    while WordPress keeps the published copy — so the next run re-fetches and
    re-pays the LLM for the same stories."""
    for name, steps in _database_writers():
        for step in steps:
            run = _code(step["run"])
            if "for attempt in" not in run:
                continue
            assert "::error::" in run and run.rstrip().endswith("exit 1"), (
                f"{name} exhausts its push attempts without failing the run"
            )


# --- A rejected push is a race, not an ending ------------------------------
#
# retract.yml run 30481782149 (2026-07-29) withdrew a row from WordPress and
# from its checked-out database, logged
# "retracted 5999b8ad31d6c468e2fc855a4691543c: wordpress=1 local=1", and then
# lost `git push` to a commit that landed in the gap between its rebase and its
# push: "! [rejected] main -> main (fetch first)". The run went red and the
# queue marked the ticket failed, so this was LOUD. The damage was the state it
# left: the row withdrawn on the site and still current in the committed
# database, which the next run therefore reads as live. It stayed that way until
# a human re-queued it.
#
# The racer is usually not another database writer at all — drain-writers.yml
# commits data/writer_queue.json every fifteen minutes — so the rebase almost
# always succeeds and the push simply needs trying again.

# Writers that make a single push attempt ON PURPOSE, with the reason. Held as
# data rather than as an exception inside the loop, so that "it was deliberate"
# has to be written down rather than assumed for the next writer that merely
# forgot.
SINGLE_PUSH_ATTEMPT_BY_DESIGN = {
    "correct-form-d.yml":
        "edits rows in place, so a rerun has to be loud rather than automatic; "
        "documented in CLAUDE.md and in the workflow itself",
}


def _push_steps():
    """Every commit step of every database writer, as (name, script)."""
    for name, steps in _database_writers():
        for step in steps:
            run = _code(step.get("run") or "")
            if "git push" in run:
                yield name, run


def _sole_step(workflow, predicate):
    """The one step of `workflow` whose script matches, as the step dict."""
    import yaml

    parsed = yaml.safe_load(
        (Path(__file__).parent.parent / ".github/workflows" / workflow).read_text())
    steps = [s for job in parsed["jobs"].values() for s in job.get("steps", [])
             if predicate(_code(s.get("run") or ""))]
    assert len(steps) == 1, (
        f"{workflow}: expected exactly one matching step, found {len(steps)}")
    return steps[0]


def test_every_database_writer_retries_a_rejected_push():
    """One attempt loses to any commit that lands while the run works.

    A retry is only meaningful if the run gets back onto current main between
    attempts, so the loop has to contain either the rebase or the
    reset-and-merge — pushing the same rejected commit five times is not a fix.
    """
    seen = set()
    for name, run in _push_steps():
        seen.add(name)
        if name in SINGLE_PUSH_ATTEMPT_BY_DESIGN:
            continue
        assert "for attempt in" in run, (
            f"{name} makes a single push attempt, so any commit landing while it "
            "ran leaves its work on a runner that is about to be deleted. If that "
            "is deliberate, say so in SINGLE_PUSH_ATTEMPT_BY_DESIGN.")
        loop = run[run.index("for attempt in"):]
        assert "git pull --rebase" in loop or "git reset --hard" in loop, (
            f"{name} retries the push without getting back onto current main, so "
            "every attempt is rejected for the same reason as the first")

    # The exemptions have to name real writers, or they read as coverage while
    # covering a file that no longer pushes anything.
    assert set(SINGLE_PUSH_ATTEMPT_BY_DESIGN) <= seen, (
        "SINGLE_PUSH_ATTEMPT_BY_DESIGN names something that is not a database "
        f"writer: {sorted(set(SINGLE_PUSH_ATTEMPT_BY_DESIGN) - seen)}")
    assert {"retract.yml", "collect.yml"} <= seen, (
        f"the writers this covers are not being found: {sorted(seen)}")


def test_the_writers_that_touch_wordpress_first_survive_a_racing_push():
    """These three apply their change to the SITE before they commit.

    So a lost push is not "collect it again next run" — it is the site and the
    database disagreeing, with no third place to look. correct-form-d.yml is
    deliberately excluded: it goes red on a race by design.
    """
    for workflow in ("retract.yml", "correct-layoff-scope.yml",
                     "correct-sec-pillar.yml"):
        step = _sole_step(workflow, lambda run: "git push" in run)
        run = _code(step["run"])

        assert "for attempt in 1 2 3 4 5" in run, f"{workflow}: no retry loop"
        loop = run[run.index("for attempt in"):]
        assert loop.index("git pull --rebase --autostash origin main") < loop.index("git push"), (
            f"{workflow} pushes before rebasing inside the loop, so the first "
            "attempt is rejected on every run and one of the five is wasted")

        # A rebase that stops on a binary conflict leaves a detached HEAD, and
        # pushing that four more times reports nothing new. Abort, and be red.
        assert "git rebase --abort" in run, (
            f"{workflow} can leave the runner mid-rebase and keep pushing")

        # The error has to say how to get the withdrawal committed, because the
        # obvious recovery — copying a database file over main — is the one that
        # destroyed 9,572 rows.
        assert "::error::" in run and run.rstrip().endswith("exit 1"), (
            f"{workflow} exhausts its attempts without failing the run")
        # Ending in `exit 1` is not enough on its own: an `exit 0` anywhere after
        # the loop makes the rest unreachable, and the run goes GREEN with the
        # site and the database disagreeing. That is worse than the incident,
        # which at least went red.
        tail = run[run.rindex("done"):]
        assert "exit 0" not in tail, (
            f"{workflow} can exit 0 after exhausting its push attempts, so a lost "
            "commit reports success")
        assert "git push || true" not in run, (
            f"{workflow} swallows the push failure")
        assert "drain-writers.yml" in run, (
            f"{workflow} fails without naming the queue that re-runs it, so the "
            "next person dispatches it directly and can have it evicted")

        # merge_db.py cannot carry these: they change existing rows rather than
        # appending a (content_hash, revision), so a merge finds nothing to
        # bring across and turns a loud rerun into a silent success.
        assert "merge_db.py" not in run, (
            f"{workflow} merges, which silently skips a change made in place")
        assert "git reset --hard" not in run, (
            f"{workflow} resets to origin/main, which discards its own change")


def test_a_retraction_is_still_committed_when_the_run_failed():
    """The rows are withdrawn from the site before this step runs.

    So the local record of the withdrawal must be committed even when the
    retraction step failed part-way — otherwise the site loses rows the
    database still believes are live, which is the 2026-07-29 state made
    permanent.
    """
    step = _sole_step("retract.yml", lambda run: "git push" in run)
    assert "cancelled()" in str(step.get("if", "")), (
        "retract.yml's commit step is conditioned on success, so a run that "
        "failed after withdrawing rows commits nothing")


def test_every_database_writer_shares_one_lock():
    """The reset-and-replace each writer performs is only safe while exactly
    one of them can run at a time.

    This used to assert that collect.yml's group was the literal string
    "collect", which passed happily while the four backfill workflows sat in a
    DIFFERENT group. Two groups guarding one file is not a lock: a backfill and
    a collector could run together, and since both end with `reset --hard
    origin/main` and then copy their own database back over it, whichever
    pushed second silently destroyed the other's rows (found 2026-07-28).

    So this asserts the PROPERTY rather than a name: every workflow that writes
    the database sits in the same group, whatever it is called, and none of
    them cancels a run already in progress.
    """
    from pathlib import Path

    import yaml

    workflows = Path(__file__).parent.parent / ".github/workflows"
    groups = {}
    for path in sorted(workflows.glob("*.yml")):
        text = path.read_text()
        if "talent_intel.db" not in text:
            continue
        concurrency = (yaml.safe_load(text) or {}).get("concurrency") or {}
        groups[path.name] = concurrency.get("group")
        assert concurrency.get("cancel-in-progress") is False, path.name

    assert len(groups) >= 5, f"expected several database writers, found {groups}"
    assert None not in groups.values(), f"a database writer has no lock: {groups}"
    assert len(set(groups.values())) == 1, (
        "database writers are split across concurrency groups, so they do not "
        f"exclude each other: {groups}"
    )


# --- Bounded backfill slices ----------------------------------------------
#
# `backfill-gdelt-2026` took the writer lock at 04:59 UTC on 2026-07-29, ran
# for 350 minutes, hit its own timeout, was CANCELLED, and had its commit step
# SKIPPED by `if: !cancelled()`. Six hours of collection was lost and every
# correction behind it waited the whole time. Priority ordering cannot preempt
# a running job, so the fix is slices that finish. These assert the properties
# that make that true, because each one of them is a way for the fix to rot
# back into the incident.

def _backfills():
    import yaml

    for path in WORKFLOWS:
        if not path.name.startswith("backfill-"):
            continue
        yield path.name, yaml.safe_load(path.read_text())


def test_there_are_backfills_to_check():
    assert list(_backfills()), "no backfill workflows found — the test is inert"


def test_a_backfill_can_never_hold_the_lock_long_enough_to_starve_it():
    """The timeout IS the bound on a lock hold. Everything else is intention.

    Kept below writer_queue.LONG_HOLD_MINUTES, the point at which the drainer
    reports the queue as starved: a sliced backfill should not be able to reach
    that condition at all.
    """
    import backfill_slices
    import writer_queue

    assert backfill_slices.SLICE_TIMEOUT_MINUTES < writer_queue.LONG_HOLD_MINUTES
    assert backfill_slices.SLICE_BUDGET_MINUTES < backfill_slices.SLICE_TIMEOUT_MINUTES, (
        "the run must stop itself BEFORE the runner kills it: a cancelled run's "
        "commit step is skipped, which is exactly how six hours were lost")

    for name, parsed in _backfills():
        for job_name, job in parsed["jobs"].items():
            timeout = job.get("timeout-minutes")
            assert timeout is not None, f"{name}:{job_name} has no timeout at all"
            assert timeout <= backfill_slices.SLICE_TIMEOUT_MINUTES, (
                f"{name}:{job_name} may hold the single writer lock for "
                f"{timeout} minutes. A backfill runs in slices; only a slice's "
                "worth of time is its to take.")


def test_every_backfill_runs_one_slice_and_emits_its_ticket():
    for name, parsed in _backfills():
        steps = [s for job in parsed["jobs"].values() for s in job.get("steps", [])]
        collecting = [_code(s.get("run") or "") for s in steps
                      if "backfill_" in (s.get("run") or "")
                      and "backfill_slices.py" not in (s.get("run") or "")]
        assert collecting, f"{name} does not run a backfill script"
        for run in collecting:
            assert "--slice" in run, (
                f"{name} runs its whole window in one job. That is the 350-minute "
                "incident: dispatch it in slices or it will hold the lock for as "
                "long as the window a human typed.")
            assert "--emit-next" in run, (
                f"{name} takes a slice but emits no ticket, so the chain stops "
                "after one and nothing says so")


def test_every_backfill_requeues_itself_after_the_reset():
    """The requeue has to survive `git reset --hard origin/main`.

    Written before it, the ticket and the cursor are both thrown away by the
    reset and the chain silently ends. This is the same trap as restoring the
    database by copying a file over it, one file along.
    """
    for name, parsed in _backfills():
        steps = [s for job in parsed["jobs"].values() for s in job.get("steps", [])]
        commit = [_code(s.get("run") or "") for s in steps
                  if "backfill_slices.py record" in (s.get("run") or "")]
        assert commit, f"{name} never records its slice, so it can never resume"
        for run in commit:
            assert "--queue" in run, (
                f"{name} records its progress but queues no successor: the "
                "backfill stops after one slice")
            assert run.index("git reset --hard") < run.index("backfill_slices.py record"), (
                f"{name} records the slice BEFORE the reset, which discards it")
            assert run.index("backfill_slices.py record") < run.index("git commit"), (
                f"{name} commits before recording, so the cursor and the ticket "
                "are not in the commit")
            assert "data/backfill_state.json" in run, (
                f"{name} does not stage the cursor, so progress is not durable")
            assert "data/writer_queue.json" in run, (
                f"{name} does not stage the queue, so the successor ticket is "
                "written and then dropped")


def test_a_backfill_slice_is_committed_even_when_the_chain_stalls():
    """Rows already collected are never the price of a broken chain.

    `backfill_slices record` exits non-zero when the cursor did not move, and a
    GitHub `run:` block is `bash -e`: letting that abort the step would throw
    away the slice's data to report a problem with the slice's bookkeeping.
    """
    for name, parsed in _backfills():
        steps = [s for job in parsed["jobs"].values() for s in job.get("steps", [])]
        for step in steps:
            run = _code(step.get("run") or "")
            if "backfill_slices.py record" not in run:
                continue
            assert "|| RECORD_RC=$?" in run, (
                f"{name} lets a failed record abort the step before the commit")
            assert run.index("git push") < run.index('if [ "$RECORD_RC" = "0" ]'), (
                f"{name} decides its exit code before it has pushed")
            assert run.rstrip().endswith("exit 1"), (
                f"{name} does not go red when the chain stops advancing")


def test_the_drainer_wakes_up_for_every_writer_there_is():
    """A writer missing from the drainer's `workflow_run` list is invisible.

    The drainer's fast path is "the moment a lock holder finishes, look for the
    next ticket". A workflow absent from that list still holds the lock and
    still releases it, but nothing notices, so the queue sits until the 15
    minute cron — and a sliced backfill, which requeues ITSELF and depends on
    that trigger to move, would take a quarter of an hour per slice instead of
    seconds. The list is exactly the kind of hand-kept thing that goes stale the
    day someone adds a writer, so it is derived and compared rather than read.
    """
    import yaml

    import writer_queue

    drainer = yaml.safe_load(
        (Path(__file__).parent.parent / ".github/workflows/drain-writers.yml").read_text())
    triggers = (drainer.get("on") or drainer.get(True))["workflow_run"]["workflows"]

    members = set(writer_queue.lock_group_workflows().values())
    missing = members - set(triggers)
    assert not missing, (
        f"these hold the writer lock but do not wake the drainer: {sorted(missing)}")

    # And nothing in the list that no longer exists, which would read as
    # coverage while covering nothing.
    stale = set(triggers) - members
    assert not stale, f"the drainer waits on workflows that are not writers: {sorted(stale)}"
