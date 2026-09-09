"""Denmark's spine must stay dormant, stay honest about what it has never seen,
and never carry a person's private life or a plaintext credential anywhere.

Four families of failure are guarded here, and the first two are unusual because
this collector has **never authenticated**.

**THE DORMANCY**, because a key-gated source that quietly starts running is the
failure this repository's standing pattern exists to prevent. Two locks: no
workflow schedules it, and `TIT_DK_CVR` defaults off. A disarmed run must make
no request and send no credential even when credentials are present, and an
ARMED run with no credentials must be a loud refusal rather than an empty list.

**THE CREDENTIAL, WHICH TRAVELS IN CLEARTEXT.** distribution.virk.dk listens on
port 80 only, so HTTP Basic over plain HTTP is the only shape offered. That
makes two ordinary things load-bearing: a redirect is never followed, and every
request asserts its own hostname before it is sent.

**THE SILENT ZERO, IN THREE SHAPES.** Elasticsearch answers a search against a
missing mapping type with 200 and no hits; a renamed field answers with 200 and
no hits; a renamed band code answers with 200 and no hits. None of the three is
a quiet fortnight. The canary, the public-mapping check and the band vocabulary
are the three answers, and the mapping check is the one that can be exercised
against a REAL captured response.

**THE PERSONAL DATA.** CVR publishes a participant's residential address, an
address-protection flag and a persistent national participant id. The owner's
ruling is that a name, a role, an employer and a date are taken at the collector
boundary and nothing else is ever persisted, so the fixture keeps every one of
those fields and the tests require that none of them reaches a stored row.

TWO FIXTURES, AND THEY ARE NOT THE SAME KIND OF THING
------------------------------------------------------

`denmark_cvr_mapping.json` is a **real, unmodified, captured response**:
`GET http://distribution.virk.dk/cvr-permanent/_mapping` on 2026-09-09, which is
public and needs no credential.

`denmark_cvr_search.json` is **assembled, not captured**, and says so in its own
`_provenance`. No `_search` body has ever been seen by this repository. It
proves the parser and the guards; it proves nothing about Danish data.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from collectors import denmark_cvr as dk
from pipeline import validate

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_FIXTURE = FIXTURES / "denmark_cvr_search.json"
MAPPING_FIXTURE = FIXTURES / "denmark_cvr_mapping.json"

TODAY = date(2026, 9, 9)
WINDOW_DAYS = 14
START = "2026-08-26"
END = "2026-09-09"
FLOOR = dk.MATERIAL_BANDS[0]


def _search() -> dict:
    with SEARCH_FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def _mapping() -> dict:
    with MAPPING_FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def _hits() -> list:
    return _search()["search"]["hits"]["hits"]


def _rows(stats=None, hits=None) -> list:
    return dk.rows_from_hits(hits if hits is not None else _hits(),
                             START, END, floor=FLOOR, stats=stats)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = "" if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Session:
    """Answers the CVR endpoints out of the two fixtures and RECORDS every
    call, so a test can assert what was not requested and with what."""

    def __init__(self, *, canary=("found",), search=None, mapping=None):
        self.calls: list = []
        self._canary = list(canary)
        self._search = search
        self._mapping = mapping
        self._scrolled = False

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if url == dk.MAPPING_URL:
            return _Resp(self._mapping if self._mapping is not None else _mapping())
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        body = json.loads(kwargs.get("data") or "{}")
        fixture = _search()
        if url.startswith(dk.SCROLL_URL):
            return _Resp(fixture["scroll_empty"])
        if body.get("size") == 0:
            answer = self._canary.pop(0) if self._canary else "missing"
            return _Resp(fixture["canary_found" if answer == "found"
                                 else "canary_missing"])
        if self._search is not None:
            return _Resp(self._search)
        return _Resp(fixture["search"])


class _RefusingSession:
    """Any request at all is a test failure."""

    def get(self, *a, **k):
        raise AssertionError("a disarmed run made an HTTP request")

    post = get


ARMED = {dk.ARM_ENV: "on", dk.USER_ENV: "u", dk.PASSWORD_ENV: "p"}


# --- the mapping, against a REAL captured response --------------------------

class TheFieldPathsAreReal(unittest.TestCase):
    """The one guard in this file that runs against live production data.

    `/cvr-permanent/_mapping` is public, so a collector written without
    credentials can still prove that every field it reads exists. Without it a
    renamed field would answer 200 with no hits, which is the silent zero.
    """

    def test_every_field_this_collector_reads_exists_in_production(self):
        found = dk.mapping_check(mapping=_mapping())
        for field in dk.REQUIRED_FIELDS:
            self.assertIn(field, found)

    def test_the_capture_is_the_production_index(self):
        self.assertIn("cvr-v-20220630", _mapping())

    def test_a_renamed_field_is_a_loud_refusal_naming_it(self):
        """Proved by mutation: drop the one field the whole materiality filter
        rests on and the run must refuse rather than return nothing."""
        mapping = _mapping()
        meta = (mapping["cvr-v-20220630"]["mappings"]["_doc"]["properties"]
                ["Vrvirksomhed"]["properties"]["virksomhedMetadata"]
                ["properties"])
        del meta["nyesteAarsbeskaeftigelse"]
        with self.assertRaises(dk.CvrError) as caught:
            dk.mapping_check(mapping=mapping)
        self.assertIn("intervalKodeAntalAnsatte", str(caught.exception))

    def test_an_empty_mapping_is_not_a_pass(self):
        with self.assertRaises(dk.CvrError):
            dk.mapping_check(mapping={})


# --- the two locks ----------------------------------------------------------

class ItShipsDormant(unittest.TestCase):

    def test_it_is_disarmed_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(dk.armed())

    def test_a_disarmed_run_makes_no_request_and_stores_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = dk.collect(days=WINDOW_DAYS, today=TODAY,
                             session=_RefusingSession())
        self.assertEqual(out, [])
        self.assertEqual(dk.LAST_RUN["armed"], False)

    def test_a_disarmed_run_sends_no_credential_even_when_one_is_present(self):
        """The arming flag is the SECOND lock and it is independent of the
        first. Credentials present and the flag off must still be silent."""
        env = {dk.USER_ENV: "u", dk.PASSWORD_ENV: "p"}
        with mock.patch.dict(os.environ, env, clear=True):
            out = dk.collect(days=WINDOW_DAYS, today=TODAY,
                             session=_RefusingSession())
        self.assertEqual(out, [])
        self.assertEqual(dk.LAST_RUN["credentials"], dk.CRED_PRESENT)

    def test_an_armed_run_with_no_credentials_refuses_loudly(self):
        """Never an empty list. Absence of a signal is not a pass."""
        with mock.patch.dict(os.environ, {dk.ARM_ENV: "on"}, clear=True):
            with self.assertRaises(dk.CvrCredentialsMissing) as caught:
                dk.collect(days=WINDOW_DAYS, today=TODAY,
                           session=_RefusingSession())
        self.assertIn(dk.USER_ENV, str(caught.exception))
        self.assertIn("ai-layoff-tracker", str(caught.exception))

    def test_the_credential_state_has_four_values_not_two(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(dk.credential_state(), dk.CRED_ABSENT)
        with mock.patch.dict(os.environ,
                             {dk.USER_ENV: "u", dk.PASSWORD_ENV: "p"},
                             clear=True):
            self.assertEqual(dk.credential_state(), dk.CRED_PRESENT)
        self.assertEqual(len({dk.CRED_ABSENT, dk.CRED_PRESENT,
                              dk.CRED_REJECTED, dk.CRED_UNKNOWN}), 4)

    def test_half_a_credential_is_no_credential(self):
        with mock.patch.dict(os.environ, {dk.USER_ENV: "u"}, clear=True):
            self.assertEqual(dk.credential_state(), dk.CRED_ABSENT)


# --- the cleartext credential ----------------------------------------------

class TheCredentialNeverLeavesTheHost(unittest.TestCase):
    """It is sent in cleartext because the service offers port 80 and nothing
    else, so the ordinary consequences of a redirect are not ordinary."""

    def test_a_request_to_another_host_is_refused(self):
        with self.assertRaises(dk.CvrError) as caught:
            dk.assert_same_host("http://example.invalid/cvr-permanent/_search")
        self.assertIn("cleartext", str(caught.exception))

    def test_the_hostname_check_is_wired_into_every_request(self):
        """Proved by mutation of the URL rather than of the guard."""
        with self.assertRaises(dk.CvrError):
            dk._request("get", "http://evil.example/x", session=_Session())

    def test_no_request_follows_a_redirect(self):
        session = _Session()
        dk._request("get", dk.MAPPING_URL, session=session)
        for _method, _url, kwargs in session.calls:
            self.assertIs(kwargs["allow_redirects"], False)

    def test_a_redirect_response_is_an_error_and_not_a_follow(self):
        with self.assertRaises(dk.CvrError) as caught:
            dk._json(_Resp({}, status=302), "the CVR search")
        self.assertIn("cleartext", str(caught.exception))

    def test_the_module_talks_to_exactly_one_host(self):
        source = (ROOT / "collectors" / "denmark_cvr.py").read_text("utf-8")
        for url in (dk.MAPPING_URL, dk.SCROLL_URL,
                    dk.BASE + dk.SEARCH_PATHS[0], dk.BASE + dk.SEARCH_PATHS[1]):
            self.assertTrue(url.startswith(dk.BASE), url)
        # datacvr is CITED and never fetched.
        self.assertNotIn("requests.get(REGISTER", source)


# --- the silent zero --------------------------------------------------------

class TheCanaryCatchesASilentZero(unittest.TestCase):
    """A search against a mapping type that does not exist answers 200 with no
    hits. The mapping says this index's type is `_doc`; every client and guide
    uses `/virksomhed/`. So the run asks a question it knows the answer to."""

    def test_the_first_path_is_used_when_it_finds_the_canary(self):
        session = _Session(canary=("found",))
        url = dk.resolve_search_url(session=session, auth=("u", "p"))
        self.assertEqual(url, dk.BASE + dk.SEARCH_PATHS[0])

    def test_the_second_path_is_tried_when_the_first_answers_zero(self):
        session = _Session(canary=("missing", "found"))
        url = dk.resolve_search_url(session=session, auth=("u", "p"))
        self.assertEqual(url, dk.BASE + dk.SEARCH_PATHS[1])

    def test_both_paths_answering_zero_is_a_refusal_and_not_a_quiet_run(self):
        session = _Session(canary=("missing", "missing"))
        with self.assertRaises(dk.CvrError) as caught:
            dk.resolve_search_url(session=session, auth=("u", "p"))
        self.assertIn(dk.CANARY_CVR, str(caught.exception))

    def test_the_canary_number_is_a_real_cvr_number(self):
        self.assertTrue(dk.valid_cvr(dk.CANARY_CVR))

    def test_a_401_is_its_own_state_and_is_not_retried_away(self):
        with self.assertRaises(dk.CvrCredentialsRejected):
            dk._json(_Resp(None, status=401), "the CVR search")

    def test_the_query_filters_only_on_indexed_unanalysed_fields(self):
        """`hovedtype` and `enhedstype` are `text` in the mapping, so a `term`
        against either matches nothing and says nothing about why. They are
        filtered in Python instead, and this proves the query never names
        them."""
        body = json.dumps(dk.search_body(START, END))
        self.assertNotIn("hovedtype", body)
        self.assertNotIn("enhedstype", body)
        self.assertIn("sidstIndlaest", body)
        self.assertIn("intervalKodeAntalAnsatte", body)

    def test_the_window_is_on_the_monotonic_cursor(self):
        """`sidstOpdateret` can be backdated by a correction, so a poll on it
        loses the correction it exists to catch."""
        body = json.dumps(dk.search_body(START, END))
        self.assertNotIn("sidstOpdateret", body)


# --- the modulus-11 check ---------------------------------------------------

class TheChecksumStandsInForADistinguishableURL(unittest.TestCase):
    """datacvr.virk.dk answers 403 to an automated client for a real company
    and an invented one alike, so Estonia's live 200-versus-303 proof was not
    available. The register's own checksum is the offline substitute."""

    def test_real_cvr_numbers_pass(self):
        for number in ("24256790", "22756214", "61126228", "10403782"):
            self.assertTrue(dk.valid_cvr(number), number)

    def test_invented_numbers_fail(self):
        for number in ("99999999", "12345678", "", None, "2425679", "abcdefgh"):
            self.assertFalse(dk.valid_cvr(number), number)

    def test_a_number_that_fails_the_checksum_has_no_citable_url(self):
        self.assertIsNone(dk.register_url("99999999"))
        self.assertEqual(dk.register_url("24256790"),
                         "https://datacvr.virk.dk/enhed/virksomhed/24256790")


