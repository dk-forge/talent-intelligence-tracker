"""Live open-role counts per employer (owner-approved 2026-10-12).

The count was already measured every day by the ats_boards collector and
written to data/ats_board_state.json; what was missing was getting it to the
page. The shipped seed froze on 2026-07-29 with 37 employers while the
collector was counting ~280 boards, because nothing ever ran
`build_board_series.py --publish`.

Three promises, each a test:

1. A count we did not read recently is NOT shown as today's count. A board
   whose last good reading is older than STALE_DAYS is marked not current and
   the page drops the headline number, saying when it last read the board.
2. Every count travels with the board URL it was counted from and the date it
   was read ("last checked").
3. The daily ATS collection publishes the series itself, best-effort, so the
   page does not freeze between plugin deploys. A failed publish is a warning,
   never a red collection: the state file is already committed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import build_board_series

ROOT = Path(__file__).parent.parent
PANEL = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / "includes"
         / "board_series.php").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "collect-structured.yml").read_text()


def _state(last_day: str) -> dict:
    return {"version": 1, "boards": {"greenhouse:acme": {
        "ats": "greenhouse", "slug": "acme", "company": "Acme",
        "company_key": "acme", "source_name": "Greenhouse job board",
        "url": "https://boards.greenhouse.io/acme",
        "history": [{"date": "2026-09-20", "total": 30},
                    {"date": last_day, "total": 31}],
    }}}


class StaleCountsAreNotTodaysCount(unittest.TestCase):
    def _board(self, last_day: str, today: str) -> dict:
        payload = build_board_series.build(_state(last_day), today=today,
                                           watchlist=[])
        return payload["boards"]["acme"][0]

    def test_a_reading_from_today_is_current(self):
        board = self._board("2026-10-12", "2026-10-12")
        self.assertTrue(board["current"])
        self.assertEqual(board["last_checked"], "2026-10-12")

    def test_a_reading_older_than_the_window_is_not_current(self):
        old = "2026-10-01"
        self.assertGreater(11, build_board_series.STALE_DAYS)
        board = self._board(old, "2026-10-12")
        self.assertFalse(board["current"])
        # The date is still carried: "no reading since D" is an answer.
        self.assertEqual(board["last_checked"], old)

    def test_the_window_is_a_week_or_less(self):
        self.assertLessEqual(build_board_series.STALE_DAYS, 7)


class ThePanelHonoursCurrency(unittest.TestCase):
    def test_a_stale_board_gets_no_headline_number(self):
        self.assertIn("empty($board['current'])", PANEL)
        self.assertIn("No reading since", PANEL)

    def test_the_last_checked_date_is_printed(self):
        self.assertIn("Last checked", PANEL)
        self.assertIn("$board['last_checked']", PANEL)

    def test_a_seed_without_the_field_still_renders_its_count(self):
        # The July seed predates `current`; it must not lose every number.
        self.assertIn("array_key_exists('current', $board)", PANEL)


class TheCollectionPublishesTheSeries(unittest.TestCase):
    def _step(self) -> str:
        m = re.search(r"- name: Publish open-role counts\n(.*?)(?=\n      - |\n  [a-z])",
                      WORKFLOW, re.S)
        self.assertIsNotNone(m, "collect-structured.yml has no publish step")
        return m.group(1)

    def test_it_runs_only_for_a_real_ats_collection(self):
        step = self._step()
        self.assertIn("!inputs.dry_run", step)
        self.assertIn("steps.pick.outputs.value == 'ats_boards'", step)

    def test_it_carries_the_key_and_never_reddens_the_run(self):
        step = self._step()
        self.assertIn("WP_API_KEY: ${{ secrets.WP_API_KEY }}", step)
        self.assertIn("python build_board_series.py --publish", step)
        self.assertIn("::warning::", step)


if __name__ == "__main__":
    unittest.main()
