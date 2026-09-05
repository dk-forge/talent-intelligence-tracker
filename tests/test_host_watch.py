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

    def test_the_drain_never_re_rules_a_held_alert(self, monkeypatch):
        """It must call `ci_alert.deliver`, and never `ci_alert.post_alert`.

        A held alert has ALREADY been ruled on by the ledger and already
        claimed. Sending it back through `post_alert` would re-derive against
        that ledger and find its own cause open -- so the alert would be
        swallowed as a duplicate of itself and never arrive -- or, for a
        resolve, clear a scope a second time. The ruling travelled with the
        payload; the drain's only job is to send it.
        """
        doc = self._held()

        def forbidden(*a, **k):
            raise AssertionError("the drain must not re-rule a held alert")

        monkeypatch.setattr(ci_alert, "post_alert", forbidden)
        monkeypatch.setattr(ci_alert, "deliver",
                            lambda *a, **k: (True, "emailed the owner", False))
        assert host_watch.drain(doc)[:2] == (1, 0)

    def test_the_idempotency_key_travels_with_the_held_message(self, monkeypatch):
        """A re-drain after a failed outbox commit is the one path that genuinely
        sends the same decision twice. The key that collapses it at Resend was
        stamped into the payload when the ledger ruled, so it is still there
        months later."""
        doc = alert_outbox.empty()
        alert_outbox.enqueue(doc, key="collect:main:abc", kind="alert",
                             scope="collect:main",
                             payload={"subject": "CI RED: collect", "body": "b",
                                      "dedupe_key": "collect:main:abc",
                                      "idempotency_key": "tit-raise-collect:main:abc-99"},
                             reason="the relay was unreachable")
        seen = {}
        monkeypatch.setattr(ci_alert, "deliver",
                            lambda payload, *a, **k: (
                                seen.update(payload),
                                (True, "emailed the owner", False))[1])
        host_watch.drain(doc)
        assert seen["idempotency_key"] == "tit-raise-collect:main:abc-99"

    def test_an_alert_held_during_an_outage_is_delivered_afterwards(self, monkeypatch):
        doc = self._held()
        monkeypatch.setattr(ci_alert, "deliver",
                            lambda *a, **k: (True, "emailed the owner", False))
        delivered, remaining, _ = host_watch.drain(doc)
        assert (delivered, remaining) == (1, 0)

    def test_a_drain_that_hits_a_dead_relay_keeps_everything(self, monkeypatch):
        doc = self._held()
        monkeypatch.setattr(ci_alert, "deliver",
                            lambda *a, **k: (False, "HTTP 504", True))
        delivered, remaining, blocked = host_watch.drain(doc)
        assert (delivered, remaining) == (0, 1) and "504" in blocked
        assert alert_outbox.pending(doc)[0]["attempts"] == 2

    def test_a_transient_failure_stops_the_drain_rather_than_hammering(self, monkeypatch):
        doc = self._held("a")
        alert_outbox.enqueue(doc, key="b", kind="alert", scope="s2",
                             payload={"subject": "second"}, reason="")
        tries = []
        monkeypatch.setattr(ci_alert, "deliver",
                            lambda *a, **k: (tries.append(1), (False, "HTTP 504", True))[1])
        host_watch.drain(doc)
        assert len(tries) == 1, "a dead relay learns nothing from the second send"

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


