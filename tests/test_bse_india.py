"""India's leadership spine must stay a filing, and must not go quiet.

The failures guarded against here are all specific and all were seen while the
collector was being built against the live API:

- an audit firm's appointment stored as a person joining a company;
- the regulation's own name stored where a description belongs;
- a re-appointment of a sitting director counted as a hire;
- BSE's link rot (AttachLive -> AttachHis) baked into a stored source_url;
- the server-side category filter silently becoming a no-op, which would store
  the entire announcement feed instead of leadership changes;
- a week of silence exiting green.

Recorded fixture, never a live call. Every row in it is a real filing captured
from the API on 2026-07-30.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from collectors import bse_india
from pipeline import validate, vocab

FIXTURE = Path(__file__).parent / "fixtures" / "bse_india_reg30.json"


class _Base(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(FIXTURE.read_text())

    def row(self, case: str):
        """The collector's raw dict for one fixture case, or None if declined."""
        block = self.cases[case]
        return bse_india._row(block["Table"][0], block["subcategory"])

    def signal(self, case: str):
        item = self.row(case)
        self.assertIsNotNone(item, f"{case} was declined but should store")
        return validate.build_signal(bse_india.as_classified(item), item,
                                     bse_india.COLLECTOR)


class TheRecordIsAFiling(_Base):
    def test_every_stored_row_reaches_verified(self):
        # A Regulation 30 disclosure is filed WITH the exchange, so the host is
        # the filing venue and not an outlet. If bseindia.com ever leaves
        # vocab.PRIMARY_SOURCE_DOMAINS this drops to 'reported' silently.
        for case in ("appointment", "reappointment", "resignation"):
            self.assertEqual(self.signal(case).confidence, "verified", case)

    def test_the_host_is_registered_as_a_primary_source(self):
        self.assertIn("www.bseindia.com", vocab.PRIMARY_SOURCE_DOMAINS)

    def test_the_source_url_is_the_announcement_not_the_pdf(self):
        # BSE moves an attachment from AttachLive to AttachHis as it ages: a
        # January 2024 PDF 404s on the first path and 200s on the second, so a
        # stored PDF link rots on its own. The newsid page does not.
        for case in ("appointment", "reappointment", "resignation"):
            parsed = urlparse(self.signal(case).source_url)
            self.assertEqual(parsed.hostname, "www.bseindia.com")
            self.assertNotIn("AttachLive", parsed.path)
            self.assertNotIn(".pdf", parsed.path.lower())
            self.assertTrue(parse_qs(parsed.query).get("newsid"))

    def test_a_malformed_newsid_yields_no_url_rather_than_a_broken_one(self):
        for bad in ("", "  ", "not-a-guid", "../../etc/passwd", "<script>"):
            self.assertIsNone(bse_india.announcement_url(bad), bad)

    def test_the_country_is_india_and_never_guessed(self):
        sig = self.signal("appointment")
        self.assertEqual(sig.country, "IN")
        # Country is a property of the exchange, so it can never be absent.
        for case in self.cases:
            item = self.row(case)
            if item:
                self.assertEqual(item["country"], "India", case)

    def test_the_pillar_is_leadership_change(self):
        self.assertEqual(self.signal("resignation").pillar, "leadership_change")

    def test_raw_text_is_set_on_every_row(self):
        # A collector that forgets raw_text posts zero records silently. That
        # bug cost the sibling weeks, so it is asserted rather than assumed.
        for case in self.cases:
            item = self.row(case)
            if item:
                self.assertTrue(item["raw_text"].strip(), case)


class AnAuditorIsNotAnEmployee(_Base):
    def test_an_audit_firms_appointment_is_declined(self):
        # "Appointment of Secretarial Auditor of the Company." arrives filed
        # under Change in Management, so the sub-category allowlist cannot
        # catch it and only the wording can.
        self.assertIsNone(self.row("auditor_only"))

    def test_a_filing_naming_both_an_auditor_and_a_director_is_kept(self):
        self.assertFalse(bse_india.is_auditor_only(
            "Appointment of Mr. A B as Managing Director and of the "
            "secretarial auditor of the Company"))

    def test_the_excluded_subcategories_are_not_collected(self):
        for excluded in bse_india.EXCLUDED_SUBCATEGORIES:
            self.assertNotIn(excluded, bse_india.SUBCATEGORIES)


class DirectionIsReadFromTheFiling(_Base):
    def test_an_appointment_is_hiring(self):
        self.assertEqual(self.signal("appointment").signal_direction, "hiring")

    def test_a_re_appointment_is_not_a_hire(self):
        # A board keeping someone is not the market hiring them.
        self.assertEqual(self.signal("reappointment").signal_direction, "neutral")
        self.assertEqual(
            bse_india.direction_for("Change in Directorate",
                                    "Reappointment of Mr. X as Director"),
            "neutral")

    def test_a_departure_is_neutral_and_never_displacement(self):
        # `displacement` would put this in the sibling tracker's scope. One
        # director leaving is not a workforce reduction.
        for sub in ("Resignation of Director", "Cessation",
                    "Resignation of Chairman"):
            self.assertEqual(
                bse_india.direction_for(sub, "Appointment of a new director"),
                "neutral", sub)
        self.assertEqual(self.signal("resignation").signal_direction, "neutral")

    def test_no_row_is_ever_displacement(self):
        for case in self.cases:
            item = self.row(case)
            if item:
                self.assertNotEqual(
                    bse_india.as_classified(item)["signal_direction"],
                    "displacement", case)


