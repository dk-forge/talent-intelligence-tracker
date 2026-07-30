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
        for k in ("WP_SITE_URL", "WP_API_KEY"):
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

    def test_a_failed_post_reddens_the_run(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ci_alert, "post_alert",
                            lambda s, k, p: (calls.append(p), (False, "HTTP 503"))[1])
        code, out = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "failure"],
            monkeypatch, WP_SITE_URL="https://example.invalid", WP_API_KEY="k")
        assert code == 1 and "::error::" in out
        assert "dedupe_key" in calls[0]

    def test_success_posts_a_resolve_and_never_a_dedupe_key(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ci_alert, "post_alert",
                            lambda s, k, p: (calls.append(p), (True, "emailed"))[1])
        code, _ = self._run(
            ["--run-id", "1", "--workflow", "collect", "--conclusion", "success",
             "--branch", "main"],
            monkeypatch, WP_SITE_URL="https://example.invalid", WP_API_KEY="k")
        assert code == 0
        assert calls[0]["resolve_scope"] == "collect:main"
        assert "dedupe_key" not in calls[0]


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
