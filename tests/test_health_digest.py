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
    """The leash is the 2x/day cadence (12h) plus queue slack. A missed run is
    a coverage hole, and the old 36h let a collector skip three runs before
    anything said so."""

    def test_just_inside_the_window_is_not_stale(self):
        buckets = health_digest.classify({"google_news": entry(13.9)}, NOW)
        self.assertEqual(buckets["stale"], [])
        self.assertEqual(buckets["ok"], ["google_news"])

    def test_just_outside_the_window_is_stale(self):
        buckets = health_digest.classify({"google_news": entry(14.1)}, NOW)
        self.assertEqual([n for n, _, _ in buckets["stale"]], ["google_news"])

    def test_exactly_at_the_window_is_not_stale(self):
        buckets = health_digest.classify({"google_news": entry(14)}, NOW)
        self.assertEqual(buckets["stale"], [])

    def test_staleness_outranks_a_healthy_status(self):
        """The blind spot this whole script exists to close: a collector whose
        last run said ok three weeks ago is a stopped collector."""
        buckets = health_digest.classify({"google_news": entry(24 * 21, "ok")}, NOW)
        self.assertEqual([n for n, _, _ in buckets["stale"]], ["google_news"])
        self.assertEqual(buckets["ok"], [])

    def test_swept_collectors_share_the_cron_leash(self):
        """gdelt and the SEC pair used to be dispatch-only with a 14-day
        leash. The collect.yml schedule sweeps them now, so a five-day
        silence from any of them means the sweep is broken, not that nobody
        remembered to dispatch them."""
        ledger = {"gdelt": entry(24 * 5), "sec_edgar": entry(24 * 5),
                  "sec_form_d": entry(24 * 5)}
        buckets = health_digest.classify(ledger, NOW)
        self.assertEqual(sorted(n for n, _, _ in buckets["stale"]),
                         ["gdelt", "sec_edgar", "sec_form_d"])

    def test_quiet_by_design_sources_keep_a_long_leash(self):
        """The quarterly bulk feed is quiet on purpose: SEC publishes the Form D
        data sets four times a year. A short leash on it is how a digest trains
        its reader to ignore it."""
        ledger = {"sec_form_d_bulk": entry(24 * 30), "press_archive": entry(24 * 30)}
        buckets = health_digest.classify(ledger, NOW)
        self.assertEqual(buckets["stale"], [])
        self.assertEqual(sorted(buckets["ok"]), ["press_archive", "sec_form_d_bulk"])

    def test_the_tripwire_is_leashed_to_the_cadence_it_actually_runs_at(self):
        """It was in the test above, as "the dormant tripwire", for three days
        after it was armed.

        Arming it on 2026-07-30 meant DELETING the cron from tripwire.yml and
        putting the Mon+Thu slot in schedule-link-hygiene.yml, because a lock
        member may not carry its own schedule. The instruction left behind in
        staleness.py said to tighten the leash "the day the schedule in
        tripwire.yml is uncommented" — a trigger that arming removes, so it
        could never fire. A live twice-weekly collector kept a 100-day leash and
        would have reported `ok` from a Monday breakage until November.
        """
        buckets = health_digest.classify({"tripwire": entry(24 * 30)}, NOW)
        self.assertEqual([n for n, _, _ in buckets["stale"]], ["tripwire"])
        buckets = health_digest.classify({"tripwire": entry(24 * 5)}, NOW)
        self.assertEqual(buckets["ok"], ["tripwire"], (
            "and it must not go the other way: the slot writes a ticket that "
            "waits behind whatever holds the writer lock, so five days of "
            "silence at a 3.5-day cadence is a queue, not a breakage"))

    def test_the_monthly_structured_sources_are_not_flagged_mid_cycle(self):
        """sec_execcomp runs on the 5th and uk_paygap on the 6th of each
        month. The old 14-day default marked both stale every month, days
        before their next scheduled run."""
        ledger = {"sec_execcomp": entry(24 * 20), "uk_paygap": entry(24 * 20)}
        buckets = health_digest.classify(ledger, NOW)
        self.assertEqual(buckets["stale"], [])

    def test_ops_status_reads_the_same_map_this_digest_does(self):
        """Two tools judging staleness from two maps disagreed about every
        collector off the 2x/day cron: ops_status applied a global 36h while
        this digest gave the same collector 336h. One shared, stdlib-only
        module is the fix, and this pins both halves of it: the digest's map
        IS the shared one, and ops_status imports nothing beyond the standard
        library on the way to it (it must run before any venv exists)."""
        import staleness

        self.assertIs(health_digest.MAX_AGE_HOURS, staleness.MAX_AGE_HOURS)

        import ast
        from pathlib import Path

        root = Path(health_digest.__file__).parent
        for module in ("ops_status.py", "staleness.py"):
            tree = ast.parse((root / module).read_text())
            top_level_imports = {
                name.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for name in node.names
            } | {
                (node.module or "").split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level == 0
            }
            third_party = top_level_imports - {
                "sqlite3", "sys", "datetime", "pathlib", "annotations",
                "__future__", "re", "unicodedata", "json", "subprocess",
                "csv", "dataclasses", "math",
                # ops_status reads spend.py's monthly allowance by PARSING it
                # rather than importing it, because spend.py imports requests
                # and this promise is the reason it may not.
                "ast",
                # Repo-local, themselves dependency-free at import time.
                "source_registry", "staleness", "writer_queue",
                "backfill_slices", "pipeline",
                # analysis.landmarks (the landmark guard) and the matcher it
                # borrows from analysis.recall are stdlib-only by design and
                # pinned as such by tests/test_landmarks.py, which is what
                # keeps this entry honest. ops_status recomputes the landmark
                # check offline at session start, so it has to be importable
                # before any venv exists.
                "analysis",
            }
            self.assertFalse(third_party,
                             f"{module} imports {third_party} at module scope")

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

    def test_a_quarantined_row_leads_the_subject_and_carries_its_instruction(self):
        """A quarantine outranks a stale scraper: one costs coverage, the other
        means a figure nobody has checked is one decision away from going out."""
        buckets = health_digest.classify({"google_news": entry(24 * 5)}, NOW)
        rows = [{"check_name": "amount", "subject": "abc",
                 "label": "X.AI Holdings Corp. $16,599,961,030",
                 "value": 16_599_961_030.0, "already_live": False,
                 "age_hours": 4.0, "grace_hours": 192}]
        subject, body = health_digest.build_email(
            buckets, False, 24 * 5, None, "local", rows)
        self.assertIn("quarantined", subject.lower())
        self.assertIn("X.AI Holdings Corp.", body)
        self.assertIn("guardrails.py", body)
        self.assertIn("Nothing was dropped", body)
        self.assertIn("Collection is NOT stopped", body)

    def test_an_already_live_row_outranks_a_merely_held_one(self):
        """A held row is the guard working. A live one is a wrong figure on the
        page that only a retraction removes, so it leads."""
        buckets = health_digest.classify({"google_news": entry(2)}, NOW)
        rows = [
            {"check_name": "amount", "subject": "held", "label": "Held Co",
             "value": 1.0, "already_live": False, "age_hours": 1.0,
             "grace_hours": 192},
            {"check_name": "vehicle_name", "subject": "live", "label": "Live Co",
             "value": 2.0, "already_live": True, "age_hours": 1.0,
             "grace_hours": 72},
        ]
        subject, body = health_digest.build_email(
            buckets, False, 2, None, "local", rows)
        self.assertIn("already live", subject.lower())
        self.assertIn("ALREADY LIVE", body)
        self.assertIn("retraction", body)

    def test_an_overdue_row_says_the_runs_are_now_red(self):
        buckets = health_digest.classify({"google_news": entry(2)}, NOW)
        rows = [{"check_name": "amount", "subject": "old", "label": "Old Co",
                 "value": 1.0, "already_live": False, "age_hours": 400.0,
                 "grace_hours": 192}]
        subject, body = health_digest.build_email(
            buckets, False, 2, None, "local", rows)
        self.assertIn("grace window", subject.lower())
        self.assertIn("exiting non-zero", body)
        self.assertIn("OVERDUE", body)

    def test_a_quarantine_inside_its_window_says_the_runs_are_still_green(self):
        buckets = health_digest.classify({"google_news": entry(2)}, NOW)
        rows = [{"check_name": "amount", "subject": "new", "label": "New Co",
                 "value": 1.0, "already_live": False, "age_hours": 1.0,
                 "grace_hours": 192}]
        _, body = health_digest.build_email(
            buckets, False, 2, None, "local", rows)
        self.assertIn("still green", body)

    def test_a_digest_with_no_guardrail_findings_says_nothing_about_them(self):
        buckets = health_digest.classify({"google_news": entry(24 * 5)}, NOW)
        _, body = health_digest.build_email(
            buckets, False, 24 * 5, None, "local", [])
        self.assertNotIn("PUBLISH GUARDRAILS", body)

    def test_guardrails_are_read_locally_even_when_the_ledger_is_live(self):
        """They run BEFORE publishing, so a blocking finding is by definition
        one the site has never been told about."""
        import inspect
        source = inspect.getsource(health_digest.main)
        self.assertIn("read_guardrails()", source)

    def test_an_unreadable_guardrail_ledger_is_never_fatal(self):
        from pathlib import Path
        self.assertEqual(health_digest.read_guardrails(Path("/nope/none.db")), [])

    def test_no_em_dashes_in_owner_facing_copy(self):
        buckets = health_digest.classify(
            {"google_news": entry(24 * 5, "degraded", "zero items")}, NOW)
        subject, body = health_digest.build_email(
            buckets, True, 24 * 5, {"month": "2026-07", "spent": 1.0,
                                    "allowance": 10.0, "at_ceiling": False},
            "live /source-health")
        self.assertNotIn("—", subject + body)


