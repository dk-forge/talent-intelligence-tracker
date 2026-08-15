"""The job-board collector must publish diffs, never adverts.

This source sits closest to the line the product must not cross. `validate`
rejects a single vacancy on purpose, and a collector reading job boards is
exactly how adverts get in. So the assertions here are about what a row IS:
a counted movement at an employer, sourced to the employer's own board, with
no vacancy URL and no vacancy title anywhere on it.

The rest guards the state file, which is the archive. These APIs have no
history anywhere and no closed-on date, so a lost baseline is a lost series.

Recorded fixtures, never a live call.
"""

from __future__ import annotations

import collections
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from collectors import ats_boards
from pipeline import dedupe, validate

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class _Boards(unittest.TestCase):
    """Fetching is stubbed at the payload boundary, so the parsing under test
    is the real thing and no network call is made."""

    payloads = {
        "greenhouse:stripe": "ats_greenhouse_stripe.json",
        "ashby:ramp": "ats_ashby_ramp.json",
        "lever:matchgroup": "ats_lever_matchgroup.json",
        "smartrecruiters:Wise": "ats_smartrecruiters_wise.json",
    }
    entries = [
        {"ats": "greenhouse", "slug": "stripe", "company": "Stripe"},
        {"ats": "ashby", "slug": "ramp", "company": "Ramp"},
        {"ats": "lever", "slug": "matchgroup", "company": "Match Group"},
        {"ats": "smartrecruiters", "slug": "Wise", "company": "Wise"},
    ]

    def setUp(self):
        self._get = ats_boards._get
        # The robots gate is stubbed for the same reason the fetch is: these
        # tests are offline, and a live robots.txt read would make them depend
        # on four third-party hosts being reachable. The gate has its own tests
        # below, against a stubbed robots.txt rather than a stubbed gate.
        self._allowed = ats_boards.board_allowed
        ats_boards.board_allowed = lambda entry: True

        def fake_get(url, **kwargs):
            for key, name in self.payloads.items():
                _ats, _, slug = key.partition(":")
                if f"/{slug}" in url or f"={slug}" in url:
                    return _fixture(name)
            raise AssertionError(f"unexpected fetch: {url}")

        ats_boards._get = fake_get

    def tearDown(self):
        ats_boards._get = self._get
        ats_boards.board_allowed = self._allowed

    def postings(self, entry):
        return ats_boards.fetch_postings(entry)

    def entry(self, ats):
        """By ATS, never by position: the list grows as boards are added, and a
        test pinned to entries[2] silently starts asserting about a different
        employer the day one is inserted above it."""
        return next(e for e in self.entries if e["ats"] == ats)


class Parsing(_Boards):
    def test_all_three_ats_shapes_parse(self):
        for entry in self.entries:
            self.assertTrue(self.postings(entry), entry["slug"])

    def test_locations_resolve_to_the_curated_vocabulary(self):
        places = {p["place"] for p in self.postings(self.entry("greenhouse"))}
        self.assertIn("city:San Francisco", places)
        self.assertIn("city:Singapore", places)
        # "Japan" is a country and no curated city, which is a country key and
        # not a discarded row.
        self.assertIn("country:JP", places)

    def test_a_worldwide_board_places_rows_outside_the_united_states(self):
        places = {p["place"] for p in self.postings(self.entry("smartrecruiters"))}
        self.assertIn("city:London", places)
        self.assertIn("city:Tokyo", places)
        # Sao Paulo, Tallinn and Kuala Lumpur used to fall back to a country
        # key on this very board, because the gazetteer had no entry for them.
        # The hub gazetteer places all three, which is the whole point of it:
        # the board always said where the job was. The country fallback is
        # still exercised — by "Japan" above and "Remote (Sweden)" below —
        # and it is no longer reachable from THIS fixture, because every
        # location in it now normalises to a city.
        self.assertIn("city:Sao Paulo", places)
        self.assertIn("city:Tallinn", places)
        self.assertIn("city:Kuala Lumpur", places)
        self.assertFalse(any(p.startswith("country:") for p in places))

    def test_remote_is_a_place_and_not_a_guess_at_a_city(self):
        self.assertEqual(ats_boards.place_key("Remote (Sweden)"), "country:SE")
        self.assertEqual(ats_boards.place_key("Remote"), "remote:")
        self.assertEqual(ats_boards.place_key("Anywhere on Mars"), "")

    def test_a_us_state_code_is_never_read_as_a_country(self):
        """Half the state codes collide with an ISO2 country code, and this
        function was reading them as countries on live boards: "Peoria, IL"
        filed under Israel, "San Jose, CA" under Canada, "Cambridge, MA" under
        Morocco, "Boise, ID" under Indonesia. Two letters after a comma on a
        US board is a state, every time."""
        for location in ("Peoria, IL", "Boise, ID", "Wilmington, DE",
                         "Baton Rouge, LA", "Bangor, ME", "Reno, NV"):
            self.assertEqual(ats_boards.place_key(location), "country:US",
                             location)

    def test_the_whole_location_is_tried_before_it_is_split(self):
        """A board writes the disambiguation the gazetteer needs into one
        field. Splitting first threw it away and filed every London, Ontario
        role in England."""
        self.assertEqual(ats_boards.place_key("London, Ontario"),
                         "city:London, Ontario")
        self.assertEqual(ats_boards.place_key("London, UK"), "city:London")
        self.assertEqual(ats_boards.place_key("Cambridge, MA"), "city:Cambridge MA")
        self.assertEqual(ats_boards.place_key("Cambridge, UK"), "city:Cambridge UK")
        self.assertEqual(ats_boards.place_key("Washington, DC"), "city:Washington DC")

    def test_a_contradicting_qualifier_falls_back_to_the_country(self):
        """Paris, Texas and Melbourne, Florida are real, and neither is the
        city we curate. Falling back to the country is the honest answer for a
        town we do not cover."""
        for location in ("Paris, TX", "Melbourne, FL", "Dublin, OH",
                         "Athens, GA", "Manchester, NH"):
            self.assertEqual(ats_boards.place_key(location), "country:US",
                             location)
        # And the same names, in the countries we do curate, still resolve.
        self.assertEqual(ats_boards.place_key("Paris, France"), "city:Paris")
        self.assertEqual(ats_boards.place_key("Melbourne, Australia"),
                         "city:Melbourne")
        self.assertEqual(ats_boards.place_key("Dublin, Ireland"), "city:Dublin")

    def test_the_hub_gazetteer_reaches_the_boards(self):
        """These are ordinary ATS location strings that used to resolve to a
        country at best. 10,357 of 17,956 postings in the committed board
        state currently carry a country key rather than a city."""
        for location, key in (
                ("Tel Aviv, Israel", "city:Tel Aviv"),
                ("Bengaluru, India", "city:Bangalore"),
                ("Sao Paulo, Brazil", "city:Sao Paulo"),
                ("Lagos, Nigeria", "city:Lagos"),
                ("Nairobi, Kenya", "city:Nairobi"),
                ("Jakarta, Indonesia", "city:Jakarta"),
                ("Seoul, South Korea", "city:Seoul"),
                ("Dubai, UAE", "city:Dubai"),
                ("Chicago, IL", "city:Chicago"),
                ("Los Angeles, CA", "city:Los Angeles"),
                ("Mexico City, Mexico", "city:Mexico City"),
                ("Kuala Lumpur", "city:Kuala Lumpur")):
            self.assertEqual(ats_boards.place_key(location), key, location)

    def test_titles_resolve_to_functions_longest_phrase_first(self):
        self.assertEqual(ats_boards.function_for_title("Data Engineer, Payments"), "data_ai")
        self.assertEqual(ats_boards.function_for_title("Software Engineer"), "engineering")
        self.assertEqual(ats_boards.function_for_title("Account Executive"), "sales")
        # A title that matches nothing carries no function rather than a made
        # up one. It still counts toward the total and toward its place.
        self.assertIsNone(ats_boards.function_for_title("Zookeeper"))