class TestTheWorkflowsThatCarryThis:
    """The properties that only exist in YAML, where nothing else checks them."""

    def _wf(self, name):
        import yaml

        return yaml.safe_load(
            (Path(__file__).resolve().parents[1] / ".github/workflows" / name).read_text())

    def test_the_watchdog_is_armed(self):
        crons = [e["cron"] for e in self._wf("host-watch.yml")[True]["schedule"]]
        assert crons, "a dormant watchdog is the state we were already in"
        assert "*/15 * * * *" in crons

    def test_the_watchdog_is_not_in_the_writer_lock(self):
        """A watchdog that queues behind a six-hour backfill is not a watchdog.
        It writes no collected data, so it needs no share of that lock."""
        wf = self._wf("host-watch.yml")
        assert (wf.get("concurrency") or {}).get("group") != "talent-collect"

    def test_the_watchdog_never_touches_the_collected_database(self):
        """tests/test_workflows.py finds database writers by searching the raw
        text for the filename and then demands they join talent-collect. Naming
        it here — even in a comment — would drag this workflow into the one lock
        it must stay out of."""
        text = (Path(__file__).resolve().parents[1]
                / ".github/workflows/host-watch.yml").read_text()
        assert "talent_intel.db" not in text

    def test_every_step_that_re_runs_the_probe_carries_the_probes_env(self):
        """A step does not inherit another step's env, and the commit step's
        retry path RE-RUNS host_watch.py after a rejected push.

        Without WP_SITE_URL there, the re-derivation exits 1 on "the host cannot
        be probed at all", so an ordinary push race — a drain tick landing in
        the same second, which happens constantly in this repo — reddened the
        watchdog and named a configuration problem that did not exist. Measured
        on run 30868552426, which had already probed the host, found it UP and
        delivered a held alert before this step ran. A watchdog red for its own
        bookkeeping cannot report an outage, which is the same failure class as
        the stuck alert that very run had just cleared.
        """
        steps = self._wf("host-watch.yml")["jobs"]["watch"]["steps"]
        probes = [s for s in steps if "host_watch.py" in (s.get("run") or "")]
        assert probes
        for step in probes:
            assert (step.get("env") or {}).get("WP_SITE_URL"), \
                f"step {step.get('name')!r} re-runs the probe without WP_SITE_URL"

    def test_the_alerter_can_commit_what_it_could_not_send(self):
        wf = self._wf("ci-alert.yml")
        assert wf["permissions"]["contents"] == "write", \
            "without write access an undeliverable alert has nowhere to go"
        steps = wf["jobs"]["alert"]["steps"]
        hold = next(s for s in steps if "alert_outbox.py enqueue" in (s.get("run") or ""))
        run = hold["run"]
        assert "git reset --hard origin/main" in run
        assert run.index("git reset --hard") < run.index("alert_outbox.py enqueue"), \
            "the envelope is folded in before the reset, which discards it"
        assert "for attempt in" in run, "a single push loses the alert on any race"
        assert "::error::" in run and run.rstrip().endswith("exit 1"), \
            "an alert that reaches neither the owner nor the queue must be loud"

    def test_the_hold_step_runs_even_when_the_alert_step_failed(self):
        """An alert sitting on a runner's disk is an alert nobody will read."""
        wf = self._wf("ci-alert.yml")
        hold = next(s for s in wf["jobs"]["alert"]["steps"]
                    if "alert_outbox.py enqueue" in (s.get("run") or ""))
        assert "cancelled()" in str(hold.get("if")), \
            "the default success gate would skip the hold on the path that needs it"

    def test_the_alerter_still_refuses_to_report_on_itself(self):
        """The recursion guard. A mail loop is not a failure mode worth
        discovering empirically, and it matters more now that a held alert
        leaves a green run rather than a red one."""
        wf = self._wf("ci-alert.yml")
        assert "CI failure alert" in wf["jobs"]["alert"]["if"]


class TestASwitchedOffAlerterIsVisible:
    """The quietest failure of all: a workflow that is simply turned off.

    'CI failure alert' was disabled by hand at 2026-07-31T01:01 UTC, two minutes
    after it failed four times POSTing to a host that was answering 504 — an
    entirely reasonable reaction to an alarm that had started amplifying an
    outage. Nothing would ever have reminded anyone to turn it back on: a
    disabled workflow is not red, produces no runs to go stale, and appears only
    in `gh workflow list`, which nobody runs at a session start.
    """

    def test_a_disabled_alerter_is_an_action_item(self):
        import ci_status

        report = ci_status.assess(
            "r/r", failures=[], cancelled=[], latest={}, default_branch="main",
            switched_off={"CI failure alert": "a red run would reach nobody"})
        assert report["problems"], "a switched-off alerter must not read as green"
        assert "DISABLED" in report["problems"][0]
        assert "gh workflow enable" in report["problems"][0], \
            "say how to undo it; the state is invisible everywhere else"

    def test_the_watchdog_and_the_alerter_are_both_on_the_list(self):
        import ci_status

        assert "CI failure alert" in ci_status.MUST_STAY_ON
        assert "host-watch" in ci_status.MUST_STAY_ON

    def test_an_unreadable_workflow_list_never_invents_a_problem(self, monkeypatch):
        """It degrades to 'nothing is off' rather than raising: the caller
        already fails loudly when gh cannot be reached, and this must not turn a
        readable report into an unreadable one."""
        import ci_status
        import writer_queue_runs

        monkeypatch.setattr(writer_queue_runs, "_gh",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("404")))
        assert ci_status.disabled_alerters("r/r") == {}


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


