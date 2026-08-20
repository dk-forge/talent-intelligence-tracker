"""Guards for ci_alert.py — the CI-failure-to-email path.

The alerting path is the one piece of infrastructure whose breakage is silent by
construction: you find out it stopped working by NOT getting an email, which is
indistinguishable from everything being fine. These are the assertions that keep
it honest. Offline, no network, no keys.

The log fixture is shaped exactly as `gh run view --log-failed` emits — the
job/step/timestamp columns matter, because stripping them wrong is a
silent-degradation bug rather than a crash.
"""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ci_alert  # noqa: E402


def _log(*content_lines: str) -> str:
    return "\n".join(
        f"collect\tRun collector\t2026-07-30T20:55:34.930{i:04d}Z {line}"
        for i, line in enumerate(content_lines))


PYTEST_FAILURE = _log(
    "=================================== FAILURES ===================================",
    "________________________ test_no_source_url_no_record _________________________",
    "    def test_no_source_url_no_record():",
    ">       assert validate(row) is None",
    "E       AssertionError: stored 3 rows with no source URL, expected 0",
    "FAILED tests/test_validate.py::test_no_source_url_no_record",
    "1 failed, 1806 passed in 42.10s",
    "##[error]Process completed with exit code 1.",
    "Post job cleanup.",
    "Cleaning up orphan processes",
)


class TestCauseExtraction:
    def test_pulls_the_assertion_not_the_useless_generic_line(self):
        cause, _ = ci_alert.extract_cause(PYTEST_FAILURE)
        assert cause == "AssertionError: stored 3 rows with no source URL, expected 0"

    def test_never_settles_for_process_completed_with_exit_code(self):
        """True, useless, and exactly the alert that gets ignored."""
        cause, _ = ci_alert.extract_cause(
            _log("some output", "##[error]Process completed with exit code 1."))
        assert "Process completed with exit code" not in cause

    def test_teardown_noise_is_never_the_cause(self):
        cause, _ = ci_alert.extract_cause(PYTEST_FAILURE)
        assert "orphan processes" not in cause and "Post job cleanup" not in cause

    def test_carries_the_failing_test_name_as_context(self):
        _, context = ci_alert.extract_cause(PYTEST_FAILURE)
        assert any("test_no_source_url_no_record" in c for c in context), context

    def test_falls_back_to_the_last_real_output_when_nothing_matches(self):
        cause, _ = ci_alert.extract_cause(
            _log("posting batch 3", "curl: (22) The requested URL returned 503",
                 "##[error]Process completed with exit code 22."))
        assert "503" in cause

    @pytest.mark.parametrize("empty", ["", None])
    def test_an_empty_log_degrades_instead_of_crashing(self, empty):
        assert ci_alert.extract_cause(empty) == ("", [])


#: The shape EVERY php harness in tests/php/ fails in: a header naming the
#: harness, then one indented bullet per failed assertion. Copied verbatim from
#: run 32059349793, which mailed the header and nothing else.
PHP_HARNESS_FAILURE = _log(
    "jsonld_xss OK",
    "dashboard FAILED:",
    "  - the markup must stay inside 184,600 bytes and was 185,146 (fixture "
    "prefixes excluded). This page is read on phones; a new card is not free.",
    "##[error]Process completed with exit code 1.",
    "Post job cleanup.",
)

PHP_HARNESS_MULTI = _log(
    "place pages FAILED in phase 'budget':",
    "  - the first cold render in a process must cost exactly 12 queries and cost 19",
    "  - a warm render must cost no queries at all, and cost 4",
    "##[error]Process completed with exit code 1.",
)