class TheSignalIsTheDiff(_Boards):
    def _state(self):
        return {"version": 1, "boards": {}}

    def test_the_first_sighting_is_a_baseline_and_not_a_signal(self):
        state = self._state()
        items = ats_boards.collect(watchlist=self.entries, state=state,
                                   today="2026-07-01", persist=False)
        self.assertEqual([i for i in items if i["kind"] == "hiring"], [])
        self.assertIn("greenhouse:stripe", state["boards"])
        self.assertTrue(state["boards"]["greenhouse:stripe"]["baseline"])

    def test_a_movement_becomes_one_counted_row_per_employer(self):
        state = self._state()
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        # Pretend the boards were much smaller a fortnight ago.
        for record in state["boards"].values():
            record["baseline"] = {"date": "2026-06-17", "total": 1,
                                  "places": {}, "functions": {}}
        items = ats_boards.collect(watchlist=self.entries, state=state,
                                   today="2026-07-01", persist=False)
        hiring = [i for i in items if i["kind"] == "hiring"]
        self.assertEqual(len(hiring), len(self.entries))
        for item in hiring:
            self.assertGreater(item["delta"], 0)

    def test_a_board_that_has_not_moved_publishes_nothing(self):
        state = self._state()
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        items = ats_boards.collect(watchlist=self.entries, state=state,
                                   today="2026-07-02", persist=False)
        self.assertEqual([i for i in items if i["kind"] == "hiring"], [])

    def test_a_shrinking_board_is_never_published_as_a_layoff(self):
        state = self._state()
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        for record in state["boards"].values():
            record["baseline"] = {"date": "2026-06-17", "total": 9_000,
                                  "places": {}, "functions": {}}
        items = ats_boards.collect(watchlist=self.entries, state=state,
                                   today="2026-07-01", persist=False)
        self.assertEqual([i for i in items if i["kind"] == "hiring"], [])

    def test_the_daily_series_is_recorded_even_when_nothing_is_published(self):
        """The archive is the point. These APIs have no history anywhere, so a
        day that is not written down is gone for good."""
        state = self._state()
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-02", persist=False)
        history = state["boards"]["greenhouse:stripe"]["history"]
        self.assertEqual([h["date"] for h in history], ["2026-07-01", "2026-07-02"])

    def test_a_second_run_on_one_day_does_not_double_the_series(self):
        state = self._state()
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        self.assertEqual(len(state["boards"]["ashby:ramp"]["history"]), 1)

    def test_a_dry_run_never_advances_the_baseline(self):
        """A rehearsal that consumed the movement would leave nothing for the
        real run to publish, and the movement cannot be recovered."""
        written = []
        real_save = ats_boards.save_state
        ats_boards.save_state = lambda state, path=None: written.append(state)
        try:
            ats_boards.collect(watchlist=self.entries, state=self._state(),
                               today="2026-07-01", dry_run=True)
            self.assertEqual(written, [])
            ats_boards.collect(watchlist=self.entries, state=self._state(),
                               today="2026-07-01", dry_run=False)
            self.assertEqual(len(written), 1)
        finally:
            ats_boards.save_state = real_save


