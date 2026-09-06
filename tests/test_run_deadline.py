"""A collect run has a clock of its own, and a killed one still says so.

WHAT WAS WRONG. `collect.yml` allows 180 minutes, `collect-press.yml` 120,
`collect-structured.yml` 180. Those ceilings bound a hang. Nothing bounded the
LOSS, because the run had no clock of its own: when `timeout-minutes` fires
GitHub marks the job `cancelled`, and every commit step in this repo is
guarded `if: ${{ !inputs.dry_run && !cancelled() }}`. So a self-timeout
discarded

  * every signal the run had stored,
  * every `seen_urls` mark, so the next run re-fetched and RE-PAID for the
    same candidates,
  * and the health row, so nothing said the run had happened at all. The
    collector simply went quiet and `staleness.py` noticed a day later with no
    cause attached.

That is the sibling tracker's orphaned-`running`-note defect in the other
direction: there a note was left open, here nothing was written, and both read
as "no problem" rather than "we never looked".

TWO GUARDS, AND THEY ARE NOT REDUNDANT. The DEADLINE is what saves the run's
work -- it stops between candidates, writes its health row and exits 0, so the
commit step RUNS. The SIGTERM handler cannot save the commit (the step is
skipped either way) but it can record WHY the run ended, for every kill the
run's own clock cannot pre-empt: an eviction from the concurrency group, a
superseded push, a human, or a deadline missed because one call hung inside
its own timeout.
"""
from __future__ import annotations

import os
import pathlib
import re
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

import run_collect
import run_deadline

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

#: Every workflow that runs `run_collect.py` on a cron. The deadline must be
#: below the smallest of their ceilings, so this list is derived rather than
#: typed: a new collect workflow joins the check by existing.
def _collect_workflows():
    out = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        # A run_collect invocation that is `--offline` reads a captured
        # fixture and makes no network call, so it cannot hit the clock. That
        # is `tests.yml`, whose 15-minute ceiling is for the suite.
        calls = [line for line in text.splitlines()
                 if "run_collect.py" in line]
        if not calls or all("--offline" in line for line in calls):
            continue
        ceilings = [int(m) for m in
                    re.findall(r"^\s*timeout-minutes:\s*(\d+)", text, re.M)]
        if ceilings:
            out[path.name] = min(ceilings)
    return out


class TestTheDeadlineFitsInsideTheWorkflowCeiling:
    def test_at_least_one_collect_workflow_was_found(self):
        """A derived check that derives nothing passes for the wrong reason."""
        assert _collect_workflows(), "no workflow invokes run_collect.py?"

    def test_the_run_budget_is_strictly_below_every_ceiling(self):
        budget_minutes = run_deadline.DEFAULT_BUDGET_SECONDS / 60
        for name, ceiling in _collect_workflows().items():
            assert budget_minutes < ceiling, (
                f"{name} allows {ceiling}min and the run gives itself "
                f"{budget_minutes:.0f}min: the job is killed before it can "
                f"stop itself, and the commit step is skipped with it")

    def test_there_is_room_left_for_the_steps_outside_the_clock(self):
        """The pip install, the spend check, ops_status and the merge-and-push
        loop all run outside this clock and inside that ceiling. A budget that
        merely squeaks under the wall leaves nothing for the push, which is
        the step that saves the work."""
        tightest = min(_collect_workflows().values())
        slack = tightest - run_deadline.DEFAULT_BUDGET_SECONDS / 60
        assert slack >= 15, f"only {slack:.0f}min left for install + push"


class TestTheClock:
    def test_it_expires_when_the_budget_is_spent(self):
        now = [1000.0]
        d = run_deadline.Deadline(60, clock=lambda: now[0])
        assert not d.expired()
        now[0] += 59
        assert not d.expired()
        now[0] += 2
        assert d.expired()

    def test_an_unreadable_override_is_the_default_and_not_zero(self, monkeypatch):
        """A budget of zero stops the run before it reads anything, which is
        an outage manufactured by a typo."""
        monkeypatch.setenv(run_deadline.ENV_VAR, "not-a-number")
        assert run_deadline.budget_seconds() == \
            float(run_deadline.DEFAULT_BUDGET_SECONDS)
        monkeypatch.setenv(run_deadline.ENV_VAR, "0")
        assert run_deadline.budget_seconds() == \
            float(run_deadline.DEFAULT_BUDGET_SECONDS)
        monkeypatch.setenv(run_deadline.ENV_VAR, "120")
        assert run_deadline.budget_seconds() == 120.0


class TestADeadlineStopIsReportedAndIsNotAFailure:
    def test_a_stopped_run_is_degraded_but_green(self):
        """Depth lost, not a defect. The same split the budget guard already
        makes: reddening the job would manufacture an alarm for a clock
        working exactly as designed."""
        degraded, failed = run_collect.run_outcome(
            observed=40, everything_rejected=False, mostly_throttled=False,
            running_degraded=False, slots_throttled=True)
        assert (degraded, failed) == (True, False)

    def test_a_run_that_read_nothing_at_all_still_fails(self):
        """If EVERY slice was throttled or the clock stopped it before the
        first candidate, `observed == 0` fails the run on its own and needs no
        threshold of its own."""
        degraded, failed = run_collect.run_outcome(
            observed=0, everything_rejected=False, mostly_throttled=False,
            running_degraded=False, slots_throttled=True)
        assert failed is True

    def test_an_untouched_run_is_unaffected(self):
        assert run_collect.run_outcome(
            observed=40, everything_rejected=False, mostly_throttled=False,
            running_degraded=False) == (False, False)

    def test_the_loop_checks_the_clock_between_candidates_not_inside_one(self):
        """A run cut mid-candidate could leave a row stored and unmarked, or
        marked and unstored. The check is the first statement of the loop."""
        import inspect
        src = inspect.getsource(run_collect.run)
        body = src[src.index("    for item in kept:"):]
        head = body[:body.index("url = item.get")]
        assert "deadline.expired()" in head
        assert "break" in head

    def test_the_health_row_says_how_many_were_left(self):
        import inspect
        src = inspect.getsource(run_collect.run)
        assert 'markers += f"DEADLINE: {deadline_stopped} unread | "' in src
        assert 'markers += f"THROTTLED: {throttled_slots} | "' in src