class TestPhpHarnessCause:
    """The php render harnesses are half of what `tests` runs, and until
    2026-08-17 a failure in any of them mailed `dashboard FAILED:` with an empty
    tail. The header matches `_LOOSE_ERROR` and the assertion underneath it
    matched nothing at all, so the one line worth reading was the one line
    dropped. An alert that names no measured value cannot be triaged from a
    phone, which is the whole promise this module makes."""

    def test_the_bullet_is_the_cause_not_the_header(self):
        cause, _ = ci_alert.extract_cause(PHP_HARNESS_FAILURE)
        assert cause.startswith("the markup must stay inside"), cause

    def test_the_cause_carries_the_measured_value_and_the_bound(self):
        cause, _ = ci_alert.extract_cause(PHP_HARNESS_FAILURE)
        assert "184,600" in cause and "185,146" in cause, cause

    def test_the_header_is_kept_as_context_so_the_harness_is_named(self):
        _, context = ci_alert.extract_cause(PHP_HARNESS_FAILURE)
        assert any("dashboard FAILED" in c for c in context), context

    def test_the_first_failed_assertion_leads_and_the_rest_are_context(self):
        """Later bullets are usually knock-on. The earliest one went wrong
        first, so it is what the subject line has to carry."""
        cause, context = ci_alert.extract_cause(PHP_HARNESS_MULTI)
        assert cause.startswith("the first cold render"), cause
        assert any("a warm render" in c for c in context), context

    def test_two_harness_failures_that_differ_only_in_numbers_are_one_cause(self):
        """The bullet carries measured values, and a budget that is over by a
        different amount every day is still one defect."""
        one, _ = ci_alert.extract_cause(PHP_HARNESS_FAILURE)
        two, _ = ci_alert.extract_cause(_log(
            "dashboard FAILED:",
            "  - the markup must stay inside 184,600 bytes and was 185,150 (fixture "
            "prefixes excluded). This page is read on phones; a new card is not free.",
            "##[error]Process completed with exit code 1."))
        assert ci_alert.normalise(one) == ci_alert.normalise(two)

    def test_a_bare_list_item_in_ordinary_output_is_not_a_cause(self):
        """The bullet only means something under a harness header. A build log
        full of `  - something` must not start manufacturing diagnoses."""
        cause, _ = ci_alert.extract_cause(_log(
            "installing:", "  - requests", "  - certifi",
            "curl: (22) The requested URL returned 503",
            "##[error]Process completed with exit code 22."))
        assert "503" in cause, cause


class TestDedupeByCause:
    """The single most important property. Eight identical emails would train
    the owner to filter this sender, which recreates the original problem."""

    def _key(self, cause, workflow="collect", branch="main"):
        return ci_alert.build_alert(
            repo="dk-forge/talent-intelligence-tracker", workflow=workflow,
            branch=branch, event="schedule", run_url="", cause=cause, context=[])[2]

    def test_the_same_defect_with_a_drifting_number_is_one_cause(self):
        assert self._key("AssertionError: stored 3 rows with no source URL, expected 0") == \
               self._key("AssertionError: stored 7 rows with no source URL, expected 0")

    def test_a_genuinely_different_assertion_is_a_different_cause(self):
        assert self._key("AssertionError: stored 3 rows with no source URL") != \
               self._key("CreditsExhausted: OpenRouter returned 402")

    def test_the_same_cause_in_two_workflows_is_two_causes(self):
        assert self._key("AssertionError: boom", workflow="collect") != \
               self._key("AssertionError: boom", workflow="recall")

    def test_timestamps_shas_and_runner_paths_normalise_away(self):
        assert "2026-07-30T20:55:34" not in ci_alert.normalise("failed at 2026-07-30T20:55:34Z")
        assert "a1b2c3d4e5f6" not in ci_alert.normalise("bad sha a1b2c3d4e5f6")
        assert "/home/runner/work" not in ci_alert.normalise(
            "FileNotFoundError: /home/runner/work/tit/tit/data/x.json")

    def test_resolve_scope_is_the_prefix_of_the_key_it_must_clear(self):
        """The load-bearing coupling. If these two drift, every failure alarm
        becomes permanent and every recovery email is lost — silently, because
        both halves keep returning 200."""
        key = self._key("AssertionError: boom", workflow="collect", branch="main")
        scope = f"{ci_alert.slug('collect')}:{ci_alert.slug('main', 32)}"
        assert key.startswith(scope + ":"), \
            f"{key!r} would never be cleared by resolve_scope {scope!r}"


