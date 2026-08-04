"""A region badge must equal the count that region's own tab returns.

The defect this exists to prevent, observed live on 2026-08-04 at 1.71.0:

    World badge          23,991
    World tab returned   25,479   (/query?detail=notable, no country parameter)
    hero, same page      25,479 all time

`tit_regions` gave World `array_sum($counts)`, and `$counts` is grouped under
`COALESCE(country, hq_country) IS NOT NULL`, so it cannot see a row we hold no
geography for. The World tab sends no country parameter at all, so it returns
those rows. 1,488 of them, contradicting the hero three inches above, on the
view that loads by default.

Every other region reconciled and still must: Americas' badge was 7,765 and
`/query?detail=notable&country=<Americas list>` returned 7,765.

Nothing here asserts on the shape of the PHP source of the function under test.
It EXECUTES `tit_regions` out of shortcodes.php through the `php` binary, the
same approach tests/test_output_encoding_guards.py takes, because a test that
matched a comment or a variable name would have passed on the defective code.
The one text assertion in this file is about the CALL SITE, which cannot be
executed without WordPress, and it says so where it is made.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import unittest
from shutil import which

from phpsource import balanced_block

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHORTCODES = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
              / "includes" / "shortcodes.php")


def php():
    for path in ("/opt/homebrew/bin/php", "/usr/bin/php", "/usr/local/bin/php"):
        if os.path.exists(path):
            return path
    return which("php")


def _region_function() -> str:
    """The real `tit_regions`, lifted whole from the plugin.

    It is a pure function of its arguments, so it runs standalone. The body is
    delimited by its own braces rather than by whatever happens to be defined
    after it, so unrelated edits to the file cannot silently shrink what is
    being tested.
    """
    src = SHORTCODES.read_text()
    assert "function tit_regions" in src, (
        "tit_regions is gone from shortcodes.php; the region strip moved and "
        "this test needs to point at wherever it lives now"
    )
    head = src[src.index("function tit_regions"):]
    body = balanced_block(head, head[:head.index("{") + 1], what="tit_regions body")
    return head[:head.index("{")] + "{" + body + "}"


class RegionBadgesEqualTheirOwnFilter(unittest.TestCase):
    def setUp(self):
        if not php():
            self.skipTest("php not installed")
        self.fn = _region_function()

    def _regions(self, counts, view_total):
        shim = self.fn + """
$in = json_decode(file_get_contents('php://stdin'), true);
echo json_encode(tit_regions($in['counts'], $in['total']));
"""
        proc = subprocess.run(
            [php(), "-r", shim],
            input=json.dumps({"counts": counts, "total": view_total}),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    # A country map that reaches five of the six real regions and deliberately
    # leaves one empty, plus a view total that is LARGER than its sum. That gap
    # is the live condition: rows counted by the view and absent from the
    # country map because they carry no country.
    COUNTS = {"US": 4000, "CA": 300, "BR": 65, "GB": 4800, "DE": 900, "LV": 3,
              "IN": 2000, "JP": 140, "AE": 120, "ZA": 60}
    UNPLACED = 1488

    def _fixture(self):
        placed = sum(self.COUNTS.values())
        return self.COUNTS, placed + self.UNPLACED, placed

    def test_world_badge_is_the_view_total_not_the_sum_of_the_countries(self):
        """The defect. World applies no country filter, so its badge is every
        row in the view, including the ones with no country."""
        counts, view_total, placed = self._fixture()
        world = [r for r in self._regions(counts, view_total) if r["codes"] == ""]
        self.assertEqual(len(world), 1, "there must be exactly one unfiltered tab")
        self.assertEqual(
            world[0]["n"], view_total,
            "the World badge does not equal the count the World tab returns: "
            f"badge {world[0]['n']}, tab {view_total}. It is {placed}, the sum "
            "over country codes, which skips the rows holding no country.")

    def test_world_never_reports_fewer_rows_than_the_regions_inside_it(self):
        """Stated as an ordering as well, because the failure a reader notices
        is a total smaller than its own parts."""
        counts, view_total, _ = self._fixture()
        regions = self._regions(counts, view_total)
        world = next(r for r in regions if r["codes"] == "")
        inside = sum(r["n"] for r in regions if r["codes"] != "")
        self.assertGreaterEqual(world["n"], inside)

    def test_every_other_badge_is_the_sum_over_its_own_code_list(self):
        """The control: a region tab sends its code list to /query, so its badge
        has to be the count of the rows carrying those codes, and the fix must
        not have moved any of them."""
        counts, view_total, _ = self._fixture()
        for region in self._regions(counts, view_total):
            if region["codes"] == "":
                continue
            expected = sum(counts.get(c, 0) for c in region["codes"].split(","))
            with self.subTest(region=region["name"]):
                self.assertEqual(region["n"], expected,
                                 f"{region['name']} badge disagrees with its own filter")

    def test_a_region_holding_nothing_is_dropped_rather_than_drawn_at_zero(self):
        counts, view_total, _ = self._fixture()
        named = {r["name"] for r in self._regions(counts, view_total)}
        self.assertIn("World", named)
        self.assertNotIn("Oceania", named,
                         "Oceania has no rows in this fixture and must not be drawn")

    def test_world_survives_a_view_where_no_row_has_a_country(self):
        """The extreme of the same bug: an empty country map used to make the
        only way back read zero."""
        world = next(r for r in self._regions({}, 900) if r["codes"] == "")
        self.assertEqual(world["n"], 900)


class TheCallSitePassesTheViewTotal(unittest.TestCase):
    """A source-level check, plainly.

    The renderer around the call cannot run without WordPress, so this reads
    text. It is still a real check: `tit_regions` is correct only if it is
    HANDED the view total, and a caller that passed the country map's sum back
    in would restore the exact defect with a correct function underneath. There
    is one call site, and this asserts it forwards `$total`, the variable the
    hero and the detail control print.
    """

    def test_the_only_caller_hands_it_the_view_total(self):
        src = SHORTCODES.read_text()
        calls = [line.strip() for line in src.splitlines()
                 if "tit_regions(" in line and "function tit_regions" not in line]
        self.assertEqual(len(calls), 1, f"expected one call site, found: {calls}")
        self.assertIn("$total", calls[0],
                      "the region strip must be handed the view's own total; "
                      "without it World falls back to a country sum that omits "
                      "every row with no country")
        self.assertNotIn("array_sum", calls[0])


if __name__ == "__main__":
    unittest.main()