def coverage(archived=71, in_scope=656, queue=0, never=585, days_ago=1):
    """A link-health reading, `days_ago` days since its newest snapshot."""
    return {
        "collectors": ["national_press"],
        "in_scope": in_scope, "archived": archived, "unavailable": 0,
        "capture_queue": queue, "never_probed": never,
        "pct": round(100.0 * archived / in_scope, 1) if in_scope else 0.0,
        "newest_snapshot": (NOW - timedelta(days=days_ago)).isoformat(),
    }


class TestArchiveCoverageIsReported(unittest.TestCase):
    """The failure this closes is not a wrong number, it is no number.

    On 2026-07-30 the archiver spent a day running green and recording nothing,
    and what hid it was that nothing anybody read reported on it at all.
    """

    def test_a_stalled_archiver_is_not_stale_degraded_or_expensive(self):
        """Which is exactly why it needs its own check.

        Every other signal in this digest reads healthy while archiving has
        stopped producing: the job is not stale (it ran), not degraded (it
        succeeded), and costs nothing.
        """
        self.assertTrue(health_digest.archiving_stalled(
            coverage(days_ago=health_digest.ARCHIVE_STALL_DAYS + 1), NOW))

    def test_a_recent_snapshot_is_not_a_stall(self):
        self.assertFalse(health_digest.archiving_stalled(coverage(days_ago=1), NOW))

    def test_nothing_left_to_do_is_not_a_stall(self):
        """A finished archiver is quiet for the same reason a broken one is.

        Crying wolf here would be worse than silence: an owner who learns to
        ignore this line ignores it on the week it means something.
        """
        self.assertFalse(health_digest.archiving_stalled(
            coverage(queue=0, never=0, days_ago=90), NOW))

    def test_never_having_recorded_a_snapshot_is_a_stall(self):
        reading = coverage()
        reading["newest_snapshot"] = None
        self.assertTrue(health_digest.archiving_stalled(reading, NOW))

    def test_coverage_is_reported_even_when_nothing_is_wrong(self):
        """A metric that appears only once it is bad cannot show a slow slide."""
        buckets = health_digest.classify({"google_news": entry(6)}, NOW)
        _, body = health_digest.build_email(buckets, False, 6, None, "local",
                                            [], coverage())
        self.assertIn("SOURCE LINKS", body)
        self.assertIn("71 of 656", body)

    def test_a_stall_names_itself_in_the_subject(self):
        buckets = health_digest.classify({"google_news": entry(6)}, NOW)
        subject, body = health_digest.build_email(
            buckets, False, 6, None, "local", [],
            coverage(days_ago=99), archive_stalled=True)
        self.assertIn("archiving", subject.lower())
        self.assertIn("STALLED", body)
        # The paste-ready instruction has to send the owner at the RUNS, not at
        # the script. The script was fine every time this has fired; what broke
        # was a dispatch that carried the dry_run default.
        self.assertIn("dry_run=false", body)
        self.assertNotIn("—", subject + body)

    def test_an_unreadable_link_ledger_is_never_fatal(self):
        from pathlib import Path
        self.assertIsNone(health_digest.read_link_health(Path("/nope/none.db")))
        self.assertFalse(health_digest.archiving_stalled(None, NOW))


class TestDelivery(unittest.TestCase):
    def test_missing_configuration_is_reported_not_claimed_as_sent(self):
        sent, note = health_digest.send_alert("s", "b", site="", key="")
        self.assertFalse(sent)
        self.assertIn("WP_SITE_URL", note)
        self.assertIn("WP_API_KEY", note)


if __name__ == "__main__":
    unittest.main()
