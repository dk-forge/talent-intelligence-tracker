"""The weekly CI-noise report: one email naming the causes, or none at all.

Measured 2026-07-26..08-02: ~190 non-green runs, of which the overwhelming
majority were repeats of a handful of already-reported facts. The structural
fixes live in writer_queue.py; this report is the regression alarm over them,
so its own arithmetic has to be pinned: what counts as noise, what stays
signal, and above all that a quiet week sends NOTHING.
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ci_alert
import ci_noise_report as cnr
import host_watch

NOW = datetime(2026, 8, 3, 13, 20, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def _run(run_id, workflow, conclusion, *, days_ago=1, job_count=1,
         event="schedule", status="completed"):
    created = NOW - timedelta(days=days_ago)
    return {
        "databaseId": run_id,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "createdAt": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "job_count": job_count,
    }


class TestClassify:
    def test_a_quiet_week_is_zero_noise(self):
        runs = [_run(1, "collect", "success"), _run(2, "tests", "success")]
        result = cnr.classify(runs, {}, SINCE)
        assert result["noise"] == 0
        assert result["failed_runs"] == 0

    def test_the_first_red_of_a_cause_is_signal_not_noise(self):
        """Category-(a) protection, as arithmetic: one real failure that
        alerted once contributes ZERO to the noise count."""
        runs = [_run(1, "collect", "failure")]
        result = cnr.classify(runs, {"1": "ConnectionError: key limit reached"},
                              SINCE)
        assert result["failed_runs"] == 1
        assert result["repeats"] == 0
        assert result["noise"] == 0

    def test_repeats_of_one_cause_count_as_noise(self):
        """THE 180-red-drain-runs week, in miniature: same workflow, same
        cause, N runs -> N-1 noise."""
        cause = "the writer queue has items that need a human"
        runs = [_run(i, "drain-writers", "failure") for i in range(1, 6)]
        causes = {str(i): cause for i in range(1, 6)}
        result = cnr.classify(runs, causes, SINCE)
        assert result["repeats"] == 4
        assert result["noise"] == 4

    def test_causes_differing_only_in_numbers_are_one_cause(self):
        """Dedup by cause means normalised: 'waiting 3h' and 'waiting 11h'
        describe one fact. ci_alert.normalise is the shared authority."""
        runs = [_run(1, "drain-writers", "failure"),
                _run(2, "drain-writers", "failure")]
        causes = {"1": "backfill has been waiting 3h - the lock is starved",
                  "2": "backfill has been waiting 11h - the lock is starved"}
        result = cnr.classify(runs, causes, SINCE)
        assert result["repeats"] == 1

    def test_unread_causes_group_per_workflow_not_across(self):
        """Past the log cap we know the workflow but not the cause. Two unread
        failures in one workflow plausibly repeat; across two workflows they
        are two facts and neither is a repeat."""
        runs = [_run(1, "collect", "failure"), _run(2, "collect", "failure"),
                _run(3, "enrich", "failure")]
        result = cnr.classify(runs, {}, SINCE)
        assert result["repeats"] == 1
        by_wf = {(wf, cause): n for wf, cause, n in result["causes"]}
        assert by_wf[("collect", cnr.UNREAD)] == 2
        assert by_wf[("enrich", cnr.UNREAD)] == 1

    def test_a_zero_job_cancellation_is_noise_and_a_started_one_is_not(self):
        """job_count == 0 is the lock-displacement fingerprint; a run cancelled
        after starting is a human's decision or a timeout, not noise."""
        runs = [_run(1, "collect", "cancelled", job_count=0),
                _run(2, "backfill-gdelt-2026", "cancelled", job_count=1)]
        result = cnr.classify(runs, {}, SINCE)
        assert len(result["evictions"]) == 1
        assert result["evictions"][0]["run_id"] == "1"
        assert result["noise"] == 1

    def test_runs_outside_the_window_do_not_count(self):
        runs = [_run(1, "collect", "failure", days_ago=9),
                _run(2, "collect", "cancelled", days_ago=10, job_count=0)]
        result = cnr.classify(runs, {"1": "boom"}, SINCE)
        assert result["failed_runs"] == 0
        assert result["noise"] == 0

    def test_an_unfinished_run_is_not_judged(self):
        runs = [_run(1, "collect", None, status="in_progress")]
        result = cnr.classify(runs, {}, SINCE)
        assert result["window_runs"] == 0


