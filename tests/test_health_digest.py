"""The alerting must not cry wolf, and must not stay quiet.

Both halves are load-bearing. An alert that fires on a healthy ledger gets
filtered within a month, and then the real one is invisible too. The sibling
tracker paid for this: its digest reported ITSELF as degraded whenever another
collector was degraded, which showed one problem as two and read as "the alert
is broken" when it had just worked.

Written with unittest so `python3 -m unittest discover -s tests` runs it with no
third-party packages installed. pytest collects it just the same.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import health_digest  # noqa: E402

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def entry(hours_ago, status="ok", detail=""):
    """A ledger row whose last run was `hours_ago` hours before NOW."""
    return {
        "status": status,
        "detail": detail,
        "run_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }


class TestClassification(unittest.TestCase):
    def test_a_fresh_ok_collector_is_ok(self):
        buckets = health_digest.classify({"google_news": entry(6)}, NOW)
        self.assertEqual(buckets["ok"], ["google_news"])
        self.assertEqual(buckets["degraded"], [])
        self.assertEqual(buckets["stale"], [])

    def test_degraded_is_reported_with_its_detail(self):
        buckets = health_digest.classify(
            {"google_news": entry(2, "degraded", "zero items found")}, NOW)
        self.assertEqual(buckets["degraded"],
                         [("google_news", "degraded", "zero items found")])
        self.assertEqual(buckets["ok"], [])

    def test_error_status_is_not_benign(self):
        buckets = health_digest.classify(
            {"google_news": entry(1, "error", "HTTP 500")}, NOW)
        self.assertEqual([n for n, _, _ in buckets["degraded"]], ["google_news"])


class TestStalenessBoundary(unittest.TestCase):
    """36 hours is two missed runs on the 2x/day cron. One missed run is not
    an incident; two means it stopped."""

    def test_just_inside_the_window_is_not_stale(self):
        buckets = health_digest.classify({"google_news": entry(35.9)}, NOW)
        self.assertEqual(buckets["stale"], [])
        self.assertEqual(buckets["ok"], ["google_news"])

    def test_just_outside_the_window_is_stale(self):
        buckets = health_digest.classify({"google_news": entry(36.1)}, NOW)
        self.assertEqual([n for n, _, _ in buckets["stale"]], ["google_news"])

    def test_exactly_at_the_window_is_not_stale(self):
        buckets = health_digest.classify({"google_news": entry(36)}, NOW)
        self.assertEqual(buckets["stale"], [])

    def test_staleness_outranks_a_healthy_status(self):
        """The blind spot this whole script exists to close: a collector whose
        last run said ok three weeks ago is a stopped collector."""
        buckets = health_digest.classify({"google_news": entry(24 * 21, "ok")}, NOW)
        self.assertEqual([n for n, _, _ in buckets["stale"]], ["google_news"])
        self.assertEqual(buckets["ok"], [])

    def test_dispatch_only_collectors_get_a_longer_leash(self):
        """gdelt and the SEC collectors are not on the cron. Holding them to
        the cron's 36 hours would mail the owner every single week."""
        ledger = {"gdelt": entry(24 * 5), "sec_edgar": entry(24 * 5)}
        buckets = health_digest.classify(ledger, NOW)
        self.assertEqual(buckets["stale"], [])
        self.assertEqual(sorted(buckets["ok"]), ["gdelt", "sec_edgar"])

    def test_an_unreadable_timestamp_is_surfaced_not_swallowed(self):
        buckets = health_digest.classify(
            {"google_news": {"status": "ok", "run_at": "not a date"}}, NOW)
        self.assertEqual(buckets["unknown_age"], [("google_news", "ok")])

    def test_a_checked_at_key_is_accepted_too(self):
        """The sibling's ledger names the field checked_at. Reading either
        keeps the classifier usable against both shapes."""
        ledger = {"google_news": {"status": "ok",
                                  "checked_at": (NOW - timedelta(hours=2)).isoformat()}}
        self.assertEqual(health_digest.classify(ledger, NOW)["ok"], ["google_news"])


class TestBenignStatuses(unittest.TestCase):
    def test_ok_retired_and_disabled_are_all_benign(self):
        self.assertEqual(health_digest.BENIGN_STATUSES,
                         {"ok", "retired", "disabled"})

    def test_a_retired_collector_is_never_stale(self):
        """It was stopped on purpose, so an ancient timestamp is correct."""
        buckets = health_digest.classify(
            {"gdelt": entry(24 * 400, "retired")}, NOW)
        self.assertEqual(buckets["stale"], [])
        self.assertEqual(buckets["ok"], ["gdelt"])

    def test_a_disabled_collector_is_never_stale(self):
        buckets = health_digest.classify(
            {"gdelt": entry(24 * 400, "disabled")}, NOW)
        self.assertEqual(buckets["stale"], [])

    def test_status_case_does_not_matter(self):
        buckets = health_digest.classify({"gdelt": entry(1, "OK")}, NOW)
        self.assertEqual(buckets["ok"], ["gdelt"])


