"""AN EMPTY MONEY CHART HAS TO SAY WHY IT IS EMPTY.

THE DEFECT, from the owner's own use. He set Looking For to "Pay and Benefits"
and Where to "United States", and all three money charts said:

    No US dollar amounts in this view yet.

That sentence is true and useless. It reads as data we failed to collect, and
the owner read it that way and asked why no cities were showing. Nothing was
missing: pay and benefits updates carry no funding amount, so no money chart
can fill under that pillar however the rest of the page is set. Measured on the
live endpoint the day this was written, `pillar=rewards_comp` alone returns one
dollar-stated update out of 8,838, and it names no country, no city and no
industry, so every one of the three charts is structurally empty there. Under
`pillar=rewards_comp&country=US` the view holds no funding update at all, while
`company_development` puts real millions against New York, San Francisco,
Austin, Boston, Seattle and Los Angeles. The data was fine. The filters
disagreed, and the page would not say so.

THREE CAUSES, AND THEY NEED THREE DIFFERENT SENTENCES.

  unplaced  the view HAS dollar amounts and this dimension can place none of
            them. Real here: only 655 of the 4,094 amount-bearing rows carry a
            city. That is a coverage gap, and telling the reader to change a
            filter would be a confidently WRONG explanation, which is worse
            than a vague one.
  pillar    no amount in the view, and the selected pillar could not fill this
            chart with every other filter taken off. The pillar is the cause,
            so name it, in the word the control itself uses.
  filters   no amount in the view and the pillar is not the reason.

WHAT THIS FILE ASSERTS, AND HOW.

Both halves run the SHIPPED code, never a copy of it.

  - The browser half executes the real paintMoney() and moneyEmptyNote() out of
    assets/dashboard.js in headless Chrome, against the real chart markup, and
    reads `innerText` off the rendered chart ancestor. innerText and not
    innerHTML, and not textContent: this page hides text in closed <details>
    and in visually-hidden spans, both of which textContent reports as present.
    innerText on a rendered subtree reports what a reader can actually read,
    which is the only thing worth asserting about an explanation.
  - The PHP half executes the real tit_money_empty_note() out of
    includes/shortcodes.php, extracted by matching brackets rather than by
    slicing between two literals, and asserts it produces the SAME sentences.
    The server prints one of these on first paint and the browser reprints it
    on every filter change; a divergence would be the page rewriting its own
    explanation as the reader typed.

NO PHP, NO NODE, NO CHROME: SKIP LOUDLY. Absence of a signal is not a pass.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cdp import Browser, CDPUnavailable, find_chrome  # noqa: E402
from tests.phpsource import balanced_block  # noqa: E402

PLUGIN = ROOT / "wordpress-plugin/talent-intelligence-tracker"
JS_PATH = PLUGIN / "assets/dashboard.js"
PHP_PATH = PLUGIN / "includes/shortcodes.php"
PHP = shutil.which("php")

# The sentence this replaced. It must not survive anywhere that renders.
USELESS = "No US dollar amounts in this view yet."

# The three views, each one a `money` payload shaped exactly as /aggregate
# returns it. Numbers here are INPUTS, standing in for what the server
# measured; the point of the last test in this file is that the copy carries
# whatever it is handed rather than a figure somebody typed into a string.
VIEWS = {
    # 4,094 amount-bearing rows, 655 of them with a city: the real live shape.
    "unplaced": {
        "coverage": {"with": 4094, "all": 4118},
        "placed": {"country": 4094, "city": 655, "industry": 3800},
        "empty": {"pillar": "", "pillar_placed": None},
    },
    # The owner's filter: Pay and Benefits plus United States.
    "pillar": {
        "coverage": {"with": 0, "all": 0},
        "placed": {"country": 0, "city": 0, "industry": 0},
        "empty": {"pillar": "Pay and Benefits",
                  "pillar_placed": {"country": 0, "city": 0, "industry": 0}},
    },
    # A pillar that CAN carry money, narrowed by something else until it holds
    # none. The pillar is not the cause, so the copy must not blame it.
    "filters": {
        "coverage": {"with": 0, "all": 0},
        "placed": {"country": 0, "city": 0, "industry": 0},
        "empty": {"pillar": "Leadership Moves",
                  "pillar_placed": {"country": 12, "city": 9, "industry": 11}},
    },
}

# The chart markup tit_money_chart() emits, down to the class names paintMoney()
# reaches for. The <details> is deliberate: a closed disclosure is exactly the
# text innerText must not report, and its presence is what makes this assertion
# about what a reader sees rather than about what the DOM holds.
FIXTURE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body>
<div class="tit-chart tit-chart-money" id="chart-money-city">
  <div class="tit-chart-head"><h4>Money Raised by City</h4>
    <details class="tit-chart-note"><summary>About this chart</summary>
      <p class="tit-money-note">USD-stated amounts only.</p>
    </details>
  </div>
  <div class="tit-rank" tabindex="0" role="group" aria-label="Money Raised by City"></div>
</div>
</body></html>
"""


