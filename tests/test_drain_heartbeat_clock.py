"""The drainer-is-down alarm was reading a clock that is suppressed on purpose.

`writer_queue._cmd_tick` does not advance `last_tick` on a tick that found an
empty queue -- its own comment says writing it every idle tick "would commit to
main four times an hour forever". So after any quiet spell the heartbeat is
arbitrarily old, and `state["waiting"]` flips true the INSTANT a ticket is
queued. `ops_status._report_writer_queue` compared `now - last_tick` against two
hours the moment both were true, which made the alarm structurally guaranteed to
fire on the first ops_status run after any idle queue receives work.

It did, on 2026-09-01: a ticket queued at 19:25:47Z was thirteen minutes old,
drain-writers had run green at 18:49, 15:32 and 14:36, and the report said
"work is queued but drain-writers.yml has not ticked in 5h -- the drainer itself
is down". CLAUDE.md tells every session to read ops_status first, so a false
alarm there is expensive noise.

THE FIX IS NOT A BIGGER THRESHOLD. Two hours is right; the clock was wrong. The
question the alarm asks is "has anything looked at this work since it was asked
for?", so it now starts at the later of the last tick and the oldest waiting
ticket's `requested_at`. A drainer that has genuinely stopped still trips it on
the same two hours, and the tests below are mostly about that: a fix that only
made the alarm quieter would be the worst possible outcome here.

The other half of the pair is untouched. A drainer that IS ticking and still not
moving the queue is `writer_queue`'s `idle-stall` at 90 minutes, which arrives
through state["problems"] and is asserted on below so this change cannot be read
as having covered for it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ops_status  # noqa: E402

NOW = datetime(2026, 9, 1, 19, 38, tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ticket(requested: datetime, state: str = "queued"):
    return {"id": "t1", "workflow": "archive-sources.yml", "state": state,
            "requested_at": _iso(requested), "attempts": 0, "inputs": {}}


def _run(*, last_tick: datetime | None, waiting: list, problems=None,
         idle_since=None):
    """Drive _report_writer_queue over a scripted queue file."""
    summary = {
        "counts": {"queued": len(waiting)},
        "waiting": waiting,
        "orphans": [],
        "deferred": [],
        "problems": list(problems or []),
        "last_tick": _iso(last_tick) if last_tick else None,
        "last_dispatch": None,
        "idle_since": _iso(idle_since) if idle_since else None,
    }

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    with mock.patch.object(ops_status, "_commits_behind_origin",
                           return_value=0), \
         mock.patch.object(ops_status, "datetime", _FrozenNow), \
         mock.patch("writer_queue.load", return_value={}), \
         mock.patch("writer_queue.summary", return_value=summary), \
         mock.patch("writer_queue.dispatch_key", return_value=0), \
         mock.patch("writer_queue.dispatch_reason", return_value=""), \
         mock.patch.object(ops_status.Path, "exists", lambda self: True):
        return ops_status._report_writer_queue()


def _down_alarms(problems):
    return [p for p in problems if "the drainer itself is down" in p]


class TestTheFalsePositiveIsGone:
    def test_fresh_work_behind_an_old_idle_heartbeat_is_not_an_alarm(self):
        """The 2026-09-01 shape, exactly.

        MUTATION: measure from `last_tick` alone and this fails.
        """
        problems = _run(last_tick=NOW - timedelta(hours=5),
                        waiting=[_ticket(NOW - timedelta(minutes=13))])
        assert not _down_alarms(problems), (
            "a ticket queued 13 minutes ago must not be reported as a dead "
            f"drainer just because the queue was idle before it: {problems}")

    def test_an_hour_of_waiting_is_still_inside_the_window(self):
        problems = _run(last_tick=NOW - timedelta(days=3),
                        waiting=[_ticket(NOW - timedelta(minutes=59))])
        assert not _down_alarms(problems)


class TestTheTruePositiveStillFires:
    """The half that matters. A quieter alarm would be worse than a noisy one."""

    def test_work_waiting_three_hours_with_no_tick_after_it_alarms(self):
        problems = _run(last_tick=NOW - timedelta(hours=9),
                        waiting=[_ticket(NOW - timedelta(hours=3))])
        assert _down_alarms(problems), (
            "a ticket that has waited three hours with no drain tick since it "
            f"was asked for is a dead drainer and must be reported: {problems}")

    def test_the_alarm_names_the_heartbeat_it_read(self):
        problems = _down_alarms(_run(last_tick=NOW - timedelta(hours=9),
                                     waiting=[_ticket(NOW - timedelta(hours=3))]))
        assert "last tick" in problems[0] and "2026-09-01" in problems[0], (
            "the alarm has to say which heartbeat it read, or the next session "
            f"cannot tell a dead drainer from a slow one: {problems[0]}")

    def test_a_tick_AFTER_the_ticket_clears_it_however_old_the_ticket(self):
        """This is the boundary the fix turns on, so it is asserted on directly.

        A tick that happened after the work was queued means the drainer looked.
        Whether it then moved the queue is the idle-stall alarm's question.
        """
        problems = _run(last_tick=NOW - timedelta(minutes=10),
                        waiting=[_ticket(NOW - timedelta(hours=30))])
        assert not _down_alarms(problems)

    def test_the_oldest_ticket_is_the_one_that_counts(self):
        """A fresh ticket arriving must not reset an old one's clock."""
        problems = _run(
            last_tick=NOW - timedelta(hours=9),
            waiting=[_ticket(NOW - timedelta(minutes=2)),
                     _ticket(NOW - timedelta(hours=6))])
        assert _down_alarms(problems), (
            "the alarm looked at the newest ticket, so a steady trickle of new "
            "work would hide a ticket that has been stuck for six hours")

    def test_an_empty_queue_never_alarms_however_old_the_heartbeat(self):
        assert not _down_alarms(_run(last_tick=NOW - timedelta(days=30),
                                     waiting=[]))


class TestTheOtherAlarmIsUntouched:
    def test_idle_stall_still_reaches_the_report(self):
        """The drainer ticking and achieving nothing is a DIFFERENT failure and
        must not have been absorbed into the one above."""
        stall = ("the writer queue has 1 ticket(s) waiting and the "
                 "talent-collect lock group has been EMPTY ...")
        problems = _run(last_tick=NOW - timedelta(minutes=5),
                        waiting=[_ticket(NOW - timedelta(hours=4))],
                        problems=[stall])
        assert stall in problems, (
            "a stalled-but-ticking drainer stopped being reported")

    def test_the_two_alarms_can_both_be_true_at_once(self):
        stall = "the writer queue has 1 ticket(s) waiting ..."
        problems = _run(last_tick=NOW - timedelta(hours=9),
                        waiting=[_ticket(NOW - timedelta(hours=4))],
                        problems=[stall])
        assert stall in problems and _down_alarms(problems)


def test_a_ticket_with_an_unreadable_timestamp_does_not_crash_the_report():
    bad = _ticket(NOW)
    bad["requested_at"] = "not a date"
    problems = _run(last_tick=NOW - timedelta(hours=9), waiting=[bad])
    # Falls back to the heartbeat rather than inventing a waiting time.
    assert _down_alarms(problems)


@pytest.mark.parametrize("value,expected", [
    ("2026-09-01T19:25:47Z", datetime(2026, 9, 1, 19, 25, 47,
                                      tzinfo=timezone.utc)),
    ("", None), (None, None), ("nonsense", None),
])
def test_parse_iso(value, expected):
    assert ops_status._parse_iso(value) == expected