class TestBehaviour:
    def _run(self, argv, monkeypatch, **env):
        for k in ("WP_SITE_URL", "WP_API_KEY", "ALERT_ENVELOPE"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ci_alert.main(argv)
        return code, buf.getvalue()

    def test_cancelled_is_not_alertable(self, monkeypatch):
        """This repo cancels runs BY DESIGN — the talent-collect lock evicts a
        pending run whenever another is dispatched past it. Alerting on those
        would fire constantly and bury the failures that matter. Evictions have
        their own detection in ops_status [2b], which can tell them apart."""
        monkeypatch.setattr(ci_alert, "fetch_annotations",
                            lambda *a, **k: "The operation was canceled.")
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "cancelled"],
            monkeypatch)
        assert code == 0 and "not alertable" in out

    def test_a_failure_with_no_credentials_is_loud_not_silent(self, monkeypatch):
        """A quiet 'no key so I did nothing' is the same class of lie as a green
        drain tick that dispatched nothing for eleven consecutive ticks."""
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "failure"],
            monkeypatch)
        assert code == 1, "a missing key must redden the alerter's own run"
        assert "::error::" in out

    def test_an_undeliverable_alert_is_held_and_does_not_redden_the_run(
            self, monkeypatch, tmp_path):
        """THE 2026-07-31 DEFECT, in one assertion.

        Bluehost 504'd for seven minutes. enrich failed, drain-writers correctly
        went red, and this alerter then failed four times reporting them —
        because /alert is a route on the host it was reporting about. Exiting 1
        there turned one outage into four EXTRA red runs, each of which said
        'the alerter is broken' when the alerter was working and the host was
        down.

        A held alert is a kept promise. It exits 0, and it says so loudly.
        """
        envelope = tmp_path / "held.json"
        calls = []
        monkeypatch.setattr(
            ci_alert, "post_alert",
            lambda s, k, p, **kw: (calls.append(p), (False, "HTTP 504 from /alert", True))[1])
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "failure",
             "--envelope", str(envelope)],
            monkeypatch, WP_SITE_URL="https://example.invalid", WP_API_KEY="k")

        assert code == 0, "an outage must not manufacture a red run of its own"
        assert "::warning::" in out and "HELD" in out
        assert "dedupe_key" in calls[0]

        held = json.loads(envelope.read_text())
        assert held["key"] == calls[0]["dedupe_key"]
        assert held["payload"]["subject"].startswith("CI RED:")

    def test_an_undeliverable_alert_with_nowhere_to_go_is_red(self, monkeypatch):
        """The one state that still deserves a red run: the alert could not be
        delivered AND could not be held, so nobody will ever be told. Degrading
        honestly means being loud here and nowhere else."""
        monkeypatch.setattr(
            ci_alert, "post_alert",
            lambda s, k, p, **kw: (False, "HTTP 504 from /alert", True))
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "failure"],
            monkeypatch, WP_SITE_URL="https://example.invalid", WP_API_KEY="k")
        assert code == 1
        assert "::error::" in out and "nobody will be told" in out.lower()

    def test_a_settled_refusal_is_held_but_said_out_loud(self, monkeypatch, tmp_path):
        """A 401 is not a bad night, it is a wrong key, and it will not fix
        itself while the queue quietly grows."""
        envelope = tmp_path / "held.json"
        monkeypatch.setattr(
            ci_alert, "post_alert",
            lambda s, k, p, **kw: (False, "HTTP 401 from /alert", False))
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "failure",
             "--envelope", str(envelope)],
            monkeypatch, WP_SITE_URL="https://example.invalid", WP_API_KEY="k")
        assert code == 0, "still held, still not a red run"
        assert "::error::" in out, "a settled refusal must not be whispered"

    def test_transient_failures_are_retried_inside_the_run(self, monkeypatch):
        """A single bad response from a shared host is not an outage. Retrying
        is the cheap half of the fix; the outbox is the half that survives one."""
        answers = [(False, "HTTP 503", True), (False, "HTTP 503", True),
                   (True, "emailed the owner", False)]
        monkeypatch.setattr(ci_alert, "_post_once",
                            lambda *a, **k: answers.pop(0))
        ok, note, _ = ci_alert.post_alert("https://x.invalid", "k", {},
                                          sleep=lambda _s: None)
        assert ok and not answers, "it stopped retrying before it succeeded"

    def test_a_settled_refusal_is_not_retried(self, monkeypatch):
        tries = []
        monkeypatch.setattr(
            ci_alert, "_post_once",
            lambda *a, **k: (tries.append(1), (False, "HTTP 404", False))[1])
        ci_alert.post_alert("https://x.invalid", "k", {}, sleep=lambda _s: None)
        assert len(tries) == 1, "retrying a settled no only makes the run longer"

    def test_success_posts_a_resolve_and_never_a_dedupe_key(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            ci_alert, "post_alert",
            lambda s, k, p, **kw: (calls.append(p), (True, "emailed", False))[1])
        code, _ = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "success",
             "--branch", "main"],
            monkeypatch, WP_SITE_URL="https://example.invalid", WP_API_KEY="k")
        assert code == 0
        assert calls[0]["resolve_scope"] == "collect:main"
        assert "dedupe_key" not in calls[0]