def js_function(name):
    """The source text of one `function <name>(` declaration in dashboard.js."""
    src = JS_PATH.read_text(encoding="utf-8")
    needle = "\n  function %s(" % name
    if src.count(needle) != 1:
        raise AssertionError(
            "dashboard.js must declare `%s` exactly once; found %d. It was "
            "renamed or moved, so this test is no longer reading the code it "
            "means to assert about." % (name, src.count(needle)))
    start = src.index(needle) + 1
    depth = 0
    i = src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


def php_function(name):
    """The real PHP declaration of `function <name>(...)`, brackets matched."""
    src = PHP_PATH.read_text(encoding="utf-8")
    head = "function %s(" % name
    args = balanced_block(src, head, what=head + ")")
    body = balanced_block(src, "%s%s) {" % (head, args), what=head + ") body")
    return "function %s(%s) {%s}" % (name, args, body)


def php_notes(view, dims=("country", "city", "industry")):
    """Run the SHIPPED tit_money_empty_note() and return its sentences."""
    if not PHP:
        raise unittest.SkipTest("php is not installed; cannot execute shortcodes.php")
    # The two WordPress functions this one touches, and nothing else. _n() picks
    # by count and number_format_i18n() groups thousands, which is all either
    # does to these sentences.
    stubs = (
        "function _n($one, $many, $n, $d = '') { return $n == 1 ? $one : $many; }\n"
        "function number_format_i18n($n) { return number_format((float) $n); }\n"
    )
    call = "$m = json_decode(<<<'J'\n%s\nJ\n, true);\n$o = array();\n" % json.dumps(view)
    call += ("foreach (%s as $d) { $o[$d] = tit_money_empty_note($m, $d); }\n"
             "echo json_encode($o);\n" % json.dumps(list(dims)).replace('"', "'"))
    script = stubs + php_function("tit_money_empty_note") + "\n" + call
    proc = subprocess.run([PHP, "-r", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("php failed:\n%s" % (proc.stderr.strip() or proc.stdout.strip()))
    return json.loads(proc.stdout)


class MoneyEmptyStateTests(unittest.TestCase):
    """One Chrome, one page, every view painted through the real renderer."""

    rendered = None

    @classmethod
    def setUpClass(cls):
        if not find_chrome():
            raise unittest.SkipTest("no Chrome; cannot measure what a reader sees")
        try:
            with Browser() as br:
                br.navigate("data:text/html;charset=utf-8," + _urlquote(FIXTURE))
                br.eval_js(cls._setup_script())
                out = {}
                for name, money in VIEWS.items():
                    out[name] = br.eval_js(
                        "window.__paint(%s, 'city')" % json.dumps(money))
                cls.rendered = out
        except CDPUnavailable as exc:
            raise unittest.SkipTest("Chrome would not start: %s" % exc)

    def visible(self, name):
        text = (self.rendered or {}).get(name)
        self.assertTrue(
            text, "nothing rendered for the %r view; the chart drew no explanation" % name)
        return " ".join(text.split())

    # ---------------------------------------------------------------- cases

    def test_a_dimension_that_cannot_place_the_money_says_so_and_blames_no_filter(self):
        text = self.visible("unplaced")
        self.assertIn("4,094 updates with a US dollar amount", text,
                      "the coverage-gap sentence must carry the count it is about, "
                      "so a reader can see how much money we hold and cannot place")
        self.assertIn("not one of them names a city", text)
        self.assertIn("not a filter you can widen", text,
                      "we hold the money and not the place. Telling the reader to "
                      "change a filter here would be a wrong explanation, which is "
                      "worse than a vague one")
        self.assertNotIn("Looking For", text,
                         "a coverage gap must not be dressed up as a filter mismatch")

    def test_a_pillar_that_can_never_carry_money_names_itself_and_the_way_out(self):
        text = self.visible("pillar")
        self.assertIn(
            "No Pay and Benefits update we hold pairs a US dollar amount with a city",
            text,
            "the owner's case. The page has to say that this KIND of update "
            "carries no amount, not that the data is missing")
        self.assertIn("Try Looking For: Raised Money.", text,
                      "an explanation that does not point at the filter that would "
                      "show something leaves the reader exactly where they were")

    def test_a_filter_combination_is_not_blamed_on_the_pillar(self):
        text = self.visible("filters")
        self.assertIn("No update in this view states a US dollar amount.", text)
        self.assertIn("Try a wider country or date range.", text)
        self.assertNotIn(
            "Leadership Moves", text,
            "this pillar CAN fill the chart (pillar_placed is non-zero), so the "
            "pillar is not the cause and must not be named as it")

    def test_the_three_causes_do_not_share_a_sentence(self):
        seen = {name: self.visible(name) for name in VIEWS}
        self.assertEqual(len(set(seen.values())), 3,
                         "three different causes rendered fewer than three "
                         "different explanations: %s" % json.dumps(seen, indent=2))
        for name, text in seen.items():
            self.assertNotIn(USELESS, text,
                             "the %r view still shows the sentence this replaced" % name)

    def test_every_explanation_is_actually_visible_to_a_reader(self):
        # innerText off the rendered ancestor, so a closed <details> and any
        # visually-hidden span are excluded by the layout engine rather than by
        # this test guessing. The floor is a sentence-and-a-bit; the real
        # counts are reported so a shrinking explanation is visible in the log.
        counts = {}
        for name in VIEWS:
            text = self.visible(name)
            counts[name] = len(text)
            self.assertNotIn("USD-stated amounts only", text,
                             "innerText reported the paragraph inside a CLOSED "
                             "disclosure, so this whole file is asserting on "
                             "markup rather than on what a reader can read")
            self.assertGreater(
                len(text), 60,
                "the %r explanation rendered %d characters, which is not an "
                "explanation: %r" % (name, len(text), text))
        print("rendered characters: %s" % json.dumps(counts, sort_keys=True))

    # ------------------------------------------------------- honesty + mirror

    def test_no_figure_in_the_copy_is_hardcoded(self):
        """The count moves with the data, or it is decoration."""
        script = """
        (function () {
          var out = {};
          [1, 7, 4094, 1234567].forEach(function (n) {
            var m = JSON.parse(JSON.stringify(%s));
            m.coverage.with = n;
            out[n] = window.__paint(m, 'city');
          });
          return JSON.stringify(out);
        })();
        """ % json.dumps(VIEWS["unplaced"])
        try:
            with Browser() as br:
                br.navigate("data:text/html;charset=utf-8," + _urlquote(FIXTURE))
                br.eval_js(self._setup_script())
                said = json.loads(br.eval_js(script))
        except CDPUnavailable as exc:
            raise unittest.SkipTest("Chrome would not start: %s" % exc)

        self.assertIn("1 update with a US dollar amount", said["1"],
                      "one update must not read as '1 updates'")
        self.assertIn("it does not name a city", said["1"])
        for n in (7, 4094, 1234567):
            self.assertIn("{:,}".format(n) + " updates", said[str(n)],
                          "the sentence printed something other than the count it "
                          "was handed, so the figure is not the data's")
        self.assertEqual(len(set(said.values())), 4,
                         "four different counts produced fewer than four "
                         "sentences, so at least one number is written down "
                         "rather than measured")

    def test_the_server_and_the_browser_say_the_same_words(self):
        for name, money in VIEWS.items():
            php = php_notes(money)
            self.assertIn(
                php["city"], self.visible(name),
                "shortcodes.php and dashboard.js explain the %r view "
                "differently, so the page rewrites its own reason the moment a "
                "reader changes a filter" % name)

    # ------------------------------------------------------------- plumbing

    @classmethod
    def _setup_script(cls):
        bodies = "\n".join(js_function(n) for n in
                           ("esc", "nfmt", "moneyShort", "moneyFull",
                            "coverageFull", "coverageNote", "moneyEmptyNote",
                            "paintMoney"))
        return """
        (function () {
          var MONEY_UNITS = ['K', 'M', 'B', 'T'];
          %s
          window.__paint = function (money, dim) {
            var chart = document.getElementById('chart-money-city');
            paintMoney(chart, [], function (k) { return k; }, money, dim, false);
            return chart.innerText;
          };
        })();
        """ % bodies


class TheSentenceThisReplacedTests(unittest.TestCase):
    """Needs no browser and no PHP, so it reds on the defect everywhere.

    Deliberately its own class: the tests above cannot run at all until the two
    renderers exist, and a suite that ERRORS on a missing function says nothing
    about the copy a reader is being shown. This one reads the shipped files and
    fails on the defect itself.
    """

    def test_no_money_chart_still_shows_the_useless_sentence(self):
        for path in (JS_PATH, PHP_PATH):
            src = path.read_text(encoding="utf-8")
            # Comments in this repo quote the copy they replaced, on purpose.
            # Only a LIVE occurrence counts, so a mention in prose is dropped by
            # requiring the string on the same line as the element that renders
            # it.
            live = re.findall(r"tit-rank-empty[^\n]*" + re.escape(USELESS), src)
            self.assertEqual(
                live, [],
                "%s still renders %r as a money chart's empty state. It is true "
                "of all three causes and useful for none of them: it reads as "
                "data we failed to collect even when the real answer is that "
                "this kind of update never carries an amount." % (path.name, USELESS))


def _urlquote(text):
    from urllib.parse import quote
    return quote(text, safe="")


if __name__ == "__main__":
    unittest.main()
