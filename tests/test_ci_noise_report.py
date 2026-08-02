"""The weekly CI-noise report: one email naming the causes, or none at all.

Measured 2026-07-26..08-02: ~190 non-green runs, of which the overwhelming
majority were repeats of a handful of already-reported facts. The structural
fixes live in writer_queue.py; this report is the regression alarm over them,
so its own arithmetic has to be pinned: what counts as noise, what stays
signal, and above all that a quiet week sends NOTHING.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import ci_noise_report as cnr

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


class TestMain:
    def _patch(self, monkeypatch, runs, causes=None):
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
