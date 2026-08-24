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


def test_the_schedule_sweeps_every_collector_this_workflow_owns():
    """A schedule that only names google_news is a health blind spot.

    collect.yml's cron passed `--source google_news` unconditionally, so gdelt,
    sec_edgar and sec_form_d only ever ran when a human remembered to dispatch
    them — twice each, in their whole lives — while their last manual run sat
    in the ledger saying "ok". This pins the scheduled branch to the full
    sweep, one invocation per source because run_collect takes exactly one.
    """
    from pathlib import Path

    import yaml

    wf = yaml.safe_load(
        (Path(__file__).parent.parent / ".github/workflows/collect.yml").read_text())
    step = next(s for s in wf["jobs"]["collect"]["steps"]
                if s.get("name") == "Collect")
    run = _code(step["run"])

    assert "github.event_name" in step["run"] and "schedule" in run, (
        "the collect step no longer branches on the schedule, so either the "
        "sweep or the single-source dispatch is gone")
    assert "for source in google_news gdelt sec_edgar sec_form_d" in run, (
        "the scheduled sweep must run every built collector this workflow "
        "owns, or the ones it skips go back to running only when a human "
        "remembers them")
    # Each invocation is a fresh process, so each carries its own read-through
    # ceiling. Without these the sweep quadruples the worst-case bill instead
    # of adding a rounding error to it.
    assert "TIT_READTHROUGH_CAP" in run, (
        "the sweep sets no per-source read cap, so every source gets the "
        "google_news production ceiling")
    # One failed source must not silence the sources after it in the loop, and
    # must still turn the step red at the end.
    assert "|| overall=1" in run and "exit $overall" in run
    # The dispatch path keeps its single-source input.
    assert "inputs.source || 'google_news'" in step["run"]

    # Still exactly ONE merge-and-push per job: the sweep stores through one
    # database connection and a second commit step would race the first.
    commits = [s for s in wf["jobs"]["collect"]["steps"]
               if "Commit" in (s.get("name") or "")]
    assert len(commits) == 1


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


# --- gate labels: the same reset destroys them, so the same merge saves them --

def _gate_running_workflows():
    """Every workflow that runs an entry point which calls the paid LLM gate.

    Derived from the scripts themselves rather than from a list kept here: a
    hand-maintained list is exactly what let five backfills classify for months
    with no label ever reaching main.
    """
    import re

    root = Path(__file__).parent.parent
    classifiers = {p.name for p in root.glob("*.py")
                   if "classify.classify(" in p.read_text(encoding="utf-8")}
    assert classifiers, "no script calls classify.classify — the scan is broken"

    for path in WORKFLOWS:
        text = path.read_text()
        invoked = set(re.findall(r"python3?\s+([a-z_0-9]+\.py)", text))
        if invoked & classifiers:
            yield path.name, text


def test_there_are_gate_running_workflows():
    assert list(_gate_running_workflows())


def test_every_workflow_that_classifies_merges_its_labels_back():
    """A gate verdict costs money whoever bought it.

    The commit step resets to origin/main, which throws this run's ledger away
    exactly as it throws the database away, so the labels need the same
    treatment the rows get: saved before the reset, merged after it, staged
    before the commit. The daily collectors did all three. The five backfills
    did none of them, so every verdict they paid for was collected, buffered,
    written and then discarded by the reset — which is the most expensive way
    to lose data, because the money was already spent.
    """
    missing = {}
    for name, text in _gate_running_workflows():
        if "git reset --hard" not in text:
            continue          # nothing destroys the ledger, nothing to restore
        problems = []
        if "merge_gate_labels.py" not in text:
            problems.append("never merges its gate labels back after the reset")
        # The destination under $RUNNER_TEMP differs by workflow
        # (collect-structured.yml keeps a whole `keep/` tree), so this asserts
        # only that the ledger leaves the working copy before the reset.
        if 'cp -R data/gate_labels "$RUNNER_TEMP' not in text:
            problems.append("never saves data/gate_labels before the reset")
        if "git add -A data/gate_labels" not in text:
            problems.append("never stages data/gate_labels, so nothing is committed")
        if problems:
            missing[name] = problems

    assert not missing, (
        "these workflows pay for gate calls and then let `git reset --hard` "
        f"discard the verdicts: {missing}"
    )


def test_the_label_merge_happens_after_the_reset_and_before_the_commit():
    """Same ordering rule as merge_db.py, and it fails the same silent way:
    merged before the reset it is discarded, staged after the commit it is not
    in the commit."""
    for name, text in _gate_running_workflows():
        if "merge_gate_labels.py" not in text:
            continue
        code = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("#"))
        assert code.index("git reset --hard") < code.index("merge_gate_labels.py"), (
            f"{name} merges its gate labels before the reset, which discards them"
        )
        assert code.index("merge_gate_labels.py") < code.index("git commit"), (
            f"{name} commits before merging its gate labels back"
        )


