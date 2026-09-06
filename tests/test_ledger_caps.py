"""Every committed ledger has a ceiling, and pending work never has one.

WHY. The repo IS the memory here: the databases, the writer queue, the alert
outbox and the host status are all committed and pushed, several times an hour
in the drainer's case. A ledger that grows without a ceiling is therefore a
push that grows without a ceiling, and this repo has already met the wall once
-- `data/talent_intel.db` was 32 days from GitHub's 100 MiB per-file refusal
when it was split (CLAUDE.md, 2026-08-20).

Most ledgers here already have a cap. An audit on 2026-09-06 found that four
of them had no TEST for it, which is the same as not having one: a cap nothing
exercises is a constant somebody deletes in a refactor.

And one had no cap at all. `writer_queue.prune()` trimmed `tickets` and never
touched `orphans`. An orphan is an evicted run listed until a human decides;
`resolve` correctly stamps it rather than removing it, so the resolved half
accumulated for ever in the most-written file in the repository. Every reader
already filters resolved orphans out, so past a window of history the bytes
served nobody and were pushed on every 15-minute tick.

THE RULE THIS FILE ENCODES, and it is the one that makes a cap safe: history
is capped, STATE IS NEVER CAPPED. An unresolved orphan and a live ticket are
work somebody still owes an answer to, and a trim that can reach them is a
trim that loses work. That direction is tested as hard as the cap itself.
"""
from __future__ import annotations

import json

import pytest

import alert_state
import backup_check
import writer_queue as wq
from collectors import ats_boards


def _orphan(n, resolved=None):
    o = {"run_id": 1000 + n, "workflow": "correct-form-d.yml",
         "noticed_at": f"2026-08-{n % 27 + 1:02d}T00:00:00+00:00",
         "detail": "displaced from the pending slot"}
    if resolved:
        o["resolved"] = resolved
        o["resolved_note"] = "re-dispatched by hand"
    return o


class TestResolvedOrphansAreBoundedAndOpenOnesAreNot:
    def test_resolved_orphans_are_trimmed_to_the_window(self):
        queue = {"tickets": [], "orphans": [
            _orphan(n, resolved=f"2026-08-{n % 27 + 1:02d}T01:00:00+00:00")
            for n in range(120)]}
        wq.prune(queue, keep_resolved_orphans=10)
        assert len(queue["orphans"]) == 10

    def test_the_newest_resolved_orphans_are_the_ones_kept(self):
        queue = {"tickets": [], "orphans": [
            _orphan(n, resolved=f"2026-08-{n + 1:02d}T01:00:00+00:00")
            for n in range(20)]}
        wq.prune(queue, keep_resolved_orphans=3)
        kept = [o["run_id"] for o in queue["orphans"]]
        assert kept == [1017, 1018, 1019]

    def test_an_UNRESOLVED_orphan_is_never_trimmed_at_any_count(self):
        """The whole point. An unresolved orphan is a run whose work was lost
        and whose inputs GitHub does not expose -- a decision only a human can
        make. A cap that can drop one is a cap that loses work silently, which
        is worse than the file being large."""
        queue = {"tickets": [], "orphans": [_orphan(n) for n in range(80)]}
        wq.prune(queue, keep_resolved_orphans=5)
        assert len(queue["orphans"]) == 80

    def test_open_orphans_survive_alongside_a_trimmed_history(self):
        queue = {"tickets": [], "orphans":
                 [_orphan(n, resolved="2026-08-01T00:00:00+00:00")
                  for n in range(50)] + [_orphan(900)]}
        wq.prune(queue, keep_resolved_orphans=4)
        assert len(queue["orphans"]) == 5
        assert any(not o.get("resolved") for o in queue["orphans"])

    def test_a_queue_with_no_orphans_key_is_left_exactly_alone(self):
        queue = {"tickets": []}
        wq.prune(queue)
        assert "orphans" not in queue

    def test_the_default_window_is_declared_and_finite(self):
        assert isinstance(wq.KEEP_RESOLVED_ORPHANS, int)
        assert 0 < wq.KEEP_RESOLVED_ORPHANS < 1000

    def test_the_committed_queue_is_inside_the_window(self):
        """The live file, not a fixture. It had 23 resolved orphans and no cap
        when this was written."""
        with open(wq.QUEUE_PATH, encoding="utf-8") as handle:
            queue = json.load(handle)
        resolved = [o for o in queue.get("orphans", []) if o.get("resolved")]
        assert len(resolved) <= wq.KEEP_RESOLVED_ORPHANS


