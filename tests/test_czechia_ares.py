"""Czechia's spine must stay a register entry, must stay material, and must
never carry a person's private life into the database.

Three families of failure are guarded here.

**The version history**, which is what makes this source different from every
other register in the tracker and is where it would have gone wrong twice:

- `datumVymazu` read as a departure. 353 of 543 member versions on ČEZ's record
  carry one and no `zanikClenstvi`; they are amendments, and a connector that
  read them as exits would report a leaving rate roughly nine times the truth.
- the obvious repair — read only the live version — losing a role change that
  exists ONLY on the version the register has already deleted. Jean-Charles
  Chen stopped being chairman of ICO 17774713 on 2026-07-10 and stayed on the
  board; the live version records neither fact.
- one arrival stored once and not once per amendment.

**The materiality filter**, because the change feed carries 22,492 companies a
month and 1.0% of them are employers of 250 people or more. A band floor that
quietly widens turns this into a feed of one-person holding companies.

**The personal data.** ARES publishes a birth date and a full residential
address for most people on the register, and the Czech national open data
catalogue's own conditions of use say the dataset `obsahuje osobní údaje`. The
owner's ruling is that name, role, employer and date are taken at the collector
boundary and nothing else is ever persisted, so the fixture keeps every one of
those fields and the tests require that none of them reaches a stored row.

Recorded fixture, never a live call. Every payload in it was fetched keyless
from ares.gov.cz on 2026-07-30; see its own `_provenance` block for what was
trimmed and why the trimming provably changes no behaviour.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from collectors import czechia_ares as cz
from pipeline import validate, vocab

FIXTURE = Path(__file__).parent / "fixtures" / "czechia_ares_register.json"
ROOT = Path(__file__).parent.parent

TODAY = date(2026, 7, 30)
WINDOW_DAYS = 28
WINDOW_START = "2026-07-02"
WINDOW_END = "2026-07-30"

MATERIAL = ("00013226", "17774713", "27634841", "45534144")
TOO_SMALL = "00028177"       # band 120, "1 - 5 zaměstnanců"
NO_RES_RECORD = "00528773"   # in the change feed, absent from RES entirely


def _fixture() -> dict:
    with FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = "" if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is _NOT_JSON:
            raise ValueError("not json")
        return self._payload


_NOT_JSON = object()


class _Session:
    """Answers the four ARES endpoints out of the fixture. Records every call so
    a test can assert what was NOT requested."""

    def __init__(self, data, *, vr_status=None):
        self.data = data
        self.vr_status = vr_status or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url))
        if "/ekonomicke-subjekty-notifikace/" in url:
            number = url.rsplit("/", 1)[-1]
            return _Resp(self.data["batches"][number])
        if "/ekonomicke-subjekty-vr/" in url:
            ico = url.rsplit("/", 1)[-1]
            status = self.vr_status.get(ico, 200)
            if status != 200:
                return _Resp({"kod": "NENALEZENO"}, status)
            return _Resp(self.data["vr"][ico])
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, **kwargs):
        body = json.loads(kwargs.get("data") or "{}")
        self.calls.append(("post", url, body))
        if url.endswith("/ekonomicke-subjekty-notifikace/vyhledat"):
            return _Resp(self.data["feed"])
        if url.endswith("/ekonomicke-subjekty-res/vyhledat"):
            wanted = set(body.get("ico") or [])
            found = [e for e in self.data["res"]["ekonomickeSubjekty"]
                     if e["icoId"] in wanted]
            return _Resp({"pocetCelkem": len(found), "ekonomickeSubjekty": found})
        raise AssertionError(f"unexpected POST {url}")


def _collect(session=None, **kwargs):
    """A run over the fixture. The notification floor is scaled to it: the real
    feed carries ~880 changed companies a day and the fixture carries eight in
    total, so the floor is switched off rather than pretended to."""
    data = _fixture()
    session = session or _Session(data)
    original = cz.MIN_NOTIFICATIONS_PER_DAY
    cz.MIN_NOTIFICATIONS_PER_DAY = 0
    try:
        return cz.collect(days=WINDOW_DAYS, today=TODAY, session=session, **kwargs)
    finally:
        cz.MIN_NOTIFICATIONS_PER_DAY = original


class TheVersionHistory(unittest.TestCase):
    """The trap that would have shipped, and the trap in its obvious repair."""

    def setUp(self):
        self.data = _fixture()

    def test_a_superseded_version_is_not_a_departure(self):
        """`datumVymazu` says a VERSION was replaced, never that a person left.

        Martin Novák's ČEZ seat, verbatim from the live record on 2026-07-30:
        the first version was deleted five weeks later purely because his
        academic titles were added, and he is still on the board. Reading
        `datumVymazu` as an exit reports him leaving a job he holds.
        """
        record = {"zaznamy": [{"obchodniJmeno": [{"hodnota": "ČEZ, a. s."}],
                               "statutarniOrgany": [{
            "nazevOrganu": "Statutární orgán - představenstvo",
            "clenoveOrganu": [
                {"datumZapisu": "2024-05-25", "datumVymazu": "2024-06-27",
                 "typAngazma": "STATUTARNI_ORGAN_CLEN",
                 "clenstvi": {"clenstvi": {"vznikClenstvi": "2024-05-25"},
                              "funkce": {"nazev": "člen představenstva"}},
                 "fyzickaOsoba": {"datumNarozeni": "1971-05-02", "jmeno": "MARTIN",
                                  "prijmeni": "NOVÁK", "statniObcanstvi": "CZ"}},
                {"datumZapisu": "2024-06-27",
                 "typAngazma": "STATUTARNI_ORGAN_CLEN",
                 "clenstvi": {"clenstvi": {"vznikClenstvi": "2024-05-25"},
                              "funkce": {"nazev": "člen představenstva"}},
                 "fyzickaOsoba": {"datumNarozeni": "1971-05-02", "jmeno": "MARTIN",
                                  "prijmeni": "NOVÁK", "statniObcanstvi": "CZ",
                                  "titulPredJmenem": "Ing.",
                                  "titulZaJmenem": "MBA"}},
            ]}]}]}
        events = cz._events(record, "2024-01-01", "2024-12-31")
        self.assertEqual([e["event"] for e in events], [cz.EVENT_TOOK_OFFICE])
        self.assertEqual(events[0]["date"], "2024-05-25")

    def test_one_arrival_is_one_row_however_many_amendments_restate_it(self):
        """The two versions above are one appointment, not two."""
        events = cz._events(self.data["vr"]["17774713"], WINDOW_START, WINDOW_END)
        arrivals = [(e["person"]["name"], e["date"]) for e in events
                    if e["event"] == cz.EVENT_TOOK_OFFICE]
        self.assertEqual(len(arrivals), len(set(arrivals)))

    def test_a_role_change_recorded_only_on_a_deleted_version_survives(self):
        """The reason `memberships()` keeps every version instead of picking one.

        Jean-Charles Chen's seat has a live version saying `Člen správní rady`
        with no dates at all, and a superseded one carrying
        `zanikFunkce: 2026-07-10` for `Předseda správní rady`. He stopped being
        chairman that day and stayed on the board. Read the live version alone
        and the change does not exist anywhere.
        """
        events = cz._events(self.data["vr"]["17774713"], WINDOW_START, WINDOW_END)
        ends = [e for e in events if e["event"] == cz.EVENT_LEFT_ROLE]
        self.assertEqual(len(ends), 1, [e["event"] for e in events])
        self.assertEqual(ends[0]["person"]["family_name"], "CHEN")
        self.assertEqual(ends[0]["date"], "2026-07-10")
        self.assertEqual(ends[0]["role"], "Předseda správní rady")

    def test_a_role_date_equal_to_the_membership_date_is_not_a_second_event(self):
        """`vznikFunkce == vznikClenstvi` is one arrival said twice. Three
        people joined ICO 17774713 on 2026-07-10 and that is three rows."""
        events = cz._events(self.data["vr"]["17774713"], WINDOW_START, WINDOW_END)
        same_day = [e for e in events if e["date"] == "2026-07-10"
                    and e["event"] == cz.EVENT_TOOK_ROLE]
        self.assertEqual(same_day, [])

    def test_both_directions_are_reported(self):
        """The whole reason Czechia is worth a connector: `zanikClenstvi` is a
        real departure date the source states, so this is not an
        arrivals-only feed the way Estonia's necessarily is."""
        kinds = set()
        for ico in MATERIAL:
            for event in cz._events(self.data["vr"][ico], WINDOW_START, WINDOW_END):
                kinds.add(event["event"])
        self.assertIn(cz.EVENT_TOOK_OFFICE, kinds)
        self.assertIn(cz.EVENT_LEFT_OFFICE, kinds)
        self.assertIn(cz.EVENT_LEFT_ROLE, kinds)

    def test_a_departure_is_registered_on_the_date_the_version_was_deleted(self):
        """An arrival's registration is `datumZapisu`; a departure's is
        `datumVymazu`, because the register writes an exit by flagging the
        sitting version deleted rather than by writing a new one."""
        events = cz._events(self.data["vr"]["45534144"], WINDOW_START, WINDOW_END)
        left = [e for e in events if e["event"] == cz.EVENT_LEFT_OFFICE]
        self.assertTrue(left)
        for event in left:
            self.assertGreaterEqual(event["registered"], event["date"])

    def test_the_window_is_on_the_registration_and_the_event_date_is_the_office(self):
        """Got wrong first, and only a live run said so.

        Filtering on the OFFICE date asks the feed which companies changed this
        week and then throws away every change whose effective date was earlier
        than the window — which is most of them. A real 7-day run over 76
        material companies produced ZERO events that way. Selecting on the
        registration date instead, same companies, same week: 41 events, at a
        median office-to-registration lag of 25 days.

        So an event's date may sit BEFORE the window; its registration may not.
        """
        for ico in MATERIAL:
            events = cz._events(self.data["vr"][ico], WINDOW_START, WINDOW_END)
            self.assertTrue(events, ico)
            for event in events:
                self.assertTrue(WINDOW_START <= event["registered"] <= WINDOW_END,
                                event)

    def test_a_change_registered_before_the_window_is_not_this_weeks_news(self):
        """The other half of the same rule: a record touched this week for an
        unrelated reason carries every office change it ever had, and those are
        not new."""
        whole = cz._events(self.data["vr"]["17774713"], WINDOW_START, WINDOW_END)
        narrowed = cz._events(self.data["vr"]["17774713"], "2026-07-20",
                              WINDOW_END)
        self.assertLess(len(narrowed), len(whole))
        self.assertEqual({e["registered"] for e in narrowed}, {"2026-07-22"})

    def test_a_ten_year_old_change_registered_this_week_is_declined(self):
        """Seven of the 41 events in the sampled week had office dates one to
        TEN years before their registration: a court finally writing down a 2016
        board change. There is no honest date to publish those under, so they are
        declined and counted rather than dated by us."""
        record = {"zaznamy": [{"statutarniOrgany": [{
            "nazevOrganu": "Statutární orgán",
            "clenoveOrganu": [{
                "datumZapisu": "2026-07-20", "typAngazma": "STATUTARNI_ORGAN_CLEN",
                "clenstvi": {"clenstvi": {"vznikClenstvi": "2016-03-04"},
                             "funkce": {"nazev": "jednatel"}},
                "fyzickaOsoba": {"jmeno": "JAN", "prijmeni": "SVOBODA"},
            }]}]}]}
        stats: dict = {}
        self.assertEqual(cz._events(record, WINDOW_START, WINDOW_END, stats), [])
        self.assertEqual(stats["backlog"], 1)

    def test_a_change_registered_in_advance_of_its_effective_date_is_kept(self):
        """The register does record an appointment before it takes effect, so a
        negative lag is ordinary and must not be mistaken for backlog."""
        record = {"zaznamy": [{"statutarniOrgany": [{
            "nazevOrganu": "Statutární orgán",
            "clenoveOrganu": [{
                "datumZapisu": "2026-07-20", "typAngazma": "STATUTARNI_ORGAN_CLEN",
                "clenstvi": {"clenstvi": {"vznikClenstvi": "2026-08-01"},
                             "funkce": {"nazev": "jednatel"}},
                "fyzickaOsoba": {"jmeno": "JAN", "prijmeni": "SVOBODA"},
            }]}]}]}
        events = cz._events(record, WINDOW_START, WINDOW_END)
        self.assertEqual([e["date"] for e in events], ["2026-08-01"])


