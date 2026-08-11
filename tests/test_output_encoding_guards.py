"""Two output-encoding defects on the dashboard side, both defence in depth.

1. `tit_csv_guard` inspected `$value[0]` only. Excel and LibreOffice STRIP
   leading whitespace before deciding what a cell is, so a leading TAB or CR
   carried a formula straight through the guard written to stop it.

2. `dashboard.js` repainted `source_url` and `archive_url` into `href` with
   `esc()`, which is ENTITY escaping. `javascript:alert(1)` contains no
   character `esc()` touches, so it survives intact. The SERVER paint of the
   same field uses `esc_url()`, which enforces a scheme allowlist. So the two
   paints of one field disagreed: a poisoned row was inert on first paint and
   live the moment a filter change repainted the card in JavaScript. That gap
   is the interesting part, because it is invisible to anyone testing the page
   as delivered.

Neither is live today: `validate.py` refuses a record with no source URL and
every stored value normalises through a fixed vocabulary. Depth is the point.
Both tests fail on the pre-fix code.
"""
import json
import os
import pathlib
import re
import subprocess
import unittest
from shutil import which

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
EXPORT = PLUGIN / "includes" / "export.php"
DASHBOARD_JS = PLUGIN / "assets" / "dashboard.js"


def php():
    for path in ("/opt/homebrew/bin/php", "/usr/bin/php", "/usr/local/bin/php"):
        if os.path.exists(path):
            return path
    return which("php")


class CsvFormulaGuard(unittest.TestCase):
    """Runs the real PHP function rather than asserting on its source."""

    def setUp(self):
        if not php():
            self.skipTest("php not installed")
        src = EXPORT.read_text()
        fn = src[src.index("function tit_csv_guard"):]
        self.fn = fn[:fn.index("\n}\n") + 3]

    def _guard(self, values):
        shim = self.fn + """
$in = json_decode(file_get_contents('php://stdin'), true);
$out = array();
foreach ($in as $v) { $out[] = tit_csv_guard($v); }
echo json_encode($out);
"""
        proc = subprocess.run([php(), "-r", shim], input=json.dumps(values),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_a_leading_tab_or_cr_no_longer_smuggles_a_formula_past(self):
        payloads = ["\t=cmd|'/c calc'!A1", "\r=HYPERLINK(\"http://x\")",
                    "\t\t+1+1", "\n-2+3", " @SUM(1)", "\r\n=1+1"]
        for original, guarded in zip(payloads, self._guard(payloads)):
            with self.subTest(payload=repr(original)):
                self.assertTrue(guarded.startswith("'"),
                                f"{original!r} still reads as a formula")

    def test_the_plain_cases_still_work(self):
        for original, guarded in zip(["=1+1", "+1", "-1", "@x"],
                                     self._guard(["=1+1", "+1", "-1", "@x"])):
            self.assertEqual(guarded, "'" + original)

    def test_ordinary_values_are_left_exactly_alone(self):
        plain = ["Acme Corp", "", "1200", "Singapore", "a-b", "x@y.com",
                 "Series B + extension"]
        self.assertEqual(self._guard(plain), plain,
                         "a guard that mangles ordinary cells is a data bug")


class DashboardHrefsAreSchemeChecked(unittest.TestCase):
    def setUp(self):
        self.src = DASHBOARD_JS.read_text()

    def test_no_href_is_painted_with_entity_escaping_alone(self):
        """The regression this file exists to prevent."""
        offenders = re.findall(r"""href="'\s*\+\s*esc\(""", self.src)
        self.assertEqual(offenders, [],
                         "esc() is entity escaping and does not check a URL "
                         "scheme; use escUrl(), which agrees with esc_url()")

    def test_escUrl_exists_and_allows_only_http_and_https(self):
        self.assertIn("function escUrl", self.src)
        fn = self.src[self.src.index("function escUrl"):]
        fn = fn[:fn.index("\n  }") + 4]
        self.assertRegex(fn, r"https\?", "the allowlist must be http/https")
        self.assertIn("return ''", fn, "anything else must yield no href")

    def test_it_strips_the_control_characters_that_hide_a_scheme(self):
        """`java\\tscript:` is parsed as `javascript:` by browsers, so a check
        on the raw string alone is not a check."""
        fn = self.src[self.src.index("function escUrl"):]
        fn = fn[:fn.index("\n  }") + 4]
        self.assertRegex(fn, r"u0000|x00|\\s",
                         "control characters must be removed before the "
                         "scheme is tested")

    def test_it_behaves_correctly_when_actually_run(self):
        if not which("node"):
            self.skipTest("node not installed")
        fn = self.src[self.src.index("  function esc(value)"):]
        fn = fn[:fn.index("\n  function srcAnchor")]
        script = fn + """
const cases = [
  ['https://publisher.example/a', true],
  ['http://publisher.example/a', true],
  ['javascript:alert(1)', false],
  ['JaVaScRiPt:alert(1)', false],
  ['java\\tscript:alert(1)', false],
  ['  javascript:alert(1)', false],
  ['data:text/html,<script>x</script>', false],
  ['vbscript:msgbox', false],
  ['//evil.example/x', false],
  ['', false]
];
const out = cases.map(([u, want]) => [u, escUrl(u) !== '', want]);
console.log(JSON.stringify(out));
"""
        proc = subprocess.run([which("node"), "-e", script],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for url, allowed, expected in json.loads(proc.stdout):
            with self.subTest(url=url):
                self.assertEqual(allowed, expected,
                                 f"escUrl({url!r}) allowed={allowed}")

    def test_the_server_paint_still_uses_esc_url(self):
        """If the server ever stopped, the two paints would agree at the WRONG
        level and this file would be guarding nothing."""
        shortcodes = (PLUGIN / "includes" / "shortcodes.php").read_text()
        self.assertIn("esc_url($r['source_url'])", shortcodes)


if __name__ == "__main__":
    unittest.main()
