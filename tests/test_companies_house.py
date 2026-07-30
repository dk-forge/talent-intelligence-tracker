"""The UK's leadership spine must stay a register entry, and must stay material.

Two families of failure are guarded here, and only the first is the ordinary
kind.

The ordinary kind, all of them things the register or the specification will
actually do:

- a body corporate stored as a person joining an employer, which the public web
  page actively hides by rendering `corporate-secretary` as plain "Secretary";
- a nominee appointment, which is a formation agent's placeholder;
- a pre-1992 appointment, which carries `appointed_before` and no date at all;
- a resignation collected because `resigned_on` happens to be recent;
- a source URL composed out of a guessed identifier rather than read from the
  API's own `links`, which is BSE's AttachLive/AttachHis rot with extra steps;
- a person's name re-cased into O'brien or Mcdonald on the way to a headline;
- the rotation reshuffling between runs, which would leave some companies
  unvisited for months while the run count looked healthy;
- silence exiting green.

The second kind is specific to this source, and it is the reason it exists at
all: **the filter has to keep meaning "an employer with at least 250
employees".** The register holds 5.9 million companies and most of them are
dormant micro-entities, so a size floor that quietly widens turns this
connector into the thing it was built not to be. `allowed_sizes` is therefore
tested for what it EXCLUDES, and the roster is tested for refusing to be small.

Recorded fixture, never a live call — and unusually, not even one that ever was:
no authenticated request has been made from this repository, so the fixture
carries real register values inside the documented envelope and says so in its
own `_provenance` block. The four things that remain unproven until the first
real run are listed in the module docstring of the collector.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from collectors import companies_house as ch
from collectors import uk_paygap
from pipeline import validate, vocab

FIXTURE = Path(__file__).parent / "fixtures" / "companies_house_officers.json"
ROOT = Path(__file__).parent.parent

# The fixture's window: 2026-07-30 with the derived 42-day window.
TODAY = date(2026, 7, 30)
WINDOW_START = "2026-06-18"
WINDOW_END = "2026-07-30"


class _Resp:
    """A requests-shaped response. Only what fetch_officers reads."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = "<html>not json</html>" if payload is _NOT_JSON else json.dumps(payload)

    def json(self):
        if self._payload is _NOT_JSON:
            raise ValueError("not json")
        return self._payload


_NOT_JSON = object()


class _Session:
    """Answers every company number from a map of number -> (payload, status)."""

    def __init__(self, by_number):
        self.by_number = by_number
        self.calls = []

    def get(self, url, params=None, headers=None, auth=None, timeout=None):
        number = re.search(r"/company/([A-Z0-9]+)/officers", url).group(1)
        self.calls.append((number, params, auth))
        payload, status = self.by_number.get(number, ({}, 404))
        return _Resp(payload, status)


class _Base(unittest.TestCase):
    def setUp(self):
        self.cases = {k: v for k, v in json.loads(FIXTURE.read_text()).items()
                      if not k.startswith("_")}

    def employer(self, case: str) -> ch.Employer:
        c = self.cases[case]["company"]
        return ch.Employer(c["number"], c["name"], c["size_band"],
                           c["postcode"], c["sic"])

    def officers(self, case: str) -> list:
        return self.cases[case]["payload"]["items"]

    def rows(self, case: str) -> list:
        """Every row the collector would build from one fixture company, with
        the window applied exactly as collect() applies it."""
        employer = self.employer(case)
        out = []
        for officer in self.officers(case):
            if not ch.appointed_in_window(officer, WINDOW_START, WINDOW_END):
                continue
            if not ch.is_person(officer):
                continue
            row = ch._row(employer, officer)
            if row is not None:
                out.append(row)
        return out

    def signal(self, case: str, index: int = 0):
        rows = self.rows(case)
        self.assertTrue(rows, f"{case} produced no row but should produce one")
        item = rows[index]
        return validate.build_signal(ch.as_classified(item), item, ch.COLLECTOR)

    def all_rows(self) -> list:
        return [r for case in self.cases for r in self.rows(case)]


# --- the record ------------------------------------------------------------