class TestAnUnsendableKeyCannotEnterTheRetryLoop:
    """The 2026-08-03 outage in one class.

    `ci_noise_report.py` composed its ISO week with `%G-W%V` and minted
    `ci-noise:2026-W32`. Uppercase W is not in the character class `/alert`
    accepts, so every POST earned a SETTLED 400. The outbox retried it anyway,
    sixteen times, passed FAIL_LOUD_ATTEMPTS, declared the entry `stuck` — and
    host-watch, which reports "alerts are stuck with the host UP", then failed
    every fifteen-minute tick for five hours while the host answered HTTP 200 in
    under a second.

    The individual bad key was repaired by hand. These pin the CLASS: the queue
    itself now refuses to accept anything it could only retry forever, so the
    next caller to hand-compose a key cannot repeat it.
    """

    def _envelope(self, tmp_path, key):
        env = tmp_path / "envelope.json"
        env.write_text(json.dumps({
            "key": key, "kind": "alert", "scope": "ci-noise",
            "payload": {"subject": "CI noise", "dedupe_key": key},
            "reason": "HTTP 400 from /alert"}))
        return env

    def test_the_exact_key_that_took_the_watchdog_down_is_repaired_on_the_way_in(
            self, tmp_path):
        out = tmp_path / "alert_outbox.json"
        alert_outbox.enqueue_envelope(
            self._envelope(tmp_path, "ci-noise:2026-W32"), out)
        held = alert_outbox.pending(alert_outbox.load(out))
        assert len(held) == 1
        assert held[0]["key"] == "ci-noise:2026-w32"
        assert alert_outbox.KEY_SAFE.match(held[0]["key"])
        # and the repair is auditable rather than silent
        assert held[0]["original_key"] == "ci-noise:2026-W32"

    def test_no_key_the_endpoint_would_reject_can_reach_the_queue(self, tmp_path):
        # Every shape the endpoint's regex refuses: uppercase, a leading
        # separator, characters outside the class, an over-long key, and empty.
        for bad in ("ci-noise:2026-W32", ":leading-colon", "has spaces",
                    "sl/ash", "éaccent", "x" * 400, ""):
            out = tmp_path / f"outbox-{abs(hash(bad))}.json"
            alert_outbox.enqueue_envelope(self._envelope(tmp_path, bad), out)
            held = alert_outbox.pending(alert_outbox.load(out))
            assert len(held) == 1, bad
            assert alert_outbox.KEY_SAFE.match(held[0]["key"]), \
                f"{bad!r} was queued as {held[0]['key']!r}, which /alert rejects"

    def test_a_key_that_was_already_valid_is_left_exactly_alone(self, tmp_path):
        # The repair must not renumber healthy keys: two runs of one cause have
        # to keep colliding on one key, or the dedupe that keeps eight identical
        # emails down to one stops working.
        good = "collect:main:abc-123.def_4"
        out = tmp_path / "alert_outbox.json"
        for _ in range(3):
            alert_outbox.enqueue_envelope(self._envelope(tmp_path, good), out)
        held = alert_outbox.pending(alert_outbox.load(out))
        assert len(held) == 1 and held[0]["key"] == good
        assert "original_key" not in held[0]
        assert held[0]["attempts"] == 3

    def test_the_python_mirror_is_the_one_the_queue_enforces(self):
        # One definition. A second copy is a second thing to drift from the PHP,
        # and the copy that drifts passes here and 400s there.
        assert ci_alert.KEY_SAFE is alert_outbox.KEY_SAFE


