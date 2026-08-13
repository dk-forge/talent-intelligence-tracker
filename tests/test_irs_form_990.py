"""Form 990 must cite the return, must not name the person, and must not claim
to be the chief executive's pay.

The failures guarded here are all specific and all were available:

- citing the 200MB batch zip because the per filing URL 404s (a dataset is not
  a receipt, and this source was blocked on exactly that for a scoping pass),
- storing the highest number as if it were the chief executive's,
- naming the individual, which is the reason the same scoping pass refused
  state payroll portals,
- reading the tax period end as the date the pay was earned,
- assuming a filer is American,
- dividing total payroll by the employee count and calling it a salary,
- ingesting the year unfiltered, which is thirteen times the whole database,
- collecting the open index year, where four filings in five have no copy of
  the return posted yet and so cannot be cited at all.

Recorded fixtures and a stubbed lookup, never a live call: the real batch file
is 246MB.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from urllib.parse import urlparse

from collectors import irs_form_990 as f990
from pipeline import validate, vocab

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "irs_form_990_sample.xml").read_bytes()
FOREIGN = (FIXTURES / "irs_form_990_foreign.xml").read_bytes()

# The real answer for EIN 310707369, trimmed. Verified live 2026-08-13.
TEOS_ITEMS = [
    {"EIN": "310707369", "TAX_PERIOD": "202407", "RETURN_TYPE": "990",
     "STATICFILEPATH": "/pub/epostcard/cor/310707369_202407_990_2025081423655359.pdf"},
    {"EIN": "310707369", "TAX_PERIOD": "202207", "RETURN_TYPE": "990",
     "STATICFILEPATH": "/pub/epostcard/cor/310707369_202207_990_2023061221445419.pdf"},
]


class _Stub:
    """One canned TEOS answer. Counts calls, because one request per
    ORGANISATION rather than per filing is a property worth asserting."""

    def __init__(self, items=TEOS_ITEMS, status=200):
        self.items, self.status, self.calls = items, status, 0

    def get(self, url, **kwargs):
        self.calls += 1
        stub = self

        class Resp:
            status_code = stub.status

            @staticmethod
            def json():
                return {"items": stub.items, "message": "success"}
        return Resp()


class Parsing(unittest.TestCase):
    def setUp(self):
        self.row = f990.parse_filing(SAMPLE, floor=100_000_000)

    def test_the_highest_paid_row_wins_and_it_is_not_the_president(self):
        """The president is on $1,378,789 and the basketball coach is on
        $1,502,004. Picking 'the CEO' would need title matching, which is
        inventing; picking the largest filed figure is reading."""
        self.assertEqual(self.row["amount_usd"], 1502004)
        self.assertEqual(self.row["title"], "HEAD COACH, BASKETBALL")

    def test_related_organisation_pay_is_never_added_in(self):
        """The coach also has $250,000 from a related organisation. That is
        different money in a different column and summing them would state a
        total the return does not."""
        self.assertNotIn("250,000", self.row["raw_text"])
        self.assertNotIn("1,752,004", self.row["raw_text"])

    def test_a_corporate_trustees_fee_is_not_somebodys_pay(self):
        """The fixture's largest Part VII figure is $20,052,864 against a bank
        with InstitutionalTrusteeInd set. It is a trustee fee filed in the same
        column as a salary, and it must not become this employer's pay row."""
        self.assertNotEqual(self.row["amount_usd"], 20052864)
        self.assertNotIn("20,052,864", self.row["raw_text"])
        self.assertNotEqual(self.row["title"], "TRUSTEE")

    def test_no_individual_is_named_anywhere_on_the_row(self):
        for name in ("Decker", "Alex Rivera", "Washington", "Briskey", "Unpaid"):
            for field in ("raw_text", "headline", "company", "title"):
                self.assertNotIn(name, self.row[field],
                                 f"{name} leaked into {field}")

    def test_the_row_says_out_loud_that_it_drops_the_name(self):
        self.assertIn("names the individual", self.row["raw_text"])

    def test_the_pay_year_is_not_the_tax_period_end(self):
        """Part VII states pay for the calendar year ending with or within the
        tax year, so a return for the year ended 2024-07-31 carries calendar
        2023 pay. A row that does not say so is read as current pay."""
        self.assertIn("calendar year ending with or within the tax year",
                      self.row["raw_text"])

    def test_payroll_and_headcount_are_stored_but_never_divided(self):
        self.assertIn("$54,360,597", self.row["raw_text"])
        self.assertEqual(self.row["employees"], 1239)
        # 54360597 / 1239 = 43874.6. Anything near it means somebody divided.
        self.assertNotIn("43,874", self.row["raw_text"])
        self.assertIn("not an average salary", self.row["raw_text"])

    def test_a_filing_under_the_revenue_floor_is_not_a_row(self):
        self.assertIsNone(f990.parse_filing(SAMPLE, floor=500_000_000))

    def test_a_foreign_filer_is_not_filed_as_american(self):
        row = f990.parse_filing(FOREIGN, floor=10_000_000)
        self.assertEqual(row["country"], "UK")
        self.assertEqual(row["state"], "")
        self.assertEqual(vocab.normalize_country(row["country"]), "GB")

    def test_an_unparseable_or_wrong_form_returns_none_rather_than_raising(self):
        self.assertIsNone(f990.parse_filing(b"not xml at all", floor=0))
        self.assertIsNone(f990.parse_filing(
            SAMPLE.replace(b"<ReturnTypeCd>990<", b"<ReturnTypeCd>990EZ<"),
            floor=0))

    def test_a_return_with_no_part_vii_pay_is_not_a_pay_row(self):
        stripped = SAMPLE.replace(b"ReportableCompFromOrgAmt>1502004<",
                                  b"ReportableCompFromOrgAmt>0<")
        stripped = stripped.replace(b"ReportableCompFromOrgAmt>1378789<",
                                    b"ReportableCompFromOrgAmt>0<")
        stripped = stripped.replace(b"ReportableCompFromOrgAmt>369620<",
                                    b"ReportableCompFromOrgAmt>0<")
        self.assertIsNone(f990.parse_filing(stripped, floor=100_000_000))