# --- what is collected ------------------------------------------------------

class WhatIsCollected(unittest.TestCase):

    def test_both_directions_are_read_from_one_company(self):
        """The whole reason this register was worth the ask: an arrival and a
        departure, each with a date the register itself states."""
        rows = [r for r in _rows() if r["cvr"] == "24256790"]
        self.assertEqual({r["event"] for r in rows},
                         {dk.EVENT_TOOK_OFFICE, dk.EVENT_LEFT_OFFICE})
        left = [r for r in rows if r["event"] == dk.EVENT_LEFT_OFFICE][0]
        self.assertEqual(left["event_date"], "2026-08-31")
        self.assertEqual(left["person_name"], "Anders Holm Sørensen")

    def test_a_legal_person_on_a_board_is_not_a_person(self):
        names = {r["person_name"] for r in _rows()}
        self.assertNotIn("NOVO HOLDINGS A/S", names)

    def test_an_auditor_is_not_an_office(self):
        names = {r["person_name"] for r in _rows()}
        self.assertNotIn("Revisor Jens Andersen", names)

    def test_beneficial_owners_are_not_an_office(self):
        names = {r["person_name"] for r in _rows()}
        self.assertNotIn("Signe Dahl Poulsen", names)

    def test_an_unknown_function_is_declined_counted_and_printed_verbatim(self):
        stats: dict = {}
        rows = _rows(stats)
        self.assertNotIn("Karen Toft Nielsen", {r["person_name"] for r in rows})
        self.assertGreaterEqual(stats.get("unknown_function", 0), 1)
        self.assertIn("VICEDIREKTØR", stats.get("unknown_values", set()))

    def test_a_known_function_is_matched_whatever_its_case(self):
        """The same real document spells one attribute `Direktion` and the
        matching member attribute `DIREKTION`."""
        rows = [r for r in _rows() if r["cvr"] == "22756214"]
        self.assertEqual([r["role"] for r in rows], ["Formand"])

    def test_an_event_outside_the_window_is_not_read(self):
        rows = [r for r in _rows() if r["cvr"] == "61126228"]
        self.assertEqual(rows, [])

    def test_a_company_below_the_band_floor_is_dropped_and_counted(self):
        stats: dict = {}
        rows = _rows(stats)
        self.assertNotIn("10403782", {r["cvr"] for r in rows})
        self.assertEqual(stats.get("below_floor"), 1)

    def test_an_advertising_protected_company_is_declined(self):
        """The conservative side of the one question the signed agreement
        governs, taken because this module cannot read the agreement."""
        stats: dict = {}
        rows = _rows(stats)
        self.assertNotIn("30000005", {r["cvr"] for r in rows})
        self.assertEqual(stats.get("reklamebeskyttet"), 1)

    def test_the_dates_come_from_the_membership_not_the_organisation(self):
        """In the one real document available these disagree by eight months.
        Reading the organisation's period would date this row 2026-01-15 and
        put it outside the window entirely."""
        rows = [r for r in _rows() if r["cvr"] == "30000013"]
        self.assertEqual([r["event_date"] for r in rows], ["2026-09-06"])

    def test_a_cvr_number_that_fails_the_checksum_stores_nothing(self):
        self.assertNotIn("99999999", {r["cvr"] for r in _rows()})

    def test_every_row_is_neutral_and_never_a_hire_or_a_cut(self):
        for row in _rows():
            self.assertEqual(dk.as_classified(row)["signal_direction"],
                             "neutral")

    def test_the_run_is_not_empty(self):
        self.assertGreaterEqual(len(_rows()), 3)