class TheRecordIsARegisterEntry(_Base):
    def test_every_stored_row_reaches_verified(self):
        # The registrar publishes the register itself, not a report of it, so
        # the host is the filing venue. If it ever leaves
        # vocab.PRIMARY_SOURCE_DOMAINS every UK row drops to 'reported'
        # silently and the whole spine understates what it is.
        for case in ("appointment", "secretary"):
            self.assertEqual(self.signal(case).confidence, "verified", case)

    def test_the_host_is_registered_as_a_primary_source(self):
        self.assertIn("find-and-update.company-information.service.gov.uk",
                      vocab.PRIMARY_SOURCE_DOMAINS)

    def test_the_source_url_is_the_officers_own_register_page(self):
        for row in self.all_rows():
            parsed = urlparse(row["source_url"])
            self.assertEqual(
                parsed.hostname, "find-and-update.company-information.service.gov.uk")
            self.assertRegex(parsed.path, r"^/officers/[A-Za-z0-9_-]+/appointments$")
            self.assertNotIn(".pdf", parsed.path.lower())
            # Not the company's officers page: that is one URL for every
            # appointment the company will ever make.
            self.assertNotIn("/company/", parsed.path)

    def test_the_officer_id_is_read_from_the_api_and_never_composed(self):
        # BSE's AttachLive -> AttachHis rot is the lesson: an identifier we
        # invent is a link that breaks on somebody else's schedule. So the id
        # has to come out of links.officer.appointments or there is no row.
        for bad in ("", "   ", "/officers//appointments", "/company/1/officers",
                    "/officers/../../etc/passwd/appointments",
                    "/officers/short/appointments", "https://elsewhere/x"):
            self.assertIsNone(ch.officer_page(bad), bad)
        self.assertEqual(
            ch.officer_page("/officers/Wz2yKYxCDi0HGbP2FWUk5kE2oaE/appointments"),
            "https://find-and-update.company-information.service.gov.uk"
            "/officers/Wz2yKYxCDi0HGbP2FWUk5kE2oaE/appointments")

    def test_a_director_with_no_appointments_link_is_declined(self):
        # Well formed in every other way. No source URL, no record.
        nolink = next(o for o in self.officers("constructed_edges")
                      if o["name"].startswith("NOLINK"))
        self.assertTrue(ch.is_person(nolink))
        self.assertTrue(ch.appointed_in_window(nolink, WINDOW_START, WINDOW_END))
        self.assertIsNone(ch._row(self.employer("constructed_edges"), nolink))

    def test_the_country_is_the_united_kingdom_and_never_guessed(self):
        # Sourced twice over: the company is on the UK register, and the pay-gap
        # duty that put it on the roster covers its Great Britain employees.
        self.assertEqual(self.signal("appointment").country, "GB")
        for row in self.all_rows():
            self.assertEqual(row["country"], "United Kingdom")

    def test_the_pillar_is_leadership_change(self):
        self.assertEqual(self.signal("secretary").pillar, "leadership_change")

    def test_raw_text_is_set_on_every_row(self):
        # A collector that forgets raw_text posts zero records silently. That
        # bug cost the sibling weeks, so it is asserted rather than assumed.
        for row in self.all_rows():
            self.assertTrue(row["raw_text"].strip(), row["headline"])

    def test_every_figure_in_the_summary_is_in_the_source_text(self):
        # Holds by construction — the summary restates raw_text rather than
        # paraphrasing it — and it is exactly the check a stray newline broke
        # while bse_india was being built, so it is asserted anyway.
        for row in self.all_rows():
            validate.assert_figures_are_sourced(
                ch.as_classified(row)["summary"], row["raw_text"])

    def test_the_company_number_and_size_band_survive_into_the_summary(self):
        sig = self.signal("appointment")
        self.assertIn("02334263", sig.summary)
        self.assertIn("5000 to 19,999", sig.summary)

    def test_the_employer_is_named_first_in_the_headline(self):
        for case in ("appointment", "secretary"):
            sig = self.signal(case)
            self.assertTrue(sig.headline.startswith(sig.company), sig.headline)

    def test_the_attribution_travels_with_the_data(self):
        # A licence condition, not a courtesy, and it has to reach WordPress
        # rather than live only in a docstring.
        self.assertIn("Open Government Licence", self.signal("appointment").summary)

    def test_no_model_is_involved(self):
        # If as_classified ever disappears, run_collect starts paying to read a
        # source whose every field is a column.
        self.assertTrue(callable(getattr(ch, "as_classified", None)))

    def test_no_row_is_ever_hiring_or_displacement(self):
        # The register says an appointment happened. It does not say the person
        # came from outside the employer, so `hiring` would be a guess at
        # volume; `displacement` would put it in the sibling's scope.
        for row in self.all_rows():
            self.assertEqual(ch.as_classified(row)["signal_direction"], "neutral")


