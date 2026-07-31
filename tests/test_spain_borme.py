"""Spain's spine must stay an inscribed act, must stay a chief executive, and
must never report a departure the register did not record.

Four families of failure are guarded here.

**The cancel-and-re-inscribe pair**, which is where this source goes wrong
first and worst. A Spanish board renewal is inscribed as a total cancellation
followed by a total re-appointment, so 46 of 373 person-company-date keys in
the measured week carry the SAME office in both directions and nobody left. A
collector that stores both halves reports a leaving rate that is not real —
the Czech `datumVymazu` finding in a new shape. And the obvious over-correction
is wrong too: SPLA SA ceased Javier Muñoz Gómez as `Con.Delegado` and appointed
him `Cons.Del.Sol` on the same date, which IS a change the register made, and
collapsing on the person alone would delete it.

**The URL**, because the nice one is forbidden. `boe.es/robots.txt` disallows
`/diario_borme/xml.php?`, which is the clean XML of exactly this text. A test
asserts the collector never names that path.

**The office filter.** BORME carries 494 board-grade acts a day and 64 at the
consejero delegado. A filter that quietly widens turns Spain into 123,455 rows
a year and the largest thing in the database by an order of magnitude.

**The personal data.** BORME publishes a name and nothing else for these acts
today, so unlike the Czech and Estonian files there is nothing to strip. The
fixture therefore INVENTS a birth date, a DNI and a home address inside a
real-shaped entry, because the guard has to hold against the bulletin changing
rather than against the bulletin as it is.

Recorded fixture, never a live call. Every real paragraph in it was fetched
keyless from www.boe.es on 2026-07-30; see the fixture's own `_provenance`
block for what was trimmed, what was invented and why.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from collectors import spain_borme as es
from pipeline import validate, vocab

FIXTURE = Path(__file__).parent / "fixtures" / "spain_borme_bulletin.json"
ROOT = Path(__file__).parent.parent

DAY = date(2026, 7, 22)


def _fixture() -> dict:
    with FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


def _collect(*, documents=None, summaries=None, today=DAY, days=1):
    """Run the whole path against the fixture with the live floors relaxed.

    The floors are measured against the real bulletin — 28 to 32 province files
    and ~2,230 company entries a day — and a fixture is deliberately smaller
    than either. They are restored in every case, so a test that forgets cannot
    disarm the guard for the ones after it.
    """
    payload = _fixture()
    saved = (es.MIN_PROVINCE_FILES_PER_DAY, es.MIN_ENTRIES_PER_DAY,
             es.FLOOR_EVENTS_PER_DAY)
    es.MIN_PROVINCE_FILES_PER_DAY = 1
    es.MIN_ENTRIES_PER_DAY = 1
    es.FLOOR_EVENTS_PER_DAY = 0
    try:
        return es.collect(
            days=days, today=today,
            summaries=summaries or {DAY: payload["summary_payload"]},
            documents=documents if documents is not None
            else payload["documents"])
    finally:
        (es.MIN_PROVINCE_FILES_PER_DAY, es.MIN_ENTRIES_PER_DAY,
         es.FLOOR_EVENTS_PER_DAY) = saved


class TheCancelAndReinscribePair(unittest.TestCase):
    """The finding that decides whether this source tells the truth."""

    def test_the_same_person_at_the_same_office_on_one_date_is_not_a_departure(self):
        rows = _collect()
        gonher = [row for row in rows if "GONHER" in row["company"]]
        self.assertEqual(
            gonher, [],
            "GONHER SA cancels and re-inscribes Vicente José Anguiz Cortés as "
            "Con.Delegado on one date. Neither half is an event; storing the "
            "cese reports a departure that did not happen.")

    def test_a_pair_at_two_different_offices_survives_both_halves(self):
        rows = _collect()
        spla = sorted((row["act_direction"], row["office"]) for row in rows
                      if row["company"].startswith("SPLA"))
        self.assertEqual(
            spla,
            [("arrival", "Consejero delegado solidario"),
             ("departure", "Consejero delegado")],
            "SPLA SA moved one person from a sole delegation to a joint one. "
            "The register states two offices, so there are two events.")

    def test_the_collapse_keys_on_office_and_not_on_the_person_alone(self):
        rows = [
            {"registry_entry": "1", "person_name": "A", "office": "X",
             "inscribed_on": "2026-07-15", "act_direction": "arrival"},
            {"registry_entry": "1", "person_name": "A", "office": "X",
             "inscribed_on": "2026-07-15", "act_direction": "departure"},
            {"registry_entry": "1", "person_name": "A", "office": "Y",
             "inscribed_on": "2026-07-15", "act_direction": "arrival"},
        ]
        kept, dropped = es.drop_reinscriptions(rows)
        self.assertEqual(dropped, 2)
        self.assertEqual([row["office"] for row in kept], ["Y"])

    def test_two_different_people_at_one_office_are_a_real_succession(self):
        rows = _collect()
        olympus = sorted((row["act_direction"], row["person_name"])
                         for row in rows if "OLYMPUS" in row["company"])
        self.assertEqual(
            olympus,
            [("arrival", "JEROME GERARD MARIE CHEVILLOTTE"),
             ("departure", "GABRIELE UTA MOLNAR SLANY")],
            "one holder replaced by another is exactly the event this source "
            "exists to report, and must survive the collapse.")


class TheForbiddenUrl(unittest.TestCase):
    def test_the_collector_never_names_the_robots_disallowed_xml_path(self):
        source = (ROOT / "collectors" / "spain_borme.py").read_text(
            encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith(("#", "*")))
        # The docstring explains WHY the path is refused, so the ban is on
        # building a request out of it rather than on the eleven letters.
        self.assertNotIn("xml.php?id=", code.split('"""', 2)[-1])
        self.assertIn("diario_borme/txt.php", es.DOCUMENT_URL)

    def test_every_stored_row_cites_boe_and_nothing_else(self):
        for row in _collect():
            host = urlparse(row["source_url"]).netloc
            self.assertEqual(host, "www.boe.es")
            self.assertIn("diario_borme/txt.php", row["source_url"])

    def test_the_cited_host_is_a_primary_source(self):
        """Without this the whole country caps at `reported`."""
        self.assertIn("www.boe.es", vocab.PRIMARY_SOURCE_DOMAINS)
        self.assertIn("boe.es", vocab.PRIMARY_SOURCE_DOMAINS)


