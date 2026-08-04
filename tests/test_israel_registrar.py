"""The Israeli registrar's changes file, pinned.

Everything here runs offline from a fixture shaped exactly like the live CKAN
datastore response, including its Hebrew column names and its tilde-for-quote
encoding, both of which were read off the real endpoint on 2026-08-03.

The point of the fixture is that the traps in this source are all SILENT ones:
a date parsed month-first is a real date on the wrong day, a tilde left alone
is a company name with punctuation in the middle of it on a public page, an act
code that stops matching returns an empty list rather than an error, and a row
whose raw_text is empty stores nothing at all while every counter still reads
healthy. None of those raise on their own, so each gets a test.
"""

import unittest

from collectors import israel_registrar as il
from pipeline import validate

# The registrar's own column names. Imported from the module rather than
# retyped, because a test that spells them itself passes while the collector
# spells them wrong.
NUM, NAME = il.COL_NUMBER, il.COL_NAME
ACT, DATE, CODE = il.COL_ACT, il.COL_DATE, il.COL_CODE

TODAY = il.date(2026, 8, 3)
WINDOW = 14
# The window collect() derives from those two: 2026-07-20 .. 2026-08-03.
INSIDE = "27/07/2026"
START_EDGE = "20/07/2026"
END_EDGE = "03/08/2026"
OUTSIDE = "19/07/2026"


def record(code=2, when=INSIDE, name="ברייטמרג' ישראל בע~מ", number=513612515):
    """One row in the shape data.gov.il actually returns."""
    return {
        "_id": 195,
        NUM: number,
        NAME: name,
        ACT: "הקצאת מניות",
        DATE: when,
        "מזהה השיעבוד": None,
        CODE: code,
    }


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


class _Session:
    """Answers one page of records per act code, then an empty page."""

    def __init__(self, by_code, status=200):
        self.by_code = by_code
        self.status = status
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        import json as _json
        code = int(_json.loads(params["filters"])[CODE])
        self.calls.append((code, params["offset"]))
        rows = self.by_code.get(code, []) if not params["offset"] else []
        return _Response({"success": True, "result": {"records": rows}},
                         self.status)


class TheDateIsReadDayFirst(unittest.TestCase):
    """The file writes DD/MM/YYYY. Month-first is the silent corruption."""

    def test_a_day_above_twelve_proves_the_order(self):
        self.assertEqual(il.parse_date("27/07/2026"), "2026-07-27")

    def test_an_ambiguous_date_is_still_read_day_first(self):
        # 03/08 is a real date either way, and only this assertion catches a
        # collector that switched to month-first on the strength of it.
        self.assertEqual(il.parse_date("03/08/2026"), "2026-08-03")

    def test_a_malformed_date_is_none_rather_than_today(self):
        for bad in ("", "2026-07-27", "27-07-2026", "not a date", "99/99/2026"):
            self.assertIsNone(il.parse_date(bad), bad)

    def test_an_impossible_day_is_refused_rather_than_rolled_over(self):
        self.assertIsNone(il.parse_date("31/02/2026"))


class TheCompanyNameIsRestoredNotImproved(unittest.TestCase):
    """The feed encodes the Hebrew gershayim as a tilde, so `בע~מ` is `בע"מ`,
    the Hebrew for Ltd, and it is in nearly every name in the file."""

    def test_the_tilde_becomes_a_quote(self):
        self.assertEqual(il.company_name("גז פרו בע~מ"), 'גז פרו בע"מ')

    def test_no_tilde_survives_into_a_stored_row(self):
        row = il._row(record())
        self.assertNotIn("~", row["company"])
        self.assertNotIn("~", row["raw_text"])
        self.assertNotIn("~", row["headline"])

    def test_runs_of_whitespace_collapse_but_nothing_else_changes(self):
        # The live file carries names like "גז פרו   בע~מ" with padding.
        self.assertEqual(il.company_name("גז פרו   בע~מ"), 'גז פרו בע"מ')

    def test_a_name_without_a_tilde_is_returned_unchanged(self):
        self.assertEqual(il.company_name("Mobileye Global"), "Mobileye Global")


