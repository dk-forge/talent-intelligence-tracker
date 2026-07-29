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

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from collectors import ats_boards
from pipeline import validate

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
        self.assertTrue(any(p.startswith("country:") for p in places))

    def test_remote_is_a_place_and_not_a_guess_at_a_city(self):
        self.assertEqual(ats_boards.place_key("Remote (Sweden)"), "country:SE")
        self.assertEqual(ats_boards.place_key("Remote"), "remote:")
        self.assertEqual(ats_boards.place_key("Anywhere on Mars"), "")

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

    def test_only_annual_usd_salary_components_are_averaged(self):
        """Mixing currencies into one median produces a number that describes
        nothing, and converting them would be a guessed rate on a pay figure."""
        self.assertIsNone(ats_boards._salary({"compensation": {"summaryComponents": [
            {"compensationType": "Salary", "interval": "1 YEAR",
             "currencyCode": "GBP", "minValue": 80000, "maxValue": 100000}]}}))
        self.assertIsNone(ats_boards._salary({"compensation": {"summaryComponents": [
            {"compensationType": "EquityPercentage", "interval": "NONE",
             "currencyCode": None, "minValue": None, "maxValue": None}]}}))
        self.assertEqual(ats_boards._salary({"compensation": {"summaryComponents": [
            {"compensationType": "Salary", "interval": "1 YEAR",
             "currencyCode": "USD", "minValue": 100, "maxValue": 200}]}}), (100, 200))

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

    def test_only_annual_usd_lever_bands_are_read(self):
        self.assertEqual(ats_boards._lever_salary({"salaryRange": {
            "interval": "per-year-salary", "currency": "USD",
            "min": 150000, "max": 180000}}), (150000, 180000))
        self.assertIsNone(ats_boards._lever_salary({"salaryRange": {
            "interval": "per-year-salary", "currency": "GBP",
            "min": 90000, "max": 110000}}))
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