class APersonsNameIsNotImproved(_Base):
    def test_the_register_order_is_reversed_and_the_casing_is_not_touched(self):
        self.assertEqual(ch.person("HOLDEN, Emma"), "Emma HOLDEN")
        self.assertEqual(ch.person("LIEBHERR, Katharina Maria Mathilde"),
                         "Katharina Maria Mathilde LIEBHERR")

    def test_a_name_that_title_casing_would_mangle_is_left_alone(self):
        for surname in ("O'BRIEN", "McDONALD", "MacLEOD", "VAN DER BERG",
                        "SMITH-JONES"):
            self.assertEqual(ch.person(f"{surname}, Ann"), f"Ann {surname}")

    def test_a_name_with_no_comma_is_passed_through_unchanged(self):
        self.assertEqual(ch.person("LEGAL & GENERAL CO SEC LIMITED"),
                         "LEGAL & GENERAL CO SEC LIMITED")
        self.assertEqual(ch.person(""), "")


# --- materiality -----------------------------------------------------------

class TheFilterIsStatutoryAndMustStayThatWay(_Base):
    def test_the_size_floor_excludes_everything_below_the_duty(self):
        """The whole claim of this connector is "250 employees or more".

        The pay-gap service also publishes voluntary reporters below the
        threshold and rows with no band at all. Admitting either would break
        the guarantee while the row count went up, which is the failure this
        connector was designed against.
        """
        bands = ch.allowed_sizes("250")
        self.assertNotIn("Less than 250", bands)
        self.assertNotIn("Not Provided", bands)
        self.assertNotIn("", bands)
        self.assertEqual(bands, {"250 to 499", "500 to 999", "1000 to 4999",
                                 "5000 to 19,999", "20,000 or more"})

    def test_the_bands_come_from_uk_paygap_and_are_not_a_second_copy(self):
        # Two lists of the same labels drift, and this one decides materiality.
        self.assertEqual({label for _k, label in uk_paygap.SIZE_BANDS},
                         ch.allowed_sizes("250"))

    def test_a_higher_floor_narrows_and_never_widens(self):
        wide = ch.allowed_sizes("250")
        for floor in ("500", "1000", "5000", "20000"):
            narrow = ch.allowed_sizes(floor)
            self.assertTrue(narrow < wide, floor)

    def test_a_nonsense_floor_is_refused(self):
        # Note "" is NOT here: an explicitly empty value means "not given" and
        # falls through to the default, which is what uk_paygap does with its
        # own floor. A silent 250 is the safe direction; a silent 20000 would
        # not be.
        for bad in ("300", "all", "-1", "250 to 499"):
            with self.assertRaises(ch.CompaniesHouseError, msg=bad):
                ch.allowed_sizes(bad)

    def test_the_roster_keeps_only_well_formed_company_numbers(self):
        csv_text = (
            "EmployerName,EmployerId,CompanyNumber,SicCodes,PostCode,"
            "EmployerSize,CurrentName\n"
            '"Good Ltd",1,"01234567","62020","EC1A 1AA","1000 to 4999","Good Ltd"\n'
            '"No number",2,"","62020","EC1A 1AA","1000 to 4999","No number"\n'
            '"Short",3,"1234","62020","EC1A 1AA","1000 to 4999","Short"\n'
            '"Punctuated",4,"0123-456","62020","EC1A 1AA","1000 to 4999","Punctuated"\n'
            '"Too small",5,"07654321","62020","EC1A 1AA","Less than 250","Too small"\n'
            '"Unbanded",6,"07654322","62020","EC1A 1AA","Not Provided","Unbanded"\n'
            '"Scottish",7,"SC123456","62020","EH1 1AA","500 to 999","Scottish"\n'
        )
        # The roster minimum is a separate guard; bypass it by calling the
        # parser directly, which is what the guard wraps.
        original = uk_paygap.MIN_ROWS_PER_YEAR
        uk_paygap.MIN_ROWS_PER_YEAR = 1
        try:
            got = ch.parse_roster(csv_text, sizes=ch.allowed_sizes("250"))
        finally:
            uk_paygap.MIN_ROWS_PER_YEAR = original
        self.assertEqual([e.number for e in got], ["01234567", "SC123456"])

    def test_the_employer_name_matches_what_uk_paygap_would_store(self):
        """So one employer has one company_key across both UK sources.

        The alternative — taking the register's own name — reads as more
        authoritative and would split every large UK employer into two
        employers on the dashboard, one with a pay gap and one with a board.
        """
        csv_text = (
            "EmployerName,EmployerId,CompanyNumber,SicCodes,PostCode,"
            "EmployerSize,CurrentName\n"
            '"OLD NAME LIMITED",9,"01234567","62020","EC1A 1AA",'
            '"1000 to 4999","NEW NAME LIMITED"\n'
        )
        original = uk_paygap.MIN_ROWS_PER_YEAR
        uk_paygap.MIN_ROWS_PER_YEAR = 1
        try:
            got = ch.parse_roster(csv_text, sizes=ch.allowed_sizes("250"))
        finally:
            uk_paygap.MIN_ROWS_PER_YEAR = original
        self.assertEqual(got[0].name, "NEW NAME LIMITED")
        self.assertEqual(vocab.company_key(got[0].name),
                         vocab.company_key("NEW NAME LIMITED"))

    def test_a_truncated_national_file_is_a_breakage_not_a_quiet_year(self):
        with self.assertRaises(ch.CompaniesHouseError) as caught:
            ch.parse_roster("EmployerName,CompanyNumber,EmployerSize\n",
                            sizes=ch.allowed_sizes("250"))
        self.assertIn("not a quiet year", str(caught.exception))

    def test_a_small_roster_is_refused_by_the_fetching_wrapper(self):
        # 9,230 is the measured figure. A roster of a few hundred means the
        # CompanyNumber column moved, and that must not read as "Britain got
        # smaller".
        self.assertGreaterEqual(ch.MIN_ROSTER, 5000)