class TestSelfTimeoutIsNotAnEviction:
    """A job killed by its own `timeout-minutes` concludes `cancelled`, not
    `timed_out`. Until this, that was silent in BOTH channels: ci_alert filtered
    every `cancelled`, and ci_status only reports a cancelled run that created
    ZERO jobs (the eviction signature) — a self-timeout creates jobs.

    Both directions are pinned here on purpose. The quiet on evictions is
    load-bearing (this repo evicts by design and an undeduped alarm is a
    filtered alarm), so a fix that bought the timeout class by mailing on every
    cancellation would be a regression wearing a fix's clothes.
    """

    def _run(self, argv, monkeypatch, **env):
        for k in ("WP_SITE_URL", "WP_API_KEY", "ALERT_ENVELOPE"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ci_alert.main(argv)
        return code, buf.getvalue()

    def test_an_eviction_is_still_silent(self, monkeypatch):
        """The thirteen writer runs evicted on 2026-07-28/29 would each have
        mailed. ops_status.py [2b] is what reports those, and it can tell an
        eviction from a failure; this cannot, and must not pretend to."""
        monkeypatch.setattr(ci_alert, "fetch_annotations",
                            lambda *a, **k: "The operation was canceled.")
        code, out = self._run(
            ["--run-id", "1", "--workflow", "correct-form-d",
             "--conclusion", "cancelled"], monkeypatch)
        assert code == 0
        assert "outside the job" in out
        assert "SELF-TIMEOUT" not in out

    def test_a_cancelled_run_with_no_annotations_at_all_stays_silent(self, monkeypatch):
        """Fail QUIET here, not open. `gh` missing, unauthenticated, or a
        check-runs call that 403s must degrade to 'routine cancellation' — the
        alternative is mailing on every eviction the moment a token narrows."""
        monkeypatch.setattr(ci_alert, "fetch_annotations", lambda *a, **k: "")
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "cancelled"],
            monkeypatch)
        assert code == 0 and "SELF-TIMEOUT" not in out

    def test_a_self_timeout_alerts_even_though_it_reads_as_cancelled(self, monkeypatch):
        """THE HOLE THIS CLOSES. PR #32 gave collect, collect-press,
        deploy-plugin, retract and tests a `timeout-minutes` they never had, and
        those ceilings are generous precisely because hitting one was quiet. In
        the sibling repo an archive job died at its 20-minute ceiling on every
        run it ever had, never once completing, and no email ever fired."""
        monkeypatch.setattr(
            ci_alert, "fetch_annotations",
            lambda *a, **k: ("The job has exceeded the maximum execution time "
                             "of 45m0s\nThe operation was canceled."))
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "cancelled",
             "--dry-run"], monkeypatch)
        assert code == 0
        assert "CI SELF-TIMEOUT" in out
        assert "45m0s" in out and "cancelled ITSELF" in out

    def test_the_marker_is_read_from_annotations_and_not_from_the_log(self):
        """It is NOT in the log. A self-killed job's log ends on a bare
        '##[error]The operation was canceled.', character-for-character what an
        evicted run prints, and `--log-failed` returns nothing at all because a
        cancelled run has no failed STEP."""
        assert ci_alert.self_timeout_cause(
            "2026-08-12T09:26:01.3465610Z ##[error]The operation was canceled.") is None
        assert ci_alert.self_timeout_cause(
            "The job has exceeded the maximum execution time of 20m0s") is not None
        assert ci_alert.self_timeout_cause("") is None
        assert ci_alert.self_timeout_cause(None) is None

    def test_the_self_timeout_path_never_reads_the_failed_log(self, monkeypatch):
        """A cancelled run has no failed step, so `gh run view --log-failed`
        returns nothing and costs a 180-second subprocess to say so."""
        monkeypatch.setattr(
            ci_alert, "fetch_annotations",
            lambda *a, **k: "The job has exceeded the maximum execution time of 20m0s")
        monkeypatch.setattr(ci_alert, "fetch_failed_log", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the self-timeout path must not read the failed log")))
        code, _ = self._run(
            ["--run-id", "1", "--workflow", "tests", "--conclusion", "cancelled",
             "--dry-run"], monkeypatch)
        assert code == 0

    def test_a_self_timeout_clears_on_the_same_workflows_green_run(self):
        """The scope must not fork by class, or a workflow that starts passing
        again leaves its self-timeout alert open forever — the permanently red
        alarm this whole module exists to abolish."""
        _s, _b, key = ci_alert.build_alert(
            repo="dk-forge/talent-intelligence-tracker", workflow="collect",
            branch="main", event="schedule", run_url="",
            cause="the job cancelled ITSELF on timeout-minutes", context=[],
            label="CI SELF-TIMEOUT")
        scope = f"{ci_alert.slug('collect')}:{ci_alert.slug('main', 32)}"
        assert key.startswith(scope + ":"), \
            f"{key!r} would never be cleared by resolve_scope {scope!r}"

    def test_the_listener_admits_cancelled_so_the_script_can_judge_it(self):
        """The YAML filter and the script have to agree. A `cancelled` run
        screened out in YAML never reaches the annotation check at all, and the
        class goes back to being invisible with the code to handle it still
        sitting here, tested and never run."""
        yml = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
               / "ci-alert.yml").read_text()
        assert '"cancelled"' in yml, \
            "the listener screens cancelled out before ci_alert can judge it"
        assert "checks: read" in yml, (
            "annotations need checks:read; without it the self-timeout marker "
            "cannot be read and every cancellation reads as routine")


