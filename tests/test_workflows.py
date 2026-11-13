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
    run = step["run"]

    assert "for attempt in" in run, "a single push attempt loses the data on any race"
    assert "git fetch origin main" in run and "git reset --hard origin/main" in run
    # Our file is the checked-out database plus this run's rows, so it has to be
    # put back after the reset or the reset throws the collection away.
    assert run.index("git reset --hard") < run.index('cp "$RUNNER_TEMP/collected.db"',
                                                     run.index("git reset --hard"))
    # Failure must be loud. Silently exiting 0 here is how a month of runs
    # quietly stores nothing.
    assert "::error::" in run and run.rstrip().endswith("exit 1")


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

    A workflow counts as a writer if it NAMES the database or DECLARES the
    group. The second half was added after the first half was found to miss
    enrich.yml, which writes the database from a heredoc that never spells the
    filename: it had joined the group by hand, and this test, whose whole job
    is catching a writer with no group, could not see it either way.
    """
    from pathlib import Path

    import sys

    import yaml

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dropped_runs import writer_workflows

    workflows = Path(__file__).parent.parent / ".github/workflows"
    # Shared with the watchdog on purpose, so the lock and the thing that
    # watches the lock cannot disagree about what counts as a writer. It
    # ignores comments: dropped-runs.yml explains the database at length and
    # writes none of it.
    writers = {p.rsplit("/", 1)[-1] for p in writer_workflows(workflows)}
    groups = {}
    for path in sorted(workflows.glob("*.yml")):
        if path.name not in writers:
            continue
        concurrency = (yaml.safe_load(path.read_text()) or {}).get("concurrency") or {}
        groups[path.name] = concurrency.get("group")
        assert concurrency.get("cancel-in-progress") is False, path.name

    assert len(groups) >= 5, f"expected several database writers, found {groups}"
    assert None not in groups.values(), f"a database writer has no lock: {groups}"
    assert len(set(groups.values())) == 1, (
        "database writers are split across concurrency groups, so they do not "
        f"exclude each other: {groups}"
    )


def test_the_dropped_run_watchdog_stays_out_of_the_lock():
    """The watchdog must not be queued behind what it is watching.

    It writes nothing, so it does not need the group. Putting it in
    `talent-collect` would park it behind an hours-long backfill and let it be
    evicted by the very bug it exists to report, which would be silent in
    exactly the same way. It only needs to be able to READ runs.
    """
    from pathlib import Path

    import yaml

    path = Path(__file__).parent.parent / ".github/workflows/dropped-runs.yml"
    assert path.exists(), "the dropped-run watchdog is gone"
    wf = yaml.safe_load(path.read_text())

    assert "concurrency" not in wf, (
        "the watchdog is in a concurrency group, so a long writer can drop it")

    permissions = wf.get("permissions") or {}
    assert permissions.get("actions") == "read", (
        "listing runs and their jobs is the whole job; it needs actions: read")
    assert permissions.get("contents") == "read", (
        "the watchdog must never be able to write the repository")

    # A watchdog nobody schedules is a watchdog nobody hears. This is the one
    # alert path for a failure mode that produces no red run to notice.
    schedule = (wf.get("on") or wf.get(True) or {}).get("schedule")
    assert schedule, "the watchdog is not scheduled, so it will never fire"