class ItIsNotAJobBoard(_Boards):
    def _hiring_signals(self):
        state = {"version": 1, "boards": {}}
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        for record in state["boards"].values():
            record["baseline"] = {"date": "2026-06-17", "total": 1,
                                  "places": {}, "functions": {}}
        items = ats_boards.collect(watchlist=self.entries, state=state,
                                   today="2026-07-01", persist=False)
        return [validate.build_signal(ats_boards.as_classified(i), i,
                                      ats_boards.COLLECTOR)
                for i in items if i["kind"] == "hiring"]

    def test_a_row_survives_the_gate_that_rejects_single_adverts(self):
        for signal in self._hiring_signals():
            self.assertEqual(signal.pillar, "company_development")
            self.assertEqual(signal.signal_direction, "hiring")
            self.assertEqual(signal.headcount_scope, "new_roles")
            self.assertGreater(signal.headcount, 0)

    def test_the_source_url_is_the_board_and_never_a_vacancy(self):
        for signal in self._hiring_signals():
            path = urlparse(signal.source_url).path
            # One path segment: the employer's board. A vacancy URL carries the
            # posting id, and validate rejects the /jobs/ path outright.
            self.assertEqual(len(path.strip("/").split("/")), 1, signal.source_url)
            self.assertNotIn("/jobs/", signal.source_url)

    def test_no_individual_job_title_reaches_a_stored_row(self):
        for signal in self._hiring_signals():
            text = f"{signal.headline} {signal.summary} {signal.talent_readthrough}"
            for title in ("Account Executive", "Security Engineer",
                          "Workplace Operations Coordinator"):
                self.assertNotIn(title, text)

    def test_the_count_is_never_dressed_up_as_a_verified_fact(self):
        """The board is the employer's publication; the COUNT is our
        measurement of it on two dates. Those are not the same tier."""
        for signal in self._hiring_signals():
            self.assertEqual(signal.confidence, "reported")

    def test_the_summary_quotes_only_figures_the_source_text_carries(self):
        state = {"version": 1, "boards": {}}
        ats_boards.collect(watchlist=self.entries, state=state,
                           today="2026-07-01", persist=False)
        for record in state["boards"].values():
            record["baseline"] = {"date": "2026-06-17", "total": 1,
                                  "places": {}, "functions": {}}
        for item in ats_boards.collect(watchlist=self.entries, state=state,
                                       today="2026-07-01", persist=False):
            validate.assert_figures_are_sourced(
                ats_boards.as_classified(item)["summary"], item["raw_text"])


class PostedPay(_Boards):
    def test_ashby_pay_ranges_become_a_pay_row_with_a_place(self):
        items = ats_boards.collect(watchlist=[self.entry("ashby")],
                                   state={"version": 1, "boards": {}},
                                   today="2026-07-01", persist=False)
        pay = [i for i in items if i["kind"] == "pay"]
        self.assertEqual(len(pay), 1)
        signal = validate.build_signal(ats_boards.as_classified(pay[0]),
                                       pay[0], ats_boards.COLLECTOR)
        self.assertEqual(signal.pillar, "rewards_comp")
        self.assertEqual(signal.signal_direction, "comp_shift")

    def test_only_annual_salary_components_are_read_and_the_currency_is_kept(self):
        """A band is read in the currency the posting states, and only when the
        component is an annual SALARY.

        This used to discard everything that was not USD, which was the right
        instinct expressed as the wrong rule: the danger is POOLING currencies
        into one median, not reading them. Keeping the code makes sterling and
        euro boards readable without converting anything, and `snapshot` is
        where the no-pooling half is enforced."""
        self.assertEqual(ats_boards._salary({"compensation": {"summaryComponents": [
            {"compensationType": "Salary", "interval": "1 YEAR",
             "currencyCode": "GBP", "minValue": 80000, "maxValue": 100000}]}}),
            (80000, 100000, "GBP"))
        self.assertEqual(ats_boards._salary({"compensation": {"summaryComponents": [
            {"compensationType": "Salary", "interval": "1 YEAR",
             "currencyCode": "USD", "minValue": 100000, "maxValue": 200000}]}}),
            (100000, 200000, "USD"))
        # Equity is not salary, and an hourly component is not an annual band.
        self.assertIsNone(ats_boards._salary({"compensation": {"summaryComponents": [
            {"compensationType": "EquityPercentage", "interval": "NONE",
             "currencyCode": None, "minValue": None, "maxValue": None}]}}))
        self.assertIsNone(ats_boards._salary({"compensation": {"summaryComponents": [
            {"compensationType": "Salary", "interval": "1 HOUR",
             "currencyCode": "USD", "minValue": 40, "maxValue": 60}]}}))

    def test_an_unmoved_band_is_not_republished(self):
        state = {"version": 1, "boards": {}}
        ats_boards.collect(watchlist=[self.entry("ashby")], state=state,
                           today="2026-07-01", persist=False)
        again = ats_boards.collect(watchlist=[self.entry("ashby")], state=state,
                                   today="2026-07-02", persist=False)
        self.assertEqual([i for i in again if i["kind"] == "pay"], [])


class FailLoud(_Boards):
    def test_most_of_the_watchlist_failing_is_a_breakage_not_a_quiet_day(self):
        def dead(url, **kwargs):
            raise ValueError("boom")

        ats_boards._get = dead
        with self.assertRaises(ats_boards.BoardError):
            ats_boards.collect(watchlist=self.entries,
                               state={"version": 1, "boards": {}},
                               today="2026-07-01", persist=False)

    def test_a_board_returning_zero_counts_as_a_failure(self):
        """All three APIs answer 200 with an empty list for a slug that does
        not exist, so a mistyped slug looks exactly like an employer with
        nothing open."""
        ats_boards._get = lambda url, **kwargs: {"jobs": [], "content": []}
        with self.assertRaises(ats_boards.BoardError):
            ats_boards.collect(watchlist=self.entries,
                               state={"version": 1, "boards": {}},
                               today="2026-07-01", persist=False)

    def test_an_unreadable_state_file_stops_the_run(self):
        bad = FIXTURES / "not-json-state.json"
        bad.write_text("{ this is not json")
        try:
            with self.assertRaises(ats_boards.BoardError):
                ats_boards.load_state(bad)
        finally:
            bad.unlink()