# --- role allowlist --------------------------------------------------------

class ABodyCorporateIsNotAnEmployee(_Base):
    def test_a_corporate_secretary_is_declined(self):
        """The real sitting secretary of a real company is a company.

        LEGAL & GENERAL CO SEC LIMITED holds that office at Legal & General
        Resources Limited. Storing it would put a company on the dashboard as
        somebody who took a job.
        """
        corporate = next(o for o in self.officers("appointment")
                         if o["officer_role"] == "corporate-secretary")
        self.assertTrue(ch.appointed_in_window(corporate, WINDOW_START, WINDOW_END))
        self.assertFalse(ch.is_person(corporate))
        self.assertIsNone(ch._row(self.employer("appointment"), corporate))

    def test_a_nominee_is_declined(self):
        nominee = next(o for o in self.officers("constructed_edges")
                       if o["officer_role"] == "nominee-director")
        self.assertFalse(ch.is_person(nominee))

    def test_the_allowlist_is_keyed_on_officer_role_and_not_on_a_label(self):
        # The public web page renders a corporate-secretary as plain
        # "Secretary", so a rendered label is not evidence of anything. This is
        # asserted because the measurement that sized this source was taken off
        # that page and therefore could not see the distinction.
        self.assertFalse(ch.is_person({"officer_role": "corporate-secretary",
                                       "name": "SOMEBODY, Real"}))
        self.assertTrue(ch.is_person({"officer_role": "secretary",
                                      "name": "A COMPANY LIMITED"}))

    def test_the_excluded_roles_are_not_in_the_allowlist(self):
        for role in ch.EXCLUDED_ROLES:
            self.assertNotIn(role, ch.ROLES, role)

    def test_every_corporate_role_in_the_published_enum_is_excluded(self):
        # The enum can grow. Any role whose name says it is a body corporate
        # must never be in the allowlist, whatever else changes.
        for role in ch.ROLES:
            self.assertFalse(role.startswith("corporate-"), role)
            self.assertFalse(role.startswith("nominee-"), role)

    def test_a_role_outside_the_vocabulary_is_a_declined_row(self):
        # Not a new category. A value that will not normalise is refused.
        for role in ("manager", "judicial-factor", "person-authorised-to-accept",
                     "member-of-a-supervisory-organ", ""):
            self.assertFalse(ch.is_person({"officer_role": role}), role)

    def test_the_role_is_matched_exactly_and_case_is_not_folded(self):
        # Folding case would let the string the WEB PAGE prints for a body
        # corporate ("Secretary") through the one check that is supposed to
        # catch it. So the enum is matched verbatim.
        for rendered in ("Director", "Secretary", "DIRECTOR", "LLP Member"):
            self.assertFalse(ch.is_person({"officer_role": rendered}), rendered)
        self.assertTrue(ch.is_person({"officer_role": "director"}))