class TheRowIsARegisterEntry(unittest.TestCase):

    def test_raw_text_is_set_and_not_empty(self):
        # The guard for this repository's known silent failure: a collector
        # that leaves raw_text empty stores nothing and reports success.
        row = il._row(record())
        self.assertTrue(row["raw_text"].strip())

    def test_the_summary_is_a_literal_part_of_raw_text(self):
        row = il._row(record())
        self.assertIn(row["summary"], row["raw_text"])

    def test_no_figure_in_the_summary_is_unsourced(self):
        row = il._row(record())
        validate.assert_figures_are_sourced(
            il.as_classified(row)["summary"], row["raw_text"])

    def test_the_citation_names_the_company_number(self):
        row = il._row(record(number=513612515))
        self.assertIn("513612515", row["source_url"])

    def test_a_non_funding_act_is_declined(self):
        # 3 is a share TRANSFER: existing shares change hands and no new money
        # enters the company, so it is not the act this collector reads.
        self.assertIsNone(il._row(record(code=3)))
        # 4032 is a director appointment, deliberately deferred: see the
        # module docstring for the named blocker.
        self.assertIsNone(il._row(record(code=4032)))

    def test_a_row_with_an_unreadable_date_is_dropped_not_dated_today(self):
        self.assertIsNone(il._row(record(when="")))
        self.assertIsNone(il._row(record(when="garbage")))

    def test_a_row_with_no_company_name_is_dropped(self):
        self.assertIsNone(il._row(record(name="")))

    def test_the_row_states_no_amount(self):
        # The file carries no share count, price or sum. The one number on a
        # row is the company number, and nothing may imply a figure for the
        # raise.
        row = il._row(record())
        for invented in ("$", "₪", "million", "raised $"):
            self.assertNotIn(invented, row["raw_text"])


class TheClassificationIsDerived(unittest.TestCase):

    def setUp(self):
        self.item = il._row(record())
        self.out = il.as_classified(self.item)

    def test_a_raise_is_a_company_development(self):
        self.assertEqual(self.out["pillar"], "company_development")

    def test_the_direction_is_neutral_because_no_amount_is_stated(self):
        self.assertEqual(self.out["signal_direction"], "neutral")

    def test_the_country_is_israel_and_no_city_is_invented(self):
        self.assertEqual(self.out["country"], "Israel")
        self.assertEqual(self.out["headquarters_city"], "")

    def test_it_builds_a_signal(self):
        signal = validate.build_signal(self.out, self.item, il.COLLECTOR)
        self.assertTrue(signal)

    def test_the_readthrough_names_the_missing_size_filter(self):
        # Israel publishes no employee count anywhere in the registrar's data,
        # and a reader comparing these rows with the UK ones must be told.
        self.assertIn("employee count", self.out["talent_readthrough"])


class TheWindowIsInclusiveAtBothEnds(unittest.TestCase):

    def collect(self, rows):
        return il.collect(days=WINDOW, today=TODAY,
                          session=_Session({2: rows}))

    def test_a_row_on_the_first_day_is_kept(self):
        out = self.collect([record(when=START_EDGE)] * 1 + [record()] * 3)
        self.assertIn("2026-07-20", [r["registered_on"] for r in out])

    def test_a_row_on_the_last_day_is_kept(self):
        out = self.collect([record(when=END_EDGE)] + [record()] * 3)
        self.assertIn("2026-08-03", [r["registered_on"] for r in out])

    def test_a_row_the_day_before_the_window_is_dropped(self):
        out = self.collect([record(when=OUTSIDE)] + [record()] * 3)
        self.assertNotIn("2026-07-19", [r["registered_on"] for r in out])

    def test_the_same_act_twice_is_stored_once(self):
        out = self.collect([record(), record()] + [record(number=1)] * 3)
        pairs = [(r["company_number"], r["registered_on"], r["act_code"])
                 for r in out]
        self.assertEqual(len(pairs), len(set(pairs)))


class ItFailsLoudlyRatherThanQuietly(unittest.TestCase):

    def test_an_empty_run_raises_instead_of_reporting_a_quiet_fortnight(self):
        with self.assertRaises(il.IsraelRegistrarError):
            il.collect(days=WINDOW, today=TODAY, session=_Session({}))

    def test_a_non_200_raises(self):
        with self.assertRaises(il.IsraelRegistrarError):
            il.collect(days=WINDOW, today=TODAY,
                       session=_Session({2: [record()]}, status=503))

    def test_a_window_wider_than_the_file_is_refused(self):
        import os
        os.environ["TIT_IL_DAYS"] = "400"
        try:
            with self.assertRaises(il.IsraelRegistrarError):
                il.window_days()
        finally:
            del os.environ["TIT_IL_DAYS"]

    def test_the_four_act_codes_are_all_queried(self):
        session = _Session({2: [record()] * 4})
        il.collect(days=WINDOW, today=TODAY, session=session)
        self.assertEqual({code for code, _offset in session.calls},
                         set(il.FUNDING_ACTS))


if __name__ == "__main__":
    unittest.main()