class TestSharedWithCiStatus:
    """ci_status.py surfaces the same failure a session reads and the same one
    ci_alert.py mails. One extractor, so the dashboard and the email can never
    describe a single failure two different ways — the same discipline that put
    the live invariants in one registry in the sibling repo."""

    def test_ci_status_uses_this_extractor(self):
        import ci_status

        assert hasattr(ci_status, "_cause_line")
        src = Path(ci_status.__file__).read_text()
        assert "ci_alert" in src and "extract_cause" in src, \
            "ci_status must share the extractor, not grow a second one"

    def test_a_cause_that_cannot_be_read_never_costs_us_the_red(self, monkeypatch):
        """Best-effort by design: the RED is the finding that matters, and a
        missing log line must degrade the detail, never suppress the alarm."""
        import ci_status

        assert ci_status._cause_line("dk-forge/nope", None) == ""

        def boom(*a, **k):
            raise OSError("gh is not installed here")

        monkeypatch.setattr("subprocess.run", boom)
        assert ci_status._cause_line("dk-forge/nope", 1) == ""


class TestGreenDrainTickIsNotARecovery:
    """writer_queue.select_red (2026-08-02) makes drain-writers red ONCE per
    needs-human item: the tick after the red is deliberately green with the
    item still waiting. A RECOVERED mail off that green would tell the owner a
    failure a human has never touched is fixed. The queue file is the
    authority, so the resolve is gated on it — and ONLY for drain-writers;
    every other workflow's green still resolves immediately."""

    def _run(self, argv, monkeypatch):
        for k in ("WP_SITE_URL", "WP_API_KEY", "ALERT_ENVELOPE"):
            monkeypatch.delenv(k, raising=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ci_alert.main(argv)
        return code, buf.getvalue()

    def _queue_with(self, problems):
        import writer_queue as wq
        queue = wq.empty_queue()
        if problems:
            ticket = wq.enqueue(queue, "correct-form-d.yml", {"dry_run": "false"})
            ticket["state"] = "failed"
        return queue

    def test_a_green_tick_with_items_still_waiting_sends_no_recovered(self, monkeypatch):
        import writer_queue as wq
        monkeypatch.setattr(wq, "load", lambda *a, **k: self._queue_with(True))
        code, out = self._run(
            ["--run-id", "1", "--workflow", "drain-writers",
             "--conclusion", "success", "--dry-run"], monkeypatch)
        assert code == 0
        assert "skip resolve" in out
        assert "[dry-run] resolve" not in out, (
            "the resolve must not be built at all while the queue holds "
            "unhandled problems — a RECOVERED here is a false all-clear")

    def test_a_green_tick_over_a_clear_queue_resolves_normally(self, monkeypatch):
        import writer_queue as wq
        monkeypatch.setattr(wq, "load", lambda *a, **k: self._queue_with(False))
        code, out = self._run(
            ["--run-id", "1", "--workflow", "drain-writers",
             "--conclusion", "success", "--dry-run"], monkeypatch)
        assert code == 0 and "[dry-run] resolve" in out

    def test_other_workflows_greens_still_resolve_even_with_a_dirty_queue(self, monkeypatch):
        import writer_queue as wq
        monkeypatch.setattr(wq, "load", lambda *a, **k: self._queue_with(True))
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect",
             "--conclusion", "success", "--dry-run"], monkeypatch)
        assert code == 0 and "[dry-run] resolve" in out

    def test_an_unreadable_queue_never_eats_a_real_recovery(self, monkeypatch):
        """Fail open: absence of the queue signal must degrade to the old
        behaviour (resolve), never to a permanently open alert."""
        import writer_queue as wq
        def boom(*a, **k):
            raise OSError("no queue file on this runner")
        monkeypatch.setattr(wq, "load", boom)
        code, out = self._run(
            ["--run-id", "1", "--workflow", "drain-writers",
             "--conclusion", "success", "--dry-run"], monkeypatch)
        assert code == 0 and "[dry-run] resolve" in out