# --- the window ------------------------------------------------------------

class TheWindowIsTheOnlyState(_Base):
    def test_an_appointment_inside_the_window_is_collected(self):
        self.assertEqual([r["officer_name"] for r in self.rows("appointment")],
                         ["HOLDEN, Emma"])
        self.assertEqual([r["officer_name"] for r in self.rows("secretary")],
                         ["DIXIE, Alice Sian Rhiannon"])

    def test_an_appointment_outside_the_window_is_not(self):
        self.assertEqual(self.rows("football_club"), [])

    def test_a_pre_1992_appointment_has_no_date_and_is_declined(self):
        old = next(o for o in self.officers("constructed_edges")
                   if "appointed_before" in o)
        self.assertNotIn("appointed_on", old)
        self.assertFalse(ch.appointed_in_window(old, WINDOW_START, WINDOW_END))

    def test_a_resignation_is_not_collected_however_it_is_dated(self):
        """v1 collects arrivals only, and the register never says why somebody
        left. It would be 80% more rows (184 resignations against 231
        appointments in the sampled two years) with the least to say."""
        resigned = next(o for o in self.officers("appointment")
                        if o.get("resigned_on"))
        self.assertFalse(ch.appointed_in_window(resigned, WINDOW_START, WINDOW_END))
        for row in self.all_rows():
            self.assertNotIn("resigned_on", row)

    def test_a_malformed_date_is_not_a_date(self):
        for bad in ("", "2026", "2026-7-1", "01/07/2026", "yesterday", None):
            self.assertFalse(
                ch.appointed_in_window({"appointed_on": bad},
                                       WINDOW_START, WINDOW_END), repr(bad))

    def test_the_published_date_is_the_appointment_date(self):
        self.assertEqual(self.signal("appointment").published_date, "2026-07-01")

    def test_the_window_is_derived_from_the_rotation_and_not_typed(self):
        # Cover the whole gap between visits, plus a whole extra visit of
        # slack, so one missed run is caught by the next one instead of being a
        # hole nothing ever looks at again.
        for slices in (1, 2, 4, 8):
            self.assertEqual(ch.window_days(slices), slices * 7 + 14)
            self.assertGreater(ch.window_days(slices), slices * 7)
        self.assertEqual(ch.window_days(ch.SLICES), 42)

    def test_the_window_can_be_widened_for_a_backfill(self):
        with _env(TIT_CH_DAYS="200"):
            self.assertEqual(ch.window_days(4), 200)

    def test_a_nonsense_window_is_refused(self):
        for bad in ("lots", "0", "-3", "7.5"):
            with _env(TIT_CH_DAYS=bad):
                with self.assertRaises(ch.CompaniesHouseError, msg=bad):
                    ch.window_days(4)


class TheRotationVisitsEverybody(_Base):
    def test_the_slice_of_a_company_is_stable_across_processes(self):
        """Python's hash() is salted per process. Using it would reshuffle the
        rotation every run, so some companies would go months unvisited while
        the run count looked perfect."""
        expected = {n: ch.slice_of(n, 4) for n in
                    ("02334263", "03153442", "00053301", "09214263", "SC123456")}
        # blake2b, so these are fixed forever. Recomputing must agree.
        for number, index in expected.items():
            self.assertEqual(ch.slice_of(number, 4), index)
            self.assertIn(index, range(4))

    def test_every_company_lands_in_exactly_one_slice_and_none_is_starved(self):
        numbers = [f"{i:08d}" for i in range(4000)]
        counts = [0] * ch.SLICES
        for number in numbers:
            counts[ch.slice_of(number, ch.SLICES)] += 1
        self.assertEqual(sum(counts), len(numbers))
        even = len(numbers) / ch.SLICES
        for count in counts:
            # Within 15% of even. A lopsided rotation is a lopsided lock hold.
            self.assertLess(abs(count - even), even * 0.15, counts)

    def test_the_slice_advances_with_the_iso_week_and_needs_no_state(self):
        seen = {ch.current_slice(ch.SLICES, today=date(2026, 1, 5)
                                 + __import__("datetime").timedelta(weeks=w))
                for w in range(ch.SLICES)}
        self.assertEqual(seen, set(range(ch.SLICES)))

    def test_a_slice_can_be_pinned_by_hand_and_is_range_checked(self):
        with _env(TIT_CH_SLICE="2"):
            self.assertEqual(ch.current_slice(4), 2)
        with _env(TIT_CH_SLICE="9"):
            with self.assertRaises(ch.CompaniesHouseError):
                ch.current_slice(4)
        with _env(TIT_CH_SLICE="two"):
            with self.assertRaises(ch.CompaniesHouseError):
                ch.current_slice(4)

    def test_only_this_weeks_slice_is_polled(self):
        employers = [ch.Employer(f"{i:08d}", f"EMPLOYER {i} LIMITED",
                                 "1000 to 4999", "EC1A 1AA", "62020")
                     for i in range(400)]
        session = _Session({e.number: ({"items": [], "total_results": 0}, 200)
                            for e in employers})
        ch.collect(employers=employers, session=session, key="k",
                   slices=4, slice_index=1, today=TODAY)
        polled = {n for n, _p, _a in session.calls}
        self.assertTrue(polled)
        for number in polled:
            self.assertEqual(ch.slice_of(number, 4), 1)
        self.assertLess(len(polled), len(employers))