class TestDoNotFlagYourself(unittest.TestCase):
    """The lesson the sibling paid for: the digest's status describes whether
    the DIGEST ran, not what it found."""

    def test_the_digest_is_never_classified(self):
        ledger = {
            "health_digest": entry(24 * 30, "degraded", "3 collectors stale"),
            "google_news": entry(2),
        }
        buckets = health_digest.classify(ledger, NOW)
        self.assertEqual(buckets["ok"], ["google_news"])
        self.assertEqual(buckets["stale"], [])
        self.assertEqual(buckets["degraded"], [])

    def test_the_digest_cannot_hold_the_pipeline_open(self):
        """A recent digest row must not disguise collectors that all stopped."""
        ledger = {
            "health_digest": entry(1, "ok"),
            "google_news": entry(24 * 10),
        }
        self.assertTrue(health_digest.pipeline_stopped(ledger, NOW))


class TestPipelineStopped(unittest.TestCase):
    """The loudest check: the failure mode that produces no red runs at all."""

    def test_a_recent_collect_means_running(self):
        ledger = {"google_news": entry(7), "gdelt": entry(24 * 9)}
        self.assertFalse(health_digest.pipeline_stopped(ledger, NOW))

    def test_nothing_within_36_hours_means_stopped(self):
        ledger = {"google_news": entry(40), "gdelt": entry(24 * 9)}
        self.assertTrue(health_digest.pipeline_stopped(ledger, NOW))

    def test_an_empty_ledger_is_stopped(self):
        self.assertTrue(health_digest.pipeline_stopped({}, NOW))

    def test_retired_collectors_cannot_prop_it_up(self):
        ledger = {"gdelt": entry(1, "retired"), "google_news": entry(24 * 4)}
        self.assertTrue(health_digest.pipeline_stopped(ledger, NOW))

    def test_newest_run_hours_reports_the_freshest(self):
        ledger = {"google_news": entry(30), "sec_edgar": entry(4)}
        self.assertAlmostEqual(
            health_digest.newest_run_hours(ledger, NOW), 4.0, places=3)


class TestEmail(unittest.TestCase):
    def test_the_body_carries_a_paste_ready_instruction(self):
        buckets = health_digest.classify({"google_news": entry(24 * 5)}, NOW)
        subject, body = health_digest.build_email(
            buckets, False, 24 * 5, None, "live /source-health")
        self.assertIn("google_news", subject)
        self.assertIn("open a Claude Code session", body)
        self.assertIn("collectors/", body)
        self.assertIn("run_collect.py --dry-run", body)

    def test_a_stopped_pipeline_leads_the_subject(self):
        buckets = health_digest.classify({"google_news": entry(24 * 5)}, NOW)
        subject, body = health_digest.build_email(
            buckets, True, 24 * 5, None, "live /source-health")
        self.assertIn("stopped", subject.lower())
        self.assertIn("collect.yml", body)

    def test_spend_at_the_ceiling_is_stated(self):
        buckets = health_digest.classify({"google_news": entry(2)}, NOW)
        spend = {"month": "2026-07", "spent": 9.4, "allowance": 10.0,
                 "at_ceiling": True}
        _, body = health_digest.build_email(buckets, False, 2, spend, "local")
        self.assertIn("9.40", body)
        self.assertIn("AT THE CEILING", body)

    def test_no_em_dashes_in_owner_facing_copy(self):
        buckets = health_digest.classify(
            {"google_news": entry(24 * 5, "degraded", "zero items")}, NOW)
        subject, body = health_digest.build_email(
            buckets, True, 24 * 5, {"month": "2026-07", "spent": 1.0,
                                    "allowance": 10.0, "at_ceiling": False},
            "live /source-health")
        self.assertNotIn("—", subject + body)


class TestDelivery(unittest.TestCase):
    def test_missing_configuration_is_reported_not_claimed_as_sent(self):
        sent, note = health_digest.send_alert("s", "b", site="", key="")
        self.assertFalse(sent)
        self.assertIn("WP_SITE_URL", note)
        self.assertIn("WP_API_KEY", note)


if __name__ == "__main__":
    unittest.main()