# ---------------------------------------------------------------------------
# A log marker is never a cause.
#
# REAL LINES from `collect` run 32307688627 (main, 2026-08-19T22:48Z), shaped as
# `gh run view --log-failed` emits them. That run mailed an alert whose entire
# WHAT FAILED section read "##[endgroup]": no bucket matched the log, so the
# fallback took the last non-empty body line, and the last non-empty body line
# was the fold marker that closes the step.
# ---------------------------------------------------------------------------

ENDGROUP_RUN = _log(
    "[sec_form_d] searching SEC filings",
    "[sec_form_d] 0 fetched, 0 filtered out, 0 going to the classifier",
    "[sec_form_d] found=0 stored=0 duplicate=0 rejected=0 deferred=0",
    "##[warning][guardrail] amount/3a2a9b08 Nvidia $150,000,000,000 - held back, never published, red in 139h",
    "##[warning][guardrail] amount/26c3c9ab Lovable $13,300,000,000 - held back, never published, red in 91h",
    "[guardrails] Answer them:  python3 guardrails.py",
    "",
    "[publish] sent=0 stored=0 duplicate=0 errors=0",
    "##[endgroup]",
    "##[error]Process completed with exit code 1.",
    "##[group]Run git config user.name  'talent-intel-bot'",
    "Cleaning up orphan processes",
)