class TestCompose:
    def test_the_key_carries_the_week_so_next_week_is_a_new_cause(self):
        runs = [_run(i, "drain-writers", "failure") for i in (1, 2)]
        result = cnr.classify(runs, {}, SINCE)
        _s, _b, key1 = cnr.compose(result, repo="dk-forge/x", days=7, now=NOW)
        _s, _b, key2 = cnr.compose(result, repo="dk-forge/x", days=7,
                                   now=NOW + timedelta(days=7))
        assert key1 != key2
        assert key1.startswith("ci-noise:")

    def test_the_body_marks_singletons_as_correct_not_noisy(self):
        runs = [_run(1, "collect", "failure"),
                _run(2, "drain-writers", "failure"),
                _run(3, "drain-writers", "failure")]
        result = cnr.classify(runs, {"1": "boom"}, SINCE)
        _s, body, _k = cnr.compose(result, repo="dk-forge/x", days=7, now=NOW)
        assert "reported once, correctly" in body
        assert "1 repeat red(s)" in body


def _endpoint_regex() -> str:
    """The literal `tit_api_alert()` actually validates against, read from the
    PHP source. Mirroring a regex in two languages is only safe if a test fails
    when the two drift apart."""
    php = (Path(__file__).resolve().parents[1] / "wordpress-plugin"
           / "talent-intelligence-tracker" / "includes" / "api.php").read_text()
    found = re.search(r"\$safe\s*=\s*'/\^(.*?)\$/';", php)
    assert found, ("tit_api_alert() no longer declares $safe as a literal, so "
                   "the Python mirror in ci_alert.KEY_SAFE is now unpinned")
    return found.group(1)


class TestTheKeyIsOneTheEndpointAccepts:
    """A key the endpoint REJECTS is not a bad email, it is no email.

    Measured here on 2026-08-03. `compose()` formatted the ISO week with
    `%G-W%V` and minted `ci-noise:2026-W32`. `tit_api_alert()` validates both
    `dedupe_key` and `resolve_scope` against `^[a-z0-9][a-z0-9:._-]{0,159}$`,
    so the uppercase W came back HTTP 400 `bad dedupe_key` — a SETTLED failure
    that no amount of retrying can clear. The report was held in
    data/alert_outbox.json, retried 16 times, passed FAIL_LOUD_ATTEMPTS and
    went `stuck`; host_watch.py then failed every tick from 2026-08-03T21:55Z
    on "alerts are stuck with the host up", six consecutive red runs and
    counting. A permanently red watchdog cannot report an outage, so one
    uppercase character in a cache key disabled the outage alarm.

    So the shape is pinned in three places at once: against the PHP literal,
    across a whole year of week numbers, and for every other key composed by
    hand rather than through `slug()`.
    """

    def test_the_python_mirror_matches_the_endpoint_literal(self):
        assert ci_alert.KEY_SAFE.pattern.strip("^$") == _endpoint_regex()

    def test_every_week_of_the_year_composes_an_accepted_key(self):
        runs = [_run(i, "drain-writers", "failure") for i in (1, 2)]
        result = cnr.classify(runs, {}, SINCE)
        endpoint = re.compile("^" + _endpoint_regex() + "$")
        for offset in range(0, 366, 7):
            moment = NOW + timedelta(days=offset)
            subject, _body, key = cnr.compose(
                result, repo="dk-forge/talent-intelligence-tracker", days=7,
                now=moment)
            assert endpoint.match(key), f"rejected key on {moment.date()}: {key}"
            assert ci_alert.KEY_SAFE.match(key)
            # The subject quotes the same token, so the email in the inbox can
            # be tied to the key in the endpoint's open-alert state.
            assert key.split(":", 1)[1] in subject

    def test_the_host_down_key_is_accepted_too(self):
        """The same defect in the worse place: `since` is an ISO timestamp, so
        the un-slugged key carried an uppercase `T` and a `+`. The one email
        whose entire job is to arrive after the host comes back could not."""
        doc = {"since": "2026-08-03T21:55:00+00:00", "consecutive_failures": 3,
               "last_detail": "HTTP 504", "state": "down"}
        summary = host_watch.outage_summary(doc, now=NOW)
        assert ci_alert.KEY_SAFE.match(summary["dedupe_key"]), \
            summary["dedupe_key"]
        # Still one key per outage: a different outage must not be suppressed
        # as a repeat of this one.
        other = dict(doc, since="2026-08-04T07:10:00+00:00")
        assert (host_watch.outage_summary(other, now=NOW)["dedupe_key"]
                != summary["dedupe_key"])

    def test_the_bug_itself_would_be_caught(self):
        """Proof this test is able to fail."""
        assert not ci_alert.KEY_SAFE.match("ci-noise:2026-W32")
        assert not ci_alert.KEY_SAFE.match(
            "host-unreachable:2026-08-03T21:55:00+00:00")