def test_no_two_scheduled_writers_queue_in_the_same_minute():
    """Sharing one lock is necessary; queuing into it together is not free.

    With `cancel-in-progress: false` GitHub keeps at most ONE run pending per
    concurrency group. A third arrival does not join a queue — it CANCELS the
    run already waiting. So two scheduled writers on the same cron minute are
    not "one waits for the other": whichever queues first is the one that gets
    thrown away as soon as anything else shows up.

    collect-press.yml and collect-structured.yml both sat on '0 9 * * *'. The
    morning press run was cancelled on 2026-07-29, 07-31, 08-01 and 08-02 while
    its uncontended 21:00 slot succeeded every time, so a collector scheduled
    twice a day ran once, ~24h apart, against a 14h staleness leash — and the
    leash took the blame for a schedule that could not be kept.
    """
    import collections
    from pathlib import Path

    import yaml

    workflows = Path(__file__).parent.parent / ".github/workflows"
    slots = collections.defaultdict(list)
    for path in sorted(workflows.glob("*.yml")):
        text = path.read_text()
        if "talent_intel.db" not in text:
            continue
        doc = yaml.safe_load(text) or {}
        # PyYAML parses a bare `on:` key as the boolean True.
        triggers = doc.get("on") or doc.get(True) or {}
        for entry in (triggers.get("schedule") or []):
            cron = (entry or {}).get("cron")
            if not cron:
                continue
            # Compare the SLOT, not the literal string: '0 9,21 * * *' and
            # '0 9 * * *' are different strings that both fire at 09:00, which
            # is exactly the pair that caused this. Expand the comma lists in
            # minute and hour; a wildcard or step in either field means the
            # workflow is not on a fixed daily slot and is out of scope here.
            minute, hour, *rest = cron.split()
            if any(c in minute + hour for c in "*/-"):
                continue
            for mm in minute.split(","):
                for hh in hour.split(","):
                    slots[(f"{int(hh):02d}:{int(mm):02d}", " ".join(rest))].append(
                        path.name)

    assert slots, "no scheduled database writers found — the test is inert"
    clashes = {slot: sorted(set(names))
               for slot, names in slots.items() if len(set(names)) > 1}
    assert not clashes, (
        "these scheduled database writers queue into the shared lock in the "
        f"same minute, so one of them will be cancelled rather than run: "
        f"{clashes}. Move one to a free minute."
    )


def test_the_retraction_reason_never_reaches_the_shell_unquoted():
    """A withdrawal must be able to state a dollar figure.

    `${{ }}` is pasted into a `run` block as text before bash sees it, so bash
    then expands what it finds. On 2026-08-04 the CXMT withdrawal was sent with
    the reason "... an $8.6bn Shanghai STAR Market IPO ..." and `$8` expanded to
    the empty eighth positional parameter: WordPress and the database both
    recorded "an .6bn", losing the one figure the retraction existed to state.
    The run was green.

    `reason` is the only genuinely free-text input any writer takes — the rest
    are dates, row limits and slugs — which makes it the only one where a `$`,
    a backtick or a `$(...)` is likely rather than merely possible. It travels
    through the environment, quoted, and this is what says so.
    """
    path = Path(__file__).parent.parent / ".github" / "workflows" / "retract.yml"
    parsed = yaml.safe_load(path.read_text())

    steps = [s for job in parsed["jobs"].values() for s in job.get("steps", [])]
    running = [s for s in steps if "retract.py" in (s.get("run") or "")]
    assert running, "retract.yml no longer invokes retract.py — this test is inert"

    for step in running:
        run = step["run"]
        for field in ("reason", "signal_id", "bare_domains"):
            assert f"inputs.{field}" not in run, (
                f"retract.yml interpolates inputs.{field} into a run block. "
                "bash will expand it: a reason containing $8.6bn loses the "
                "figure, and one containing a backtick is executed. Pass it "
                "through env: and quote the variable."
            )
        env = step.get("env") or {}
        assert any("inputs.reason" in str(v) for v in env.values()), (
            "retract.yml must carry the reason through env:, not the shell"
        )
        assert '"$TIT_REASON"' in run, (
            "the reason variable must be quoted, or the shell splits it on "
            "whitespace and the withdrawal states something else again"
        )
        assert '"$TIT_SIGNAL_ID"' in run, (
            "the id list must reach python as ONE quoted argument. A bash loop "
            "over the ids puts every one of them back through word splitting, "
            "which is the defect this whole test exists for"
        )


