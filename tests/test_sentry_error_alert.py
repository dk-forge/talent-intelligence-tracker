"""Offline tests for sentry_error_alert.py.

No network anywhere: `fetch` is always stubbed, and the metered call is
always stubbed or exercised through `summarize()`'s own `call=` seam. Mail is
never actually sent - `opsmail.send_once` is patched so the real
`ops_notify` -> `ci_alert` -> `alert_state` chain runs for real (proving the
dedupe/remind/resolve wiring) without any HTTP request leaving the process.
`ALERT_STATE_COMMIT` is left unset, so `alert_state.claim()` writes straight
to a temporary file and never touches git.
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import opsmail  # noqa: E402
import sentry_error_alert as sea  # noqa: E402


def _issue(title="TypeError: cannot read property 'x' of undefined",
          culprit="app.views.dashboard", minutes_ago=5, count=3):
    seen = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago))
    return {
        "title": title,
        "culprit": culprit,
        "count": count,
        "firstSeen": seen.isoformat().replace("+00:00", "Z"),
        "permalink": "https://sentry.io/organizations/dk-forge/issues/123/",
        "metadata": {"value": title},
    }


class _Env(unittest.TestCase):
    """One temp dir per test: an alert ledger and a spend ledger, and a spy on
    the transport so nothing real is ever sent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "sentry_alert_state.json"
        self.spend_path = Path(self.tmp.name) / "sentry_ops_spend.json"
        self.sent = []

        def spy_send_once(subject, body, idempotency_key=""):
            self.sent.append((subject, body))
            return True, "emailed the owner", False

        patcher = mock.patch.object(opsmail, "send_once", spy_send_once)
        patcher.start()
        self.addCleanup(patcher.stop)

        env_patcher = mock.patch.dict(os.environ, {
            "RESEND_API_KEY": "fake-key-for-tests",
        }, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        self.addCleanup(os.environ.pop, "ALERT_STATE_PATH", None)

    def run_once(self, issues, *, ops_key="ops-key", call_stub=None, now=None):
        def fetch(token, org=None, project=None):
            return issues

        kwargs = dict(
            sentry_token="sentry-token",
            openrouter_ops_key=ops_key,
            state_path=self.state_path,
            spend_path=self.spend_path,
            fetch=fetch,
            now=now,
        )
        if call_stub is not None:
            with mock.patch.object(sea, "_one_request", call_stub):
                return sea.run(**kwargs)
        return sea.run(**kwargs)


class NoModelCallWhenNothingIsNew(_Env):

    def test_empty_sentry_result_calls_no_model_and_sends_no_mail(self):
        calls = []

        def stub(api_key, text):
            calls.append((api_key, text))
            return "summary", 0.001

        code = self.run_once([], call_stub=stub)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(self.sent, [])


class NoModelCallAndAPlainAlertWhenTheCapIsReached(_Env):

    def test_cap_reached_sends_without_a_summary_and_says_so(self):
        month = sea._month_key()
        sea.record_spend(self.spend_path, sea.MONTHLY_CAP_USD)  # exactly at cap
        self.assertGreaterEqual(sea.spent_this_month(sea.load_spend(self.spend_path),
                                                     month), sea.MONTHLY_CAP_USD)

        calls = []

        def stub(api_key, text):
            calls.append((api_key, text))
            return "should never be reached", 0.02

        code = self.run_once([_issue()], call_stub=stub)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [], "the model was called despite the cap")
        self.assertEqual(len(self.sent), 1)
        subject, body = self.sent[0]
        self.assertIn("No summary was attached", body)
        self.assertIn("cap is reached", body)


class ACallThatRaisesStillAlerts(_Env):

    def test_a_raising_call_still_sends_and_says_the_summary_is_missing(self):
        def boom(api_key, text):
            raise RuntimeError("upstream exploded")

        code = self.run_once([_issue()], call_stub=boom)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.sent), 1)
        subject, body = self.sent[0]
        self.assertIn("No summary was attached", body)
        self.assertIn("summary call raised", body)
        self.assertIn("RuntimeError", body)
        # And it did not spend anything on a call that never returned a cost.
        self.assertEqual(sea.spent_this_month(sea.load_spend(self.spend_path)), 0.0)


class RedactionRemovesSecretsBeforeThePrompt(unittest.TestCase):

    def test_removes_an_email_address(self):
        out = sea.redact("please contact dak@dakotta.com about this")
        self.assertNotIn("dak@dakotta.com", out)
        self.assertIn("<redacted-email>", out)

    def test_removes_a_bearer_token(self):
        out = sea.redact("failed request: Authorization: Bearer sk-or-v1-abcdefghijklmnop")
        self.assertNotIn("sk-or-v1-abcdefghijklmnop", out)

    def test_removes_an_api_key(self):
        out = sea.redact("config dump: api_key=sk-1234567890abcdef and done")
        self.assertNotIn("sk-1234567890abcdef", out)
        self.assertIn("<redacted-key>", out)

    def test_removes_an_authorization_header_without_the_word_bearer(self):
        out = sea.redact("X-Api-Key: abcdef0123456789")
        self.assertNotIn("abcdef0123456789", out)