class Receipt(unittest.TestCase):
    def test_the_source_url_is_the_return_itself_not_the_batch_zip(self):
        stub = _Stub()
        url = f990.receipt_url("310707369", "202407", session=stub)
        parsed = urlparse(url)
        self.assertEqual(parsed.hostname, "apps.irs.gov")
        self.assertTrue(parsed.path.startswith("/pub/epostcard/cor/"))
        self.assertTrue(parsed.path.endswith(".pdf"))
        self.assertNotIn(".zip", url)
        self.assertNotIn("/xml/", url)

    def test_a_filing_with_no_posted_copy_gets_no_url_and_is_not_faked(self):
        """The open index year is 81% this case. The answer is None, which
        drops the row; it is never the zip and never a guessed filename."""
        self.assertIsNone(f990.receipt_url("310707369", "202507", session=_Stub()))

    def test_a_path_outside_the_copies_directory_is_refused(self):
        stub = _Stub(items=[{"TAX_PERIOD": "202407", "RETURN_TYPE": "990",
                             "STATICFILEPATH": "/app/eos/details/"}])
        self.assertIsNone(f990.receipt_url("310707369", "202407", session=stub))

    def test_a_990_t_is_never_cited_for_a_990_figure(self):
        """The unrelated business income return is a different form for the
        same tax period, with no Part VII in it. A prefix match on '990' cited
        it: the first dry run put nine McLaren hospital rows on a 990-T URL,
        which is a real IRS document that does not contain the row's figure."""
        stub = _Stub(items=[
            {"TAX_PERIOD": "202309", "RETURN_TYPE": "990T",
             "STATICFILEPATH": "/pub/epostcard/cor/381434090_202309_990T_2024103022784678.pdf"},
        ])
        self.assertIsNone(f990.receipt_url("381434090", "202309", session=stub))

    def test_the_most_recently_posted_copy_wins(self):
        """One tax period can have two copies posted. The tail of the filename
        is the IRS posting date, so citing the smaller one links a reader to a
        superseded document."""
        stub = _Stub(items=[
            {"TAX_PERIOD": "202312", "RETURN_TYPE": "990",
             "STATICFILEPATH": "/pub/epostcard/cor/046010342_202312_990_2025030723163896.pdf"},
            {"TAX_PERIOD": "202312", "RETURN_TYPE": "990T",
             "STATICFILEPATH": "/pub/epostcard/cor/046010342_202312_990T_2025011522993932.pdf"},
            {"TAX_PERIOD": "202312", "RETURN_TYPE": "990",
             "STATICFILEPATH": "/pub/epostcard/cor/046010342_202312_990_2025041123355445.pdf"},
        ])
        self.assertTrue(f990.receipt_url("046010342", "202312", session=stub)
                        .endswith("_990_2025041123355445.pdf"))

    def test_a_lookup_failure_is_a_breakage_not_a_missing_filing(self):
        with self.assertRaises(f990.Form990Error):
            f990.receipt_url("310707369", "202407", session=_Stub(status=503))

    def test_one_request_per_organisation_not_per_filing(self):
        stub, cache = _Stub(), {}
        f990.receipt_url("310707369", "202407", session=stub, cache=cache)
        f990.receipt_url("310707369", "202207", session=stub, cache=cache)
        self.assertEqual(stub.calls, 1)

    def test_the_ein_survives_in_the_url_so_no_schema_change_is_needed(self):
        """The measured employer join is 0 correct matches in 526, so no EIN
        column was added. The EIN is still recoverable from any stored row,
        which is what makes that a deferral rather than a door closing."""
        url = f990.receipt_url("310707369", "202407", session=_Stub())
        self.assertIn("310707369", url.rsplit("/", 1)[-1])