class TestMain:
    def _patch(self, monkeypatch, runs, causes=None):
        # main() derives its window from the wall clock, while every fixture
        # below is stamped relative to the fixed NOW. Without this seam the
        # class passed for seven days and then went red on the eighth, on a
        # schedule, with no code change on either side: the fixture runs aged
        # out of main()'s own 7-day window, classify saw nothing, and main()
        # took the quiet-week early return. TestClassify and TestCompose
        # already inject the same instant explicitly via SINCE / now=; this
        # gives TestMain the same footing rather than re-dating the fixtures,
        # which would only move the expiry a week down the road.
        monkeypatch.setattr(cnr, "_now", lambda: NOW)
        monkeypatch.setattr(cnr.writer_queue_runs, "run_list",
                            lambda **kw: runs)
        monkeypatch.setattr(cnr.writer_queue_runs, "attach_job_counts",
                            lambda r, repo=None: r)
        monkeypatch.setattr(cnr.ci_alert, "fetch_failed_log",
                            lambda repo, run_id: (causes or {}).get(str(run_id), ""))
        monkeypatch.setattr(cnr.ci_alert, "extract_cause",
                            lambda log: (log, []))
        sent = []
        monkeypatch.setattr(cnr.ci_alert, "post_alert",
                            lambda site, key, payload, **kw:
                            sent.append(payload) or (True, "delivered", False))
        return sent

    def _main(self, argv, monkeypatch, **env):
        for name in ("WP_SITE_URL", "WP_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cnr.main(argv)
        return code, buf.getvalue()

    def test_a_quiet_week_posts_nothing_and_says_so(self, monkeypatch):
        sent = self._patch(monkeypatch, [_run(1, "collect", "success")])
        code, out = self._main([], monkeypatch,
                               WP_SITE_URL="https://x", WP_API_KEY="k")
        assert code == 0
        assert not sent, "no noise means NO post — silence is the product"
        assert "quiet week" in out

    def test_a_noisy_week_posts_exactly_one_alert(self, monkeypatch):
        runs = [_run(i, "drain-writers", "failure") for i in range(1, 5)]
        causes = {str(i): "queue needs a human" for i in range(1, 5)}
        sent = self._patch(monkeypatch, runs, causes)
        code, _out = self._main([], monkeypatch,
                                WP_SITE_URL="https://x", WP_API_KEY="k")
        assert code == 0
        assert len(sent) == 1, "one summary, never one post per run"
        assert "3 noisy run(s)" in sent[0]["subject"]

    def test_dry_run_posts_nothing_even_when_noisy(self, monkeypatch):
        runs = [_run(i, "drain-writers", "failure") for i in (1, 2)]
        sent = self._patch(monkeypatch, runs, {"1": "x", "2": "x"})
        code, out = self._main(["--dry-run"], monkeypatch,
                               WP_SITE_URL="https://x", WP_API_KEY="k")
        assert code == 0 and not sent and "--- subject ---" in out

    def test_noise_with_no_credentials_is_loud_not_silent(self, monkeypatch):
        runs = [_run(i, "drain-writers", "failure") for i in (1, 2)]
        self._patch(monkeypatch, runs, {"1": "x", "2": "x"})
        code, out = self._main([], monkeypatch)
        assert code == 1 and "NOT sent" in out

    def test_an_unreachable_gh_is_unknown_never_a_quiet_week(self, monkeypatch):
        def boom(**kw):
            raise cnr.writer_queue_runs.GhUnavailable("no gh here")
        monkeypatch.setattr(cnr.writer_queue_runs, "run_list", boom)
        code, out = self._main([], monkeypatch)
        assert code == 3, "'could not check' must exit 3, not read as clear"
        assert "could not read the run list" in out


class TestWorkflowFile:
    def test_the_report_is_scheduled_weekly_and_is_not_a_writer(self):
        from pathlib import Path
        import yaml
        path = (Path(__file__).resolve().parent.parent
                / ".github" / "workflows" / "ci-noise-report.yml")
        text = path.read_text()
        parsed = yaml.safe_load(text)
        trigger = parsed.get("on") or parsed.get(True)
        assert any(c["cron"].split()[4] == "1"
                   for c in trigger["schedule"]), "weekly, on Mondays"
        assert "talent_intel.db" not in text, (
            "a reader that became a writer would need the talent-collect lock")
        group = (parsed.get("concurrency") or {}).get("group")
        assert group != "talent-collect"