class ARepeatCauseDoesNotAlertTwice(_Env):

    def test_same_cause_twice_sends_once(self):
        issue = _issue()

        def stub(api_key, text):
            return "a summary", 0.001

        code1 = self.run_once([issue], call_stub=stub)
        code2 = self.run_once([issue], call_stub=stub)
        self.assertEqual((code1, code2), (0, 0))
        self.assertEqual(len(self.sent), 1,
                         f"expected exactly one send, got {len(self.sent)}: "
                         f"{[s for s, _ in self.sent]}")

    def test_second_run_makes_no_second_model_call(self):
        issue = _issue()
        calls = []

        def stub(api_key, text):
            calls.append(1)
            return "a summary", 0.001

        self.run_once([issue], call_stub=stub)
        self.run_once([issue], call_stub=stub)
        self.assertEqual(len(calls), 1)


class TheGateIsMeteredAroundExactlyOneRequest(unittest.TestCase):
    """summarize() itself, isolated from the alert path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spend_path = Path(self.tmp.name) / "spend.json"

    def test_no_key_no_call(self):
        calls = []
        summary, note = sea.summarize(
            "text", api_key="", spend_path=self.spend_path,
            call=lambda k, t: calls.append(1) or ("s", 0.01))
        self.assertIsNone(summary)
        self.assertEqual(calls, [])
        self.assertIn("OPENROUTER_OPS_KEY", note)

    def test_success_records_spend(self):
        summary, note = sea.summarize(
            "text", api_key="k", spend_path=self.spend_path,
            call=lambda k, t: ("three lines", 0.0123))
        self.assertEqual(summary, "three lines")
        self.assertEqual(
            sea.spent_this_month(sea.load_spend(self.spend_path)), 0.0123)

    def test_cap_is_a_hard_ceiling_not_a_suggestion(self):
        sea.record_spend(self.spend_path, 2.999999)
        calls = []
        summary, note = sea.summarize(
            "text", api_key="k", spend_path=self.spend_path, cap=3.0,
            call=lambda k, t: calls.append(1) or ("s", 0.01))
        # Just under the cap: still allowed to spend once more.
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(summary)

        calls2 = []
        summary2, note2 = sea.summarize(
            "text", api_key="k", spend_path=self.spend_path, cap=3.0,
            call=lambda k, t: calls2.append(1) or ("s", 0.01))
        self.assertEqual(calls2, [])
        self.assertIsNone(summary2)
        self.assertIn("cap is reached", note2)

    def test_redact_is_applied_before_the_call_is_made(self):
        seen_text = {}

        def capture(api_key, text):
            seen_text["text"] = text
            return "s", 0.001

        sea.summarize("email me at dak@dakotta.com", api_key="k",
                     spend_path=self.spend_path, call=capture)
        self.assertNotIn("dak@dakotta.com", seen_text["text"])


class TheRequestGoesToTheRegionAndOrgThatActuallyExist(unittest.TestCase):
    """The 404 that made this job fail every hour for days was not a bug in
    any branch below - the request never reached an org. A Sentry
    organization answers only on its own region host, and this one is
    `dakotta-labs` on `de.sentry.io`. Both are pinned here because a wrong
    value produces a working process that reports nothing: `run()` catches
    the HTTPError, prints "could not read Sentry", exits 1, and no issue is
    ever seen. Change these only alongside a real org or region move."""

    def test_api_base_is_the_orgs_own_region(self):
        self.assertEqual(sea.SENTRY_API, "https://de.sentry.io/api/0")

    def test_default_org_is_the_one_that_exists(self):
        self.assertEqual(sea.DEFAULT_ORG, "dakotta-labs")

    def test_fetch_builds_the_region_scoped_project_issues_url(self):
        built = {}

        class _Resp:
            def read(self):
                return b"[]"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=30):
            built["url"] = req.full_url
            built["auth"] = req.get_header("Authorization")
            return _Resp()

        with mock.patch.object(sea.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(sea.fetch_new_issues("tok"), [])

        self.assertTrue(
            built["url"].startswith(
                "https://de.sentry.io/api/0/projects/dakotta-labs/"),
            built["url"])
        self.assertEqual(built["auth"], "Bearer tok")

    def test_the_base_stays_overridable_for_a_region_move(self):
        self.assertIn("SENTRY_API_BASE", Path(sea.__file__).read_text())

    def test_an_empty_repo_variable_falls_back_to_the_default(self):
        """An unset GitHub repository variable arrives as "", not absent.
        `.get(name, default)` would hand that empty string straight through
        and build a URL with no host in it."""
        src = Path(sea.__file__).read_text()
        self.assertNotIn('os.environ.get("SENTRY_API_BASE", ', src)
        self.assertNotIn('os.environ.get("SENTRY_ORG_SLUG", ', src)
        self.assertNotIn('"SENTRY_PROJECT_SLUG", ', src)

    def test_a_404_names_the_address_that_answered_it(self):
        """A 404 is UNKNOWN, never "nothing is throwing". It also must not
        read as a bug in the code below it, which is what a bare
        "HTTP Error 404: Not Found" did for days."""

        def fetch_404(token, *, org=None, project=None):
            raise sea.urllib.error.HTTPError(
                "https://de.sentry.io/api/0/projects/o/p/issues/",
                404, "Not Found", {}, None)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = sea.run(sentry_token="tok", openrouter_ops_key="",
                           fetch=fetch_404)
        out = buf.getvalue()

        self.assertEqual(code, 1)
        self.assertIn("UNKNOWN, not a pass", out)
        self.assertIn("de.sentry.io", out)
        self.assertIn("dakotta-labs", out)
        self.assertIn("SENTRY_PROJECT_SLUG", out)


if __name__ == "__main__":
    unittest.main()