class TheOfficeFilter(unittest.TestCase):
    def test_only_the_delegated_directorship_is_collected(self):
        for row in _collect():
            self.assertIn(row["office_label"], es.OFFICES)
            self.assertTrue(row["office"].startswith("Consejero delegado"))

    def test_the_declined_offices_are_data_so_widening_is_deliberate(self):
        overlap = set(es.OFFICES) & set(es.OFFICES_DECLINED)
        self.assertEqual(overlap, set(),
                         "an office cannot be both read and declined")
        for wide in ("Presidente", "Consejero", "Adm. Unico"):
            self.assertIn(wide, es.OFFICES_DECLINED)

    def test_an_entry_whose_offices_are_all_declined_yields_nothing(self):
        rows = _collect()
        self.assertEqual([row for row in rows if "IDILIA" in row["company"]], [])

    def test_a_re_election_is_declined_because_leadership_did_not_change(self):
        rows = _collect()
        self.assertEqual([row for row in rows if "DISBUR" in row["company"]], [])
        self.assertIn("Reelecciones", es.CONTINUATIONS)
        self.assertNotIn("Reelecciones", es.ARRIVALS + es.DEPARTURES)

    def test_a_legal_person_holding_the_office_is_declined(self):
        rows = _collect()
        self.assertEqual([row for row in rows if "AM FRESH" in row["company"]],
                         [])
        for corporate in ("BLUEMED EXPERIENCES SL",
                          "BLACKROCK TECH&FIN INVESTMENTS SL",
                          "MARCRISFER SL", "TALDE ADVISOR, S.L.U.",
                          "ACME HOLDING SA"):
            self.assertTrue(es.is_legal_person(corporate), corporate)
        for human in ("MUÑOZ GOMEZ JAVIER", "GABRIELE UTA MOLNAR SLANY",
                      "MARC BAIGET MORENO", "SAENZ MATALLANA ALVARO NICOLAS"):
            self.assertFalse(es.is_legal_person(human), human)