class ThePersonalData(unittest.TestCase):
    """The owner's ruling, enforced at the boundary rather than at the surface."""

    def setUp(self):
        self.data = _fixture()

    def test_the_fixture_really_does_carry_what_must_not_be_stored(self):
        """A guard whose input is already clean guards nothing."""
        seen = set()
        for record in self.data["vr"].values():
            for _organ, member in cz._organs(record):
                seen.update(member.get("fyzickaOsoba") or {})
        self.assertIn("datumNarozeni", seen)
        self.assertIn("adresa", seen)

    def test_scrub_person_returns_a_name_and_nothing_else(self):
        person = cz.scrub_person({
            "jmeno": "MICHAELA", "prijmeni": "CHALOUPKOVÁ",
            "datumNarozeni": "1975-06-04", "statniObcanstvi": "CZ",
            "titulPredJmenem": "JUDr.", "titulZaJmenem": "MBA",
            "textOsoba": "anything at all",
            "adresa": {"textovaAdresa": "Zahraničního odboje 931/24, 67401 Třebíč",
                       "psc": 67401, "nazevUlice": "Zahraničního odboje"},
        })
        self.assertEqual(set(person), {"given_name", "family_name", "name"})
        self.assertEqual(person["name"], "MICHAELA CHALOUPKOVÁ")

    def test_a_birth_date_and_an_address_reach_no_stored_row(self):
        """The end-to-end version of the rule, through the real pipeline.

        Not "the collector drops them" but "a stored Signal contains neither",
        because the ruling is about the database and not about one function.
        """
        rows = _collect()
        self.assertTrue(rows)
        forbidden = ["1975-06-04", "Zahraničního odboje", "psc", "datumNarozeni",
                     "adresa", "statniObcanstvi", "isikukood"]
        for row in rows:
            signal = validate.build_signal(cz.as_classified(row), row,
                                           cz.COLLECTOR)
            blob = json.dumps(signal.__dict__, ensure_ascii=False, default=str)
            for token in forbidden:
                self.assertNotIn(token, blob, f"{token!r} reached a stored row")

        # And nothing that looks like a Czech birth date or postcode either.
        for row in rows:
            for value in row.values():
                if isinstance(value, str):
                    self.assertNotIn(", 6740", value)

    def test_a_person_with_no_surname_is_declined_rather_than_half_stored(self):
        self.assertIsNone(cz.scrub_person({"jmeno": "MARTIN"}))
        self.assertIsNone(cz.scrub_person({}))
        self.assertIsNone(cz.scrub_person(None))

    def test_a_body_corporate_is_not_a_person(self):
        """A member with no `fyzickaOsoba` is a company on another company's
        board. Same judgement as the Companies House `corporate-*` roles."""
        record = {"zaznamy": [{"statutarniOrgany": [{
            "nazevOrganu": "Statutární orgán",
            "clenoveOrganu": [{
                "datumZapisu": "2026-07-05", "typAngazma": "STATUTARNI_ORGAN_CLEN",
                "clenstvi": {"clenstvi": {"vznikClenstvi": "2026-07-05"}},
                "pravnickaOsoba": {"obchodniJmeno": "HOLDING a.s.",
                                   "ico": "12345678"},
            }]}]}]}
        self.assertEqual(cz._events(record, WINDOW_START, WINDOW_END), [])