# --- personal data ----------------------------------------------------------

class PersonalDataStopsAtTheBoundary(unittest.TestCase):
    """CVR publishes a residential address, an address-protection flag and a
    persistent national participant id for participants. The fixture keeps all
    of them so these tests can require that none of them arrives."""

    FORBIDDEN = ("Skovvej", "Kgs. Lyngby", "Lyngby-Taarbæk", "2800",
                 "4000668624", "adresseHemmelig", "forretningsnoegle",
                 "postadresse")

    def test_no_address_flag_or_participant_id_reaches_a_row(self):
        blob = json.dumps(_rows(), ensure_ascii=False)
        for token in self.FORBIDDEN:
            self.assertNotIn(token, blob, token)

    def test_none_of_it_reaches_the_derived_record_either(self):
        blob = json.dumps([dk.as_classified(r) for r in _rows()],
                          ensure_ascii=False)
        for token in self.FORBIDDEN:
            self.assertNotIn(token, blob, token)

    def test_scrub_person_returns_a_name_and_nothing_else(self):
        deltager = _hits()[0]["_source"]["Vrvirksomhed"]["deltagerRelation"][0]["deltager"]
        self.assertEqual(set(dk.scrub_person(deltager)), {"name"})

    def test_scrub_person_declines_a_company(self):
        relations = _hits()[0]["_source"]["Vrvirksomhed"]["deltagerRelation"]
        company = [r for r in relations
                   if r["deltager"]["enhedstype"] == "VIRKSOMHED"][0]
        self.assertIsNone(dk.scrub_person(company["deltager"]))