class TheInscriptionDate(unittest.TestCase):
    def test_the_two_digit_year_pivots_on_the_publication_date(self):
        """`(03.02.97)` is 1997. Read as 2097 it is a date 71 years out."""
        self.assertEqual(
            es.inscribed_on("Datos registrales. S 8 , H M 1, I/A 5 (03.02.97).",
                            published=DAY),
            date(1997, 2, 3))
        self.assertEqual(
            es.inscribed_on("Datos registrales. S 8 , H M 1, I/A 5 (20.07.26).",
                            published=DAY),
            date(2026, 7, 20))

    def test_an_entry_with_no_inscription_date_is_declined(self):
        rows = _collect()
        self.assertEqual(
            [row for row in rows if "SIN FECHA" in row["company"]], [],
            "the publication date is not the event date, and stamping it would "
            "be a figure the bulletin never stated.")

    def test_an_old_inscription_is_declined_rather_than_dated_today(self):
        rows = _collect()
        for stale in ("VIEJA INSCRIPCION", "ATRASADA"):
            self.assertEqual([row for row in rows if stale in row["company"]],
                             [], stale)
        self.assertEqual(es.MAX_BACKLOG_DAYS, 365)

    def test_the_row_carries_the_inscription_date_not_the_publication_date(self):
        for row in _collect():
            self.assertEqual(row["published_date"], row["inscribed_on"])
            self.assertLess(row["inscribed_on"], row["published_on"])


class ThePersonalData(unittest.TestCase):
    def test_nothing_but_a_name_survives_the_collector_boundary(self):
        payload = _fixture()
        probe = payload["personal_data_probe"]
        documents = dict(payload["documents"])
        documents[probe["ident"]] = probe["html"]
        rows = _collect(documents=documents)
        leaked = [row for row in rows if "SOLER MARTI" in row["person_name"]]
        self.assertTrue(leaked, "the probe entry must be read, not skipped")
        self.assertEqual(leaked[0]["person_name"], "SOLER MARTI CARMEN")
        for row in rows:
            signal = validate.build_signal(es.as_classified(row), row,
                                           es.COLLECTOR)
            blob = json.dumps(signal.__dict__, ensure_ascii=False, default=str)
            for token in probe["forbidden"]:
                self.assertNotIn(token, blob,
                                 f"{token!r} reached a stored row")
                self.assertNotIn(token, json.dumps(row, ensure_ascii=False))

    def test_scrub_person_returns_a_name_and_nothing_else(self):
        scrubbed = es.scrub_person({"name": "  SOLER MARTI CARMEN ",
                                    "birth_date": "1975-06-04",
                                    "dni": "12345678Z",
                                    "address": "Calle Mayor 1"})
        self.assertEqual(scrubbed, {"name": "SOLER MARTI CARMEN"})
        # The shape the bulletin would actually take if it grew these fields:
        # inside the name string, where a dict key cannot help.
        self.assertEqual(
            es.scrub_person("SOLER MARTI CARMEN (nacida el 04.06.1975, DNI "
                            "12345678Z, domicilio en Calle Mayor 1, 28013 "
                            "Madrid)"),
            {"name": "SOLER MARTI CARMEN"})
        # A name with a digit left in it is not a name. Two of 534 real holder
        # strings carry one and both are companies.
        self.assertIsNone(es.scrub_person("GRUPO MOORE 2019 SL"))
        self.assertIsNone(es.scrub_person(""))
        self.assertIsNone(es.scrub_person(None))

    def test_the_dropped_field_vocabulary_is_written_down(self):
        for field in ("birth_date", "dni", "address", "nacionalidad"):
            self.assertIn(field, es.PERSONAL_FIELDS_DROPPED)


class TheNames(unittest.TestCase):
    def test_diacritics_round_trip_and_nothing_is_recased(self):
        rows = _collect()
        stored = {row["person_name"] for row in rows}
        for name in ("MUÑOZ AÑÓN JOSÉ MARÍA", "GOIKOETXEA ARRIETA IÑIGO",
                     "MUÑOZ GOMEZ JAVIER"):
            self.assertIn(name, stored)

    def test_the_register_prints_both_name_orders_and_neither_is_reordered(self):
        """`AROSA BELASTEGUI JON` and `MARC BAIGET MORENO` are both real, and
        no field says which order a given entry uses. Guessing would rewrite a
        person's name to make a column look tidy."""
        source = " ".join((ROOT / "collectors" / "spain_borme.py").read_text(
            encoding="utf-8").split())
        self.assertIn("never reordered", source)
        for original in ("AROSA BELASTEGUI JON", "MARC BAIGET MORENO"):
            self.assertEqual(es.scrub_person(original)["name"], original)