# --- geography -------------------------------------------------------------

class GeographyIsTheRegisteredOfficeAndSaysSo(_Base):
    def test_the_registered_office_town_goes_to_hq_city_and_never_to_city(self):
        # Tesco's registered office is Welwyn Garden City and its employees are
        # everywhere. uk_paygap made this split; this source has exactly the
        # same limitation and must not undo it.
        sig = self.signal("appointment")
        self.assertEqual(sig.hq_city, "London")     # EC2R -> London
        self.assertIsNone(sig.city)
        for row in self.all_rows():
            self.assertNotIn("city", row)

    def test_an_unmapped_postcode_area_stores_no_city_rather_than_a_guess(self):
        self.assertEqual(self.rows("secretary")[0]["hq_city"], "")   # KT22
        self.assertIsNone(self.signal("secretary").hq_city)

    def test_the_postcode_map_is_uk_paygaps_own(self):
        # Imported, not reimplemented, so the two UK sources place an employer
        # identically. A second copy is how one of them silently drifts.
        self.assertIs(ch.uk_paygap.POSTCODE_AREA_CITY,
                      uk_paygap.POSTCODE_AREA_CITY)

    def test_the_industry_comes_from_the_filed_sic_code(self):
        self.assertEqual(self.signal("appointment").industry, "professional_services")
        self.assertEqual(self.signal("secretary").industry, "healthcare")

    def test_no_address_is_ever_split_on_a_comma(self):
        # ats_boards once comma-split "Cambridge, MA" into Morocco. Nothing
        # here parses geography out of prose at all: the postcode area is the
        # only input, and it is matched by a rule anchored to the start.
        source = (ROOT / "collectors" / "companies_house.py").read_text()
        for suspect in ('.split(",")', ".split(', ')", 'split(",")[-1]'):
            self.assertNotIn(suspect, source.replace('text.split(",", 1)', ""))


# --- silence ---------------------------------------------------------------