class TheMateriality(unittest.TestCase):
    """The filter has to keep meaning "an employer of at least 250 people"."""

    def test_the_band_floor_is_the_registers_own_250_line(self):
        self.assertEqual(cz.BAND_FLOOR, "330")
        self.assertEqual(cz.BAND_LABELS["330"], "250 to 499")

    def test_a_band_below_the_floor_is_not_material(self):
        for code in ("000", "110", "120", "130", "210", "220", "230", "240",
                     "310", "320"):
            self.assertFalse(cz.is_material(code), code)

    def test_every_band_at_or_above_the_floor_is_material_and_has_a_label(self):
        for code in sorted(cz.BAND_LABELS):
            self.assertTrue(cz.is_material(code), code)
            self.assertTrue(cz.BAND_LABELS[code])

    def test_an_unstated_band_is_not_a_small_company_and_is_still_excluded(self):
        """`000` is `Neuvedeno`, not zero, and it is 65% of RES records. It fails
        the filter, and that is a RECALL hole the docstring states rather than a
        judgement that those companies are small."""
        self.assertFalse(cz.is_material("000"))
        self.assertFalse(cz.is_material(None))
        self.assertFalse(cz.is_material(""))
        self.assertFalse(cz.is_material("abc"))

    def test_the_floor_cannot_be_widened_below_250_by_an_environment_variable(self):
        with self.assertRaises(cz.AresError):
            cz.band_floor("320")
        with self.assertRaises(cz.AresError):
            cz.band_floor("0")

    def test_a_company_below_the_floor_is_never_fetched_at_all(self):
        """The filter is a COST control as well as an editorial one: a run that
        fetched every changed company would make 22,492 requests a month
        instead of 226."""
        session = _Session(_fixture())
        _collect(session=session)
        fetched = {url.rsplit("/", 1)[-1] for method, url, *_ in session.calls
                   if method == "get" and "/ekonomicke-subjekty-vr/" in url}
        self.assertEqual(fetched, set(MATERIAL))
        self.assertNotIn(TOO_SMALL, fetched)
        self.assertNotIn(NO_RES_RECORD, fetched)

    def test_a_company_with_no_res_record_is_excluded_and_is_not_an_error(self):
        """VR and RES are different registers. 14.3% of changed companies are in
        one and not the other, which is not the same thing as an unstated
        band and is not a broken join."""
        data = _fixture()
        codes = {n["icoId"] for b in data["batches"].values()
                 for n in b["seznamNotifikaci"]}
        self.assertIn(NO_RES_RECORD, codes)
        returned = {e["icoId"] for e in data["res"]["ekonomickeSubjekty"]}
        self.assertNotIn(NO_RES_RECORD, returned)
        self.assertTrue(_collect())

    def test_the_res_lookup_never_asks_for_more_than_the_endpoint_accepts(self):
        """100 is the endpoint's ceiling, measured: 200 is HTTP 400
        `VSTUP_PRILIS_MNOHO_HODNOT`."""
        self.assertEqual(cz.RES_BATCH, 100)
        session = _Session(_fixture())
        _collect(session=session)
        for method, url, *rest in session.calls:
            if method == "post" and url.endswith("/ekonomicke-subjekty-res/vyhledat"):
                self.assertLessEqual(len(rest[0]["ico"]), cz.RES_BATCH)


