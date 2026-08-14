"""A city total says what makes a row belong to a city.

WHY THIS FILE EXISTS.

`tit_city_expr()` is `COALESCE(city, hq_city)`. `city` is a STATED job location
and `hq_city` is where the employer is based, and the ribbon unions them.
Measured on the live table on 2026-08-13:

    London          2,296 = 22 stated + 2,274 employer based there
    New York          553 = 444 stated + 109
    San Francisco     220 = 167 stated +  53
    Manchester        179 =   5 stated + 174
    Prague             26 =   0 stated +  26

A reader takes "London 2,296" as 2,296 events in London. It is not that, and
outside the United States it is not close.

THE COUNT IS NOT THE THING TO CHANGE, and this file pins that in both
directions. The pill under the number resolves as
`city = %s OR (city IS NULL AND hq_city = %s)`, so a strip counting only stated
cities would print a number its own click contradicts. That is the defect the
strip already carries a comment about, arriving from the other side. The union
is also what `/aggregate`'s by_city applies and what the sibling exposes as
`country_basis=any`.

So this follows the country ribbon's own fix of 2026-08-13 rather than
inventing a second pattern: when a surface's words and its number disagree,
change whichever of the two is wrong. There the caption said "Updates Held"
while the query filtered to non-routine, so the QUERY was wrong. Here the query
is the documented union that every sibling surface applies, so the WORDS were.

WHAT IS PINNED.

  1. The basis line names both halves of the union, in visible prose above the
     rows, alongside what was already there.
  2. The instance is COMPUTED, never typed. `tit_place_caveat` established that
     rule for the same reason: a figure written into copy goes stale quietly,
     and this repo has already published a coverage percentage for sixteen days
     after its denominator moved.
  3. It disappears when the leading city is mostly stated, because there the
     sentence would be noise, and it prints nothing at all rather than a wrong
     number when the cached bundle predates the `stated` column.
  4. Every other surface stating the same basis states it the same way. Today
     that is the Money Raised by City card, which groups by the same expression.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SHORTCODES_PHP = (
    ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / "includes" / "shortcodes.php"
)


def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def code():
    return strip_comments(SHORTCODES_PHP.read_text(encoding="utf-8"))


def basis_paragraph(src):
    marker = 'class="tit-places-note tit-places-basis"'
    assert marker in src, (
        "the place ribbon prints no basis line, so there is nothing to check "
        "the wording of. See tests/test_place_ribbon_names_its_unit.py."
    )
    start = src.index(marker)
    return src[start:src.index("</p>", start)]


class TheBasisNamesBothHalvesOfTheUnion(unittest.TestCase):
    def test_the_stated_job_location_is_named(self):
        flat = re.sub(r"\s+", " ", basis_paragraph(code())).lower()
        self.assertIn("states that job location", flat,
                      "the basis line does not say that a stated job location "
                      "puts a row in a city. Half the union is invisible.")

    def test_the_employers_base_is_named(self):
        flat = re.sub(r"\s+", " ", basis_paragraph(code())).lower()
        self.assertIn("employer is based there", flat,
                      "the basis line does not say that an employer's own "
                      "base puts a row in a city. That is 2,274 of London's "
                      "2,296 and it is the half a reader cannot guess.")

    def test_the_line_still_says_what_it_said_before(self):
        """Adding a clause must not quietly drop the ones already tested."""
        flat = re.sub(r"\s+", " ", basis_paragraph(code())).lower()
        self.assertIn("not a ranking of the market", flat)
        self.assertIn("registry", flat)

    def test_the_copy_carries_no_dash_punctuation(self):
        block = basis_paragraph(code())
        for ch, name in (("—", "em dash"), ("–", "en dash")):
            self.assertNotIn(ch, block,
                             f"the place ribbon copy contains an {name}")


class TheInstanceIsComputed(unittest.TestCase):
    def test_no_city_figure_is_typed_into_the_copy(self):
        """A number in the prose is a number that goes stale silently."""
        block = basis_paragraph(code())
        self.assertNotRegex(
            block, r"\d[\d,]{2,}",
            "a figure is written into the place ribbon copy. tit_place_caveat "
            "computes its numbers for exactly this reason: the denominator "
            "under a hand-written percentage moved once and the figure was "
            "quoted for sixteen days afterwards.")

    def test_the_sentence_comes_from_the_helper(self):
        self.assertIn("tit_city_basis_note(", basis_paragraph(code()))

    def test_the_strip_query_carries_the_stated_count(self):
        src = code()
        self.assertIn("stated", src[src.index("$facts['cities']"):
                                     src.index("$facts['cities']") + 900],
                      "the cities query does not compute a stated count, so "
                      "the note has nothing to say the split from.")


class TheHelperBehaves(unittest.TestCase):
    """Driven in a real PHP, because the branches are the whole safety of it."""

    def setUp(self):
        if not shutil.which("php"):
            self.skipTest("no php on PATH, so this cannot be measured. "
                          "Absence of a signal is not a pass.")

    def _run(self, rows_php):
        harness = (
            "<?php\n"
            "function esc_html($s) { return $s; }\n"
            "function number_format_i18n($n) { return number_format($n); }\n"
            "function _n($a, $b, $n, $d = '') { return $n === 1 ? $a : $b; }\n"
            "$src = file_get_contents(getenv('TIT_SHORTCODES'));\n"
            "preg_match('/function tit_city_basis_note\\(.*?\\n}\\n/s', $src, $m);\n"
            "eval($m[0]);\n"
            f"echo tit_city_basis_note({rows_php});\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False) as fh:
            fh.write(harness)
            path = fh.name
        out = subprocess.run(
            ["php", path], capture_output=True, text=True,
            env={"TIT_SHORTCODES": str(SHORTCODES_PHP), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_a_mostly_based_city_gets_the_sentence(self):
        got = self._run("array(array('k' => 'London', 'n' => 2296, 'stated' => 22))")
        self.assertIn("London reads 2,296", got)
        self.assertIn("22 state London as the job location", got)
        self.assertIn("2,274 are employers based there", got)

    def test_a_mostly_stated_city_gets_nothing(self):
        """New York is 444 of 553 stated. The sentence would be noise."""
        got = self._run("array(array('k' => 'New York', 'n' => 553, 'stated' => 444))")
        self.assertEqual(got.strip(), "")

    def test_a_bundle_with_no_stated_column_prints_nothing(self):
        """An older cached bundle must not read as 'zero state it'."""
        got = self._run("array(array('k' => 'London', 'n' => 2296))")
        self.assertEqual(got.strip(), "")

    def test_an_empty_ribbon_prints_nothing(self):
        self.assertEqual(self._run("array()").strip(), "")


class EverySurfaceStatingTheBasisStatesItTheSameWay(unittest.TestCase):
    def test_the_money_by_city_card_names_the_union_too(self):
        """It groups by tit_city_expr() as well, so it counts the same union."""
        src = code()
        start = src.index("'city', 'Money Raised by City'")
        sub = src[start:start + 400]
        self.assertIn("the employer is based in", sub,
                      "Money Raised by City groups by COALESCE(city, hq_city) "
                      "and its subtitle names only the dimension. Two surfaces "
                      "reading one column must not describe it two ways.")


if __name__ == "__main__":
    unittest.main()
