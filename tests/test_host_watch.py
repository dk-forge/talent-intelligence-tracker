"""The alerting path has to survive the host it alerts about.

2026-07-31, 00:48-00:55 UTC. Bluehost answered 504 for everything under /blog/.
enrich failed because it could not reach the host; drain-writers correctly went
red refusing to auto-retry a failed writer; and the CI failure alert then failed
FOUR times, "HTTP 504 from /alert", because /alert is a route on the host that
was down. The alarm was mute at exactly the moment it was needed, and it turned
one outage into four extra red runs while doing it.

These tests pin the three properties that fix costs:

  1. an alert raised during an outage is HELD and eventually delivered,
  2. a delivery failure never becomes a new red run,
  3. something records whether the host is up, without adding load to it.

No network anywhere in this file. Every probe answer is injected, which is the
only way to test what a watchdog does on its third consecutive failure without
waiting for a third consecutive failure.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import alert_outbox  # noqa: E402
import ci_alert  # noqa: E402
import host_watch  # noqa: E402


def _at(hours: float = 0) -> datetime:
    return datetime(2026, 7, 31, 0, 48, tzinfo=timezone.utc) + timedelta(hours=hours)


class TestTheOutageStateMachine:
    def test_one_failed_probe_is_not_an_outage(self):
        """A single bad response from shared hosting is a bad packet. Alerting
        on it is how a channel earns a filter rule."""
        ledger = host_watch.load_ledger("/nonexistent")
        d = host_watch.apply_probe(ledger, False, "HTTP 504", now=_at())
        assert ledger["state"] == "down"
        assert not d["sustained"], "one failure must not announce anything"

    def test_it_announces_only_after_sustained_failures(self):
        ledger = host_watch.load_ledger("/nonexistent")
        for i in range(host_watch.SUSTAINED_FAILURES - 1):
            d = host_watch.apply_probe(ledger, False, "HTTP 504", now=_at(i / 4))
            assert not d["newly_sustained"]
        d = host_watch.apply_probe(ledger, False, "HTTP 504", now=_at(1))
        assert d["newly_sustained"]

    def test_neither_2026_07_31_outage_would_have_emailed(self):
        """Both lasted under ten minutes and healed themselves, and the held
        alerts arrived minutes later. An alarm that fires on every seven-minute
        wobble on shared hosting is one the owner learns to ignore — which is
        the original problem in a new hat."""
        probes_in_seven_minutes = 1  # at a 15-minute cadence
        assert probes_in_seven_minutes < host_watch.SUSTAINED_FAILURES

    def test_it_announces_once_and_not_once_per_probe(self):
        ledger = host_watch.load_ledger("/nonexistent")
        announced = 0
        for i in range(10):
            d = host_watch.apply_probe(ledger, False, "HTTP 504", now=_at(i / 4))
            if d["newly_sustained"]:
                announced += 1
                ledger["announced"] = True
        assert announced == 1, "an outage must open one issue, not one per probe"

    def test_recovery_re_arms_the_announcement(self):
        ledger = host_watch.load_ledger("/nonexistent")
        for i in range(host_watch.SUSTAINED_FAILURES):
            host_watch.apply_probe(ledger, False, "HTTP 504", now=_at(i / 4))
        ledger["announced"] = True
        d = host_watch.apply_probe(ledger, True, "HTTP 200 in 300ms", now=_at(2))
        assert d["recovered"] and ledger["state"] == "up"
        assert ledger["consecutive_failures"] == 0
        assert not ledger["announced"], "the next outage must be able to speak"

    def test_a_4xx_still_means_the_site_is_serving(self, monkeypatch):
        """The probe measures 'is WordPress routing requests', not 'is this one
        route happy'. A 404 from a renamed endpoint is a deploy problem, and
        calling it an outage would send the owner looking at the wrong thing."""
        class _Err(Exception):
            code = 404

        import urllib.error

        def boom(*a, **k):
            raise urllib.error.HTTPError("u", 404, "gone", {}, None)

        monkeypatch.setattr(host_watch.urllib.request, "urlopen", boom)
        ok, detail = host_watch.probe_once("https://example.invalid")
        assert ok and "answered" in detail


class TestTheLedgerDoesNotBecomeCommitNoise:
    def test_a_quiet_healthy_probe_does_not_commit(self):
        ledger = host_watch.load_ledger("/nonexistent")
        host_watch.apply_probe(ledger, True, "HTTP 200", now=_at())
        ledger["_committed_at"] = host_watch._iso(_at())
        d = host_watch.apply_probe(ledger, True, "HTTP 200", now=_at(0.25))
        assert not host_watch.needs_commit(ledger, d, now=_at(0.25),
                                           outbox_changed=False), \
            "96 commits a day of one unchanged line is not a ledger, it is noise"

    def test_a_long_quiet_stretch_still_leaves_a_heartbeat(self):
        """Otherwise 'the host has been fine all day' is indistinguishable from
        'this watchdog stopped running a week ago and its last word was fine'."""
        ledger = host_watch.load_ledger("/nonexistent")
        d = host_watch.apply_probe(ledger, True, "HTTP 200", now=_at())
        ledger["_committed_at"] = host_watch._iso(_at())
        later = _at(host_watch.HEARTBEAT_HOURS + 0.1)
        d = host_watch.apply_probe(ledger, True, "HTTP 200", now=later)
        assert host_watch.needs_commit(ledger, d, now=later, outbox_changed=False)

    def test_a_state_change_always_commits(self):
        ledger = host_watch.load_ledger("/nonexistent")
        host_watch.apply_probe(ledger, True, "HTTP 200", now=_at())
        ledger["_committed_at"] = host_watch._iso(_at())
        d = host_watch.apply_probe(ledger, False, "HTTP 504", now=_at(0.25))
        assert host_watch.needs_commit(ledger, d, now=_at(0.25), outbox_changed=False)


class TestHeldAlertsSurviveAndArrive:
    def _held(self, key="collect:main:abc"):
        doc = alert_outbox.empty()
        alert_outbox.enqueue(doc, key=key, kind="alert", scope="collect:main",
                             payload={"subject": "CI RED: collect", "body": "b",
                                      "dedupe_key": key},
                             reason="HTTP 504 from /alert")
        return doc

    def test_an_alert_held_during_an_outage_is_delivered_afterwards(self, monkeypatch):
        doc = self._held()
        monkeypatch.setattr(ci_alert, "post_alert",
                            lambda *a, **k: (True, "emailed the owner", False))
        delivered, remaining, _ = host_watch.drain(doc, "https://x.invalid", "k")
        assert (delivered, remaining) == (1, 0)

    def test_a_drain_that_hits_a_still_down_host_keeps_everything(self, monkeypatch):
        doc = self._held()
        monkeypatch.setattr(ci_alert, "post_alert",
                            lambda *a, **k: (False, "HTTP 504", True))
        delivered, remaining, blocked = host_watch.drain(doc, "https://x.invalid", "k")
        assert (delivered, remaining) == (0, 1) and "504" in blocked
        assert alert_outbox.pending(doc)[0]["attempts"] == 2

    def test_a_transient_failure_stops_the_drain_rather_than_hammering(self, monkeypatch):
        doc = self._held("a")
        alert_outbox.enqueue(doc, key="b", kind="alert", scope="s2",
                             payload={"subject": "second"}, reason="")
        tries = []
        monkeypatch.setattr(ci_alert, "post_alert",
                            lambda *a, **k: (tries.append(1), (False, "HTTP 504", True))[1])
        host_watch.drain(doc, "https://x.invalid", "k")
        assert len(tries) == 1, "a down host learns nothing from the second POST"

    def test_the_outbox_survives_a_round_trip_through_the_file(self, tmp_path):
        path = tmp_path / "alert_outbox.json"
        doc = self._held()
        alert_outbox.save(doc, path)
        assert json.loads(path.read_text())["entries"][0]["state"] == "pending"
        assert len(alert_outbox.pending(alert_outbox.load(path))) == 1


class TestTheQueueDoesNotBecomeItsOwnProblem:
    def test_the_same_alert_twice_is_held_once(self):
        doc = alert_outbox.empty()
        for _ in range(8):
            alert_outbox.enqueue(doc, key="k", kind="alert", scope="s",
                                 payload={"subject": "x"}, reason="HTTP 504")
        held = alert_outbox.pending(doc)
        assert len(held) == 1 and held[0]["attempts"] == 8

    def test_a_recovery_cancels_a_red_that_was_never_sent(self):
        """The eight-emails-about-nothing case. If 'collect' failed while the
        host was down and passed before the queue drained, the RED never went
        out, so there is nothing to clear and nothing to tell anyone."""
        doc = alert_outbox.empty()
        alert_outbox.enqueue(doc, key="collect:main:abc", kind="alert",
                             scope="collect:main", payload={"subject": "CI RED"},
                             reason="HTTP 504")
        outcome, _ = alert_outbox.enqueue(
            doc, key="resolve:collect:main", kind="resolve", scope="collect:main",
            payload={"resolve_scope": "collect:main"}, reason="HTTP 504")
        assert outcome == "cancelled"
        assert not alert_outbox.pending(doc), \
            "the owner would have been mailed a failure and its recovery, both stale"

    def test_a_recovery_for_a_scope_with_nothing_held_is_still_queued(self):
        """The RED may have been delivered BEFORE the host went down, in which
        case the endpoint holds open state that only a resolve can clear."""
        doc = alert_outbox.empty()
        outcome, _ = alert_outbox.enqueue(
            doc, key="resolve:recall:main", kind="resolve", scope="recall:main",
            payload={"resolve_scope": "recall:main"}, reason="HTTP 504")
        assert outcome == "queued" and len(alert_outbox.pending(doc)) == 1

    def test_delivery_order_is_the_order_things_happened(self):
        doc = alert_outbox.empty()
        alert_outbox.enqueue(doc, key="first", kind="alert", scope="a",
                             payload={}, reason="")
        alert_outbox.enqueue(doc, key="second", kind="alert", scope="b",
                             payload={}, reason="")
        doc["entries"][0]["raised_at"] = "2026-07-31T00:48:00+00:00"
        doc["entries"][1]["raised_at"] = "2026-07-31T00:52:00+00:00"
        assert [e["key"] for e in alert_outbox.pending(doc)] == ["first", "second"]

    def test_a_queue_that_never_drains_becomes_loud(self):
        doc = alert_outbox.empty()
        for _ in range(alert_outbox.FAIL_LOUD_ATTEMPTS):
            alert_outbox.enqueue(doc, key="k", kind="alert", scope="s",
                                 payload={}, reason="HTTP 401")
        assert alert_outbox.stuck(doc), \
            "a queue that quietly never drains is the original silence with steps"

    def test_history_is_bounded_but_pending_work_never_is(self):
        doc = alert_outbox.empty()
        for i in range(alert_outbox.HISTORY_KEPT + 50):
            _, entry = alert_outbox.enqueue(doc, key=f"k{i}", kind="alert",
                                            scope="s", payload={}, reason="")
            alert_outbox.mark_delivered(entry)
        alert_outbox.enqueue(doc, key="live", kind="alert", scope="s",
                             payload={}, reason="")
        doc["entries"] = alert_outbox._trim(doc["entries"])
        assert len(alert_outbox.pending(doc)) == 1
        assert len(doc["entries"]) == alert_outbox.HISTORY_KEPT + 1

    def test_an_unreadable_outbox_is_an_empty_one_not_an_exception(self, tmp_path):
        """This is read from the FAILURE path of the alerter. A notifier that
        crashes while handling a failure has told nobody anything."""
        path = tmp_path / "alert_outbox.json"
        path.write_text("{not json")
        assert alert_outbox.load(path)["entries"] == []


class TestTheFallbackChannelIsDeduplicatedByConstruction:
    def test_one_marker_means_one_issue(self, monkeypatch):
        import gh_fallback

        calls = []

        def fake(args, repo):
            calls.append(args[:2])
            if args[:2] == ["issue", "list"]:
                return True, json.dumps(
                    [{"number": 7, "title": "x",
                      "body": gh_fallback.MARKER + "\n## What has been held so far\n"}])
            return True, ""

        monkeypatch.setattr(gh_fallback, "_gh", fake)
        ok, note = gh_fallback.open_or_update("r/r", line="- another")
        assert ok and "#7" in note
        assert ["issue", "create"] not in calls, \
            "a second issue per outage is the undeduped noise this replaces"
        assert ["issue", "edit"] in calls, "editing is what does not email"

    def test_a_missing_gh_degrades_loudly_and_never_raises(self, monkeypatch):
        import gh_fallback

        monkeypatch.setattr(gh_fallback.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        ok, note = gh_fallback.open_or_update("r/r", line="- x")
        assert not ok and "gh is not installed" in note
