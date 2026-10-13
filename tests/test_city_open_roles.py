""""Who's hiring in [city]" (owner-approved 2026-10-12, item 2).

The city pages already existed (places.php) with their SEO furniture. This adds
the one thing a job hunter or recruiter asks first, from a measurement we
already take every day: how many roles employers we track have listed in that
city on their own job boards, and which employers.

Honesty rules, each a test:

* Only CURRENT boards count. A board with no good reading in a week is not
  "open roles today" (see test_open_roles_live.py).
* The figure is a sum over the boards we track, never "all jobs in London".
  The page says whose boards and on what date.
* Every employer line links to the board it was counted from.
* Layoffs in the same metro are NOT shown: the sibling's public API has no city
  field (rows carry country and state only), and a country figure on a city
  page would be a number about somewhere else.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import build_board_series

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
PANEL = (PLUGIN / "includes" / "board_series.php").read_text()
PLACES = (PLUGIN / "includes" / "places.php").read_text()


def _board(slug, company, last_day, places):
    return {
        "ats": "greenhouse", "slug": slug, "company": company,
        "company_key": company.lower(), "source_name": "Greenhouse job board",
        "url": f"https://boards.greenhouse.io/{slug}",
        "history": [{"date": "2026-09-20", "total": 5},
                    {"date": last_day, "total": sum(places.values())}],
        "last": {"date": last_day, "total": sum(places.values()),
                 "places": places},
    }


STATE = {"version": 1, "boards": {
    "greenhouse:acme": _board("acme", "Acme", "2026-10-12",
                              {"city:London": 12, "city:Dublin": 3}),
    "greenhouse:beta": _board("beta", "Beta", "2026-10-12",
                              {"city:London": 30}),
    # Stale: last good reading eleven days ago. Must not count.
    "greenhouse:gone": _board("gone", "Gone", "2026-10-01",
                              {"city:London": 99}),
}}


class TheCityRollup(unittest.TestCase):
    def setUp(self):
        self.places = build_board_series.build(
            STATE, today="2026-10-12", watchlist=[])["places"]

    def test_it_sums_current_boards_only(self):
        london = self.places["London"]
        self.assertEqual(london["open_roles"], 42)
        self.assertEqual(london["employer_count"], 2)
        self.assertEqual(london["as_of"], "2026-10-12")

    def test_employers_are_ranked_and_carry_their_board(self):
        rows = self.places["London"]["employers"]
        self.assertEqual([r["company"] for r in rows], ["Beta", "Acme"])
        self.assertEqual(rows[0]["open_roles"], 30)
        self.assertTrue(all(r["source_url"].startswith("https://") for r in rows))
        self.assertEqual(rows[0]["company_key"], "beta")

    def test_a_city_held_only_by_a_stale_board_is_absent(self):
        only_stale = {"version": 1, "boards": {
            "greenhouse:gone": _board("gone", "Gone", "2026-10-01",
                                      {"city:Lisbon": 9})}}
        places = build_board_series.build(only_stale, today="2026-10-12",
                                          watchlist=[])["places"]
        self.assertNotIn("Lisbon", places)

    def test_the_employer_list_is_bounded(self):
        self.assertLessEqual(build_board_series.CITY_TOP_EMPLOYERS, 25)


class TheCityPage(unittest.TestCase):
    def test_the_city_page_calls_the_panel_and_survives_a_partial_deploy(self):
        self.assertIn("function_exists('tit_board_series_city_panel')", PLACES)
        self.assertIn("tit_board_series_city_panel($cell['key'])", PLACES)
        self.assertIn("!empty($spec['open_roles'])", PLACES)

    def test_the_panel_states_its_basis(self):
        body = re.search(r"function tit_board_series_city_panel.*?\n}", PANEL, re.S).group(0)
        self.assertIn("employers we track", body)
        self.assertIn("not every job in", body)
        self.assertIn("esc_url($employer['source_url']", body)
        self.assertIn("rel=\"nofollow noopener\"", body)

    def test_a_city_heading_answers_who_is_hiring(self):
        # Data in tit_place_kinds(), not a branch: the three page types differ
        # only in data (test_place_pages.py).
        self.assertIn("'heading'    => \"Who's Hiring In %s", PLACES)
        self.assertIn("'title'      => \"Who's hiring in %s", PLACES)
        self.assertIn("'open_roles' => true", PLACES)
        title = re.search(r"function tit_place_title\(.*?\n}", PLACES, re.S).group(0)
        self.assertIn("['title']", title)
        head = re.search(r"function tit_place_head\(\).*?\n}", PLACES, re.S).group(0)
        self.assertIn("open roles", head)

    def test_the_other_headings_are_unchanged(self):
        self.assertIn("'%s: Hiring, Funding And Leadership Signals'", PLACES)


if __name__ == "__main__":
    unittest.main()