class TheDescriptionIsSourced(_Base):
    def test_boilerplate_becomes_the_mandated_category(self):
        # "Announcement under Regulation 30 (LODR) - Change in Directorate"
        # tells a reader nothing the category does not, and the category is the
        # part that was never freeform.
        sig = self.signal("boilerplate")
        self.assertIn("Change in Directorate", sig.headline)

    def test_a_covering_note_becomes_the_category(self):
        for note in ("Disclosure under Regulation 30of SEBI (LODR), 2015",
                     "Please see enclosed annexure",
                     "Please refer the attached file.",
                     "Announcement as attached"):
            self.assertEqual(bse_india.describe(note, "Change in Management"),
                             "Change in Management", note)

    def test_a_covering_note_that_names_a_role_is_kept_as_filed(self):
        # "Outcome of Board Meeting - Changes in Senior Management Personnel"
        # opens like a covering note but says more than its category does.
        # Replacing it would throw away the only specific thing in the filing.
        for real in ("Outcome of Board Meeting - Changes in Senior Management "
                     "Personnel (SMP)",
                     "Intimation regarding Change in Senior Management",
                     "Intimation of resignation of Independent Director."):
            self.assertEqual(bse_india.describe(real, "Change in Management"),
                             real, real)

    def test_a_real_description_is_quoted_not_replaced(self):
        sig = self.signal("appointment")
        self.assertIn("Neha Rathi", sig.headline)
        self.assertIn("Neha Rathi", sig.summary)

    def test_the_employer_is_named_in_the_headline(self):
        for case in ("appointment", "boilerplate", "resignation"):
            sig = self.signal(case)
            self.assertTrue(sig.headline.startswith(sig.company), sig.headline)

    def test_every_figure_in_the_summary_is_in_the_source_text(self):
        # The summary quotes the filed text rather than paraphrasing it, so this
        # holds by construction — and it is exactly the check that a stray
        # newline in raw_text broke while this was being built.
        for case in self.cases:
            item = self.row(case)
            if item:
                validate.assert_figures_are_sourced(
                    bse_india.as_classified(item)["summary"], item["raw_text"])

    def test_no_model_is_involved(self):
        # If as_classified ever disappears, run_collect starts paying to read a
        # source whose every field is a column, and the cost discipline in
        # CLAUDE.md quietly stops holding.
        self.assertTrue(callable(getattr(bse_india, "as_classified", None)))


class SilenceIsNotHealth(_Base):
    def test_a_window_below_the_floor_raises_rather_than_returning_empty(self):
        calls = []

        class _Session:
            def get(self, url, params=None, headers=None, timeout=None):
                calls.append(params["subcategory"])
                return _Resp({"Table": []})

        with self.assertRaises(bse_india.BseError) as caught:
            bse_india.collect(session=_Session())
        self.assertIn("not a quiet week", str(caught.exception))
        # Every sub-category was tried before the run gave up.
        self.assertEqual(len(calls), len(bse_india.SUBCATEGORIES))

    def test_a_non_200_is_an_error_and_names_the_referer_trap(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({}, status=403)

        with self.assertRaises(bse_india.BseError) as caught:
            bse_india.fetch_page("Change in Directorate", "20260101",
                                 "20260108", 1, session=_Session())
        self.assertIn("Referer", str(caught.exception))

    def test_a_payload_without_the_table_key_is_a_breakage(self):
        class _Session:
            def get(self, *a, **kw):
                return _Resp({"Rows": []})

        with self.assertRaises(bse_india.BseError) as caught:
            bse_india.fetch_page("Change in Directorate", "20260101",
                                 "20260108", 1, session=_Session())
        self.assertIn("shape has changed", str(caught.exception))

    def test_the_server_side_filter_going_inert_stops_the_run(self):
        # If BSE ever ignores `subcategory`, every page comes back as the whole
        # announcement feed. Storing that would bury the tracker in newspaper
        # publications and investor presentations, so the run refuses instead.
        row = dict(self.cases["appointment"]["Table"][0])
        row["SUBCATNAME"] = "Investor Presentation"

        class _Session:
            def get(self, *a, **kw):
                return _Resp({"Table": [row]})

        with self.assertRaises(bse_india.BseError) as caught:
            bse_india.collect(session=_Session())
        self.assertIn("no longer", str(caught.exception))

    def test_the_leash_matches_the_weekly_cron(self):
        import staleness
        self.assertEqual(staleness.max_age_hours("bse_india"), 180)


class Configuration(_Base):
    def test_the_window_defaults_to_a_week(self):
        self.assertEqual(bse_india.days_from_env(), bse_india.DEFAULT_DAYS)

    def test_a_nonsense_window_is_refused(self):
        import os
        original = os.environ.get("TIT_BSE_DAYS")
        for bad in ("lots", "0", "-3", "7.5"):
            os.environ["TIT_BSE_DAYS"] = bad
            try:
                with self.assertRaises(bse_india.BseError, msg=bad):
                    bse_india.days_from_env()
            finally:
                if original is None:
                    os.environ.pop("TIT_BSE_DAYS", None)
                else:
                    os.environ["TIT_BSE_DAYS"] = original

    def test_the_window_is_the_api_date_format(self):
        from datetime import datetime, timezone
        start, end = bse_india.window(
            7, today=datetime(2026, 7, 30, tzinfo=timezone.utc))
        self.assertEqual((start, end), ("20260723", "20260730"))

    def test_the_collector_is_registered(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["bse_india"], bse_india)

    def test_india_is_structured_official_because_a_connector_runs(self):
        import source_registry as registry
        market = next(m for m in registry.MARKETS if m.iso2 == "IN")
        self.assertEqual(market.status, registry.STRUCTURED_OFFICIAL)
        self.assertIn("bse_india", market.live_sources)


class _Resp:
    """A requests-shaped response. Only what fetch_page reads."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


if __name__ == "__main__":
    unittest.main()
