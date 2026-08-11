"""Estonia must stay honest about the half of the truth it cannot see, must
stay material, and must never carry a person's private life into the database.

Three families of failure.

**The gap that cannot be closed.** `lopp_kpv` is null on all 520,895 rows of
the published file, because the file holds current office-holders only. So this
source reports appointments and never departures, and the danger is not that
somebody rebuilds it wrong — it is that the gap goes unstated and a reader takes
a country where nobody ever leaves for a country with stable boards. The
sentence is required on the record itself, not only in a docstring, and it is
tested for.

**The threshold.** 202 appointments a day are published, ~74,000 a year, from a
country of 1.3 million people, and `JUHL` at a one-person `OÜ` is 86% of the
file. A floor that quietly widens turns this into the thing the Spanish and
British registers were refused for.

**The personal data.** The file carries a home address on 60,930 rows, a birth
date on 16,099, an email on 14,360 and a national-ID hash on 485,719. The
owner's ruling is that name, role, employer and date are taken at the collector
boundary and nothing else is ever persisted, so the fixture keeps every one of
those fields and the tests require that none reaches a stored row.

Recorded fixture, never a live call: nine real companies and their matching 2025
annual-report rows, lifted verbatim out of the 2026-07-30 open-data files. See
the fixture's own `_provenance` block, including the one entry that is
constructed and says so.
"""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from collectors import estonia_ariregister as ee
from pipeline import validate, vocab

FIXTURES = Path(__file__).parent / "fixtures"
PERSONS = FIXTURES / "estonia_ariregister_persons.json"
GENERAL = FIXTURES / "estonia_ariregister_report_general.csv"
ELEMENTS = FIXTURES / "estonia_ariregister_report_elements.csv"
ROOT = Path(__file__).parent.parent

TODAY = date(2026, 7, 30)
WINDOW_DAYS = 91
WINDOW_START = "2026-04-30"

BAUHOF = "10636638"          # 492 FTE, and a name beginning B
BONDORA = "11483929"         # 54 FTE, just over the floor
KODUREMONT = "16374731"      # 4 FTE, far under it
BAUHAUS = "11866180"         # 195 FTE, but only TOSAN/UOSAN partners on the card
ENEFIT = "10579981"          # 1,405 FTE, and a LEGAL person on the card
ESWIRE = "11189064"          # 101 FTE, and a birth date and a home address
GPV = "11045112"             # 769 FTE, and a PROK
METROSERT = "10397748"       # 118 FTE, and the duplicated entry


def _persons_payload() -> dict:
    with PERSONS.open(encoding="utf-8") as fh:
        return json.load(fh)


def _zip_of(name: str, data: bytes) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def _archives(companies=None):
    """The three published archives, rebuilt from the reviewable fixtures.

    The real files are a 45MB zip of ~1GB of JSON and two 20MB zips of ~250MB of
    CSV. Storing them would make the fixture unreadable, so the fixture is the
    rows and this puts the zip envelope back on.
    """
    payload = _persons_payload()
    body = json.dumps(companies if companies is not None else payload["companies"],
                      ensure_ascii=False, indent=4)
    return (
        _zip_of("ettevotja_rekvisiidid__kaardile_kantud_isikud.json",
                body.encode("utf-8")),
        _zip_of("4.2025_aruannete_elemendid_kuni_30062026.csv",
                ELEMENTS.read_bytes()),
        _zip_of("1.aruannete_yldandmed_kuni_30062026.csv", GENERAL.read_bytes()),
    )