# --- the SIGTERM half, in a real child process ------------------------------

CHILD = textwrap.dedent('''
    import os, signal, sys, time
    sys.path.insert(0, {root!r})
    import run_deadline
    from pipeline import schema, store

    DB = {db!r}
    COLLECTOR = "google_news"

    def note(detail):
        conn = schema.connect(DB)
        try:
            store.report_health(conn, COLLECTOR, status="error", detail=detail)
            conn.commit()
        finally:
            conn.close()

    # The run declares itself open, exactly as run_collect does.
    run_deadline.mark_open(COLLECTOR, note)
    open(sys.argv[1], "w").write("ready")
    # ...and then collects for a long time, like a real one.
    while True:
        time.sleep(0.05)
''')


def _kill_a_running_collector(tmp_path, sig=signal.SIGTERM):
    db = str(tmp_path / "t.db")
    script = tmp_path / "child.py"
    script.write_text(CHILD.format(root=str(ROOT), db=db), encoding="utf-8")
    ready = tmp_path / "ready"
    proc = subprocess.Popen([sys.executable, str(script), str(ready)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    deadline = time.monotonic() + 30
    while not ready.exists():
        assert proc.poll() is None, proc.communicate()[0]
        assert time.monotonic() < deadline, "child never started"
        time.sleep(0.05)
    proc.send_signal(sig)
    out = proc.communicate(timeout=30)[0]
    return proc.returncode, out, db


def _health_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT collector, status, detail FROM source_health").fetchall()
    finally:
        conn.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
class TestAKilledRunWritesATerminalNote:
    def test_sigterm_leaves_a_note_and_not_silence(self, tmp_path):
        code, out, db = _kill_a_running_collector(tmp_path)
        rows = _health_rows(db)
        assert rows, ("the collector was killed and recorded NOTHING; that is "
                      "the defect -- absence read as no problem")
        collector, status, detail = rows[0]
        assert collector == "google_news"
        assert status == "error", "a killed run must not read as ok"
        assert detail.startswith("interrupted: SIGTERM"), detail
        assert "job cancelled, evicted or timed out" in detail

    def test_the_exit_is_still_killed_by_the_signal(self, tmp_path):
        """The handler records and then hands the signal back. A bookkeeper
        that swallowed the kill would report a run that pretended to finish."""
        code, _out, _db = _kill_a_running_collector(tmp_path)
        assert code in (-int(signal.SIGTERM), 128 + int(signal.SIGTERM)), code

    def test_sigint_is_handled_the_same_way(self, tmp_path):
        code, _out, db = _kill_a_running_collector(tmp_path, signal.SIGINT)
        rows = _health_rows(db)
        assert rows and rows[0][2].startswith("interrupted: SIGINT")


class TestTheDrainIsIdempotent:
    def test_a_second_signal_finds_nothing_and_writes_nothing(self):
        written = []
        run_deadline.mark_open("x", written.append)
        try:
            assert run_deadline.close_open_runs_as_interrupted("SIGTERM", 5) == ["x"]
            assert run_deadline.close_open_runs_as_interrupted("SIGTERM", 5) == []
            assert len(written) == 1, "a double SIGTERM must not write twice"
        finally:
            run_deadline.mark_closed("x")

    def test_a_writer_that_raises_never_hides_the_kill(self):
        def boom(_detail):
            raise sqlite3.OperationalError("database is locked")

        run_deadline.mark_open("y", boom)
        try:
            assert run_deadline.close_open_runs_as_interrupted("SIGTERM", 1) == []
        finally:
            run_deadline.mark_closed("y")

    def test_run_collect_registers_and_then_releases(self):
        """The registration must be released when the run reaches its own end,
        or a clean exit would look like a kill to anything reading the set."""
        import inspect
        src = inspect.getsource(run_collect.run)
        assert "run_deadline.mark_open(collector, _interrupted_note)" in src
        assert "run_deadline.mark_closed(collector)" in src
        assert src.index("mark_open") < src.index("mark_closed")

    def test_a_run_that_ends_EARLY_still_releases_its_registration(self):
        """`run()` has half a dozen early returns and one exception path.
        Asking each to remember is how one of them does not: a full-suite run
        turned up a leaked `google_news` registration from a test that ran the
        pipeline and returned early, and the next signal would have written an
        interrupted note about a collector that finished minutes ago."""
        @run_deadline.released
        def blows_up():
            run_deadline.mark_open("google_news", lambda _d: None)
            raise RuntimeError("EDGAR refused page 0")

        with pytest.raises(RuntimeError):
            blows_up()
        assert run_deadline.open_runs() == {}

    def test_the_decorator_is_actually_on_run(self):
        import inspect
        src = inspect.getsource(run_collect)
        assert "@run_deadline.released\n@_with_gate_labels\ndef run(" in src
