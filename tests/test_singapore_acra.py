"""Singapore must stay an INCORPORATION signal, and must stay honest about
being the narrowest source in the tracker.

Three families of failure are guarded here.

**The silent zero.** This source is a monthly snapshot read through two API
hops and a signed url, and every one of those can fail in a way that produces
an empty list rather than an error: a renamed column, a poll-download that did
not succeed, an expired url, a collection that lists fewer files than it has.
Each of those looks exactly like "Singapore incorporated no software companies
this month", which is not a thing that happens. So each of them raises, and the
run has an emptiness floor underneath it.

**The wrong filter.** The whole claim of this connector is "new software and IT
companies", and the only field that supports the claim is `primary_ssic_code`.
`primary_ssic_description` was literally the string "na" on every sampled row,
so the description is a trap rather than a second opinion: rendered unchecked it
puts "operates in na" on a public page. The code filter is tested for what it
EXCLUDES, and the description is tested for never being rendered.

**The wrong date.** A snapshot states one date per company and has no second
field to fall back on, so a row whose `registration_incorporation_date` will not
parse must be DROPPED rather than stored against today. The window is tested at
both boundaries, because an off-by-one there is a month of companies silently
appearing or disappearing.

Offline throughout. The fixture below is an inline CSV in the published shape,
carrying the four real entities measured in dataset
d_af2042c77ffaf0db5d75561ce9ef5688 (the letter "W" file) on 2026-08-03, plus
constructed edge rows that say so. No test here makes a network call.
"""

from __future__ import annotations

import os
import unittest
from datetime import date

from collectors import singapore_acra as sg
from pipeline import validate, vocab

# The fixture's window: 45 days to 2026-08-03, so 2026-06-19..2026-08-03.
TODAY = date(2026, 8, 3)
WINDOW_DAYS = 45
WINDOW_START = "2026-06-19"

DATASET = "d_af2042c77ffaf0db5d75561ce9ef5688"      # the real letter "W" id

COLUMNS = [
    "uen", "entity_name", "entity_type_description", "entity_status_description",
    "registration_incorporation_date", "primary_ssic_code",
    "primary_ssic_description", "primary_user_described_activity",
    "no_of_officers",
]


def _csv(rows) -> str:
    """A published-shape CSV from a list of column tuples."""
    lines = [",".join(COLUMNS)]
    for row in rows:
        lines.append(",".join('"%s"' % str(value) for value in row))
    return "\n".join(lines) + "\n"


# Four real rows, verbatim from the measured file, then the constructed edges.
# Every constructed row is marked in this comment rather than left to be guessed:
# BOUNDARY_IN / BOUNDARY_OUT / SLASHDATE / NODATE / HOTEL / STRUCKOFF / TWICE.
ROWS = [
    # --- real, measured 2026-08-03 ---
    ("202500001W", "WORLDAI PTE. LTD.", "LOCAL COMPANY", "Live Company",
     "2026-07-15", "62011", "na", "na", "1"),
    ("202500002W", "WISAGENT PTE. LTD.", "LOCAL COMPANY", "Live Company",
     "2026-07-01", "62021", "na", "na", "4"),
    ("202500003W", "WENYA LABS PTE. LTD.", "LOCAL COMPANY", "Live Company",
     "2026-06-19", "62011", "na", "na", "3"),          # BOUNDARY_IN: exactly start
    ("202500004W", "WHIZHACK TECHNOLOGIES PTE. LTD.", "LOCAL COMPANY",
     "Live Company", "2026-06-18", "62013", "na", "na", "6"),   # BOUNDARY_OUT
    # --- constructed edges ---
    ("202500005W", "WRONG DATE PTE. LTD.", "LOCAL COMPANY", "Live Company",
     "15/07/2026", "62011", "na", "na", "2"),                   # SLASHDATE
    ("202500006W", "NO DATE PTE. LTD.", "LOCAL COMPANY", "Live Company",
     "", "62011", "na", "na", "2"),                             # NODATE
    ("202500007W", "WATERFRONT HOTEL PTE. LTD.", "LOCAL COMPANY", "Live Company",
     "2026-07-02", "55101", "Hotels with restaurant", "na", "40"),   # HOTEL
    ("202500008W", "WOUND UP PTE. LTD.", "LOCAL COMPANY", "Struck Off",
     "2026-07-03", "62011", "na", "na", "1"),                   # STRUCKOFF
    ("202500001W", "WORLDAI PTE. LTD.", "LOCAL COMPANY", "Live Company",
     "2026-07-15", "62011", "na", "na", "1"),                   # TWICE: same UEN
]