# --- the record -------------------------------------------------------------

class TheRecord(unittest.TestCase):

    def test_the_summary_is_a_literal_prefix_of_the_raw_text(self):
        """Built once in `_row` and returned unchanged by `as_classified`, so
        every figure in it is verbatim in the source text by construction. It
        cost the Estonian connector twelve of its first 66 rows to learn that
        two carefully written sentences are not the same guarantee."""
        for row in _rows():
            self.assertIn(row["summary"], row["raw_text"])
            self.assertEqual(dk.as_classified(row)["summary"], row["summary"])

    def test_a_row_survives_the_shared_validator(self):
        for row in _rows():
            signal = validate.build_signal(dk.as_classified(row), row,
                                           dk.COLLECTOR)
            # `Denmark` normalises to the ISO code through the shared
            # vocabulary, exactly as every other connector's country does.
            self.assertEqual(signal.country, "DK")
            self.assertEqual(signal.pillar, "leadership_change")

    def test_the_register_is_a_primary_source(self):
        from pipeline import vocab
        self.assertIn("datacvr.virk.dk", vocab.PRIMARY_SOURCE_DOMAINS)

    def test_the_row_says_the_band_edge_is_200_and_never_says_250(self):
        """The register publishes no boundary at 250, so a row must not imply
        one just because the sibling connectors draw their line there."""
        for row in _rows():
            self.assertIn(row["size_label"], row["summary"])
            self.assertNotIn("250 employees", row["raw_text"])
        self.assertIn("200", _rows()[0]["raw_text"])

    def test_no_model_is_called_on_any_path(self):
        source = (ROOT / "collectors" / "denmark_cvr.py").read_text("utf-8")
        for token in ("openai", "OpenAI", "OPENROUTER", "metered_call",
                      "import spend", "from spend"):
            self.assertNotIn(token, source, token)