class Watchlist(unittest.TestCase):
    def test_the_shipped_watchlist_is_usable(self):
        boards = ats_boards.load_watchlist()
        self.assertGreaterEqual(len(boards), 20)
        for entry in boards:
            self.assertIn(entry["ats"], ats_boards.API_URLS)
            self.assertTrue(entry.get("company"), entry)
            # A name derived from a slug reads as 'Scaleai'. Names come from
            # the employer, so at least one must differ from its slug.
        self.assertTrue(any(b["company"].lower() != b["slug"].lower()
                            for b in boards))

    def test_every_ats_in_the_watchlist_has_a_public_board_url(self):
        for entry in ats_boards.load_watchlist():
            url = ats_boards.BOARD_URLS[entry["ats"]].format(slug=entry["slug"])
            self.assertTrue(urlparse(url).path.strip("/"), url)

    def test_no_board_url_is_an_aggregator_validate_would_reject(self):
        """LinkedIn, Indeed and the rest are blocked in validate.py, and their
        terms forbid this anyway. A watchlist entry pointing at one would be
        rejected row by row rather than caught here, which is a slow way to
        find out."""
        for entry in ats_boards.load_watchlist():
            url = ats_boards.BOARD_URLS[entry["ats"]].format(slug=entry["slug"])
            host = urlparse(url).netloc.lower()
            self.assertNotIn(host, validate._JOB_BOARD_HOSTS, url)
            self.assertIsNone(validate._JOB_POSTING_PATH.search(urlparse(url).path), url)

    def test_a_withdrawn_board_is_recorded_with_its_reason_and_never_read(self):
        """SmartRecruiters is not deleted, it is withdrawn: the file says which
        boards we could read and choose not to, and why. load_watchlist must
        never hand one of them to the fetcher."""
        payload = json.loads(
            (Path(ats_boards.WATCHLIST_PATH)).read_text())
        withdrawn = payload.get("withdrawn") or []
        self.assertTrue(withdrawn)
        for entry in withdrawn:
            self.assertTrue(entry.get("reason"), entry)
        live = {f"{b['ats']}:{b['slug']}" for b in ats_boards.load_watchlist()}
        for entry in withdrawn:
            self.assertNotIn(f"{entry['ats']}:{entry['slug']}", live)


class RobotsIsTheGate(_Boards):
    """A publisher's terms decide whether we may count their boards at all.

    The gate is the press collector's `robots_allows`, imported rather than
    reimplemented, so a fix there covers both sources and neither can drift.
    """

    def test_the_gate_asks_about_the_endpoint_we_actually_fetch(self):
        asked = []
        real = ats_boards.robots_allows
        ats_boards.robots_allows = lambda url, **kw: asked.append(url) or True
        ats_boards.board_allowed = self._allowed   # the real gate, stubbed robots
        try:
            ats_boards.board_allowed({"ats": "greenhouse", "slug": "stripe"})
        finally:
            ats_boards.robots_allows = real
            ats_boards.board_allowed = lambda entry: True
        self.assertEqual(len(asked), 1)
        self.assertIn("boards-api.greenhouse.io", asked[0])
        self.assertIn("stripe", asked[0])

    def test_a_disallowed_board_is_never_requested(self):
        """Not fetched-then-discarded. The request does not happen."""
        def explode(url, **kwargs):
            raise AssertionError(f"a robots-blocked board was fetched: {url}")

        ats_boards.board_allowed = lambda entry: entry["ats"] != "smartrecruiters"
        ats_boards._get = lambda url, **kw: (
            explode(url) if "smartrecruiters" in url else _fixture(
                self.payloads["greenhouse:stripe"]))
        items = ats_boards.collect(watchlist=[self.entry("smartrecruiters"),
                                              self.entry("greenhouse")],
                                   state={"version": 1, "boards": {}},
                                   today="2026-07-01", persist=False)
        self.assertEqual(items, [])
        self.assertEqual(ats_boards.LAST_RUN["robots_blocked"], 1)
        self.assertEqual(ats_boards.LAST_RUN["read"], 1)

    def test_a_blocked_board_is_not_counted_as_a_breakage(self):
        """Their terms are a decision, not an outage. A watchlist that is mostly
        blocked must not raise, and one blocked board must not excuse the rest
        failing either."""
        ats_boards.board_allowed = lambda entry: entry["ats"] == "greenhouse"
        ats_boards.collect(watchlist=self.entries,
                           state={"version": 1, "boards": {}},
                           today="2026-07-01", persist=False)
        self.assertEqual(ats_boards.LAST_RUN["robots_blocked"], 3)
        self.assertEqual(ats_boards.LAST_RUN["failed"], 0)

        # Same run, but the one board we were allowed to read is broken.
        ats_boards._get = lambda url, **kw: {"jobs": [], "content": []}
        with self.assertRaises(ats_boards.BoardError):
            ats_boards.collect(watchlist=self.entries,
                               state={"version": 1, "boards": {}},
                               today="2026-07-01", persist=False)