class Population(unittest.TestCase):
    PAGE = """
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_01A.zip">a</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_02A.zip">b</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_03A.zip">c</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_04A.zip">d</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_05A.zip">e</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_05B.zip">f</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_06A.zip">g</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_07A.zip">h</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_08A.zip">i</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_09A.zip">j</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_10A.zip">k</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2025/2025_TEOS_XML_11A.zip">l</a>
        <a href="https://apps.irs.gov/pub/epostcard/990/xml/2026/2026_TEOS_XML_01A.zip">m</a>
    """

    def test_the_batch_list_is_read_off_the_page_and_scoped_to_the_year(self):
        urls = f990.parse_batch_urls(self.PAGE, 2025)
        self.assertEqual(len(urls), 12)
        self.assertTrue(all("/2025/" in u for u in urls))

    def test_a_half_published_year_is_a_breakage_not_a_quiet_year(self):
        with self.assertRaises(f990.Form990Error):
            f990.parse_batch_urls(self.PAGE, 2026)

    def test_the_open_index_year_is_never_the_default(self):
        from datetime import datetime, timezone
        self.assertEqual(
            f990.latest_complete_year(datetime(2026, 8, 13, tzinfo=timezone.utc)),
            2025)

    def test_a_year_the_page_cannot_enumerate_is_refused(self):
        import os
        os.environ["TIT_FORM990_YEARS"] = "2021"
        try:
            with self.assertRaises(f990.Form990Error):
                f990.years_from_env()
        finally:
            os.environ.pop("TIT_FORM990_YEARS", None)

    def test_the_revenue_floor_defaults_high_enough_to_be_depth(self):
        """Unfiltered this source is 376,920 returns a year against a 29,329
        row database. The floor is what keeps it a feature."""
        self.assertGreaterEqual(f990.min_revenue(), 100_000_000)

    def test_a_nonsense_floor_is_refused_rather_than_silently_zero(self):
        with self.assertRaises(f990.Form990Error):
            f990.min_revenue("lots")


class Validation(unittest.TestCase):
    def _signal(self, floor=100_000_000, blob=SAMPLE):
        row = f990.parse_filing(blob, floor=floor)
        row["source_url"] = f990.receipt_url(row["ein"], row["tax_period"],
                                             session=_Stub())
        row["discovery_url"] = row["source_url"]
        return validate.build_signal(f990.as_classified(row), row, f990.COLLECTOR)

    def test_it_stores_as_a_us_pay_row(self):
        signal = self._signal()
        self.assertEqual(signal.pillar, "rewards_comp")
        self.assertEqual(signal.signal_direction, "comp_shift")
        self.assertEqual(signal.country, "US")
        self.assertEqual(signal.state, "OH")
        self.assertEqual(signal.employer_type, "nonprofit")

    def test_the_row_is_dated_by_the_tax_period_end(self):
        self.assertEqual(self._signal().published_date, "2024-07-31")

    def test_the_headcount_is_the_filed_employee_count(self):
        signal = self._signal()
        self.assertEqual(signal.headcount, 1239)
        self.assertEqual(signal.headcount_scope, "total_workforce")

    def test_the_summary_quotes_only_figures_the_return_carries(self):
        row = f990.parse_filing(SAMPLE, floor=100_000_000)
        validate.assert_figures_are_sourced(
            f990.as_classified(row)["summary"], row["raw_text"])

    def test_confidence_is_earned_by_the_host_not_asserted(self):
        self.assertEqual(self._signal().confidence, "verified")

    def test_the_readthrough_refuses_to_call_it_a_salary_band(self):
        signal = self._signal()
        self.assertIn("ceiling", signal.talent_readthrough)
        self.assertIn("not the chief executive", signal.talent_readthrough)

    def test_no_individual_reaches_a_stored_field(self):
        signal = self._signal()
        blob = " ".join(filter(None, (signal.headline, signal.summary,
                                      signal.talent_readthrough, signal.company)))
        for name in ("Decker", "Rivera", "Washington", "Briskey"):
            self.assertNotIn(name, blob)


if __name__ == "__main__":
    unittest.main()