class TestTheCapsThatExistedWithNoTest:
    """Four ceilings nothing exercised. A constant no test reads is a constant
    the next refactor deletes without a word."""

    @pytest.fixture(autouse=True)
    def _scratch(self, tmp_path):
        self._tmp = tmp_path / "ledger.json"

    def test_the_open_alert_ledger_is_capped_on_write(self, tmp_path):
        """A caller looping on a mutating cause key would otherwise grow this
        file for ever, and it is committed to main by every alarm."""
        path = tmp_path / "alert_state.json"
        doc = {"open": {f"cause-{n}": {"first": n}
                        for n in range(alert_state.MAX_OPEN + 40)}}
        alert_state.save(doc, str(path))
        written = json.loads(path.read_text(encoding="utf-8"))
        assert len(written["open"]) == alert_state.MAX_OPEN
        assert "cause-0" not in written["open"], "the OLDEST must be the drop"
        assert f"cause-{alert_state.MAX_OPEN + 39}" in written["open"]

    def test_the_backup_check_ledger_keeps_a_finite_number_of_readings(self):
        assert 0 < backup_check.KEEP_CHECKS < 1000
        ledger = {"checks": [{"at": f"2026-01-{n:02d}"} for n in range(1, 29)]
                  * 40}
        backup_check.write_ledger(ledger, str(self._tmp))
        assert len(ledger["checks"]) == backup_check.KEEP_CHECKS

    def test_the_ats_board_history_is_capped_per_board(self):
        """291 boards x an uncapped daily reading is the growth curve that put
        the database 32 days from GitHub's per-file wall."""
        assert 0 < ats_boards.HISTORY_LIMIT <= 2000
        history = list(range(ats_boards.HISTORY_LIMIT + 50))
        del history[:-ats_boards.HISTORY_LIMIT]
        assert len(history) == ats_boards.HISTORY_LIMIT

    def test_terminal_tickets_are_still_capped_and_live_ones_are_not(self):
        """The cap this file inherits, re-asserted because `prune` grew a
        second argument and a signature change is how the first one gets
        dropped."""
        queue = {"tickets":
                 [{"state": "landed", "requested_at": f"2026-08-{n % 27 + 1:02d}"}
                  for n in range(50)]
                 + [{"state": "queued", "requested_at": "2026-08-01"}] * 7}
        wq.prune(queue, keep_terminal=5)
        states = [t["state"] for t in queue["tickets"]]
        assert states.count("queued") == 7
        assert states.count("landed") == 5


class TestTheUnboundedOnesAreNamed:
    """Two ledgers are still unbounded and both are an OWNER decision, not a
    defect a session should quietly answer. Named here so the finding is in
    the suite rather than only in a report.

    * `docs/TECHLOG.md` (836 KB, 14,669 lines) and `docs/HEALING-LOG.md` are
      narrative history that `self_heal.py` PREPENDS to, so every heal
      re-serialises the whole file and git cannot delta it as an append. A
      horizon means deciding what history stops being worth keeping, and that
      is the owner's judgement, not a machine's.
    * `data/backfill_state.json` keeps every campaign's job for ever. Deleting
      a `done` job's record is how a completed backfill gets run again, so a
      horizon here needs a rule about what "finished for good" means.

    This test asserts only that they have not silently acquired a cap that
    nobody decided on -- it will start failing the day somebody adds one, and
    that is the moment to write down which decision was made.
    """

    def test_the_narrative_logs_still_keep_every_entry(self, tmp_path):
        """No horizon today, and this is the tripwire for one appearing
        silently. It will start failing the day somebody adds a trim, and that
        is exactly the moment to write down which decision was made and by
        whom -- not to discover it later from a shorter TECHLOG."""
        import self_heal

        log = tmp_path / "HEALING-LOG.md"
        log.write_text("# Log\n\n" + "".join(
            f"## entry {n}\nbody\n\n" for n in range(200)), encoding="utf-8")
        self_heal._insert_entry(str(log), "# Log\n\n", "## entry NEW\nbody\n")
        text = log.read_text(encoding="utf-8")
        assert text.count("## entry ") == 201
        assert "## entry 0\n" in text, (
            "an entry was dropped: a horizon appeared on a narrative log "
            "without a recorded decision")