class LeverIsNotLikeTheOthers(_Boards):
    def test_a_lever_board_parses_places_functions_and_a_posted_band(self):
        postings = self.postings(self.entry("lever"))
        self.assertTrue(postings)
        places = {p["place"] for p in postings}
        self.assertIn("city:New York", places)
        self.assertIn("city:Tokyo", places)
        self.assertTrue(any(p["salary"] for p in postings))
        self.assertTrue(any(p["function"] for p in postings))

    def test_only_annual_lever_bands_are_read_and_the_currency_is_kept(self):
        self.assertEqual(ats_boards._lever_salary({"salaryRange": {
            "interval": "per-year-salary", "currency": "USD",
            "min": 150000, "max": 180000}}), (150000, 180000, "USD"))
        self.assertEqual(ats_boards._lever_salary({"salaryRange": {
            "interval": "per-year-salary", "currency": "GBP",
            "min": 90000, "max": 110000}}), (90000, 110000, "GBP"))
        # An hourly wage is not an annual band, and is never scaled into one.
        self.assertIsNone(ats_boards._lever_salary({"salaryRange": {
            "interval": "per-hour-wage", "currency": "USD",
            "min": 40, "max": 60}}))

    def test_a_missing_lever_slug_kills_one_board_and_not_the_run(self):
        """Lever is the only one of these APIs that tells a missing board from
        an empty one, and it says so with an error object rather than a 404."""
        def fake_get(url, **kwargs):
            if "lever" in url:
                return {"ok": False, "error": "Document not found"}
            for key, name in self.payloads.items():
                _ats, _, slug = key.partition(":")
                if f"/{slug}" in url or f"={slug}" in url:
                    return _fixture(name)
            raise AssertionError(url)

        ats_boards._get = fake_get
        ats_boards.collect(watchlist=self.entries,
                           state={"version": 1, "boards": {}},
                           today="2026-07-01", persist=False)
        self.assertEqual(ats_boards.LAST_RUN["failed"], 1)
        self.assertEqual(ats_boards.LAST_RUN["read"], len(self.entries) - 1)


class _NewerBoards(_Boards):
    """The same offline machinery, over payloads captured for the work-mode and
    pay-range work. Two providers in their REAL shape, and deliberately not the
    same two:

        ashby:netgear      43 roles, every one of them carrying the ATS's own
                           typed `workplaceType`, across all three values, and
                           a `compensation` object priced in three currencies.
        greenhouse:dropbox 35 roles from a provider that types NO work-mode
                           field at all, so every mode here is read out of
                           location prose, and `pay_input_ranges` bands in four
                           currencies.
        greenhouse:airtable a board whose postings publish an on-target-earnings
                           range and a base-salary range side by side, which is
                           the case that decides whether a sales commission
                           plan gets published as somebody's salary.
    """

    payloads = {
        "greenhouse:dropbox": "ats_greenhouse_dropbox.json",
        "greenhouse:airtable": "ats_greenhouse_airtable.json",
        "ashby:netgear": "ats_ashby_netgear.json",
        "ashby:havocai": "ats_ashby_havocai.json",
        "lever:matchgroup": "ats_lever_matchgroup.json",
    }
    entries = [
        {"ats": "greenhouse", "slug": "dropbox", "company": "Dropbox"},
        {"ats": "greenhouse", "slug": "airtable", "company": "Airtable"},
        {"ats": "ashby", "slug": "netgear", "company": "Netgear"},
        {"ats": "ashby", "slug": "havocai", "company": "HavocAI"},
        {"ats": "lever", "slug": "matchgroup", "company": "Match Group"},
    ]

    def entry(self, slug):
        return next(e for e in self.entries if e["slug"] == slug)

    def snap(self, slug):
        return ats_boards.snapshot(self.postings(self.entry(slug)))

    def items(self, slugs, *, state=None, today="2026-08-14"):
        return ats_boards.collect(
            watchlist=[self.entry(s) for s in slugs],
            state=state if state is not None else {"version": 1, "boards": {}},
            today=today, persist=False)