FIXTURE = _csv(ROWS)

# A file with nothing this collector wants in it. Used for the emptiness floor
# and for the other 26 letters, so a run reads a plausible number of files.
BARREN = _csv([("202500099X", "XYZ TRADING PTE. LTD.", "LOCAL COMPANY",
                "Live Company", "2026-07-04", "46900", "na", "na", "2")])

SIGNED = "https://s3.example.test/{dataset_id}.csv"


class _Resp:
    def __init__(self, payload=None, *, text="", status=200):
        self._payload = payload
        self.status_code = status
        self.text = text if payload is None else "{}"

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Session:
    """Answers the three hops: collection metadata, poll-download, signed CSV."""

    # poll_http defaults to 201, which is what the LIVE endpoint returns: the
    # portal models a download request as creating an export job. The first
    # version of this fixture used 200 here, so the whole file passed against a
    # collector that could not complete a single real run, and only a live dry
    # run caught it. A fixture that is politer than the server is not a test.
    def __init__(self, csv_by_dataset, *, poll_status="DOWNLOAD_SUCCESS",
                 poll_code=0, download_status=200, metadata_status=200,
                 poll_http=201, child_datasets=None):
        self.csv_by_dataset = csv_by_dataset
        self.poll_status = poll_status
        self.poll_code = poll_code
        self.download_status = download_status
        self.metadata_status = metadata_status
        self.poll_http = poll_http
        self.child_datasets = (child_datasets if child_datasets is not None
                               else list(csv_by_dataset))
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(url)
        if url == sg.COLLECTION_METADATA_URL:
            return _Resp({"code": 0, "data": {"collectionMetadata": {
                "collectionId": "2", "frequency": "monthly",
                "lastUpdatedAt": "2026-07-17T15:41:12+08:00",
                "childDatasets": self.child_datasets}}},
                status=self.metadata_status)
        if "poll-download" in url:
            dataset_id = url.rsplit("/", 2)[-2]
            return _Resp({"code": self.poll_code,
                          "data": {"status": self.poll_status,
                                   "url": SIGNED.format(dataset_id=dataset_id)}},
                         status=self.poll_http)
        dataset_id = url.rsplit("/", 1)[-1].removesuffix(".csv")
        return _Resp(text=self.csv_by_dataset.get(dataset_id, BARREN),
                     status=self.download_status)


def _session(**kwargs) -> _Session:
    return _Session({DATASET: FIXTURE}, **kwargs)


def _run(session=None, **kwargs):
    session = session or _session()
    return sg.collect(datasets=[DATASET], session=session, today=TODAY,
                      days=WINDOW_DAYS, **kwargs)


class _Base(unittest.TestCase):
    def setUp(self):
        self.rows = _run()

    def by_company(self, name: str) -> dict:
        return next(r for r in self.rows if r["company"] == name)

    def signal(self, name: str):
        item = self.by_company(name)
        return validate.build_signal(sg.as_classified(item), item, sg.COLLECTOR)


# --- the record ------------------------------------------------------------

