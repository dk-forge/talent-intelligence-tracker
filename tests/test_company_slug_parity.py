"""The offline checker's slug must be the slug the site actually serves.

`ops_status.py` decides which employers are unpublishable by recomputing their
profile URL in Python. It cannot run PHP, so `_profile_slug` is a hand-written
MIRROR of `tit_company_slug()` in the plugin -- and a mirror is worth exactly
its fidelity. Two ways it was wrong on 2026-09-01, both found by comparing it
to the real function rather than by reading it:

1. It folded with `unicodedata.normalize("NFKD")`. WordPress folds with
   `remove_accents()`, whose table also covers the ATOMIC letters NFKD leaves
   alone because they are not a base plus a combining mark: 'd', 'l', 'i',
   'o', 'ae', 'th'. So the checker printed `/company/giay-thuong-inh/` for a
   page the site publishes at `/company/giay-thuong-dinh/`, and
   `/company/gornik-eczna/` for `/company/gornik-leczna/`. Every URL it named
   in those rows was one nobody could visit.

2. NFKD also DECOMPOSES a Hangul syllable into conjoining jamo, so the
   romaniser added beside it had nothing left to match and every Korean name
   collapsed to its Latin fragment again -- the exact bug the romaniser exists
   to fix, reintroduced by the order of two lines.

Neither was visible to any test, because the only thing checking the mirror was
the mirror. So this walks the WHOLE corpus through both implementations and
fails on a single disagreement. It is the guard, not the fix.

The PHP is run for real, so `remove_accents()` has to be supplied -- WordPress
is not here. It is defined ONCE, in Python, and emitted into the harness, so
the two sides cannot disagree about the fold while agreeing about everything
else. The stub in tests/php/route_company_slugs.php used
`iconv('UTF-8', 'ASCII//TRANSLIT')`, whose output is LOCALE AND PLATFORM
DEPENDENT: on macOS it renders 'e' as "'e", so 'estee lauder' slugged as
`the-est-ee-lauder-companies` locally and `the-estee-lauder-companies` on the
Linux runner. A test that asserts different things on two machines is not
asserting anything.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ops_status  # noqa: E402

COMPANY_PHP = ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / \
    "includes" / "company.php"
DB = ROOT / "data" / "talent_intel.db"

#: WordPress remove_accents(), restricted to what the corpus needs and written
#: as data rather than as a call into whatever the host's libc will do today.
#: Keys are lowercase because tit_company_slug() lowercases first.
def _wp_remove_accents_table() -> dict[str, str]:
    """WordPress remove_accents(), modelled rather than transcribed.

    Over the Latin ranges WordPress does exactly one thing: canonical
    decomposition with the combining marks dropped, plus a short list of ATOMIC
    letters that have no decomposition because they are their own letter rather
    than a base plus a mark. Both halves are below. Everything OUTSIDE the Latin
    ranges is left alone, which is the property that matters here: Hangul, Han,
    Hebrew and Arabic reach tit_company_slug() intact.

    Generated instead of typed out because a 200-entry table copied by hand is a
    table with a typo in it, and the typo would look exactly like a real
    disagreement between the checker and the plugin.
    """
    import unicodedata

    table = dict(_ATOMIC)
    ranges = (
        (0x00C0, 0x024F),   # Latin-1 Supplement, Extended-A, Extended-B
        (0x1E00, 0x1EFF),   # Latin Extended Additional, incl. Vietnamese
    )
    for start, end in ranges:
        for cp in range(start, end + 1):
            ch = chr(cp).lower()
            if ch in table:
                continue
            folded = "".join(c for c in unicodedata.normalize("NFD", ch)
                             if not unicodedata.combining(c))
            if folded and folded != ch and folded.isascii():
                table[ch] = folded
    return table


#: The letters with no canonical decomposition. Same list as ops_status'
#: _ATOMIC_FOLDS, and the parity test is what proves they stay the same list.
_ATOMIC = {
    "\u00e6": "ae", "\u00f8": "o", "\u00fe": "th", "\u00df": "s",
    "\u00f0": "d", "\u0111": "d", "\u0127": "h", "\u0131": "i",
    "\u0133": "ij", "\u0138": "k", "\u0142": "l", "\u014b": "n",
    "\u0153": "oe", "\u0167": "t", "\u017f": "s", "\u01a1": "o",
    "\u01b0": "u",
}

REMOVE_ACCENTS = _wp_remove_accents_table()


pytestmark = pytest.mark.skipif(shutil.which("php") is None,
                                reason="php is not installed on this machine")


def _php_slugs(keys: list[str]) -> dict[str, str]:
    """Run the PLUGIN's own tit_company_slug() over `keys`.

    company.php is included whole rather than copied, so this cannot drift from
    the shipped function the way a transcribed copy would.
    """
    folds = json.dumps(REMOVE_ACCENTS, ensure_ascii=False)
    driver = textwrap.dedent(f"""\
        <?php
        // Supplied because WordPress is not loaded here. The table comes from
        // the Python test, so both sides fold identically by construction.
        $TIT_TEST_FOLDS = json_decode(<<<'JSON'
        {folds}
        JSON
        , true);
        function remove_accents($s) {{
            global $TIT_TEST_FOLDS;
            return strtr((string) $s, $TIT_TEST_FOLDS);
        }}
        function add_action() {{}}
        function add_filter() {{}}
        function get_option($k, $d = false) {{ return $d; }}
        function home_url($p = '') {{ return 'https://example.test' . $p; }}
        function get_transient($k) {{ return false; }}
        function set_transient($k, $v, $t = 0) {{ return true; }}
        if (!defined('TIT_VERSION')) define('TIT_VERSION', 'test');
        // company.php opens with `if (!defined('ABSPATH')) exit;`, the
        // standard WordPress direct-access guard. Without this the driver
        // exits at line 22 having printed NOTHING, with status 0 -- a silent
        // pass, which is why the assertion below also requires output and not
        // just a zero exit. company.php defines TIT_COMPANY_BASE itself.
        if (!defined('ABSPATH')) define('ABSPATH', '/tmp/wp/');
        require_once {str(COMPANY_PHP)!r};
        $keys = json_decode(file_get_contents('php://stdin'), true);
        $out = array();
        foreach ($keys as $k) {{ $out[$k] = tit_company_slug($k); }}
        echo json_encode($out, JSON_UNESCAPED_UNICODE);
        """)
    # Written to a file rather than passed to `php -r`: the driver contains a
    # heredoc, and -r reparses the shell-delivered string in a way that eats it.
    with tempfile.NamedTemporaryFile("w", suffix=".php", encoding="utf-8",
                                     delete=False) as fh:
        fh.write(driver)
        path = fh.name
    try:
        proc = subprocess.run(["php", path], input=json.dumps(keys),
                              capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(path)
    assert proc.returncode == 0 and proc.stdout.strip(), (
        "the plugin's company.php did not run.\n"
        f"stderr: {proc.stderr[-2000:]}\nstdout: {proc.stdout[:500]}")
    return json.loads(proc.stdout)


def _corpus() -> list[str]:
    if not DB.exists():
        pytest.skip("the committed database is not in this checkout")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT company_key FROM signals "
            "WHERE is_current = 1 AND company_key <> ''")]
    finally:
        conn.close()


# A fixed sample that must hold with or without the database, so a checkout
# without data still runs a real assertion rather than skipping to green.
LANDMARKS = [
    "오픈ai", "페르소나ai",      # OpenAI / Persona AI
    "창원fc", "화성fc",                   # Changwon / Hwaseong
    "lg전자", "cj제일제당",       # LG Elec / CJ
    "bnk 피어엑스", "bnk캐피탈",
    "nh투자증권", "nh證",
    "giày thượng đình",
    "górnik łęczna",
    "atkinsréalis uk", "b & m retail", "dolce&gabbana",
    "ibm", "日本ibm", "acme corp", "n-able", "רשת 13",
]


class TestMirrorMatchesThePlugin:
    def test_landmarks_agree(self):
        php = _php_slugs(LANDMARKS)
        wrong = {k: (ops_status._profile_slug(k), php[k])
                 for k in LANDMARKS if ops_status._profile_slug(k) != php[k]}
        assert not wrong, (
            "ops_status._profile_slug disagrees with tit_company_slug. The "
            "checker is naming URLs the site does not serve:\n"
            + "\n".join(f"  {k!r}: checker={a!r} plugin={b!r}"
                        for k, (a, b) in wrong.items()))

    def test_the_whole_corpus_agrees(self):
        keys = _corpus()
        assert len(keys) > 1000, f"only {len(keys)} keys; is the DB truncated?"
        php = _php_slugs(keys)
        wrong = [(k, ops_status._profile_slug(k), php[k])
                 for k in keys if ops_status._profile_slug(k) != php[k]]
        assert not wrong, (
            f"{len(wrong)} of {len(keys)} employer keys slug differently in "
            f"the checker and in the plugin. First 20:\n"
            + "\n".join(f"  {k!r}: checker={a!r} plugin={b!r}"
                        for k, a, b in wrong[:20]))


class TestHangulIsRomanisedNotDeleted:
    """The behaviour the mirror is mirroring, asserted on its own.

    Without these, making both sides equally wrong would pass the parity test.
    """

    def test_a_korean_name_is_not_reduced_to_its_latin_fragment(self):
        assert ops_status._profile_slug("lg전자") == "lg-jeonja", \
            "LG Electronics must not be published at /company/lg/"

    def test_two_distinct_korean_employers_get_two_urls(self):
        a = ops_status._profile_slug("오픈ai")       # OpenAI
        b = ops_status._profile_slug("페르소나ai")  # Persona AI
        assert a != b, "two different employers still claim one profile URL"
        assert a == "opeun-ai" and b == "pereusona-ai", (a, b)

    def test_city_names_romanise_the_ordinary_way(self):
        assert ops_status._profile_slug("창원fc") == "changwon-fc"
        assert ops_status._profile_slug("화성fc") == "hwaseong-fc"

    def test_a_script_boundary_is_a_word_boundary(self):
        assert ops_status._profile_slug("cj제일제당") == \
            "cj-jeiljedang"

    def test_scripts_we_cannot_romanise_are_left_alone_not_guessed(self):
        """Han stays folded away, and that is a decision, not an oversight.

        日本 is nihon, nippon or riben depending on the language of the name.
        Guessing would invent a company name, so the collision it causes stays
        in the report where a human can see it.
        """
        assert ops_status._profile_slug("日本ibm") == "ibm"
        assert ops_status._profile_slug("ibm") == "ibm"

    def test_atomic_latin_letters_fold_the_way_wordpress_folds_them(self):
        assert ops_status._profile_slug("górnik łęczna") == \
            "gornik-leczna", "NFKD alone deletes the l-stroke"
        assert ops_status._profile_slug(
            "giày thượng đình") == \
            "giay-thuong-dinh", "NFKD alone deletes the d-stroke"
