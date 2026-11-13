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


# Every workflow that writes the database takes this one lock. The name is
# arbitrary; that there is exactly ONE of them is not.
DB_LOCK = "talent-collect"
DATABASE = "data/talent_intel.db"


def _pushes_a_commit(parsed):
    """Workflows that commit anything back to the repository.

    Deliberately broader than "mentions the database path". Every committing
    workflow in this repository writes the database, and a future one that used
    `git add -A` or a variable for the path would slip past a path match while
    racing exactly the same way. A workflow that genuinely commits something
    else can be excluded here, once someone has thought about it.
    """
    for job in parsed.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "git commit" in (step.get("run") or ""):
                return True
    return False


def _database_writers():
    return {p.name: yaml.safe_load(p.read_text())
            for p in WORKFLOWS
            if _pushes_a_commit(yaml.safe_load(p.read_text()))}


def test_the_writer_detector_is_not_vacuous():
    """A guard that silently matches nothing passes forever and protects nothing."""
    writers = _database_writers()
    assert "collect.yml" in writers, f"detector missed the daily collector: {sorted(writers)}"
    assert len(writers) >= 5, f"only found {sorted(writers)} — has the commit step changed shape?"


def test_every_database_writer_shares_one_lock():
    """Two groups means two writers, and the loser's rows exist only on its runner.

    The reset-and-replace every one of these jobs ends with is only safe while
    the job is the sole writer: on a rejected push it resets --hard to origin
    and copies its own file back over the result. Two of these overlapping and
    the second to push silently destroys the first's rows, with no error
    anywhere. The collectors used to sit in a 'collect' group while the
    backfills sat in 'talent-collect', which bought precisely nothing.
    """
    wrong = {name: (wf.get("concurrency") or {}).get("group")
             for name, wf in _database_writers().items()
             if (wf.get("concurrency") or {}).get("group") != DB_LOCK}
    assert not wrong, (
        f"these commit to the repository outside the {DB_LOCK!r} lock: {wrong}. "
        f"Anything that writes {DATABASE} must share one concurrency group."
    )


def test_no_database_writer_is_cancelled_mid_push():
    """cancel-in-progress would kill a run holding rows that exist nowhere else.

    Explicit rather than leaning on the GitHub default: the default is what a
    new workflow inherits when its author never considered the question.
    """
    wrong = {name: (wf.get("concurrency") or {}).get("cancel-in-progress")
             for name, wf in _database_writers().items()
             if (wf.get("concurrency") or {}).get("cancel-in-progress") is not False}
    assert not wrong, f"these can be cancelled mid-collection: {wrong}"