class TheRecordIsARegisterEntry(_Base):
    def test_raw_text_is_set_on_every_row(self):
        # A collector that forgets raw_text stores nothing and reports success.
        # That has already happened on this codebase, so it is asserted rather
        # than assumed.
        self.assertTrue(self.rows)
        for row in self.rows:
            self.assertTrue(row["raw_text"].strip(), row["headline"])

    def test_the_summary_is_a_literal_part_of_the_raw_text(self):
        for row in self.rows:
            self.assertIn(row["summary"], row["raw_text"])
            self.assertEqual(sg.as_classified(row)["summary"], row["summary"])

    def test_every_figure_in_the_summary_is_in_the_source_text(self):
        for row in self.rows:
            validate.assert_figures_are_sourced(
                sg.as_classified(row)["summary"], row["raw_text"])

    def test_the_uen_and_the_ssic_code_survive_into_the_summary(self):
        sig = self.signal("WORLDAI PTE. LTD.")
        self.assertIn("202500001W", sig.summary)
        self.assertIn("62011", sig.summary)

    def test_the_company_is_named_first_in_the_headline(self):
        for row in self.rows:
            self.assertTrue(row["headline"].startswith(row["company"]),
                            row["headline"])

    def test_the_attribution_travels_with_the_data(self):
        # A licence condition, not a courtesy: it has to reach WordPress rather
        # than live only in a docstring.
        summary = self.signal("WORLDAI PTE. LTD.").summary
        self.assertIn("Singapore Open Data Licence", summary)
        self.assertIn("Accounting and Corporate Regulatory Authority", summary)

    def test_the_pillar_is_company_development_and_never_a_funding_one(self):
        # An incorporation is a company development. There is no funding pillar
        # in the vocabulary, and this source states no funding anyway.
        self.assertEqual(self.signal("WISAGENT PTE. LTD.").pillar,
                         "company_development")

    def test_no_row_is_ever_hiring_or_displacement(self):
        for row in self.rows:
            self.assertEqual(sg.as_classified(row)["signal_direction"], "neutral")

    def test_the_row_reaches_verified_because_the_portal_is_a_primary_source(self):
        # The registrar publishes the register itself rather than a report of
        # it. If the host ever leaves vocab.PRIMARY_SOURCE_DOMAINS every
        # Singapore row silently drops to 'reported'.
        self.assertIn("data.gov.sg", vocab.PRIMARY_SOURCE_DOMAINS)
        self.assertEqual(self.signal("WORLDAI PTE. LTD.").confidence, "verified")

    def test_the_source_url_is_the_portals_page_for_the_file_it_was_read_from(self):
        for row in self.rows:
            self.assertEqual(row["source_url"],
                             f"https://data.gov.sg/datasets/{DATASET}/view")
            self.assertEqual(row["discovery_url"], sg.COLLECTION_PAGE)

    def test_the_shared_dataset_page_is_revisited_on_purpose(self):
        # One page is shared by every company whose name begins with that
        # letter. Marking it seen would drop every company after the first.
        self.assertTrue(sg.REVISITS_ITS_SOURCE_URL)

    def test_the_signed_download_url_is_never_stored_on_a_row(self):
        # It expires. A stored one is a dead link with a credential in it.
        for row in self.rows:
            for value in row.values():
                self.assertNotIn("s3.example.test", str(value))
                self.assertNotIn("AWSAccessKeyId", str(value))

    def test_singapore_is_placed_as_a_city_state(self):
        sig = self.signal("WORLDAI PTE. LTD.")
        self.assertEqual(sig.country, "SG")
        self.assertEqual(sig.hq_city, "Singapore")
        self.assertEqual(sig.hq_country, "SG")

    def test_no_model_is_involved(self):
        # If as_classified ever disappears, run_collect starts paying to read a
        # source whose every field is a column.
        self.assertTrue(callable(getattr(sg, "as_classified", None)))

    def test_no_public_string_carries_a_dash_that_is_not_a_hyphen(self):
        item = self.by_company("WORLDAI PTE. LTD.")
        classified = sg.as_classified(item)
        public = [sg.ATTRIBUTION, item["headline"], item["summary"],
                  item["raw_text"], classified["talent_readthrough"]]
        for text in public:
            for dash in ("—", "–"):
                self.assertNotIn(dash, text, text[:60])


# --- the industry filter ---------------------------------------------------