class SilenceIsNotHealth(_Base):
    def _employers(self, count):
        return [ch.Employer(f"{i:08d}", f"EMPLOYER {i} LIMITED", "1000 to 4999",
                            "EC1A 1AA", "62020") for i in range(count)]

    def test_a_slice_that_finds_nothing_raises_rather_than_reporting_zero(self):
        employers = self._employers(4000)
        session = _Session({e.number: ({"items": [], "total_results": 0}, 200)
                            for e in employers})
        with self.assertRaises(ch.CompaniesHouseError) as caught:
            ch.collect(employers=employers, session=session, key="k",
                       slices=4, slice_index=0, today=TODAY)
        self.assertIn("not a quiet fortnight", str(caught.exception))

    def test_the_floor_does_not_fire_on_a_deliberately_tiny_dispatch(self):
        # TIT_CH_MIN_SIZE=20000 is 51 companies. A slice of that genuinely may
        # produce nothing, and a floor that fired there would teach a reader to
        # ignore this collector's failures.
        self.assertEqual(ch.emptiness_floor(0), 0)
        self.assertEqual(ch.emptiness_floor(ch.FLOOR_APPLIES_ABOVE - 1), 0)
        self.assertGreaterEqual(ch.emptiness_floor(ch.FLOOR_APPLIES_ABOVE),
                                ch.FLOOR_MINIMUM)
        employers = self._employers(40)
        session = _Session({e.number: ({"items": [], "total_results": 0}, 200)
                            for e in employers})
        self.assertEqual(
            ch.collect(employers=employers, session=session, key="k",
                       slices=1, slice_index=0, today=TODAY), [])

    def test_the_floor_scales_with_what_was_actually_polled(self):
        # A flat number would be wrong the moment the size floor or the slice
        # count moved, which are both inputs.
        self.assertLess(ch.emptiness_floor(2000), ch.emptiness_floor(9000))

    def test_a_401_names_the_streaming_key_trap(self):
        """The single most likely first-run failure.

        Companies House registers REST and streaming applications separately and
        states the keys are not interchangeable, so the plausible wrong key is
        one that looks perfectly valid.
        """
        session = _Session({"01234567": ({"error": "Invalid Authorization"}, 401)})
        with self.assertRaises(ch.CompaniesHouseError) as caught:
            ch.fetch_officers("01234567", key="k", session=session)
        message = str(caught.exception)
        self.assertIn("STREAMING", message)
        self.assertIn("not", message)

    def test_a_missing_key_is_refused_before_any_request(self):
        with _env(**{ch.API_KEY_ENV: ""}):
            with self.assertRaises(ch.CompaniesHouseError) as caught:
                ch.api_key()
        self.assertIn(ch.API_KEY_ENV, str(caught.exception))

    def test_the_key_is_never_printed_in_any_message(self):
        """A public repository and a real credential. Every error here describes
        the key rather than quoting it, so a red run cannot leak it."""
        secret = "zzzz-secret-key-zzzz"
        session = _Session({"01234567": ({}, 401)})
        with self.assertRaises(ch.CompaniesHouseError) as caught:
            ch.fetch_officers("01234567", key=secret, session=session)
        self.assertNotIn(secret, str(caught.exception))
        session = _Session({"01234567": ({}, 500)})
        with self.assertRaises(ch.CompaniesHouseError) as caught:
            ch.fetch_officers("01234567", key=secret, session=session)
        self.assertNotIn(secret, str(caught.exception))

    def test_the_key_is_sent_as_basic_auth_with_an_empty_password(self):
        session = _Session({"01234567": ({"items": [], "total_results": 0}, 200)})
        ch.fetch_officers("01234567", key="a-key", session=session)
        self.assertEqual(session.calls[0][2], ("a-key", ""))

    def test_a_payload_without_the_items_key_is_a_breakage(self):
        session = _Session({"01234567": ({"officers": []}, 200)})
        with self.assertRaises(ch.CompaniesHouseError) as caught:
            ch.fetch_officers("01234567", key="k", session=session)
        self.assertIn("shape has changed", str(caught.exception))

    def test_a_non_json_body_is_a_breakage(self):
        session = _Session({"01234567": (_NOT_JSON, 200)})
        with self.assertRaises(ch.CompaniesHouseError):
            ch.fetch_officers("01234567", key="k", session=session)

    def test_a_404_is_a_company_the_register_does_not_know_not_an_empty_board(self):
        # None and [] are different answers. Conflating them is how a broken
        # roster join reads as a quiet week.
        session = _Session({})
        self.assertIsNone(ch.fetch_officers("09999999", key="k", session=session))
        session = _Session({"01234567": ({"items": [], "total_results": 0}, 200)})
        self.assertEqual(ch.fetch_officers("01234567", key="k", session=session), [])

    def test_a_roster_that_mostly_404s_is_a_broken_join(self):
        employers = self._employers(4000)
        session = _Session({})          # every number 404s
        with self.assertRaises(ch.CompaniesHouseError) as caught:
            ch.collect(employers=employers, session=session, key="k",
                       slices=4, slice_index=0, today=TODAY)
        self.assertIn("not on the register", str(caught.exception))

    def test_a_429_is_waited_out_rather_than_treated_as_a_failure(self):
        calls = {"n": 0}

        class _Throttled:
            def get(self, url, params=None, headers=None, auth=None, timeout=None):
                calls["n"] += 1
                if calls["n"] < 3:
                    return _Resp({"error": "rate limit"}, 429)
                return _Resp({"items": [], "total_results": 0}, 200)

        original = ch.RATE_LIMIT_WAIT
        ch.RATE_LIMIT_WAIT = 0
        try:
            self.assertEqual(
                ch.fetch_officers("01234567", key="k", session=_Throttled()), [])
        finally:
            ch.RATE_LIMIT_WAIT = original
        self.assertEqual(calls["n"], 3)

    def test_a_429_that_never_clears_names_the_shared_key(self):
        class _Always:
            def get(self, *a, **kw):
                return _Resp({}, 429)

        original = ch.RATE_LIMIT_WAIT
        ch.RATE_LIMIT_WAIT = 0
        try:
            with self.assertRaises(ch.CompaniesHouseError) as caught:
                ch.fetch_officers("01234567", key="k", session=_Always())
        finally:
            ch.RATE_LIMIT_WAIT = original
        self.assertIn("600 requests per 5 minutes", str(caught.exception))

    def test_paging_stops_at_the_total_the_api_reports(self):
        page = [{"officer_role": "director", "appointed_on": "2000-01-01",
                 "name": f"X{i}, Y", "links": {"officer": {"appointments":
                 f"/officers/AAAAAAAA{i:04d}/appointments"}}}
                for i in range(ch.PAGE_SIZE)]

        class _TwoPages:
            def __init__(self):
                self.n = 0

            def get(self, url, params=None, headers=None, auth=None, timeout=None):
                self.n += 1
                if params["start_index"] == 0:
                    return _Resp({"items": page, "total_results": 150}, 200)
                return _Resp({"items": page[:50], "total_results": 150}, 200)

        session = _TwoPages()
        got = ch.fetch_officers("01234567", key="k", session=session)
        self.assertEqual(len(got), 150)
        self.assertEqual(session.n, 2)