def test_the_retraction_timeout_outlasts_the_scripts_own_budget():
    """retract.py stops itself before GitHub kills the run, and only if the
    workflow's clock is the longer of the two.

    A run killed mid-list never reaches the commit step, and that step holds
    the ONLY local record of withdrawals the site has already applied — the
    row is gone from the page and still current in the database, so the next
    collect reads it as live. So the budget is the binding limit and the
    timeout is the backstop, never the other way round.
    """
    import retract

    path = Path(__file__).parent.parent / ".github" / "workflows" / "retract.yml"
    parsed = yaml.safe_load(path.read_text())
    timeout = parsed["jobs"]["retract"]["timeout-minutes"]

    budget_min = retract.RUN_BUDGET_SECONDS / 60
    worst_row_min = (5 * 45 + sum(retract.RETRY_PAUSES)) / 60
    assert timeout > budget_min + worst_row_min, (
        f"timeout-minutes={timeout} does not outlast retract.py's own "
        f"{budget_min:.0f} min budget plus the {worst_row_min:.1f} min a "
        f"single row's retry ladder can still cost after it — GitHub would "
        f"win the race and kill the run before it can commit"
    )


# --- The database is TWO committed files ------------------------------------
#
# Split on 2026-08-20, because GitHub refuses a single file over 100 MiB and
# `data/talent_intel.db` was 78.8 MiB growing 676 KB/day — 32 days from a
# repository that stops accepting commits. `seen_urls`, `source_links` and
# `employer_identity` moved to `data/talent_intel_cache.db`; both halves stay
# committed, so `git push` remains the compare-and-swap merge_db.py depends on.
#
# THE FAILURE THESE GUARD AGAINST is a workflow that commits one half. It does
# not look like a failure: the run is green, the push lands, and the next
# collect run opens a database whose URL cache has forgotten every story it has
# ever paid to read — so it pays again, and republishes what the site already
# holds. That is exactly the shape of the 2026-07-28 defect (audit finding 1)
# and it was invisible for a day and a half.

DB_FILE = "data/talent_intel.db"
CACHE_FILE = "data/talent_intel_cache.db"


def test_every_workflow_that_commits_the_database_commits_both_halves():
    for name, steps in _database_writers():
        for step in steps:
            code = _code(step.get("run") or "")
            for line in code.splitlines():
                if "git add" not in line or DB_FILE not in line:
                    continue
                assert CACHE_FILE in line, (
                    f"{name}: this line stages the product half of the database "
                    f"without its cache half, so the push drops seen_urls, "
                    f"source_links and employer_identity:\n    {line.strip()}"
                )


def test_every_workflow_that_saves_the_database_saves_both_halves():
    """The `cp` before `git reset --hard` has to take the pair.

    The reset discards the working tree, so whatever was not copied aside is
    gone — and merge_db.py then merges this run's product file into main while
    reading a cache file that main, not this run, wrote.
    """
    import re

    pattern = re.compile(r'^\s*cp\s+data/talent_intel\.db\s+("?[^"]+\.db"?)\s*$')
    for name, steps in _database_writers():
        for step in steps:
            lines = _code(step.get("run") or "").splitlines()
            for i, line in enumerate(lines):
                m = pattern.match(line)
                if not m:
                    continue
                expected_dst = re.sub(r'\.db("?)$', r'_cache.db\1', m.group(1))
                following = "\n".join(lines[i + 1:i + 3])
                assert f"cp {CACHE_FILE} {expected_dst}" in following, (
                    f"{name}: saves the product half aside without its cache "
                    f"half. Expected the next line to be:\n"
                    f"    cp {CACHE_FILE} {expected_dst}"
                )


def test_every_merge_db_caller_saves_the_cache_half_aside():
    """merge_db.py aborts if the cache sibling of its `ours` file is absent.

    The two guards above match a LITERAL `cp data/talent_intel.db DEST` and a
    LITERAL `git add ... data/talent_intel.db ...`. recall.yml did neither: it
    drove both the save-aside and the staging through shell variables
    (`db=data/talent_intel.db`, and a `for p in $paths $db` copy loop), so the
    product half was preserved into $RUNNER_TEMP/keep and the cache half was
    not. The literal matchers saw nothing to check, the workflow went green in
    CI, and every Monday's `recall` run then died inside merge_db.py with
    "nothing to merge: this run's cache file ... does not exist" — a red run
    with no data lost but the measurement never taken.

    This guard is mechanism-agnostic: if a commit step invokes merge_db.py it
    saves a database aside for the merge, so the cache filename must appear in
    that step's real commands however they are spelled.
    """
    for name, steps in _database_writers():
        for step in steps:
            code = _code(step.get("run") or "")
            if "merge_db.py" not in code:
                continue
            assert CACHE_FILE in code, (
                f"{name}: a commit step invokes merge_db.py but never names "
                f"{CACHE_FILE}, so the cache half of `ours` is never saved "
                f"aside and merge_db.py aborts with 'this run's cache file "
                f"... does not exist'."
            )


def test_the_cache_file_is_committed_not_ignored():
    """A .gitignore entry would make every one of the above pass and still lose
    the data, because `git add` of an ignored path is a silent no-op."""
    from pathlib import Path
    import subprocess

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", CACHE_FILE])
    assert result.returncode != 0, (
        f"{CACHE_FILE} is gitignored. It is half of the database, not scratch."
    )