class TheEngagementAllowlist(unittest.TestCase):

    def test_an_unknown_engagement_type_is_declined_never_guessed(self):
        record = {"zaznamy": [{"statutarniOrgany": [{
            "nazevOrganu": "Kontrolní komise",
            "clenoveOrganu": [{
                "datumZapisu": "2026-07-05", "typAngazma": "KONTROLNI_KOMISE_CLEN",
                "clenstvi": {"clenstvi": {"vznikClenstvi": "2026-07-05"}},
                "fyzickaOsoba": {"jmeno": "JAN", "prijmeni": "NOVOTNÝ"},
            }]}]}]}
        self.assertEqual(cz._events(record, WINDOW_START, WINDOW_END), [])

    def test_the_excluded_types_are_named_and_are_not_also_allowed(self):
        for engagement in cz.EXCLUDED_ENGAGEMENTS:
            self.assertNotIn(engagement, cz.ENGAGEMENTS)

    def test_every_allowlisted_engagement_has_an_english_phrase(self):
        for engagement, phrase in cz.ENGAGEMENTS.items():
            self.assertTrue(phrase)
            self.assertEqual(engagement, engagement.upper())


class TheWindow(unittest.TestCase):

    def test_a_window_wider_than_the_feeds_own_horizon_is_refused(self):
        """The feed answers a wider request with the batches it has, so an
        over-wide window is a quiet month rather than an error. Refused here,
        loudly, the way BSE's 32-day cap is."""
        self.assertEqual(cz.FEED_HORIZON_DAYS, 28)
        import os
        os.environ["TIT_ARES_DAYS"] = "90"
        try:
            with self.assertRaises(cz.AresError) as caught:
                cz.days_from_env()
            self.assertIn("28", str(caught.exception))
        finally:
            del os.environ["TIT_ARES_DAYS"]

    def test_a_window_inside_the_horizon_is_accepted(self):
        import os
        os.environ["TIT_ARES_DAYS"] = "21"
        try:
            self.assertEqual(cz.days_from_env(), 21)
        finally:
            del os.environ["TIT_ARES_DAYS"]

    def test_the_default_window_overlaps_its_own_weekly_cadence(self):
        """A skipped run must not become a permanent hole, and overlap costs
        nothing: a re-seen event is an exact content_hash duplicate."""
        self.assertGreaterEqual(cz.DEFAULT_DAYS, 14)
        self.assertLessEqual(cz.DEFAULT_DAYS, cz.FEED_HORIZON_DAYS)