class TheIndustryCodeIsTheFilter(_Base):
    def test_a_62_company_is_kept(self):
        self.assertEqual(
            sorted(r["company"] for r in self.rows),
            ["WENYA LABS PTE. LTD.", "WISAGENT PTE. LTD.", "WORLDAI PTE. LTD."])

    def test_a_company_outside_the_prefix_is_rejected(self):
        # A hotel is incorporated the same way and is not a software company.
        self.assertNotIn("WATERFRONT HOTEL PTE. LTD.",
                         [r["company"] for r in self.rows])
        self.assertFalse(sg.matches_ssic({"primary_ssic_code": "55101"}, ("62",)))
        self.assertFalse(sg.matches_ssic({"primary_ssic_code": ""}, ("62",)))
        for code in ("62011", "62021", "62013", "6201"):
            self.assertTrue(sg.matches_ssic({"primary_ssic_code": code}, ("62",)))

    def test_the_na_description_never_reaches_a_stored_field(self):
        """The real trap in the live data.

        `primary_ssic_description` was literally "na" on every sampled row, so
        anything that renders it unchecked puts "operates in na" on a public
        page. The CODE is the reliable field and the description is optional.
        """
        for marker in ("na", "NA", "N.A.", "n/a", "-", "", "  "):
            self.assertEqual(sg.ssic_description({"primary_ssic_description": marker}),
                             "", repr(marker))
        self.assertEqual(
            sg.ssic_description({"primary_ssic_description": "Hotels with restaurant"}),
            "Hotels with restaurant")
        for row in self.rows:
            self.assertEqual(row["ssic_description"], "")
            for text in (row["summary"], row["raw_text"],
                         sg.as_classified(row)["talent_readthrough"]):
                self.assertNotIn(" na ", f" {text.lower()} ")
                self.assertNotIn("activity as na", text.lower())

    def test_the_industry_is_taken_from_the_code_and_not_from_the_description(self):
        self.assertEqual(self.signal("WORLDAI PTE. LTD.").industry, "technology")
        # A prefix nobody mapped stores no industry rather than guessing one.
        self.assertEqual(sg.industry_for("55101", ("55",)), "")
        self.assertEqual(sg.industry_for("62011", ("62",)), "technology")

    def test_a_prefix_that_is_not_a_prefix_is_refused(self):
        # One digit is a whole SSIC section, which is the difference between an
        # industry filter and no filter at all.
        for bad in ("6", "abc", "62a", "620110", "-62"):
            with _env(TIT_SG_SSIC=bad):
                with self.assertRaises(sg.SingaporeAcraError, msg=bad):
                    sg.ssic_prefixes()
        with _env(TIT_SG_SSIC=",  ,"):
            with self.assertRaises(sg.SingaporeAcraError):
                sg.ssic_prefixes()

    def test_the_prefixes_can_be_widened_by_hand(self):
        with _env(TIT_SG_SSIC="62,63"):
            self.assertEqual(sg.ssic_prefixes(), ("62", "63"))
        with _env(TIT_SG_SSIC=""):
            self.assertEqual(sg.ssic_prefixes(), sg.DEFAULT_SSIC_PREFIXES)


# --- the window ------------------------------------------------------------

class TheWindowIsTheOnlyState(_Base):
    def test_a_company_incorporated_on_the_first_day_is_included(self):
        row = self.by_company("WENYA LABS PTE. LTD.")
        self.assertEqual(row["incorporated_on"], WINDOW_START)

    def test_a_company_incorporated_the_day_before_is_excluded(self):
        self.assertNotIn("WHIZHACK TECHNOLOGIES PTE. LTD.",
                         [r["company"] for r in self.rows])

    def test_the_published_date_is_the_incorporation_date(self):
        self.assertEqual(self.signal("WORLDAI PTE. LTD.").published_date,
                         "2026-07-15")

    def test_a_malformed_or_missing_date_is_dropped_and_never_stamped(self):
        """A snapshot states one date and has no second field to fall back on.

        Storing an incorporation against today because its date would not parse
        is worse than not storing it at all.
        """
        stored = [r["company"] for r in self.rows]
        self.assertNotIn("WRONG DATE PTE. LTD.", stored)
        self.assertNotIn("NO DATE PTE. LTD.", stored)
        for bad in ("", "   ", "15/07/2026", "2026-7-1", "2026", "yesterday",
                    "2026-13-01", "2026-02-30", None):
            self.assertIsNone(sg.parse_date(bad), repr(bad))
        self.assertEqual(sg.parse_date("2026-07-15"), "2026-07-15")

    def test_a_row_with_no_usable_date_builds_no_record_at_all(self):
        record = dict(zip(COLUMNS, ROWS[4]))            # SLASHDATE
        self.assertIsNone(sg._row(record, dataset_id=DATASET, prefixes=("62",)))

    def test_the_window_can_be_widened_and_a_nonsense_one_is_refused(self):
        with _env(TIT_SG_DAYS="120"):
            self.assertEqual(sg.window_days(), 120)
        with _env(TIT_SG_DAYS=""):
            self.assertEqual(sg.window_days(), sg.DEFAULT_DAYS)
        for bad in ("lots", "0", "-3", "7.5", str(sg.MAX_DAYS + 1)):
            with _env(TIT_SG_DAYS=bad):
                with self.assertRaises(sg.SingaporeAcraError, msg=bad):
                    sg.window_days()


