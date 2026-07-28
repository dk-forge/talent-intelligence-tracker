"""The UK pay gap source must read the direction right, and must not go quiet.

The failures this guards against are all specific: a negative gap published as
if women were paid less, a registered office being sold as a job location, a
link to the CSV instead of the employer's own report, the licence attribution
falling off the row, and a truncated national download exiting green.

Recorded fixture, never a live call: the real file is 4.4MB.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from urllib.parse import urlparse

from collectors import uk_paygap
from pipeline import validate

FIXTURE = Path(__file__).parent / "fixtures" / "uk_paygap_2025.csv"


class _Base(unittest.TestCase):
    def setUp(self):
        # The real guard is 1,000 employers, which is right for a national
        # return and impossible for a fixture. Lowered here and restored after,
        # never weakened in the module itself.
        self._min = uk_paygap.MIN_ROWS_PER_YEAR
        uk_paygap.MIN_ROWS_PER_YEAR = 1
        self.text = FIXTURE.read_text()
        self.rows = uk_paygap.parse_csv(self.text, 2025,
                                        sizes=uk_paygap.allowed_sizes("5000"))

    def tearDown(self):
        uk_paygap.MIN_ROWS_PER_YEAR = self._min


class Parsing(_Base):
    def test_the_size_floor_filters(self):
        names = {r["company"] for r in self.rows}
        # The fixture deliberately carries one 250-499 employer.
        self.assertNotIn('"RED BAND" CHEMICAL COMPANY, LIMITED', names)
        self.assertTrue(self.rows)

    def test_widening_the_floor_lets_the_small_employer_in(self):
        wide = uk_paygap.parse_csv(self.text, 2025,
                                   sizes=uk_paygap.allowed_sizes("250"))
        self.assertGreater(len(wide), len(self.rows))

    def test_an_unknown_size_floor_is_refused(self):
        with self.assertRaises(uk_paygap.PayGapError):
            uk_paygap.allowed_sizes("lots")

    def test_the_source_url_is_the_employers_own_report_not_the_csv(self):
        for row in self.rows:
            parsed = urlparse(row["source_url"])
            self.assertEqual(parsed.hostname, "gender-pay-gap.service.gov.uk")
            self.assertIn("/reporting-year-2025/", parsed.path)
            self.assertNotIn("download-data", parsed.path)

    def test_the_submission_date_is_parsed_not_passed_through(self):
        for row in self.rows:
            self.assertRegex(row["published_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_a_gap_states_which_way_round_it_is(self):
        """'-2.91%' means women are paid MORE. A bare percentage is read
        backwards by almost everyone, and a pay figure read backwards is worse
        than no pay figure."""
        self.assertIn("lower", uk_paygap._gap_phrase("7.97", "median hourly pay"))
        self.assertIn("higher", uk_paygap._gap_phrase("-2.91", "median hourly pay"))
        self.assertIn("no ", uk_paygap._gap_phrase("0", "median hourly pay"))

    def test_a_truncated_national_download_is_a_breakage_not_a_quiet_year(self):
        uk_paygap.MIN_ROWS_PER_YEAR = self._min
        with self.assertRaises(uk_paygap.PayGapError):
            uk_paygap.parse_csv(self.text, 2025)

    def test_the_first_real_sic_code_drives_the_industry(self):
        self.assertEqual(uk_paygap._first_sic_division("10120"), "10")
        self.assertEqual(uk_paygap._first_sic_division("1,86900"), "86")
        self.assertIsNone(uk_paygap._first_sic_division(""))
        self.assertIsNone(uk_paygap._first_sic_division("1"))

    def test_a_london_postcode_resolves_and_an_unknown_one_does_not(self):
        self.assertEqual(uk_paygap._hq_city("EC2A 4NE"), "London")
        self.assertEqual(uk_paygap._hq_city("M1 4BT"), "Manchester")
        # ME is Medway, not Manchester. A near-miss on a postcode area is how a
        # county ends up filed as a city.
        self.assertEqual(uk_paygap._hq_city("ME5 8AA"), "")
        self.assertEqual(uk_paygap._hq_city(""), "")


class Validation(_Base):
    def _signals(self):
        return [validate.build_signal(uk_paygap.as_classified(r), r,
                                      uk_paygap.COLLECTOR) for r in self.rows]

    def test_every_row_survives_the_credibility_gate_as_a_uk_row(self):
        for signal in self._signals():
            self.assertEqual(signal.pillar, "rewards_comp")
            self.assertEqual(signal.country, "GB")
            self.assertEqual(signal.region, "Europe")

    def test_the_registered_office_never_becomes_a_job_location(self):
        """The CSV carries where the company is registered, not where its
        people work. Those are different columns for a reason."""
        for signal in self._signals():
            self.assertIsNone(signal.city)
            self.assertEqual(signal.hq_country, "GB")

    def test_the_licence_attribution_travels_with_every_row(self):
        for signal in self._signals():
            self.assertIn("Open Government Licence", signal.summary)

    def test_the_summary_quotes_only_figures_the_source_text_carries(self):
        for row in self.rows:
            validate.assert_figures_are_sourced(
                uk_paygap.as_classified(row)["summary"], row["raw_text"])

    def test_confidence_is_capped_by_the_host_and_never_asserted(self):
        """We claim 'verified' because a statutory return deserves it. Until
        the service is listed in vocab.PRIMARY_SOURCE_DOMAINS the cap lands it
        at 'reported', which is the guard working, not a bug."""
        for signal in self._signals():
            self.assertIn(signal.confidence, ("verified", "reported"))


class Years(unittest.TestCase):
    def test_an_open_reporting_year_is_never_the_default(self):
        from datetime import datetime, timezone
        # A year's file is only complete from May of the following year.
        self.assertEqual(
            uk_paygap.latest_complete_year(datetime(2026, 7, 1, tzinfo=timezone.utc)),
            2025)
        self.assertEqual(
            uk_paygap.latest_complete_year(datetime(2026, 2, 1, tzinfo=timezone.utc)),
            2024)

    def test_a_year_before_the_duty_existed_is_refused(self):
        import os
        os.environ["TIT_PAYGAP_YEARS"] = "2015"
        try:
            with self.assertRaises(uk_paygap.PayGapError):
                uk_paygap.years_from_env()
        finally:
            os.environ.pop("TIT_PAYGAP_YEARS", None)


if __name__ == "__main__":
    unittest.main()