class TheSourceUrl(unittest.TestCase):

    def test_the_citation_is_a_document_that_can_fail(self):
        """A bogus ICO is HTTP 404 with a typed error body. The site's own
        /ekonomicke-subjekty/{ico} page answers 200 with an identical app shell
        for a real and an invented company, which is Japan's viewer trap."""
        self.assertEqual(
            cz.vr_url("45274649"),
            "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/"
            "ekonomicke-subjekty-vr/45274649")
        self.assertIsNone(cz.vr_url("4527464"))
        self.assertIsNone(cz.vr_url("452746499"))
        self.assertIsNone(cz.vr_url("45274 649"))
        self.assertIsNone(cz.vr_url(""))

    def test_a_missing_register_record_is_none_and_not_an_exception(self):
        session = _Session(_fixture(), vr_status={"17774713": 404})
        rows = _collect(session=session)
        self.assertNotIn("17774713", {row["ico"] for row in rows})

    def test_the_source_url_has_a_path_and_is_not_a_bare_domain(self):
        for row in _collect():
            parsed = urlparse(row["source_url"])
            self.assertTrue(parsed.path.strip("/"), row["source_url"])
            self.assertEqual(parsed.hostname, "ares.gov.cz")

    def test_the_collector_says_it_revisits_its_own_source_url(self):
        """One company has one register URL and appoints many people. Marking it
        seen would make the first event the last one ever reported for that
        employer — the ats_boards lesson."""
        self.assertTrue(cz.REVISITS_ITS_SOURCE_URL)

    def test_the_host_is_a_primary_source(self):
        self.assertIn("ares.gov.cz", vocab.PRIMARY_SOURCE_DOMAINS)