# --- the honest ceiling -----------------------------------------------------

class TheYieldIsUnmeasured(unittest.TestCase):
    """`czechia_ares` and `estonia_ariregister` both refuse a suspiciously
    empty run against a floor derived from a measurement. This one has no
    measurement, so it must ship with NO floor rather than a plausible guess.
    A guard calibrated on an invented number either never fires or fires on an
    ordinary week."""

    def test_no_yield_figure_is_asserted(self):
        self.assertIsNone(dk.MEASURED_YIELD)

    def test_the_emptiness_floor_is_zero_at_every_population(self):
        for material in (0, 1, 50, 500, 100000):
            self.assertEqual(dk.emptiness_floor(material), 0)

    def test_the_module_says_it_has_never_seen_a_response(self):
        source = (ROOT / "collectors" / "denmark_cvr.py").read_text("utf-8")
        self.assertIn("NOT VERIFIED", source)
        self.assertIn("UNCALIBRATED", source)

    def test_the_fixture_says_it_is_assembled_and_not_captured(self):
        provenance = _search()["_provenance"]
        self.assertIn("ASSEMBLED, NOT CAPTURED", provenance["honest_status"])


# --- one whole armed run, against the fixtures ------------------------------

class OneWholeRun(unittest.TestCase):

    def test_an_armed_run_reads_the_mapping_the_canary_then_the_search(self):
        session = _Session()
        with mock.patch.dict(os.environ, ARMED, clear=True):
            rows = dk.collect(days=WINDOW_DAYS, today=TODAY, session=session)
        self.assertGreaterEqual(len(rows), 3)
        urls = [url for _m, url, _k in session.calls]
        self.assertEqual(urls[0], dk.MAPPING_URL)
        self.assertTrue(urls[1].startswith(dk.BASE + dk.SEARCH_PATHS[0]))
        self.assertIn("read", dk.LAST_RUN)
        self.assertEqual(dk.LAST_RUN["armed"], True)

    def test_every_request_carried_the_credential_and_no_redirect(self):
        session = _Session()
        with mock.patch.dict(os.environ, ARMED, clear=True):
            dk.collect(days=WINDOW_DAYS, today=TODAY, session=session)
        for method, url, kwargs in session.calls:
            self.assertIs(kwargs["allow_redirects"], False)
            if url != dk.MAPPING_URL:
                self.assertEqual(kwargs.get("auth"), ("u", "p"))
            else:
                # The mapping is public. Sending a cleartext credential to
                # fetch it would be spending the risk for nothing.
                self.assertIsNone(kwargs.get("auth"))

    def test_a_broken_mapping_stops_the_run_before_any_credential_is_sent(self):
        mapping = _mapping()
        del (mapping["cvr-v-20220630"]["mappings"]["_doc"]["properties"]
             ["Vrvirksomhed"]["properties"]["deltagerRelation"])
        session = _Session(mapping=mapping)
        with mock.patch.dict(os.environ, ARMED, clear=True):
            with self.assertRaises(dk.CvrError):
                dk.collect(days=WINDOW_DAYS, today=TODAY, session=session)
        self.assertEqual([url for _m, url, _k in session.calls],
                         [dk.MAPPING_URL])