def _collect(companies=None, **kwargs):
    """A run over the fixture, with the three national-scale sanity floors
    scaled to it. Nine companies cannot be asserted to look like 375,305."""
    persons, elements, general = _archives(companies)
    saved = (ee.MIN_COMPANIES, ee.MIN_EMPLOYEE_FIGURES, ee.MIN_ROSTER,
             ee.FLOOR_APPLIES_ABOVE)
    ee.MIN_COMPANIES = 1
    ee.MIN_EMPLOYEE_FIGURES = 1
    ee.MIN_ROSTER = 1
    ee.FLOOR_APPLIES_ABOVE = 10 ** 9
    try:
        return ee.collect(days=kwargs.pop("days", WINDOW_DAYS),
                          today=kwargs.pop("today", TODAY),
                          persons=persons, elements=elements, general=general,
                          **kwargs)
    finally:
        (ee.MIN_COMPANIES, ee.MIN_EMPLOYEE_FIGURES, ee.MIN_ROSTER,
         ee.FLOOR_APPLIES_ABOVE) = saved


class TheGapThatCannotBeClosed(unittest.TestCase):
    """Appointments only, said on the record and not only in a comment."""

    def test_the_published_file_states_no_end_date_anywhere(self):
        """520,895 of 520,895 rows in the real file. The fixture is a sample of
        it, and if a `lopp_kpv` ever appears this assertion is the first thing
        that will say so."""
        for company in _persons_payload()["companies"]:
            for entry in company["kaardile_kantud_isikud"]:
                self.assertIn("lopp_kpv", entry)
                self.assertIn(entry["lopp_kpv"], (None, ""))

    def test_every_stored_row_says_it_reports_arrivals_only(self):
        """Not filtered at render and not left to a docstring: a reader of one
        row has to be able to see the shape of what is missing."""
        rows = _collect()
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(ee.APPOINTMENTS_ONLY, row["raw_text"])
            self.assertIn(ee.APPOINTMENTS_ONLY, row["summary"])
            classified = ee.as_classified(row)
            self.assertIn(ee.APPOINTMENTS_ONLY, classified["summary"])
            self.assertIn("never report one", classified["talent_readthrough"])

    def test_nothing_here_reads_a_departure_out_of_a_missing_row(self):
        """The refused design: diffing yesterday's file against today's. A row
        that vanished may be a departure, a correction, a merger or a company
        leaving the register, and the file states no date for any of it."""
        source = (ROOT / "collectors" / "estonia_ariregister.py").read_text(
            encoding="utf-8")
        self.assertNotIn("lopp_kpv\"]", source)
        for row in _collect():
            self.assertEqual(row["published_date"], row["appointed_on"])


class ThePersonalData(unittest.TestCase):

    def test_the_fixture_really_does_carry_what_must_not_be_stored(self):
        """A guard whose input is already clean guards nothing."""
        present = set()
        for company in _persons_payload()["companies"]:
            for entry in company["kaardile_kantud_isikud"]:
                present.update(k for k, v in entry.items() if v not in (None, ""))
        for field in ("synniaeg", "aadress_tanav_maja_korter", "isikukood_hash"):
            self.assertIn(field, present)

    def test_scrub_person_returns_a_name_and_nothing_else(self):
        person = ee.scrub_person({
            "eesnimi": "Anti", "nimi_arinimi": "Kõrve",
            "synniaeg": "1975-06-04", "email": "someone@example.ee",
            "isikukood_hash": "77a468f2-248a-5e41-8356-c975af9613a5",
            "aadress_tanav_maja_korter": "Pikk 12-4",
            "aadress_postiindeks": "10133",
        })
        self.assertEqual(set(person), {"given_name", "family_name", "name"})
        self.assertEqual(person["name"], "Anti Kõrve")

    def test_a_birth_date_an_address_and_an_id_hash_reach_no_stored_row(self):
        rows = _collect()
        self.assertTrue(rows)
        forbidden = ["synniaeg", "isikukood", "aadress", "email",
                     "kirje_id", "kaardi_nr"]
        for row in rows:
            signal = validate.build_signal(ee.as_classified(row), row,
                                           ee.COLLECTOR)
            blob = json.dumps(signal.__dict__, ensure_ascii=False, default=str)
            for token in forbidden:
                self.assertNotIn(token, blob, f"{token!r} reached a stored row")
            self.assertEqual(
                set(row) & {"synniaeg", "isikukood_hash", "email",
                            "aadress_tanav_maja_korter", "aadress_postiindeks"},
                set())

    def test_a_person_with_no_surname_is_declined_rather_than_half_stored(self):
        self.assertIsNone(ee.scrub_person({"eesnimi": "Anti"}))
        self.assertIsNone(ee.scrub_person({}))
        self.assertIsNone(ee.scrub_person(None))