# --- what is not an employer ----------------------------------------------

class AnEntityThatIsGoneIsNotANewEmployer(_Base):
    def test_a_struck_off_company_is_dropped(self):
        self.assertNotIn("WOUND UP PTE. LTD.", [r["company"] for r in self.rows])
        self.assertFalse(sg.is_live({"entity_status_description": "Struck Off"}))
        self.assertFalse(sg.is_live({"entity_status_description": "In Liquidation"}))
        self.assertFalse(sg.is_live({"entity_status_description": ""}))
        for good in ("Live Company", "Live", "live company"):
            self.assertTrue(sg.is_live({"entity_status_description": good}), good)

    def test_one_uen_stores_once_however_often_it_appears(self):
        # An entity is incorporated once. Two rows with one UEN are the same
        # company read twice, never two incorporations.
        uens = [r["uen"] for r in self.rows]
        self.assertEqual(len(uens), len(set(uens)))

    def test_the_officer_count_is_a_count_and_never_a_guessed_zero(self):
        self.assertEqual(sg.officer_count({"no_of_officers": "4"}), 4)
        self.assertEqual(sg.officer_count({"no_of_officers": "0"}), 0)
        for missing in ("", "  ", "na", "-", "two", None):
            self.assertIsNone(sg.officer_count({"no_of_officers": missing}),
                              repr(missing))
        self.assertIn("4 officer", self.by_company("WISAGENT PTE. LTD.")["summary"])


# --- silence ---------------------------------------------------------------

class TheDownloadHopAnswers201(unittest.TestCase):
    """The portal does not answer its own routes with one code.

    `poll-download` returns 201 Created, because asking for a download is
    modelled as creating an export job even when the signed URL comes straight
    back in the body. The metadata routes return a plain 200. A collector that
    insists on 200 cannot complete a single run, and this repository found that
    out the expensive way: 42 tests passed against exactly that collector
    because the fixture answered 200 where the server answers 201.
    """

    def test_a_201_on_poll_download_is_accepted(self):
        rows = _run(session=_session(poll_http=201))
        self.assertTrue(rows)

    def test_a_200_on_poll_download_is_still_accepted(self):
        # Both codes carry the same envelope, and the envelope is what is
        # checked. If the portal ever settles on 200 this must not break.
        rows = _run(session=_session(poll_http=200))
        self.assertTrue(rows)

    def test_a_real_error_code_is_still_refused_loudly(self):
        for status in (403, 404, 500, 503):
            with self.assertRaises(sg.SingaporeAcraError):
                _run(session=_session(poll_http=status))

    def test_the_refusal_does_not_send_anybody_looking_for_a_key(self):
        # The collection is keyless. A message that reads like an auth failure
        # is how an owner gets asked for a credential that does not exist.
        try:
            _run(session=_session(poll_http=500))
        except sg.SingaporeAcraError as exc:
            self.assertIn("keyless", str(exc))
            self.assertIn("not a missing credential", str(exc))
        else:
            self.fail("a 500 should have raised")


