"""The public search box is not a substring match.

MEASURED 2026-08-18, live, before the fix:

    /talent/v1/query?q=EY   -> 13,934 of 30,986 rows
    /layoffs/v1/query?q=EY  ->  1,968 of 65,441 rows   (the sibling)

Two letters with no word boundary, so "money", "survey", "Monterrey", "key"
and "attorney" all answered, and the top three hits for a reader searching a
Big Four firm were a Brazilian space startup, a Bolivian statistics agency and
a debt-collection company. The same endpoint was correct on `Workday` (4),
`OpenRouter` (1), `Stripe` (4) and `Expedia` (2), which is exactly why it
survived every spot check: it only bites on the short all-caps names this
domain is made of -- EY, PwC, IBM, SAP, BT, GE, HP, KPMG, UBS, ING.

It is the same class the sibling's INGEST gate was bitten by when `layoff`
matched `playoff`, and it takes the same answer: assert a word boundary.

THE ROW-LEVEL PROOF IS IN tests/php/search_boundary.php, which runs the REAL
tit_build_where over real SQL and asserts which companies come back. This
module holds the properties that are about the SHAPE of the fix -- the ones a
future change could satisfy row-by-row while still being the wrong fix:

1. NO MINIMUM QUERY LENGTH. `if (strlen($q) < 3) return nothing` makes every
   count above look fixed and silently deletes every two-letter employer. An
   empty result that looks honest is worse than a noisy one that obviously is
   not.
2. NO GLOBAL CASE-SENSITIVITY. `EY` versus `ey` inside a word is most of the
   signal, but a reader typing `workday` must still find `Workday`. We match
   the token, not the case of the string.
3. NO BOUNDARY ON SCRIPTS THAT DO NOT USE ONE. Japanese and Chinese are
   written without spaces, so `\\b退任\\b` matches nothing; Korean has the
   spaces and glues particles on. The corpus really holds those rows and one of
   the owner's four probe items was reachable only through a Korean headline.
4. NO ASSUMED SQL DIALECT. MySQL 8 runs ICU and takes `\\b` while rejecting the
   POSIX `[[:<:]]` it used to require; 5.7 takes `[[:<:]]` and reads `\\b` as a
   literal `b`, which matches nothing. The syntax is probed on the server with
   BOTH a positive and a negative, and neither passing falls back to substring.
5. EVERY FREE-TEXT PATH GOES THROUGH THE ONE CLAUSE, so a filter added later
   cannot reintroduce the defect on its own.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
API = (PLUGIN / "includes" / "api.php").read_text()
HARNESS = ROOT / "tests" / "php" / "search_boundary.php"
PHP = shutil.which("php")


@pytest.mark.skipif(PHP is None, reason="php not installed; CI runs the harness")
def test_the_search_clause_returns_the_right_rows():
    """The row-level proof: real SQL, real rows, real CJK headlines."""
    out = subprocess.run([PHP, str(HARNESS)], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr or out.stdout
    assert "all checks passed" in out.stdout


def test_no_minimum_query_length_was_smuggled_in():
    """A length floor is the fix that looks like a fix. If one ever lands, the
    counts above go right and every two-letter employer goes missing."""
    where = API[API.index("function tit_build_where("):]
    where = where[:where.index("\nfunction ")]
    for pattern in (r"strlen\(\s*\$search", r"mb_strlen\(\s*\$search",
                    r"strlen\(\s*\$company", r"mb_strlen\(\s*\$company"):
        assert not re.search(pattern, where), f"a length gate appeared: {pattern}"


def test_the_search_is_not_made_case_sensitive():
    """BINARY, COLLATE ..._bin or REGEXP_LIKE(..., 'c') would make `workday`
    stop finding `Workday`, which trades one wrong answer for another."""
    for banned in ("BINARY ", "_bin", "REGEXP BINARY"):
        assert banned not in API.split("function tit_build_where(")[1].split("\nfunction ")[0], banned


def test_non_latin_terms_are_excluded_from_boundary_matching_by_rule():
    """Named as a positive rule over Latin, not as a list of scripts somebody
    has to remember to extend. A script nobody thought of keeps the old
    behaviour, which cannot regress."""
    body = API[API.index("function tit_boundary_pattern("):]
    body = body[:body.index("\n}")]
    assert r"\p{Latin}" in body


def test_the_dialect_is_probed_with_a_positive_and_a_negative():
    """A pattern that merely fails to error is not a pattern that works. The
    probe must prove BOTH that a standalone token matches and that the words
    which caused this defect do not, or MySQL 5.7 reading `\\b` as a literal
    `b` would be accepted and the search box would return nothing."""
    body = API[API.index("function tit_regexp_boundary_syntax("):]
    body = body[:body.index("\n}\n")]
    assert "'EY LLP'" in body and "'money survey key'" in body
    assert "$hit === '1'" in body and "$miss === '0'" in body
    # Neither dialect passing is a third state, and it must degrade rather than
    # send a pattern the engine will misread.
    assert "'none'" in body


def test_every_free_text_path_goes_through_the_one_clause():
    where = API[API.index("function tit_build_where("):]
    where = where[:where.index("\nfunction ")]
    for param in ("'q'", "'company'"):
        start = where.index(f"get_param({param})")
        window = where[start:start + 400]
        assert "tit_freetext_clause" in window, f"{param} is not boundary-matched"
        assert "LIKE %s" not in window.split("tit_freetext_clause")[0], \
            f"{param} still builds a raw LIKE"


def test_the_regex_runs_behind_the_like_and_not_instead_of_it():
    """Neither can use an index -- no B-tree serves a leading wildcard and this
    table has no FULLTEXT -- so the LIKE stays as the cheap first pass and the
    regex only runs on the rows it already admitted. Replacing it would make
    every keystroke a regex over the whole corpus."""
    body = API[API.index("function tit_freetext_clause("):]
    body = body[:body.index("\n}")]
    assert "LIKE %s" in body and "REGEXP %s" in body
    assert body.index("LIKE %s") < body.index("REGEXP %s")
    assert "' AND ('" in body or '" AND ("' in body or "AND (" in body


def test_the_table_still_has_no_fulltext_index_to_prefer():
    """The choice of REGEXP-behind-LIKE is only right while this holds. If a
    FULLTEXT index is ever added, MATCH ... AGAINST is the better answer and
    this test is the reminder to revisit."""
    db = (PLUGIN / "includes" / "db.php").read_text()
    assert "FULLTEXT" not in db.upper()