# --- the wiring -------------------------------------------------------------

class TheWiring(unittest.TestCase):

    def test_the_collector_is_registered(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["denmark_cvr"], dk)

    def test_it_has_a_dormant_staleness_leash(self):
        import staleness
        self.assertEqual(staleness.MAX_AGE_HOURS["denmark_cvr"], 2400)

    def test_it_is_not_a_live_source_until_it_has_stored_something(self):
        """The sources page must not claim Denmark before a run has stored from
        it. This collector has never even authenticated."""
        import source_registry as registry
        self.assertNotIn("denmark_cvr",
                         set(registry.COLLECTOR_BY_SOURCE_NAME.values()))
        self.assertNotIn("Denmark", {s.country for s in registry.SOURCES
                                     if s.status == "live"})

    def test_the_workflow_lists_it_but_schedules_nothing(self):
        import yaml
        path = ROOT / ".github" / "workflows" / "collect-structured.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        options = workflow[True]["workflow_dispatch"]["inputs"]["source"]["options"]
        self.assertIn("denmark_cvr", options)
        # No cron expression maps to it: the `Pick the source` case never names
        # it, so no schedule can select it.
        self.assertNotIn("scheduled=denmark_cvr", text)

    def test_the_workflow_carries_both_secrets_and_the_arming_variable(self):
        path = ROOT / ".github" / "workflows" / "collect-structured.yml"
        text = path.read_text(encoding="utf-8")
        for name in (dk.USER_ENV, dk.PASSWORD_ENV, dk.ARM_ENV):
            self.assertIn(name, text)
        # The arming flag must NOT be reachable from a dispatch input: arming is
        # a deliberate repository change, not something a form can do.
        self.assertNotIn("TIT_DK_CVR: ${{ inputs.", text)

    def test_the_rehearsal_probe_exists(self):
        self.assertTrue((ROOT / "denmark_cvr_probe.py").exists())


if __name__ == "__main__":
    unittest.main()