class SilenceIsNotHealth(_Base):
    def test_a_run_that_finds_nothing_raises_rather_than_reporting_zero(self):
        session = _Session({DATASET: BARREN})
        with self.assertRaises(sg.SingaporeAcraError) as caught:
            _run(session)
        self.assertIn("not a quiet month", str(caught.exception))

    def test_the_floor_is_scaled_to_what_was_read_and_is_never_zero(self):
        self.assertEqual(sg.emptiness_floor(0, 45), 1)
        self.assertGreater(sg.emptiness_floor(1_500_000, 45),
                           sg.emptiness_floor(57_533, 45))

    def test_a_renamed_column_is_a_breakage_and_not_an_empty_month(self):
        """The single most likely silent zero.

        If `primary_ssic_code` or the date column is renamed, every row stops
        qualifying and the run looks like a month in which Singapore
        incorporated no software company.
        """
        for dropped in ("primary_ssic_code", "registration_incorporation_date",
                        "entity_name", "uen", "entity_status_description"):
            header = [c for c in COLUMNS if c != dropped]
            text = ",".join(header) + "\n" + ",".join("x" for _ in header) + "\n"
            with self.assertRaises(sg.SingaporeAcraError, msg=dropped) as caught:
                sg.parse_rows(text, dataset_id=DATASET)
            self.assertIn(dropped, str(caught.exception))

    def test_a_published_file_with_no_rows_is_a_breakage(self):
        with self.assertRaises(sg.SingaporeAcraError):
            sg.parse_rows(",".join(COLUMNS) + "\n", dataset_id=DATASET)

    def test_a_poll_download_that_did_not_succeed_is_not_an_empty_file(self):
        for status in ("DOWNLOAD_FAILED", "PENDING", ""):
            session = _Session({DATASET: FIXTURE}, poll_status=status)
            with self.assertRaises(sg.SingaporeAcraError, msg=status) as caught:
                sg.fetch_dataset_csv(DATASET, session=session)
            self.assertIn("DOWNLOAD_SUCCESS", str(caught.exception))

    def test_a_poll_download_error_code_is_refused(self):
        session = _Session({DATASET: FIXTURE}, poll_code=4)
        with self.assertRaises(sg.SingaporeAcraError):
            sg.fetch_dataset_csv(DATASET, session=session)

    def test_a_non_200_names_the_keyless_endpoint_rather_than_a_credential(self):
        # There is no key for this collection and none is needed. An error that
        # implies otherwise sends the next reader looking for a secret that
        # does not exist.
        session = _Session({DATASET: FIXTURE}, metadata_status=503)
        with self.assertRaises(sg.SingaporeAcraError) as caught:
            sg.collection_metadata(session=session)
        self.assertIn("keyless", str(caught.exception))

    def test_an_expired_signed_url_is_a_loud_failure(self):
        session = _Session({DATASET: FIXTURE}, download_status=403)
        with self.assertRaises(sg.SingaporeAcraError) as caught:
            sg.fetch_dataset_csv(DATASET, session=session)
        self.assertIn("expire", str(caught.exception))

    def test_a_short_collection_is_a_breakage_not_a_short_run(self):
        with self.assertRaises(sg.SingaporeAcraError) as caught:
            sg.dataset_ids({"childDatasets": [DATASET, "d_two", "d_three"]})
        self.assertIn(str(sg.MEASURED_DATASETS), str(caught.exception))
        self.assertEqual(
            len(sg.dataset_ids({"childDatasets": [f"d_{i}" for i in range(27)]})), 27)

    def test_metadata_with_no_child_datasets_is_a_shape_change(self):
        session = _Session({DATASET: FIXTURE})

        def _get(url, headers=None, params=None, timeout=None):
            return _Resp({"code": 0, "data": {"collectionMetadata": {"name": "x"}}})

        session.get = _get
        with self.assertRaises(sg.SingaporeAcraError) as caught:
            sg.collection_metadata(session=session)
        self.assertIn("shape has changed", str(caught.exception))

    def test_the_dataset_ids_are_read_from_the_collection_and_never_typed(self):
        # 27 identifiers typed here would be 27 things to keep in step with
        # somebody else's publishing, and a stale one is a letter of the
        # alphabet quietly going missing.
        source = open(sg.__file__, encoding="utf-8").read()
        self.assertEqual(source.count(DATASET), 1)   # the docstring, measured

    def test_the_request_delay_is_a_real_pause_between_files(self):
        # data.gov.sg publishes no anonymous rate limit but states that signing
        # up raises the limit, which only means anything if one exists.
        self.assertGreaterEqual(sg.REQUEST_DELAY, 1.0)


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
