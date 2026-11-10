"""A recall measurement asking for a fresh gold set is not a broken collector.

From 2026-09-07 to 2026-11-09 the weekly digest mailed the owner nine times
that `recall`, `recall_us` and `recall_eu` were "collector(s) that need
attention", and every one of those emails ended with the paste-ready line
"open its file in collectors/, check whether the upstream site or feed changed
shape, and fix the parser". There is no such file. The measurement was working
exactly as designed: `analysis/recall/series.goldset_is_due` had marked each
reference set as aged out, and assembling a replacement is a human step by
design (CLAUDE.md, "Coverage is measured, not asserted"). An instruction that
sends a session to a scraper that does not exist is how a true alarm reads as
noise for two months.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import health_digest  # noqa: E402

NOW = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)
DUE = ("held 46/169 (27.2%), clean 14/169 (8.3%) against 2026-07-v2 | new gold "
       "set due: the current set covers a window that closed 104 days ago, so "
       "it no longer describes what we reach today")


def entry(hours_ago, status="ok", detail=""):
    return {"status": status, "detail": detail,
            "run_at": (NOW - timedelta(hours=hours_ago)).isoformat()}


def ledger_all_due():
    return {
        "google_news": entry(2),
        "recall": entry(5, "degraded", DUE),
        "recall_us": entry(5, "degraded", DUE.replace("2026-07-v2", "2026-06-us-v1")),
        "recall_eu": entry(5, "degraded", DUE.replace("2026-07-v2", "2026-08-eu-v1")),
    }


class TestGoldSetDueIsItsOwnFinding(unittest.TestCase):
    def build(self, ledger):
        buckets = health_digest.classify(ledger, NOW)
        return health_digest.build_email(buckets, False, 2, None, "local")

    def test_the_subject_does_not_call_a_measurement_a_collector(self):
        subject, _ = self.build(ledger_all_due())
        self.assertNotIn("collector", subject.lower())
        self.assertIn("gold set", subject.lower())
        for name in ("recall", "recall_us", "recall_eu"):
            self.assertIn(name, subject)

    def test_the_instruction_never_points_at_a_scraper(self):
        _, body = self.build(ledger_all_due())
        self.assertNotIn("collectors/", body)
        self.assertNotIn("run_collect.py", body)
        self.assertNotIn("fix the parser", body)

    def test_the_instruction_names_each_family_and_the_human_step(self):
        _, body = self.build(ledger_all_due())
        for family in ("world", "us", "eu"):
            self.assertIn("measure_recall.py --family %s --check" % family, body)
        self.assertIn("suggested_window", body)
        # The two rules that stop a gold set flattering us.
        self.assertIn("without consulting our own database", body)
        self.assertIn("REQUIRED_SHAPE", body)

    def test_a_real_collector_still_leads_and_keeps_its_own_instruction(self):
        ledger = ledger_all_due()
        ledger["google_news"] = entry(2, "degraded", "0 items: feed moved")
        subject, body = self.build(ledger)
        self.assertIn("google_news", subject)
        self.assertIn("collector", subject.lower())
        # The scraper instruction names the scraper and nothing else.
        scraper_line = [l for l in body.splitlines() if "collectors/" in l]
        self.assertTrue(scraper_line)
        joined = " ".join(body.splitlines())
        start = joined.index("flagged these collectors:")
        flagged = joined[start:joined.index(".", start)]
        self.assertIn("google_news", flagged)
        self.assertNotIn("recall", flagged)
        # And the gold set request is still in the same email, not dropped.
        self.assertIn("measure_recall.py --family world --check", body)

    def test_a_failed_quality_gate_is_still_a_measurement_finding(self):
        ledger = {"google_news": entry(2),
                  "recall_us": entry(5, "degraded",
                                     "held 10/51 | GATE FAILED: floor: 19.6% < 30%")}
        subject, body = self.build(ledger)
        self.assertNotIn("collectors/", body)
        self.assertIn("recall_us", subject)
        self.assertIn("GATE FAILED", body)

    def test_every_recall_family_is_routed(self):
        from analysis.recall import family as families
        for fam in families.ALL:
            self.assertIn(fam.health_source, health_digest.RECALL_SOURCES)
            self.assertEqual(health_digest.RECALL_SOURCES[fam.health_source], fam.id)


if __name__ == "__main__":
    unittest.main()