class TheSummary(unittest.TestCase):
    def test_the_summary_is_a_literal_prefix_of_the_body(self):
        """Composed once in `_row`, so every figure in it is verbatim in the
        source text by construction. Estonia lost twelve of its first
        sixty-six rows to composing the two sentences separately."""
        for row in _collect():
            classified = es.as_classified(row)
            self.assertEqual(classified["summary"], row["summary"])
            self.assertIn(row["summary"], row["raw_text"])
            self.assertTrue(row["raw_text"].startswith(row["headline"]))

    def test_raw_text_is_never_empty(self):
        """An empty raw_text makes the classifier return None silently. That
        bug once made a whole source post zero rows for a month."""
        for row in _collect():
            self.assertTrue(row["raw_text"].strip())
            self.assertGreater(len(row["raw_text"]), 200)

    def test_every_number_in_the_summary_is_in_the_source_text(self):
        number = re.compile(r"\d[\d.,]*")
        for row in _collect():
            body = row["raw_text"]
            for figure in number.findall(row["summary"]):
                self.assertIn(figure, body)


class TheDerivedRecord(unittest.TestCase):
    def test_it_derives_and_never_calls_a_model(self):
        self.assertTrue(hasattr(es, "as_classified"))
        source = (ROOT / "collectors" / "spain_borme.py").read_text(
            encoding="utf-8")
        for forbidden in ("openrouter", "OPENROUTER", "classify.", "prompts"):
            self.assertNotIn(forbidden, source)

    def test_the_pillar_and_direction_are_fixed(self):
        for row in _collect():
            classified = es.as_classified(row)
            self.assertEqual(classified["pillar"], "leadership_change")
            self.assertEqual(classified["signal_direction"], "neutral")
            self.assertEqual(classified["confidence"], "verified")
            self.assertEqual(classified["country"], "Spain")

    def test_the_province_is_only_a_city_when_the_vocabulary_knows_it(self):
        rows = _collect()
        by_province = {row["province"]: es.as_classified(row) for row in rows}
        self.assertEqual(by_province["BARCELONA"]["city"], "Barcelona")
        self.assertEqual(by_province["MADRID"]["city"], "Madrid")
        self.assertIsNone(by_province["ALICANTE/ALACANT"]["city"],
                          "a province is not a city, and normalize_city never "
                          "invents one")

    def test_the_readthrough_states_the_two_limits(self):
        classified = es.as_classified(_collect()[0])
        readthrough = classified["talent_readthrough"]
        self.assertIn("week", readthrough)      # the publication lag
        self.assertIn("headcount", readthrough)  # no materiality figure exists

    def test_every_row_builds_a_signal(self):
        rows = _collect()
        self.assertTrue(rows)
        for row in rows:
            signal = validate.build_signal(es.as_classified(row), row,
                                           es.COLLECTOR)
            self.assertIsNotNone(signal, row["headline"])
            self.assertEqual(signal.pillar, "leadership_change")
            self.assertEqual(signal.country, "ES")