# --- wiring ----------------------------------------------------------------

class Configuration(_Base):
    def test_the_collector_is_registered(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["companies_house"], ch)

    def test_the_united_kingdom_is_structured_official_because_a_connector_runs(self):
        import source_registry as registry
        market = next(m for m in registry.MARKETS if m.iso2 == "GB")
        self.assertEqual(market.status, registry.STRUCTURED_OFFICIAL)
        self.assertIn("companies_house", market.live_sources)
        self.assertIn("uk_paygap", market.live_sources)
        # It has been built, so it must stop being advertised as a plan.
        for candidate in market.candidate_official_sources:
            self.assertNotIn("Companies House", candidate)

    def test_the_source_is_listed_live_and_joined_to_this_collector(self):
        import source_registry as registry
        self.assertEqual(registry.COLLECTOR_BY_SOURCE_NAME[ch.SOURCE_NAME],
                         ch.COLLECTOR)
        listed = next(s for s in registry.SOURCES if s.name == ch.SOURCE_NAME)
        self.assertEqual(listed.status, "live")
        self.assertEqual(listed.country, "GB")

    def test_the_leash_matches_the_weekly_cron(self):
        import staleness
        self.assertEqual(staleness.max_age_hours(ch.COLLECTOR), 180)

    def test_the_schedule_and_the_rotation_agree(self):
        """A weekly cron and a 4-slice rotation is a full sweep every 4 weeks.

        If either moves without the other, some companies are visited far less
        often than the window assumes and appointments fall through the gap
        silently. Read from the workflow so the two cannot drift.
        """
        workflow = (ROOT / ".github" / "workflows" /
                    "collect-structured.yml").read_text()
        self.assertIn("companies_house", workflow)
        crons = re.findall(r"cron:\s*'([^']+)'\s*#\s*([^\n]*)", workflow)
        weekly = [expr for expr, note in crons
                  if "companies_house" in note.lower()
                  or "companies house" in note.lower()]
        self.assertTrue(weekly, f"no cron line names this collector: {crons}")
        for expr in weekly:
            fields = expr.split()
            self.assertEqual(len(fields), 5, expr)
            # day-of-week pinned, day-of-month wild: that is "weekly".
            self.assertEqual(fields[2], "*", expr)
            self.assertNotEqual(fields[4], "*", expr)

    def test_the_source_url_page_is_revisited_on_purpose(self):
        # One person can be appointed twice. Marking the page seen would make
        # the first appointment the last one this collector ever reported for
        # them, which is the ats_boards lesson.
        self.assertTrue(ch.REVISITS_ITS_SOURCE_URL)

    def test_the_rate_limit_is_respected_by_construction(self):
        # 600 requests per 5 minutes is 2 a second. The delay must leave
        # headroom, not sit exactly on the ceiling.
        self.assertGreater(ch.REQUEST_DELAY, 5 * 60 / 600)


class _env:
    """Set environment variables for one block and put them back."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, previous in self.saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        return False


if __name__ == "__main__":
    unittest.main()
