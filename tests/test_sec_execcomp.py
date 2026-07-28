"""The exec-comp frame must produce filings, not datasets, and must not go quiet.

Every assertion here is a failure this source could plausibly ship: a link to
the frames JSON instead of the proxy statement, a mis-tagged billion-dollar pay
packet on the page, a summary quoting a figure the source text does not carry,
or a throttled year exiting green with nothing in it.

Recorded fixture, never a live call: the frame is 168KB and SEC rate-limits.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from collectors import sec_execcomp
from pipeline import validate

FIXTURE = Path(__file__).parent / "fixtures" / "sec_execcomp_cy2025.json"


def _entries() -> list[dict]:
    return json.loads(FIXTURE.read_text())["data"]


def _rows() -> list[dict]:
    return [r for r in (sec_execcomp._row(e, 2025) for e in _entries()) if r]


class RowBuilding(unittest.TestCase):
    def test_the_fixture_carries_the_edge_cases_it_is_here_for(self):
        names = {e["entityName"] for e in _entries()}
        self.assertIn("BLUE DOLPHIN ENERGY COMPANY", names, "no zero-value row")
        self.assertIn("LeMaitre Vascular, Inc.", names, "no mis-tagged row")

    def test_a_zero_total_is_not_a_pay_packet(self):
        companies = {r["company"] for r in _rows()}
        self.assertNotIn("BLUE DOLPHIN ENERGY COMPANY", companies)

    def test_a_mis_tagged_billion_is_dropped_not_corrected(self):
        companies = {r["company"] for r in _rows()}
        # $3.78bn of PEO pay at a small-cap is a scale error in the filer's own
        # tag. Publishing it would be a wrong number; repairing it would be an
        # invented one.
        self.assertNotIn("LeMaitre Vascular, Inc.", companies)
        self.assertNotIn("U-HAUL HOLDING COMPANY", companies)

    def test_a_genuine_mega_grant_survives(self):
        # Welltower's $821m was real and widely reported. The implausibility
        # ceiling must not quietly delete the biggest true numbers.
        companies = {r["company"] for r in _rows()}
        self.assertIn("Welltower Inc", companies)

    def test_the_source_url_is_the_filing_and_never_the_dataset(self):
        for row in _rows():
            host = urlparse(row["source_url"]).hostname
            self.assertEqual(host, "www.sec.gov", row["source_url"])
            self.assertIn("/Archives/edgar/data/", row["source_url"])
            self.assertNotIn("data.sec.gov", row["source_url"])
            self.assertNotIn("frames", row["source_url"])

    def test_the_cik_travels_so_the_row_joins_to_an_employer(self):
        for row in _rows():
            self.assertTrue(row["cik"].isdigit(), row["cik"])

    def test_a_missing_or_malformed_accession_produces_no_url(self):
        self.assertIsNone(sec_execcomp.filing_url(320193, ""))
        self.assertIsNone(sec_execcomp.filing_url(320193, "not-an-accession"))
        self.assertIsNone(sec_execcomp.filing_url("", "0001193125-26-145567"))


class Validation(unittest.TestCase):
    def test_every_row_survives_the_credibility_gate(self):
        for row in _rows():
            signal = validate.build_signal(sec_execcomp.as_classified(row),
                                           row, sec_execcomp.COLLECTOR)
            self.assertEqual(signal.pillar, "rewards_comp")
            self.assertEqual(signal.signal_direction, "comp_shift")
            self.assertEqual(signal.country, "US")
            # A filing is a primary source, so these earn the top tier.
            self.assertEqual(signal.confidence, "verified")
            self.assertTrue(signal.cik)

    def test_the_summary_quotes_only_figures_the_source_text_carries(self):
        for row in _rows():
            classified = sec_execcomp.as_classified(row)
            # Raises Rejected if the summary invents a number.
            validate.assert_figures_are_sourced(classified["summary"], row["raw_text"])

    def test_the_row_is_dated_to_the_period_it_describes(self):
        for row in _rows():
            self.assertEqual(row["published_date"], row["period_end"])
            signal = validate.build_signal(sec_execcomp.as_classified(row),
                                           row, sec_execcomp.COLLECTOR)
            self.assertEqual(signal.published_date, row["period_end"])


class Years(unittest.TestCase):
    def test_a_year_before_the_disclosure_existed_is_refused(self):
        with self.assertRaises(sec_execcomp.FrameError):
            sec_execcomp.fetch_frame(2019)

    def test_the_env_list_is_parsed_and_junk_is_refused(self):
        import os
        os.environ["TIT_EXECCOMP_YEARS"] = "2022, 2023 2024"
        try:
            self.assertEqual(sec_execcomp.years_from_env(), [2022, 2023, 2024])
            os.environ["TIT_EXECCOMP_YEARS"] = "last year"
            with self.assertRaises(sec_execcomp.FrameError):
                sec_execcomp.years_from_env()
        finally:
            os.environ.pop("TIT_EXECCOMP_YEARS", None)

    def test_the_default_is_the_last_complete_year(self):
        from datetime import datetime, timezone
        self.assertEqual(sec_execcomp.years_from_env(),
                         [datetime.now(timezone.utc).year - 1])


class UserAgent(unittest.TestCase):
    def test_the_agent_carries_a_contact_address(self):
        """SEC 403s a request with no contact address, and a workflow mapping a
        secret that does not exist sets the variable to empty string. That
        combination turned five backfill windows into silent failures."""
        self.assertIn("@", sec_execcomp.USER_AGENT)
        self.assertNotIn("Mozilla", sec_execcomp.USER_AGENT)


if __name__ == "__main__":
    unittest.main()