class TestTheSandboxOriginsRideTheSameTick:
    """2026-09-05. The AskTheRecruiter sandbox is a separate product on a
    separate Railway project, and nothing durable was watching it: an outage
    was found by whichever laptop session happened to be open. The watch above
    already runs every 15 minutes at $0 and already knows how to speak on a
    channel that is not the thing it watches, so the sandbox origins ride it.

    Same rules as the host, pinned here for the new origins specifically: one
    issue per sustained outage, none for a blip, a RECOVERED on the way back,
    and a /healthz judged strictly. No network anywhere in this class.
    """

    BACKEND = "https://sandbox.example.invalid/healthz"

    # -- what UP means for a /healthz ---------------------------------------

    def test_status_ok_with_a_version_is_up(self):
        ok, detail = host_watch.judge_healthz(
            200, b'{"status":"ok","version":"v1.0.944","uptime_seconds":7}')
        assert ok and "v1.0.944" in detail

    def test_status_degraded_is_down(self):
        ok, detail = host_watch.judge_healthz(
            200, b'{"status":"degraded","version":"v1.0.944"}')
        assert not ok and "degraded" in detail

    def test_a_non_json_body_is_down(self):
        ok, detail = host_watch.judge_healthz(200, b"<html>ok</html>")
        assert not ok and "non-JSON" in detail

    def test_http_503_is_down(self):
        ok, detail = host_watch.judge_healthz(503, b'{"status":"ok","version":"x"}')
        assert not ok and "503" in detail

    def test_an_empty_or_missing_version_is_down(self):
        assert not host_watch.judge_healthz(200, b'{"status":"ok","version":""}')[0]
        assert not host_watch.judge_healthz(200, b'{"status":"ok"}')[0]
        assert not host_watch.judge_healthz(200, b'{"status":"ok","version":7}')[0]

    def test_a_4xx_is_down_here_unlike_the_host_probe(self, monkeypatch):
        """The host probe reads a 404 as 'WordPress is routing'. A /healthz that
        404s is a service that is not serving its health check, which is DOWN."""
        import urllib.error

        def boom(*a, **k):
            raise urllib.error.HTTPError("u", 404, "gone", {}, None)

        monkeypatch.setattr(host_watch.urllib.request, "urlopen", boom)
        ok, detail = host_watch.probe_healthz_once(self.BACKEND)
        assert not ok and "404" in detail

    def test_a_timeout_is_down_and_does_not_raise(self, monkeypatch):
        import socket

        def hang(*a, **k):
            raise socket.timeout("timed out")

        monkeypatch.setattr(host_watch.urllib.request, "urlopen", hang)
        ok, detail = host_watch.probe_healthz_once(self.BACKEND)
        assert not ok and "no answer" in detail

    def test_the_probe_sends_the_repos_user_agent_and_a_ten_second_budget(self, monkeypatch):
        seen = {}

        class _Resp:
            status = 200

            def read(self, n):
                return b'{"status":"ok","version":"v1"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout):
            seen["ua"] = req.get_header("User-agent")
            seen["timeout"] = timeout
            seen["url"] = req.full_url
            return _Resp()

        monkeypatch.setattr(host_watch.urllib.request, "urlopen", fake)
        ok, _ = host_watch.probe_healthz_once(self.BACKEND)
        assert ok
        assert seen["ua"] == host_watch.USER_AGENT
        assert seen["timeout"] == host_watch.HEALTHZ_TIMEOUT == 10
        assert seen["url"].startswith(self.BACKEND + "?cb="), "cache-busted"

    # -- the outage semantics, per origin -----------------------------------

    def _run(self, origin, answers, *, ledger=None, outbox=None, calls=None,
             issues=None, monkeypatch=None, start=0):
        """Drive watch_origin through a list of (ok, detail) probe answers with
        a fake gh and no network. Returns (ledger, outbox, gh_calls). Pass the
        same ledger/outbox/calls/issues back in to continue a run."""
        import gh_fallback

        ledger = ledger if ledger is not None else host_watch.load_ledger("/nonexistent")
        outbox = outbox if outbox is not None else alert_outbox.empty()
        calls = calls if calls is not None else []
        issues = issues if issues is not None else {}

        def fake_gh(args, repo):
            calls.append(args[:2])
            if args[:2] == ["issue", "list"]:
                return True, json.dumps([{"number": n, "title": "", "body": b}
                                         for n, b in issues.items()])
            if args[:2] == ["issue", "create"]:
                issues[len(issues) + 1] = args[args.index("--body") + 1]
                return True, ""
            if args[:2] == ["issue", "close"]:
                issues.pop(int(args[2]), None)
                return True, ""
            return True, ""

        monkeypatch.setattr(gh_fallback, "_gh", fake_gh)
        for i, (ok, detail) in enumerate(answers, start=start):
            host_watch.watch_origin(
                origin, self.BACKEND, ledger, outbox, now=_at(i / 4),
                repo="r/r", fallback=True, probe=lambda url, _a=(ok, detail): _a)
        return ledger, outbox, calls

    def test_one_sustained_outage_opens_exactly_one_issue_and_one_mail(self, monkeypatch):
        origin = host_watch.ORIGINS[0]
        ledger, outbox, calls = self._run(
            origin, [(False, "HTTP 503 from /healthz")] * 10, monkeypatch=monkeypatch)
        assert calls.count(["issue", "create"]) == 1, \
            "one issue per outage, never one per tick"
        held = alert_outbox.pending(outbox)
        assert len(held) == 1 and held[0]["kind"] == "alert"
        assert held[0]["scope"] == host_watch.origin_scope(origin)
        assert alert_outbox.KEY_SAFE.match(held[0]["key"]), held[0]["key"]
        assert "sandbox backend" in held[0]["payload"]["subject"].lower()

    def test_a_blip_opens_nothing_and_mails_nothing(self, monkeypatch):
        origin = host_watch.ORIGINS[0]
        answers = [(False, "HTTP 503 from /healthz")] * (host_watch.SUSTAINED_FAILURES - 1)
        answers.append((True, "HTTP 200 status=ok version=v1"))
        ledger, outbox, calls = self._run(origin, answers, monkeypatch=monkeypatch)
        assert ["issue", "create"] not in calls
        assert not alert_outbox.pending(outbox), \
            "a blip that never announced must not send a RECOVERED either"
        assert ledger["origins"][origin["id"]]["state"] == "up"

    def test_recovery_closes_the_issue_and_sends_one_recovered(self, monkeypatch):
        origin = host_watch.ORIGINS[0]
        down = [(False, "HTTP 503 from /healthz")] * host_watch.SUSTAINED_FAILURES
        issues, calls = {}, []
        ledger, outbox, _ = self._run(origin, down, issues=issues, calls=calls,
                                      monkeypatch=monkeypatch)
        # The drain ran between the ticks and the outage notice went out.
        for entry in alert_outbox.pending(outbox):
            alert_outbox.mark_delivered(entry, "emailed the owner")
        up = [(True, "HTTP 200 status=ok version=v1")] * 3
        self._run(origin, up, ledger=ledger, outbox=outbox, issues=issues,
                  calls=calls, start=len(down), monkeypatch=monkeypatch)
        assert calls.count(["issue", "create"]) == 1
        assert calls.count(["issue", "close"]) == 1
        recovered = [e for e in outbox["entries"] if e["kind"] == "resolve"]
        assert len(recovered) == 1, "RECOVERED fires once, not once per healthy tick"
        assert "RECOVERED" in recovered[0]["payload"]["subject"]
        sub = ledger["origins"][origin["id"]]
        assert not sub["announced"] and "outage_since" not in sub, \
            "the next outage must be able to speak"

    def test_a_recovery_before_the_outage_mail_left_cancels_both(self, monkeypatch):
        """The relay was down too, the outage notice was never delivered, and
        the origin came back: the owner is told nothing, because there is
        nothing true left to tell. Same rule alert_outbox applies to CI reds."""
        origin = host_watch.ORIGINS[0]
        answers = [(False, "HTTP 503 from /healthz")] * host_watch.SUSTAINED_FAILURES
        answers.append((True, "HTTP 200 status=ok version=v1"))
        _, outbox, _ = self._run(origin, answers, monkeypatch=monkeypatch)
        assert not alert_outbox.pending(outbox)
        assert {e["state"] for e in outbox["entries"]} == {"cancelled"}

    def test_the_two_origins_and_the_host_keep_separate_issues(self, monkeypatch):
        """A sandbox outage must not hide inside the host's issue, and a
        sandbox recovery must not close the host's. The marker is the identity."""
        import gh_fallback

        markers = {host_watch.origin_marker(o) for o in host_watch.ORIGINS}
        assert len(markers) == 2 and gh_fallback.MARKER not in markers

        calls = []

        def fake(args, repo):
            calls.append(args[:2])
            if args[:2] == ["issue", "list"]:
                return True, json.dumps([{"number": 7, "title": "host",
                                          "body": gh_fallback.MARKER}])
            return True, ""

        monkeypatch.setattr(gh_fallback, "_gh", fake)
        sandbox = host_watch.origin_marker(host_watch.ORIGINS[0])
        ok, note = gh_fallback.close("r/r", note="x", marker=sandbox)
        assert ok and note == "no fallback issue was open", \
            "the host's open issue is not the sandbox's"
        assert ["issue", "close"] not in calls

    def test_the_issue_title_names_the_origin_plainly(self):
        for origin in host_watch.ORIGINS:
            title = host_watch.origin_title(origin, self.BACKEND)
            assert origin["label"] in title and "down" in title
            assert "sandbox.example.invalid" in title

    def test_a_sandbox_outage_leaves_the_hosts_ledger_alone(self, monkeypatch):
        ledger = host_watch.load_ledger("/nonexistent")
        host_watch.apply_probe(ledger, True, "HTTP 200", now=_at())
        self._run(host_watch.ORIGINS[1], [(False, "HTTP 503 from /healthz")] * 5,
                  ledger=ledger, monkeypatch=monkeypatch)
        assert ledger["state"] == "up" and ledger["consecutive_failures"] == 0
        assert ledger["origins"]["sandbox-frontend"]["state"] == "down"

    def test_a_sandbox_outage_is_a_reason_to_commit(self):
        ledger = host_watch.load_ledger("/nonexistent")
        d = host_watch.apply_probe(host_watch.origin_ledger(ledger, "sandbox-backend"),
                                   False, "HTTP 503", now=_at())
        assert host_watch.needs_commit(ledger, d, now=_at(), outbox_changed=False)

    def test_an_unconfigured_origin_is_a_red_run_not_a_silent_skip(self, monkeypatch, tmp_path):
        for origin in host_watch.ORIGINS:
            monkeypatch.delenv(origin["env"], raising=False)
        rc = host_watch.main(["--site", "https://example.invalid",
                              "--no-fallback", "--ledger", str(tmp_path / "l.json"),
                              "--outbox", str(tmp_path / "o.json")])
        assert rc == 1, "a declared origin that is silently skipped measures nothing"

    # -- the workflow --------------------------------------------------------

    def test_the_workflow_lists_all_three_origins(self):
        """The URLs live in the workflow and nowhere else; this is the list of
        what is watched."""
        wf = TestTheWorkflowsThatCarryThis()._wf("host-watch.yml")
        steps = wf["jobs"]["watch"]["steps"]
        probes = [s for s in steps if "host_watch.py" in (s.get("run") or "")]
        assert probes
        for step in probes:
            env = step.get("env") or {}
            assert env.get("WP_SITE_URL") == "https://asktherecruiter.com/blog"
            assert env.get("SANDBOX_BACKEND_URL") == \
                "https://sandbox.asktherecruiter.com/healthz"
            assert env.get("SANDBOX_FRONTEND_URL") == \
                "https://asktherecruiter-sandbox-production.up.railway.app/healthz"
        for origin in host_watch.ORIGINS:
            assert origin["env"] in probes[0]["env"], \
                f"{origin['id']} is declared in the script but the workflow carries no URL"