#: The degenerate case: the failing step printed nothing but markers. There is
#: genuinely no cause to be had, and the only honest answer is to say so.
MARKERS_ONLY = _log(
    "##[group]Run python3 run_collect.py",
    "##[endgroup]",
    "   ",
    "##[section]Finishing",
    "##[error]Process completed with exit code 1.",
    "Cleaning up orphan processes",
)


class TestAMarkerIsNeverACause:

    def test_the_endgroup_run_no_longer_reports_a_log_marker(self):
        """The regression, on the log that shipped it."""
        cause, context = ci_alert.extract_cause(ENDGROUP_RUN)
        assert "##[" not in cause
        assert ci_alert.is_cause_line(cause)
        # The documented fallback is unchanged: the last REAL output line still
        # beats "a job failed". Only formatting was ever the defect.
        assert cause == "[publish] sent=0 stored=0 duplicate=0 errors=0"
        assert not any("##[" in line for line in context)

    def test_a_step_that_printed_only_markers_extracts_nothing(self):
        cause, context = ci_alert.extract_cause(MARKERS_ONLY)
        assert cause == ""
        assert context == []

    @pytest.mark.parametrize("line", [
        "##[endgroup]", "##[group]", "##[section]", "  ##[endgroup]  ",
        # a fold's NAME says what was about to happen, not what broke
        "##[group]Run python3 run_collect.py", "##[section]Finishing",
        "##[debug]", "##[error]", "", "   ", "\t",
    ])
    def test_markers_and_whitespace_are_refused(self, line):
        assert not ci_alert.is_cause_line(line)

    @pytest.mark.parametrize("line", [
        "##[error]AssertionError: 3 != 4",
        "##[warning][guardrail] amount/3a2a9b08 Nvidia - red in 139h",
        "[publish] sent=0 stored=0",
    ])
    def test_a_marker_that_carries_a_message_is_still_a_cause(self, line):
        assert ci_alert.is_cause_line(line)

    def test_no_cause_mails_the_truth_instead_of_the_marker(self):
        """'I could not read a cause, here is the run' is actionable.
        '##[endgroup]' is not."""
        cause, context = ci_alert.extract_cause(MARKERS_ONLY)
        subject, body, key = ci_alert.build_alert(
            repo="dk-forge/talent-intelligence-tracker", workflow="collect",
            branch="main", event="schedule",
            run_url="https://github.com/dk-forge/talent-intelligence-tracker/actions/runs/32307688627",
            cause=cause, context=context)
        assert "##[" not in subject
        assert "##[" not in body
        assert "no error line could be extracted" in subject
        assert "actions/runs/32307688627" in body
        assert "could be read out of this run's log" in body

    def test_a_marker_cause_can_no_longer_become_a_dedup_key(self):
        """The half of this defect that made no noise.

        `cause` is what build_alert fingerprints. When it was "##[endgroup]",
        every unrelated no-cause failure of one workflow+branch hashed to the
        SAME key, and the second was suppressed as a duplicate of the first.
        One email per cause quietly became one email per WORKFLOW. The two real
        failures below share nothing but the trailing marker.
        """
        def key_for(*content):
            cause, context = ci_alert.extract_cause(_log(*content))
            return ci_alert.build_alert(
                repo="dk-forge/talent-intelligence-tracker", workflow="collect",
                branch="main", event="schedule", run_url="u",
                cause=cause, context=context)[2]

        a = key_for("[publish] sent=0 stored=0 duplicate=0 errors=0",
                    "##[endgroup]", "##[error]Process completed with exit code 1.")
        b = key_for("[sec_edgar] DEGRADED: 11 candidates, none stored",
                    "##[endgroup]", "##[error]Process completed with exit code 1.")
        assert a != b, "two different failures collapsed onto one key"
