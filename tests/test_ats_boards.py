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
        "smartrecruiters:Wise": "ats_smartrecruiters_wise.json",
    }
    entries = [
        {"ats": "greenhouse", "slug": "stripe", "company": "Stripe"},
        {"ats": "ashby", "slug": "ramp", "company": "Ramp"},
        {"ats": "smartrecruiters", "slug": "Wise", "company": "Wise"},
    ]

    def setUp(self):
        self._get = ats_boards._get

        def fake_get(url, **kwargs):
            for key, name in self.payloads.items():
                _ats, _, slug = key.partition(":")
                if f"/{slug}" in url or f"={slug}" in url:
                    return _fixture(name)
            raise AssertionError(f"unexpected fetch: {url}")

        ats_boards._get = fake_get

    def tearDown(self):
        ats_boards._get = self._get

    def postings(self, entry):
        return ats_boards.fetch_postings(entry)


class Parsing(_Boards):
    def test_all_three_ats_shapes_parse(self):
        for entry in self.entries:
            self.assertTrue(self.postings(entry), entry["slug"])

    def test_locations_resolve_to_the_curated_vocabulary(self):
        places = {p["place"] for p in self.postings(self.entries[0])}
        self.assertIn("city:San Francisco", places)
        self.assertIn("city:Singapore", places)
        # "Japan" is a country and no curated city, which is a country key and
        # not a discarded row.
        self.assertIn("country:JP", places)

    def test_a_worldwide_board_places_rows_outside_the_united_states(self):
        places = {p["place"] for p in self.postings(self.entries[2])}
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
        items = ats_boards.collect(watchlist=[self.entries[1]],
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
        ats_boards.collect(watchlist=[self.entries[1]], state=state,
                           today="2026-07-01", persist=False)
        again = ats_boards.collect(watchlist=[self.entries[1]], state=state,
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


if __name__ == "__main__":
    unittest.main()