class TheMateriality(unittest.TestCase):

    def test_the_floor_is_the_commissions_own_small_enterprise_boundary(self):
        """EU Recommendation 2003/361: micro under 10, small under 50, medium 50
        to 249, large 250 and above."""
        self.assertEqual(ee.DEFAULT_MIN_EMPLOYEES, 50)

    def test_a_company_below_the_floor_stores_nothing(self):
        rows = _collect()
        self.assertNotIn(KODUREMONT, {row["registry_code"] for row in rows})

    def test_a_company_just_over_the_floor_does_store(self):
        rows = _collect()
        self.assertIn(BONDORA, {row["registry_code"] for row in rows})

    def test_every_stored_row_carries_the_headcount_that_admitted_it(self):
        for row in _collect():
            self.assertGreaterEqual(row["employees"], ee.DEFAULT_MIN_EMPLOYEES)
            self.assertIn(ee._employees(row["employees"]), row["summary"])

    def test_a_company_with_no_annual_report_is_excluded(self):
        """No employee figure, no threshold, no row. That is a recall hole the
        docstring names — every company incorporated since the last reporting
        cycle — and not a judgement that they are small."""
        payload = _persons_payload()
        unknown = json.loads(json.dumps(payload["companies"][0]))
        unknown["ariregistri_kood"] = 99999999
        unknown["nimi"] = "Never Filed OÜ"
        rows = _collect(companies=payload["companies"] + [unknown])
        self.assertNotIn("99999999", {row["registry_code"] for row in rows})

    def test_the_floor_can_be_raised_but_never_read_as_a_nonsense(self):
        import os
        os.environ["TIT_EE_MIN_EMPLOYEES"] = "250"
        try:
            self.assertEqual(ee.min_employees(), 250)
        finally:
            del os.environ["TIT_EE_MIN_EMPLOYEES"]
        os.environ["TIT_EE_MIN_EMPLOYEES"] = "lots"
        try:
            with self.assertRaises(ee.AriregisterError):
                ee.min_employees()
        finally:
            del os.environ["TIT_EE_MIN_EMPLOYEES"]

    def test_the_report_files_are_discovered_rather_than_hard_coded(self):
        """The publisher versions these in the FILENAME, so a hard-coded URL
        404s on the day the next cut lands — and a 404 in a materiality filter
        is every company failing the threshold, which looks exactly like a quiet
        fortnight."""
        page = (
            '<a href="/sites/default/files/1.aruannete_yldandmed_kuni_31122026_0.zip">x</a>'
            '<a href="/sites/default/files/4.2024_aruannete_elemendid_kuni_31122026_0.zip">y</a>'
            '<a href="/sites/default/files/4.2026_aruannete_elemendid_kuni_31122026_0.zip">z</a>'
        )
        general, elements = ee.discover_report_files(page=page)
        self.assertTrue(general.endswith("1.aruannete_yldandmed_kuni_31122026_0.zip"))
        self.assertTrue(elements.endswith("4.2026_aruannete_elemendid_kuni_31122026_0.zip"))

    def test_a_download_page_that_stopped_linking_them_is_an_error(self):
        with self.assertRaises(ee.AriregisterError):
            ee.discover_report_files(page="<a href='/nothing.zip'>x</a>")

    def test_a_missing_employee_element_is_an_error_not_an_empty_threshold(self):
        elements = _zip_of(
            "4.2025_aruannete_elemendid_kuni_30062026.csv",
            b'"report_id";"tabel";"elemendi_label";"elemendi_nimetus";"vaartus"\n'
            b'"1";"Bilanss";"Kaibevarad";"CurrentAssets";"2500.0"\n')
        general = _zip_of("1.aruannete_yldandmed_kuni_30062026.csv",
                          GENERAL.read_bytes())
        with self.assertRaises(ee.AriregisterError) as caught:
            ee.employee_counts(elements, general)
        self.assertIn(ee.EMPLOYEE_ELEMENT, str(caught.exception))