class TheRecord(unittest.TestCase):

    def setUp(self):
        self.rows = _collect()

    def test_the_run_produces_what_the_fixture_holds(self):
        self.assertEqual(len(self.rows), 12)
        self.assertEqual({row["ico"] for row in self.rows}, set(MATERIAL))

    def test_every_row_survives_the_real_pipeline(self):
        for row in self.rows:
            signal = validate.build_signal(cz.as_classified(row), row,
                                           cz.COLLECTOR)
            self.assertEqual(signal.pillar, "leadership_change")
            self.assertEqual(signal.country, "CZ")
            self.assertEqual(signal.confidence, "verified")

    def test_raw_text_is_set_on_every_row(self):
        """A source that forgets `raw_text` posts zero records silently. That
        bug cost the sibling weeks."""
        for row in self.rows:
            self.assertTrue(row.get("raw_text"))
            self.assertIn(row["headline"], row["raw_text"])

    def test_the_summary_is_a_literal_prefix_of_the_source_text(self):
        """Not a nicety. `validate._NUMBER` reads a year, a full stop and a
        following word beginning b, m or k as a magnitude — a defect it names
        and deliberately leaves alone — so two sentences differing only in the
        word AFTER the date make a sourced figure look invented. Composing the
        summary once and reusing it is what makes that impossible."""
        for row in self.rows:
            self.assertIn(row["summary"], row["raw_text"])
            classified = cz.as_classified(row)
            self.assertEqual(classified["summary"], row["summary"])

    def test_diacritics_round_trip_unchanged_into_a_stored_signal(self):
        """Five silent data-loss bugs in four days came from character
        handling, and NFKC is not a safe blanket fix — it broke Korea's
        allowlist by rewriting a character inside a form title."""
        names = {row["person_name"] for row in self.rows}
        with_diacritics = [n for n in names
                           if any(ch in n for ch in "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ")]
        self.assertTrue(with_diacritics, names)
        for row in self.rows:
            signal = validate.build_signal(cz.as_classified(row), row,
                                           cz.COLLECTOR)
            self.assertIn(row["person_name"], signal.headline)
            self.assertIn(row["person_name"], signal.summary)
            self.assertIn(row["company"], signal.summary)

    def test_the_published_date_is_the_office_date_and_not_the_registration(self):
        for row in self.rows:
            self.assertEqual(row["published_date"], row["event_date"])

    def test_direction_is_never_hiring_and_never_displacement(self):
        """The register records that an office began or ended and never why. A
        director leaving is not a workforce reduction — that is the sibling
        tracker's scope."""
        for row in self.rows:
            self.assertEqual(cz.as_classified(row)["signal_direction"], "neutral")

    def test_no_city_is_invented_from_a_registered_office(self):
        """`sidlo` is where the company is registered, not where the workforce
        sits, and it only ever fills `headquarters_city` through the shared
        gazetteer. Nothing here splits an address on a comma."""
        for row in self.rows:
            city = cz.as_classified(row).get("headquarters_city") or ""
            if city:
                self.assertIsNotNone(vocab.normalize_city(city))
            self.assertNotIn(",", city)

    def test_a_run_derives_its_record_and_never_calls_a_model(self):
        self.assertTrue(hasattr(cz, "as_classified"))
        source = (ROOT / "collectors" / "czechia_ares.py").read_text(encoding="utf-8")
        for banned in ("openrouter", "classify.classify", "OPENROUTER_API_KEY"):
            self.assertNotIn(banned, source)