class TheBulletinShape(unittest.TestCase):
    def test_the_alphabetical_index_is_not_a_province(self):
        payload = _fixture()
        items = es.section_a_items(payload["summary_payload"])
        idents = [ident for ident, _ in items]
        self.assertNotIn("BORME-A-2026-139-99", idents)
        self.assertEqual(len(items), 6)

    def test_every_level_of_the_summary_may_arrive_singular(self):
        """One diario, one seccion and one item all arrive as objects rather
        than one-element arrays, and Section B arrives with no `item` key at
        all on a quiet day. Indexing `[0]` works right up until it does not."""
        singular = {"status": {"code": "200"}, "data": {"sumario": {"diario": {
            "seccion": {"codigo": "A", "item":
                        {"identificador": "BORME-A-2026-139-28",
                         "titulo": "MADRID"}}}}}}
        self.assertEqual(es.section_a_items(singular),
                         [("BORME-A-2026-139-28", "MADRID")])
        no_items = {"status": {"code": "200"}, "data": {"sumario": {"diario": [
            {"seccion": [{"codigo": "B", "nombre": "SEGUNDA"}]}]}}}
        self.assertEqual(es.section_a_items(no_items), [])
        self.assertEqual(es.section_a_items({}), [])

    def test_a_day_with_no_bulletin_is_a_holiday_and_not_a_break(self):
        rows = _collect(summaries={DAY: {}}, documents={})
        self.assertEqual(rows, [])

    def test_the_act_headings_are_the_registers_own_words(self):
        for heading in es.ARRIVALS + es.DEPARTURES + es.CONTINUATIONS:
            self.assertIn(heading, es.ACT_HEADINGS)

    def test_split_acts_reads_a_paragraph_in_the_printed_order(self):
        paragraph = ("Ceses/Dimisiones. Consejero: A B C. Nombramientos. "
                     "Con.Delegado: D E F. Datos registrales. S 8 , H M 1, "
                     "I/A 2 (20.07.26).")
        self.assertEqual([act for act, _ in es.split_acts(paragraph)],
                         ["Ceses/Dimisiones", "Nombramientos",
                          "Datos registrales"])

    def test_holders_splits_a_semicolon_list(self):
        found = es.holders("Con.Delegado: PRIMERO UNO;SEGUNDO DOS.")
        self.assertEqual(found,
                         [("Con.Delegado", "Consejero delegado",
                           ["PRIMERO UNO", "SEGUNDO DOS"])])


class TheGuards(unittest.TestCase):
    def test_a_broken_parser_raises_rather_than_reporting_a_quiet_week(self):
        payload = _fixture()
        blank = {ident: '<div id="textoxslt"></div>'
                 for ident in payload["documents"]}
        with self.assertRaises(es.BormeError):
            es.collect(days=1, today=DAY,
                       summaries={DAY: payload["summary_payload"]},
                       documents=blank)

    def test_a_shrunken_summary_raises(self):
        payload = _fixture()
        one = json.loads(json.dumps(payload["summary_payload"]))
        section = one["data"]["sumario"]["diario"]["seccion"][0]
        section["item"] = section["item"][:1]
        with self.assertRaises(es.BormeError):
            es.collect(days=1, today=DAY, summaries={DAY: one},
                       documents=payload["documents"])

    def test_the_window_is_bounded(self):
        self.assertEqual(es.days_from_env(), es.DEFAULT_DAYS)
        self.assertLessEqual(es.days_from_env(10_000), es.MAX_DAYS)

    def test_the_emptiness_floor_scales_with_the_window(self):
        self.assertEqual(es.emptiness_floor(0), 0)
        self.assertGreaterEqual(es.emptiness_floor(7), 1)
        self.assertGreater(es.emptiness_floor(28), es.emptiness_floor(7))

    def test_last_run_reports_what_was_read_and_not_what_was_stored(self):
        rows = _collect()
        self.assertGreater(es.LAST_RUN["read"], len(rows))
        for key in ("entries", "province_files", "publication_days",
                    "reinscriptions", "events"):
            self.assertIn(key, es.LAST_RUN)


class TheRegistration(unittest.TestCase):
    def test_it_is_registered_as_a_collector(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["spain_borme"], es)

    def test_it_has_a_staleness_leash(self):
        import staleness
        self.assertIn("spain_borme", staleness.MAX_AGE_HOURS)
        self.assertEqual(staleness.MAX_AGE_HOURS["spain_borme"], 180)

    def test_it_is_joined_to_a_source_name(self):
        import source_registry
        names = [name for name, collector
                 in source_registry.COLLECTOR_BY_SOURCE_NAME.items()
                 if collector == "spain_borme"]
        self.assertEqual(names, [es.SOURCE_NAME])
        self.assertIn(es.SOURCE_NAME,
                      [source.name for source in source_registry.SOURCES])

    def test_the_workflow_can_run_it(self):
        text = (ROOT / ".github" / "workflows"
                / "collect-structured.yml").read_text(encoding="utf-8")
        self.assertIn("spain_borme", text)

    def test_it_revisits_its_source_url(self):
        """One province file carries up to 653 company entries, so many rows
        share a URL. Without this the second row from a file is skipped as
        already seen."""
        self.assertTrue(es.REVISITS_ITS_SOURCE_URL)


if __name__ == "__main__":
    unittest.main()