class TheRoleAllowlist(unittest.TestCase):

    def test_a_legal_person_on_the_card_is_never_a_hire(self):
        """Nasdaq Csd Se sits on Enefit Industry's card as `ORP`, with the
        company name in the field a human's surname uses. `isiku_tyyp` is the
        only thing that separates them."""
        rows = _collect()
        for row in rows:
            self.assertNotIn("Nasdaq", row["person_name"])
        entries = [e for c in _persons_payload()["companies"]
                   if str(c["ariregistri_kood"]) == ENEFIT
                   for e in c["kaardile_kantud_isikud"]]
        self.assertTrue([e for e in entries if e["isiku_tyyp"] == "J"])
        for entry in entries:
            if entry["isiku_tyyp"] == "J":
                self.assertFalse(ee.is_person(entry))

    def test_owners_are_not_officers(self):
        """BAUHAUS Eesti UÜ clears the headcount floor and holds only `TOSAN`
        and `UOSAN` partners. Holding a stake is not holding an office."""
        rows = _collect()
        self.assertNotIn(BAUHAUS, {row["registry_code"] for row in rows})

    def test_an_unknown_role_code_is_declined_never_guessed(self):
        for code in ee.EXCLUDED_ROLES:
            self.assertNotIn(code, ee.ROLES)
            self.assertFalse(ee.is_person({"isiku_tyyp": "F", "isiku_roll": code}))
        self.assertFalse(ee.is_person({"isiku_tyyp": "F", "isiku_roll": "WHAT"}))

    def test_an_insolvency_appointment_is_not_a_talent_signal(self):
        """A court appointing a liquidator is the sibling tracker's territory,
        not a hire."""
        for code in ("LIKV", "LIKVJ", "PANKR", "AJUTPH"):
            self.assertIn(code, ee.EXCLUDED_ROLES)

    def test_every_allowlisted_role_carries_the_registers_own_label(self):
        for code, (estonian, english) in ee.ROLES.items():
            self.assertTrue(estonian)
            self.assertTrue(english)
            self.assertEqual(code, code.upper())