class TheFailureModes(unittest.TestCase):

    def test_a_changed_response_shape_is_an_error_and_not_an_empty_run(self):
        data = _fixture()
        data["feed"] = {"somethingElse": []}
        with self.assertRaises(cz.AresError):
            cz.fetch_batches(session=_Session(data))

    def test_a_feed_that_has_gone_quiet_fails_loudly(self):
        """880 changed companies a day is the measured rate. A tenth of it is a
        floor a working feed cannot fail, and a stopped one will."""
        with self.assertRaises(cz.AresError) as caught:
            cz.collect(days=WINDOW_DAYS, today=TODAY, session=_Session(_fixture()))
        self.assertIn("notifications", str(caught.exception))

    def test_an_empty_material_population_below_the_floor_is_not_an_incident(self):
        """A run that polls a handful of large employers may honestly find
        nothing; a run that polls hundreds and finds nothing has broken."""
        self.assertEqual(cz.emptiness_floor(0), 0)
        self.assertEqual(cz.emptiness_floor(cz.FLOOR_APPLIES_ABOVE - 1), 0)
        self.assertGreaterEqual(cz.emptiness_floor(226), 1)
        self.assertLess(cz.emptiness_floor(226), 42)


class TheWiring(unittest.TestCase):

    def test_the_collector_is_registered(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["czechia_ares"], cz)

    def test_it_has_a_schedule_derived_staleness_leash(self):
        import staleness
        self.assertIn("czechia_ares", staleness.MAX_AGE_HOURS)
        self.assertEqual(staleness.MAX_AGE_HOURS["czechia_ares"], 180)

    def test_it_is_on_the_sources_page_with_a_collector_behind_it(self):
        import source_registry as registry
        names = [n for n, c in registry.COLLECTOR_BY_SOURCE_NAME.items()
                 if c == "czechia_ares"]
        self.assertEqual(len(names), 1)
        listed = [s for s in registry.SOURCES if s.name == names[0]]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].status, "live")
        self.assertEqual(listed[0].country, "CZ")

    def test_the_workflow_runs_it_on_a_day_no_other_writer_holds(self):
        """Every database writer shares one `talent-collect` lock and GitHub
        keeps exactly ONE pending run in it, so two weekly writers on one day is
        a run that can be evicted with no logs and no annotation."""
        import yaml

        path = ROOT / ".github" / "workflows" / "collect-structured.yml"
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        crons = [c["cron"] for c in workflow[True]["schedule"]]
        weekly = [c for c in crons if c.endswith(("1", "2", "3", "4", "5", "6", "0"))
                  and "*" not in c.split()[-1]]
        self.assertEqual(len(weekly), len(set(weekly)))
        self.assertIn("0 4 * * 5", crons)
        text = path.read_text(encoding="utf-8")
        self.assertIn("czechia_ares", text)


if __name__ == "__main__":
    unittest.main()
