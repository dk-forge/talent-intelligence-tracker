"""tripwire ran, spent money, and committed nothing. Three times, all green.

The `tripwire` health row in data/talent_intel.db read 2026-08-02 for 736
hours while the workflow kept finishing successfully. Two independent defects,
neither of which could make a run go red, and neither visible to any test.

LEG A -- THE POISONED `git add`.

    git add data/tripwire_worklist.json analysis/tripwire/results \\
            data/talent_intel.db data/spend_month.json \\
            data/writer_deferrals 2>/dev/null || true

`git add` is ATOMIC. If any pathspec matches nothing it stages NOTHING and
exits non-zero. `data/writer_deferrals/` is created only by the "Record the
deferral" step, which runs only when the spend gate CLOSED -- so on precisely
the runs that DID buy something, the pathspec matched nothing, the whole add
aborted, `2>/dev/null` swallowed `fatal: pathspec ... did not match any files`
and `|| true` swallowed the status. The very next line, `git diff --staged
--quiet`, was then trivially true, so the step printed "No change." and exited
0. Runs 31682675950 (08-13), 32347039753 (08-20) and 32707606027 (08-24) each
logged a bought result and `health: recorded`, then discarded it on the
runner: $0.0676 of queries, 43 leads and three dated result files.

drain-writers.yml has always issued that pathspec as its own separate call.
tripwire.yml folded it into the main add on df3e9cb, and the ledger stops dead
at the commit after it.

LEG B -- THE HEALTH ROW THE WORKFLOW MADE UNREACHABLE.

`run_tripwire.report_declined()` was added on 2026-08-18 so that a run which
declines to buy files a `skipped` row, and staleness.py cut tripwire's leash
from 336h to 168h ON THE STRENGTH OF THAT. But its only caller was `main()`'s
not-spending branch, and the workflow carries
`if: steps.spend.outputs.over != 'true'` on the step that runs the script --
so on exactly the runs it was written for, the script never started. Runs
33413174671, 33132620831 and 31371431912 all show `skipped  Run the tripwire`
and `source_health_added: 0`. The fix lived inside a body the gate above it
skipped.

THE GATE IS NOT THE BUG AND WAS NOT REMOVED. Deleting it was tried first and
tests/test_budget_stop_is_not_a_failure.py caught it within one suite run:
`test_the_paid_step_skips_itself_when_the_gate_is_closed` pins the
workflow-level guard on purpose -- "exiting 0 and then spending anyway would be
the guard weakened, which is the one thing this change may not do". Both tests
now hold at once, because the health row is filed by its own cheap step
(`run_tripwire.py --report-declined`) that never reaches the paid path.

These assert on the workflow text because that is where both defects are. A
YAML file has no unit under test; the shell it contains is the unit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TRIPWIRE = ROOT / ".github" / "workflows" / "tripwire.yml"
DRAIN = ROOT / ".github" / "workflows" / "drain-writers.yml"


def _steps(path: Path) -> list[dict]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for job in doc["jobs"].values():
        out.extend(job.get("steps", []))
    return out


def _run_bodies(path: Path) -> str:
    return "\n".join(s.get("run", "") for s in _steps(path) if s.get("run"))


def _git_add_commands(path: Path) -> list[str]:
    """Every `git add ...` in the workflow, with shell continuations joined.

    Joining first is the point: the defect spans three physical lines, and a
    line-by-line scan sees `data/writer_deferrals` sitting alone on the last
    one and calls it a separate command. That is precisely the misreading that
    let this ship.
    """
    joined = re.sub(r"\\\n\s*", " ", _run_bodies(path))
    return [line.strip() for line in joined.splitlines()
            if line.strip().startswith("git add ")]


def _paths_in(command: str) -> list[str]:
    return [tok for tok in command.split()
            if tok.startswith("data/") or tok.startswith("analysis/")]


class TestOneMissingPathMustNotDiscardTheRest:
    def test_writer_deferrals_is_never_grouped_with_the_bought_data(self):
        """The regression itself.

        MUTATION: put `data/writer_deferrals` back on the end of either
        multi-path `git add` in tripwire.yml and this fails.
        """
        offenders = [cmd for cmd in _git_add_commands(TRIPWIRE)
                     if "data/writer_deferrals" in _paths_in(cmd)
                     and len(_paths_in(cmd)) > 1]
        assert not offenders, (
            "data/writer_deferrals is an OPTIONAL path -- it exists only when "
            "the spend gate closed -- and `git add` stages nothing at all when "
            "one pathspec misses. Grouped with the bought data it silently "
            "discards the whole commit on every run that actually bought "
            "something. Give it its own `git add -A ... || true`, as "
            "drain-writers.yml does. Found:\n  " + "\n  ".join(offenders))

    def test_it_is_still_staged_somewhere(self):
        """Splitting it out must not mean dropping it."""
        assert any(_paths_in(c) == ["data/writer_deferrals"]
                   for c in _git_add_commands(TRIPWIRE)), (
            "the deferral marker is no longer staged at all, so a budget stop "
            "would leave the queue claiming work that was never done")

    def test_the_optional_path_is_the_only_tolerant_add(self):
        """A tolerant `|| true` on the REQUIRED paths is the same bug again.

        If the bought data is added with its failure swallowed, a future
        missing path repeats 2026-08-13 exactly.
        """
        for line in _git_add_commands(TRIPWIRE):
            if "|| true" not in line:
                continue
            assert "writer_deferrals" in line, (
                "this `git add` swallows its own failure but does not name the "
                f"one optional path that justifies it:\n  {line.strip()}")

    def test_drain_writers_still_shows_the_shape_this_copies(self):
        assert any(_paths_in(c) == ["data/writer_deferrals"]
                   for c in _git_add_commands(DRAIN))


class TestADecliningRunStillReachesTheLedger:
    def test_a_closed_gate_files_a_health_row(self):
        """MUTATION: delete the 'Say in the ledger' step and this fails."""
        steps = _steps(TRIPWIRE)
        declined = [s for s in steps
                    if "--report-declined" in (s.get("run") or "")]
        assert declined, (
            "nothing files a health row when the spend gate closes. "
            "run_tripwire.main() cannot: the paid step is skipped, and that "
            "skip is a guard pinned by test_budget_stop_is_not_a_failure. "
            "Without a separate step the ledger freezes at the last run that "
            "spent money, which is the 736h stale.")
        cond = str(declined[0].get("if", ""))
        assert "spend.outputs.over == 'true'" in cond, (
            "the declined row must be filed only when the gate actually closed")

    def test_the_paid_guard_is_still_in_place(self):
        """The thing the first attempt at this fix broke.

        Asserted here as well as in test_budget_stop_is_not_a_failure, so this
        file cannot be the one that quietly takes it away.
        """
        step = next(s for s in _steps(TRIPWIRE)
                    if s.get("name") == "Run the tripwire")
        assert "spend.outputs.over != 'true'" in str(step.get("if", "")), (
            "the paid step lost its spend gate. Filing a health row is not "
            "worth weakening the guard that stops the money.")

    def test_the_declined_step_cannot_reach_the_paid_path(self):
        """--report-declined must return before anything can be bought."""
        source = (ROOT / "run_tripwire.py").read_text(encoding="utf-8")
        body = source[source.index("args = parser.parse_args(argv)"):]
        branch = body[:body.index("\n\n", body.index("report_declined"))]
        assert "return 0" in branch, (
            "--report-declined falls through into main()'s ordinary path, "
            "which is a step that was advertised as free reaching the money")
        assert "gather(" not in branch

    def test_the_declined_step_carries_no_model_key(self):
        step = next(s for s in _steps(TRIPWIRE)
                    if "--report-declined" in (s.get("run") or ""))
        assert "OPENROUTER_API_KEY" not in str(step.get("env", {})), (
            "a step whose whole justification is that it buys nothing should "
            "not be holding the key that buys things")

    def test_the_deferral_marker_still_depends_on_the_gate(self):
        names = {s.get("name"): str(s.get("if", "")) for s in _steps(TRIPWIRE)}
        assert "spend.outputs.over == 'true'" in names["Record the deferral"]
        assert "spend.outputs.over == 'true'" in \
            names["Tell the owner the ceiling is binding"]

    def test_report_declined_is_still_the_thing_being_reached(self):
        source = (ROOT / "run_tripwire.py").read_text(encoding="utf-8")
        assert "def report_declined" in source

    def test_a_declining_run_files_skipped_and_skipped_is_benign(self):
        """The status has to be one the staleness clock treats as alive.

        `ok` would be rewritten to `degraded` at zero items, and `degraded`
        would alarm on a working budget.
        """
        import sys
        sys.path.insert(0, str(ROOT))
        import ops_status
        source = (ROOT / "run_tripwire.py").read_text(encoding="utf-8")
        block = source[source.index("def report_declined"):
                       source.index("def report_health")]
        assert 'status="skipped"' in block
        assert "skipped" in ops_status.BENIGN_STATUSES, (
            "a declining run would file a row that alarms")


@pytest.mark.parametrize("path", [TRIPWIRE, DRAIN])
def test_the_workflows_still_parse(path):
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