class TheParsing(unittest.TestCase):

    def test_the_registers_date_format_is_the_only_one_accepted(self):
        self.assertEqual(ee.parse_date("05.06.2023"), "2023-06-05")
        self.assertEqual(ee.parse_date("31.12.2025"), "2025-12-31")
        self.assertIsNone(ee.parse_date("2023-06-05"))
        self.assertIsNone(ee.parse_date("31.02.2025"))
        self.assertIsNone(ee.parse_date(""))
        self.assertIsNone(ee.parse_date("5.6.2023"))

    def test_the_same_appointment_written_twice_stores_once(self):
        """The real file holds 25 exact (role, name, date) repeats inside a
        single company. Metrosert's card in the fixture carries one, appended by
        hand and declared in the fixture's provenance."""
        rows = [r for r in _collect() if r["registry_code"] == METROSERT]
        keys = [(r["person_name"], r["role_code"], r["appointed_on"]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_giant_json_array_is_streamed_and_not_loaded(self):
        """The published file is about 1GB. Reading it with json.load would need
        that much memory in a GitHub Actions runner, so it is decoded
        incrementally — and the incremental reader has to return every company,
        which is what this asserts."""
        persons, _elements, _general = _archives()
        streamed = list(ee.iter_companies(persons))
        self.assertEqual(len(streamed), len(_persons_payload()["companies"]))
        self.assertEqual([c["ariregistri_kood"] for c in streamed],
                         [c["ariregistri_kood"]
                          for c in _persons_payload()["companies"]])


class TheSourceUrl(unittest.TestCase):

    def test_the_citation_is_a_page_that_can_fail(self):
        """A real registry code answers 200 and an invented one answers 303,
        verified live 2026-07-30. A URL that cannot fail is not a receipt."""
        self.assertEqual(ee.register_url("10166316"),
                         "https://ariregister.rik.ee/eng/company/10166316")
        self.assertIsNone(ee.register_url("abc"))
        self.assertIsNone(ee.register_url(""))
        self.assertIsNone(ee.register_url("1234567890"))

    def test_the_source_url_has_a_path_and_is_not_a_bare_domain(self):
        for row in _collect():
            parsed = urlparse(row["source_url"])
            self.assertTrue(parsed.path.strip("/"), row["source_url"])
            self.assertEqual(parsed.hostname, "ariregister.rik.ee")

    def test_the_collector_says_it_revisits_its_own_source_url(self):
        self.assertTrue(ee.REVISITS_ITS_SOURCE_URL)

    def test_the_host_is_a_primary_source(self):
        self.assertIn("ariregister.rik.ee", vocab.PRIMARY_SOURCE_DOMAINS)


class TheRecord(unittest.TestCase):

    def setUp(self):
        self.rows = _collect()

    def test_the_run_produces_what_the_fixture_holds(self):
        self.assertTrue(self.rows)
        codes = {row["registry_code"] for row in self.rows}
        self.assertIn(BAUHOF, codes)
        self.assertIn(GPV, codes)
        self.assertNotIn(KODUREMONT, codes)
        self.assertNotIn(BAUHAUS, codes)

    def test_every_row_survives_the_real_pipeline(self):
        for row in self.rows:
            signal = validate.build_signal(ee.as_classified(row), row,
                                           ee.COLLECTOR)
            self.assertEqual(signal.pillar, "leadership_change")
            self.assertEqual(signal.country, "EE")
            self.assertEqual(signal.confidence, "verified")
            self.assertEqual(signal.signal_direction, "neutral")

    def test_raw_text_is_set_on_every_row(self):
        for row in self.rows:
            self.assertTrue(row.get("raw_text"))
            self.assertIn(row["headline"], row["raw_text"])

    def test_the_summary_is_a_literal_prefix_of_the_source_text(self):
        """Twelve of the first 66 rows built here were discarded for inventing a
        number they had not invented: `validate._NUMBER` reads
        "on 9 June 2026. BAUHOF" as the figure `2026b`, a defect it names and
        deliberately leaves alone. Composing the summary once and reusing it is
        what makes the two sentences impossible to diverge. BAUHOF GROUP AS is
        in the fixture for exactly this."""
        self.assertIn(BAUHOF, {row["registry_code"] for row in self.rows})
        for row in self.rows:
            self.assertIn(row["summary"], row["raw_text"])
            self.assertEqual(ee.as_classified(row)["summary"], row["summary"])
            validate.assert_figures_are_sourced(row["summary"], row["raw_text"])

    def test_diacritics_round_trip_unchanged_into_a_stored_signal(self):
        names = {row["person_name"] for row in self.rows}
        self.assertTrue([n for n in names if any(ch in n for ch in "õäöüšžŠŽÕÄÖÜ")],
                        names)
        for row in self.rows:
            signal = validate.build_signal(ee.as_classified(row), row,
                                           ee.COLLECTOR)
            self.assertIn(row["person_name"], signal.headline)
            self.assertIn(row["person_name"], signal.summary)
            self.assertIn(row["company"], signal.summary)

    def test_no_city_is_stated_because_the_file_carries_none(self):
        for row in self.rows:
            classified = ee.as_classified(row)
            self.assertNotIn("headquarters_city", classified)
            self.assertEqual(classified["headquarters_country"], "Estonia")

    def test_a_run_derives_its_record_and_never_calls_a_model(self):
        self.assertTrue(hasattr(ee, "as_classified"))
        source = (ROOT / "collectors" / "estonia_ariregister.py").read_text(
            encoding="utf-8")
        for banned in ("openrouter", "classify.classify", "OPENROUTER_API_KEY"):
            self.assertNotIn(banned, source)

    def test_the_paid_change_service_is_refused_rather_than_used(self):
        source = (ROOT / "collectors" / "estonia_ariregister.py").read_text(
            encoding="utf-8")
        self.assertIn("ettevotjaMuudatusedTasuline_v1", source)
        self.assertIn("Refused", source)


class TheFailureModes(unittest.TestCase):

    def test_a_truncated_persons_download_fails_loudly(self):
        """375,305 companies is the measured size. A national register cannot
        halve, so a small answer is a truncated download and not a smaller
        Estonia."""
        persons, elements, general = _archives()
        with self.assertRaises(ee.AriregisterError) as caught:
            saved = (ee.MIN_EMPLOYEE_FIGURES, ee.MIN_ROSTER)
            ee.MIN_EMPLOYEE_FIGURES, ee.MIN_ROSTER = 1, 1
            try:
                ee.collect(days=WINDOW_DAYS, today=TODAY, persons=persons,
                           elements=elements, general=general)
            finally:
                ee.MIN_EMPLOYEE_FIGURES, ee.MIN_ROSTER = saved
        self.assertIn("375,305", str(caught.exception))

    def test_a_small_roster_below_the_floor_is_not_an_incident(self):
        self.assertEqual(ee.emptiness_floor(0, 21), 0)
        self.assertEqual(ee.emptiness_floor(ee.FLOOR_APPLIES_ABOVE - 1, 21), 0)
        self.assertEqual(ee.emptiness_floor(825, 21), 3)

    def test_a_response_that_is_not_a_zip_is_an_error(self):
        class _Resp:
            status_code = 200
            content = b"<html>Not Acceptable!</html>"

        class _Session:
            def get(self, *a, **k):
                return _Resp()

        with self.assertRaises(ee.AriregisterError):
            ee.fetch_zip("https://example.invalid/x.zip", session=_Session())


class TheWiring(unittest.TestCase):

    def test_the_collector_is_registered(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["estonia_ariregister"], ee)

    def test_it_has_a_schedule_derived_staleness_leash(self):
        import staleness
        self.assertIn("estonia_ariregister", staleness.MAX_AGE_HOURS)
        self.assertEqual(staleness.MAX_AGE_HOURS["estonia_ariregister"], 180)

    def test_it_is_on_the_sources_page_with_a_collector_behind_it(self):
        import source_registry as registry
        names = [n for n, c in registry.COLLECTOR_BY_SOURCE_NAME.items()
                 if c == "estonia_ariregister"]
        self.assertEqual(len(names), 1)
        listed = [s for s in registry.SOURCES if s.name == names[0]]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].status, "live")
        self.assertEqual(listed[0].country, "EE")

    def test_the_sources_page_note_states_the_appointments_only_limit(self):
        """The gap has to be visible to a reader of the sources page too, not
        only to a reader of one row."""
        import source_registry as registry
        names = [n for n, c in registry.COLLECTOR_BY_SOURCE_NAME.items()
                 if c == "estonia_ariregister"]
        listed = [s for s in registry.SOURCES if s.name == names[0]][0]
        self.assertIn("appointments", listed.notes.lower())
        self.assertIn("departure", listed.notes.lower())

    def test_the_workflow_runs_it_on_a_day_no_other_writer_holds(self):
        import yaml

        path = ROOT / ".github" / "workflows" / "collect-structured.yml"
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        crons = [c["cron"] for c in workflow[True]["schedule"]]
        self.assertIn("0 4 * * 6", crons)
        self.assertEqual(len(crons), len(set(crons)))
        self.assertIn("estonia_ariregister",
                      path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