class WorkModeIsReadNeverInferred(_NewerBoards):
    """Every posting states remote, hybrid or onsite, or it states nothing.

    The pillar this feeds (`how_we_work`) is the thinnest in the corpus, which
    is exactly the condition under which a source starts inventing evidence to
    fill it. So the assertions here are mostly about SILENCE: what happens to a
    posting that does not say, and to a board where too few of them do.
    """

    def test_a_typed_field_is_preferred_and_all_three_values_survive(self):
        postings = self.postings(self.entry("netgear"))
        modes = collections.Counter(p["mode"] for p in postings)
        self.assertEqual(set(modes), {"remote", "hybrid", "onsite"})
        self.assertTrue(all(p["mode_source"] == "structured" for p in postings))

    def test_ashbys_isremote_boolean_is_never_read_as_the_mode(self):
        """Ashby publishes `isRemote` AND `workplaceType`, and on a role typed
        Hybrid `isRemote` is still true — it means remote-ELIGIBLE. Reading the
        boolean files hybrid roles as fully remote, which is a wrong number
        rather than a missing one."""
        hybrid_but_remote_eligible = [
            j for j in _fixture("ats_ashby_ramp.json")["jobs"]
            if j.get("workplaceType") == "Hybrid" and j.get("isRemote") is True]
        self.assertTrue(hybrid_but_remote_eligible,
                        "the fixture no longer covers the case this guards")
        for job in hybrid_but_remote_eligible:
            self.assertEqual(ats_boards.structured_work_mode(job["workplaceType"]),
                             "hybrid")

    def test_a_provider_that_types_nothing_is_read_from_prose_and_says_so(self):
        snap = self.snap("dropbox")
        self.assertEqual(snap["mode_structured"], 0)
        self.assertGreaterEqual(snap["mode_known"], 20)

    def test_a_posting_that_does_not_say_is_unknown_and_never_onsite(self):
        """The single most damaging reading available to this collector. Most
        Greenhouse postings state nothing about work mode, and calling that
        onsite would label thousands of roles off an absence."""
        self.assertIsNone(ats_boards.work_mode_from_text("San Francisco, CA"))
        self.assertIsNone(ats_boards.work_mode_from_text(""))
        self.assertIsNone(ats_boards.work_mode_from_text("Austin, TX; New York, NY"))
        # And nothing in a real board's counts is attributed to a silent posting.
        snap = self.snap("airtable")
        self.assertEqual(sum(snap["modes"].values()), snap["mode_known"])
        self.assertLess(snap["mode_known"], snap["total"])

    def test_hybrid_wins_over_remote_when_a_posting_says_both(self):
        """'Hybrid - 2 days remote' is a hybrid role. A remote-first test reads
        it as fully remote and loses the more specific of the two claims."""
        self.assertEqual(ats_boards.work_mode_from_text("Hybrid - 2 days remote"),
                         "hybrid")
        self.assertEqual(ats_boards.work_mode_from_text("Remote - US"), "remote")
        self.assertEqual(ats_boards.work_mode_from_text("London (On-site)"), "onsite")

    def test_a_board_that_mostly_says_nothing_publishes_no_mix(self):
        """Airtable states a mode on 9 of 17 roles. Publishing "100% remote"
        off nine postings, when eight said nothing, would be the most
        misleading row this collector could produce."""
        snap = self.snap("airtable")
        self.assertFalse(ats_boards._mode_qualifies(snap))
        items = self.items(["airtable"])
        self.assertEqual([i for i in items if i["kind"] == "work_mode"], [])

    def test_a_qualifying_board_publishes_one_employer_level_row(self):
        items = [i for i in self.items(["netgear"]) if i["kind"] == "work_mode"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["counted"], 43)
        self.assertTrue(item["baseline"])
        signal = validate.build_signal(ats_boards.as_classified(item), item,
                                       ats_boards.COLLECTOR)
        self.assertEqual(signal.pillar, "how_we_work")
        self.assertEqual(signal.signal_direction, "neutral")
        self.assertEqual(signal.confidence, "reported")

    def test_the_row_states_the_denominator_it_was_measured_on(self):
        """A share with no denominator is not a measurement. Both numbers have
        to be on the row, because 44% of 43 roles and 44% of 4 are not the same
        claim and the reader cannot tell them apart otherwise."""
        item = [i for i in self.items(["netgear"]) if i["kind"] == "work_mode"][0]
        self.assertIn("43", item["raw_text"])
        summary = ats_boards.as_classified(item)["summary"]
        validate.assert_figures_are_sourced(summary, item["raw_text"])
        self.assertIn(str(item["counted"]), summary)

    def test_a_second_look_at_an_unmoved_board_publishes_nothing(self):
        state = {"version": 1, "boards": {}}
        self.items(["netgear"], state=state, today="2026-08-14")
        again = self.items(["netgear"], state=state, today="2026-08-15")
        self.assertEqual([i for i in again if i["kind"] == "work_mode"], [])

    def test_a_material_shift_publishes_a_change_row_and_states_both_ends(self):
        state = {"version": 1, "boards": {}}
        self.items(["netgear"], state=state, today="2026-08-14")
        # The same board as it was a quarter ago: onsite-heavy rather than
        # hybrid-heavy. Only the baseline is rewritten — the payload is the
        # real one, so what is under test is the emit rule and not a fixture.
        record = state["boards"]["ashby:netgear"]
        record["mode_baseline"] = {"date": "2026-05-14", "total": 40,
                                   "modes": {"onsite": 30, "hybrid": 6, "remote": 4},
                                   "mode_known": 40, "mode_structured": 40,
                                   "mode_place": ""}
        items = [i for i in self.items(["netgear"], state=state, today="2026-08-14")
                 if i["kind"] == "work_mode"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertFalse(item["baseline"])
        self.assertEqual(item["mode"], "onsite")
        # Both ends of the move, so the row is a change and not a restatement.
        self.assertIn("75%", item["headline"])
        self.assertIn("23%", item["headline"])
        self.assertIn("2026-05-14", item["raw_text"])

    def test_a_shift_under_the_floor_is_churn_and_publishes_nothing(self):
        state = {"version": 1, "boards": {}}
        self.items(["netgear"], state=state, today="2026-08-14")
        record = state["boards"]["ashby:netgear"]
        # Ten points on the largest-moving mode, under the fifteen the rule
        # calls a change. Roles turn over; a policy does not move that fast.
        record["mode_baseline"] = {"date": "2026-08-01", "total": 43,
                                   "modes": {"hybrid": 23, "remote": 12, "onsite": 8},
                                   "mode_known": 43, "mode_structured": 43,
                                   "mode_place": ""}
        items = [i for i in self.items(["netgear"], state=state, today="2026-08-14")
                 if i["kind"] == "work_mode"]
        self.assertEqual(items, [])

    def test_no_vacancy_title_or_url_reaches_a_work_mode_row(self):
        """The same bar every other row here clears. A ways-of-working row is
        derived from postings and must not become a way of publishing them."""
        for item in self.items(["netgear", "dropbox"]):
            signal = validate.build_signal(ats_boards.as_classified(item), item,
                                           ats_boards.COLLECTOR)
            path = urlparse(signal.source_url).path
            self.assertEqual(len(path.strip("/").split("/")), 1, signal.source_url)
            text = f"{signal.headline} {signal.summary}"
            for job in _fixture("ats_ashby_netgear.json")["jobs"][:10]:
                self.assertNotIn(job["title"], text)


class PostedPayIsBaseAnnualPayOrNothing(_NewerBoards):
    def test_greenhouse_pay_ranges_are_read_from_the_transparency_field(self):
        """203 of the 286 boards on the watchlist are Greenhouse, and until now
        every one of them contributed zero pay evidence."""
        self.assertIn("pay_transparency=true", ats_boards.API_URLS["greenhouse"])
        snap = self.snap("dropbox")
        self.assertGreaterEqual(snap["salary"]["listed"], 10)

    def test_an_on_target_earnings_range_is_never_published_as_a_salary(self):
        """Airtable prices its sales roles with an OTE range and its other
        roles with a base range, in the same field, told apart only by the
        title the employer typed. An OTE counted as salary inflates the band by
        whatever the commission plan is worth."""
        for title in ("For work locations in Austin, the on-target earning "
                      "range for this role is:",
                      "Annual OTE Salary", "Total Targeted Cash",
                      "Total Compensation Range", "Hourly Pay Range",
                      "Monthly Salary Range"):
            self.assertFalse(ats_boards._is_base_pay_title(title), title)
        # 17 of Airtable's postings carry a range; only the base-salary ones
        # are counted.
        snap = self.snap("airtable")
        self.assertEqual(snap["total"], 17)
        self.assertLess(snap["salary"]["listed"], 17)

    def test_an_exclusion_clause_is_the_employer_confirming_it_is_base_pay(self):
        """"Annual base salary range (excluding equity and bonus)" is a base
        band. A naive bonus/equity keyword list threw away 161 real postings
        for naming what the figure does NOT include."""
        for title in ("Annual base salary range (excluding equity and bonus):",
                      "The US base salary range for this position (this does "
                      "not include bonus, equity and benefits)",
                      "At the Trade Desk, Base Salary is one part of our "
                      "competitive total compensation and benefits package"):
            self.assertTrue(ats_boards._is_base_pay_title(title), title)

    def test_a_range_with_no_currency_stores_nothing_rather_than_a_guess(self):
        self.assertIsNone(ats_boards._greenhouse_salary({"pay_input_ranges": [
            {"min_cents": 12000000, "max_cents": 16000000, "title": "Pay Range"}]}))
        self.assertIsNone(ats_boards._greenhouse_salary({"pay_input_ranges": [
            {"min_cents": 12000000, "max_cents": 16000000,
             "currency_type": "", "title": "Pay Range"}]}))
        # "Competitive salary" is not a range: there is no field at all.
        self.assertIsNone(ats_boards._greenhouse_salary({"pay_input_ranges": []}))
        self.assertIsNone(ats_boards._greenhouse_salary({}))

    def test_an_hourly_band_is_unknown_and_is_never_scaled_into_a_year(self):
        """Greenhouse's field carries NO interval, so a $28-$45 hourly band and
        a $28,000-$45,000 annual band are the same two numbers to a parser. The
        magnitude floor is the only honest way to tell them apart, and the
        answer for the hourly one is nothing, not 2,080 times something."""
        hourly = {"pay_input_ranges": [
            {"min_cents": 2800, "max_cents": 4500, "currency_type": "USD",
             "title": "Pay Range"}]}
        self.assertIsNone(ats_boards._greenhouse_salary(hourly))
        annual = {"pay_input_ranges": [
            {"min_cents": 2800000, "max_cents": 4500000, "currency_type": "USD",
             "title": "Pay Range"}]}
        self.assertEqual(ats_boards._greenhouse_salary(annual), (28000, 45000, "USD"))

    def test_currencies_are_bucketed_and_never_pooled_into_one_median(self):
        """Dropbox prices in four currencies on one board. A median across them
        would be an unstated exchange rate inside a published pay figure."""
        snap = self.snap("dropbox")
        self.assertEqual(snap["salary"]["currency"], "USD")
        self.assertTrue(snap["salary"]["other_currencies"])
        listed = snap["salary"]["listed"]
        priced = sum(1 for p in self.postings(self.entry("dropbox")) if p["salary"])
        self.assertLess(listed, priced,
                        "the dominant-currency band swallowed the other buckets")

    def test_a_non_usd_board_gets_its_own_band_rather_than_no_band(self):
        """US-first does not mean US-only: the pay-transparency laws that put
        these ranges on the page are US-state, UK and EU, and a sterling board
        is readable the moment nothing is converted."""
        postings = [{"place": "city:London", "function": None, "mode": None,
                     "salary": (90000, 110000, "GBP")} for _ in range(6)]
        snap = ats_boards.snapshot(postings)
        self.assertEqual(snap["salary"]["currency"], "GBP")
        item = ats_boards._pay_item({"ats": "greenhouse", "slug": "x",
                                     "company": "Example Ltd"},
                                    snap, None, "2026-08-14")
        self.assertIn("£90,000", item["headline"])
        self.assertIn("pounds sterling", item["raw_text"])
        self.assertNotIn("$", item["raw_text"])

    def test_a_band_only_republishes_when_the_currency_matches(self):
        """A board that priced in USD last week and GBP this week has not moved
        its bands by the ratio between two currencies."""
        usd = ats_boards.snapshot([{"place": "", "function": None, "mode": None,
                                    "salary": (100000, 120000, "USD")}
                                   for _ in range(6)])
        gbp = ats_boards.snapshot([{"place": "", "function": None, "mode": None,
                                    "salary": (100000, 120000, "GBP")}
                                   for _ in range(6)])
        entry = {"ats": "greenhouse", "slug": "x", "company": "Example"}
        self.assertIsNone(ats_boards._pay_item(entry, usd, usd, "2026-08-14"))
        self.assertIsNotNone(ats_boards._pay_item(entry, gbp, usd, "2026-08-14"))


class TheCorpusIsNotInflated(_NewerBoards):
    def test_one_run_publishes_at_most_one_row_per_employer_per_pillar(self):
        """A job board is 25,752 postings a day. The whole design rests on a
        row being about an EMPLOYER, so no run may ever emit two rows of one
        kind for one board."""
        items = self.items([e["slug"] for e in self.entries])
        seen = collections.Counter((i["company"], i["kind"]) for i in items)
        self.assertTrue(all(n == 1 for n in seen.values()), seen)

    def test_a_steady_board_publishes_once_and_then_never_again(self):
        """The row that would inflate the corpus is the periodic restatement.
        Ten more readings of an unchanged board must add nothing."""
        state = {"version": 1, "boards": {}}
        first = self.items(["netgear"], state=state, today="2026-08-14")
        self.assertTrue([i for i in first if i["kind"] == "work_mode"])
        later = []
        for day in range(15, 25):
            later += self.items(["netgear"], state=state, today=f"2026-08-{day}")
        self.assertEqual([i for i in later if i["kind"] == "work_mode"], [])

    def test_two_change_rows_for_one_employer_are_not_near_identical(self):
        """`dedupe.fuzzy_duplicate` collapses same-employer, same-pillar rows
        whose headlines overlap 85% across 400 days. A templated row would
        either be suppressed or, worse, suppress the next real movement. These
        are the two headlines the change path actually produces."""
        first = ("Netgear moved from 75% to 23% of open roles advertised as onsite")
        second = ("Netgear moved from 23% to 44% of open roles advertised as onsite")
        self.assertLess(dedupe._token_overlap(first, second), 0.85)


class TheDirectionIsARule(unittest.TestCase):
    """Volume over time is only worth publishing if the direction attached to it
    can be argued with. Each of these is the rule, not an example of it."""

    def _series(self, *pairs):
        return [{"date": d, "total": t} for d, t in pairs]

    def test_too_few_readings_says_so_rather_than_guessing(self):
        verdict = ats_boards.trajectory(
            self._series(("2026-07-01", 40), ("2026-07-02", 61)),
            today="2026-07-02")
        self.assertEqual(verdict["direction"], "unknown")
        self.assertIn("too few", verdict["basis"])

    def test_a_short_span_of_many_readings_is_still_unknown(self):
        verdict = ats_boards.trajectory(
            self._series(("2026-07-01", 40), ("2026-07-02", 55),
                         ("2026-07-03", 70), ("2026-07-04", 90)),
            today="2026-07-04")
        self.assertEqual(verdict["direction"], "unknown")

    def test_a_sustained_rise_is_evidence_of_hiring(self):
        verdict = ats_boards.trajectory(
            self._series(("2026-07-01", 40), ("2026-07-08", 46),
                         ("2026-07-15", 52), ("2026-07-25", 60)),
            today="2026-07-25")
        self.assertEqual(verdict["direction"], "rising")
        self.assertIn("+20", verdict["basis"])

    def test_a_small_move_on_a_big_board_is_flat(self):
        verdict = ats_boards.trajectory(
            self._series(("2026-07-01", 800), ("2026-07-08", 803),
                         ("2026-07-15", 806), ("2026-07-25", 807)),
            today="2026-07-25")
        self.assertEqual(verdict["direction"], "flat")

    def test_a_fall_is_reported_and_explicitly_not_called_a_cut(self):
        verdict = ats_boards.trajectory(
            self._series(("2026-07-01", 60), ("2026-07-08", 52),
                         ("2026-07-15", 46), ("2026-07-25", 40)),
            today="2026-07-25")
        self.assertEqual(verdict["direction"], "falling")
        self.assertIn("not evidence of job cuts", verdict["basis"])
        # And it never becomes a signal: displacement is the sibling tracker's
        # word and this collector may not reach for it.
        self.assertNotIn(verdict["direction"], ("displacement", "cuts"))

    def test_the_only_four_answers_are_the_ones_the_page_can_render(self):
        allowed = {"rising", "falling", "flat", "unknown"}
        for series in ([], self._series(("2026-07-01", 1)),
                       self._series(("2026-07-01", 10), ("2026-07-20", 90),
                                    ("2026-07-21", 91), ("2026-07-22", 92))):
            self.assertIn(ats_boards.trajectory(series, today="2026-07-25")["direction"],
                          allowed)


class TheSeriesReachesAProfilePage(_Boards):
    """The archive is only useful if a profile page can draw it without going
    back to Greenhouse or Lever, which publish no history at all."""

    def _state_after_two_days(self):
        state = {"version": 1, "boards": {}}
        for day in ("2026-07-01", "2026-07-20"):
            ats_boards.collect(watchlist=self.entries, state=state,
                               today=day, persist=False)
        return state

    def test_every_board_records_the_key_and_the_url_that_back_it(self):
        state = self._state_after_two_days()
        for board_id, record in state["boards"].items():
            self.assertTrue(record["company_key"], board_id)
            self.assertTrue(record["url"].startswith("https://"), board_id)
            self.assertTrue(record["source_name"], board_id)

    def test_the_export_is_keyed_by_company_and_carries_its_source(self):
        import build_board_series

        payload = build_board_series.build(self._state_after_two_days(),
                                           today="2026-07-20")
        self.assertTrue(payload["boards"])
        for key, boards in payload["boards"].items():
            self.assertEqual(key, key.strip())
            for board in boards:
                self.assertTrue(board["source_url"].startswith("https://"))
                self.assertEqual(board["series"][0][0], "2026-07-01")
                self.assertEqual(board["latest"]["date"], "2026-07-20")
                self.assertIn(board["trajectory"]["direction"],
                              {"rising", "falling", "flat", "unknown"})
        # The rule travels with the numbers, so the page never has to restate
        # it from memory.
        self.assertIn("we cannot tell", payload["rule"])

    def test_the_export_drops_nothing_the_endpoint_would_reject(self):
        """Mirrors the validation in includes/board_series.php: a board with no
        source URL, no readings or an unknown direction is refused there."""
        import build_board_series

        payload = build_board_series.build(self._state_after_two_days(),
                                           today="2026-07-20")
        self.assertTrue(payload["as_of"] and payload["rule"])
        for boards in payload["boards"].values():
            for board in boards:
                self.assertTrue(board["source_url"])
                self.assertTrue(board["series"])


if __name__ == "__main__":
    unittest.main()
