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
