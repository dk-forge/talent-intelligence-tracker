"""A ROW IS AN ENTRY HERE TOO, AND "SIGNAL" IS NOT ONE WORD.

WHY THIS TEST EXISTS. The sibling layoff tracker renamed its reader-facing
"event" to "entry", because the API field was already called `entries`, the
docs said both, and the owner could not tell from the page what a count of
them was counting. The same question was asked here, and the answer is NOT the
same, which is the whole reason this file explains itself at length.

WHAT "SIGNAL" ACTUALLY DOES ON THIS PAGE. It does three different jobs, and
only one of them is naming a row.

  1. A ROW. "No signals published yet", the per-source count on the sources
     page, the admin screen's total. These are counts of records. They are now
     "entries", for the sibling's reasons: `entries` is what a count of rows
     is, and one real-world happening can produce several records.

  2. A KIND, or the axis the kinds sit on. The trend chart's "how many updates
     of that signal", the "one signal is not drawn" note, the glance matrix's
     screen-reader corner header, the recall page's "by signal type". THIS IS
     THE LOAD-BEARING HALF. Unlike the layoff tracker, where every row is the
     same kind of thing, this tracker holds four: company development,
     leadership change, rewards and comp, and how we work (pipeline/vocab.py
     PILLARS). Calling the axis "entries" would say the four are one thing,
     which is less informative than the word it replaced, not clearer. So
     those became the plain English for what they are: a kind, a line on a
     chart, a measure. The distinction survives and the jargon does not.

  3. THE SUBJECT MATTER. The board's title, the place-page headings, the feed
     description. "Sourced Talent Signals Worldwide" is what this product
     covers, not a count of anything, and a reader meeting it is not being
     told a number. Those stay, and ALLOWED_SIGNAL below names them.

WHY "EVENT" IS LEFT ALONE ON THE RECALL PAGE, DELIBERATELY. The recall
measurement holds a sealed set of REAL-WORLD events assembled from public
sources, and asks how many of them we hold. "We hold 21 of 51 events" is only
true if "event" means the real-world thing. Renaming those to "entries" would
collapse the two halves of the measurement into one word and destroy the only
sentence on the page that says what is being measured. With "entry" now
available for our record, that page can finally say both: an event is out
there, an entry is what we hold about it.

WHAT IS NOT TOUCHED. `wp_tit_signals`, `signal_id`, `signal_direction`, the
`pillar` column and its vocabulary, every PHP and JS identifier, and the CRM
export's `Signal Date` / `Signal Direction` column headers, which are a file
somebody's spreadsheet already reads. NothingAConsumerReadsMoved asserts a
sample of those is still in place.

THE DEFINITION LINE is the part that does the work, and it is asserted on
`innerText` off the RENDERED page in real headless Chrome, through the real
shortcode via tests/php/render_dashboard.php. Not on the source, not on
`textContent`: a subtree that is not rendered returns textContent from its own
innerText, which is exactly how a caveat ships and reaches nobody.

No php or no Chrome: this SKIPS loudly. UNKNOWN is not a pass.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import style_check  # noqa: E402
from cdp import Browser, CDPUnavailable, find_chrome  # noqa: E402

PLUGIN = ROOT / "wordpress-plugin/talent-intelligence-tracker"
CSS = PLUGIN / "assets/dashboard.css"
HARNESS = ROOT / "tests/php/render_dashboard.php"

SIGNAL_RE = re.compile(r"(?i)\bsignals?\b")
EVENT_RE = re.compile(r"(?i)\bevents?\b")

# Reader-facing copy style_check does not already target.
EXTRA_TARGETS = [
    ("wordpress-plugin/talent-intelligence-tracker/"
     "talent-intelligence-tracker.php", "admin"),
]

# "Signal" survives ONLY as the name of the subject matter. Each phrase is
# listed with the surface it lives on, so an exemption cannot quietly spread.
ALLOWED_SIGNAL = (
    # The board's own title. It says what this product covers; it is beside no
    # number and counts nothing.
    "sourced talent signals worldwide",
    # Place-page h1, title and breadcrumb: "<Place>: Hiring, Funding And
    # Leadership Signals". Three kinds named in the same breath, which is the
    # opposite of flattening them.
    "leadership signals",
    # The RSS channel description, which is a product description and is
    # already followed by the plainer word: "...and ways-of-working updates,
    # each one linked to the document behind it."
    "talent market signals",
)

# "Event" survives ONLY where it means the real-world happening a measurement
# is taken against. See the module docstring: the whole recall measurement is
# "how many of these events do we hold an entry for", and one word for both
# halves is no measurement at all. The recall page is one surface; the same
# sentence is also quoted on the dashboard, the press page, the sources page
# and in the weekly digest, so the exemption is by PAGE and by PHRASE.
RECALL_SURFACES = ("recall",)
ALLOWED_EVENT = (
    # The recall paragraph, repeated verbatim on four surfaces.
    "fixed set of real events",
    # The landmarks check, in the weekly digest. A landmark is a named
    # real-world round, not a row: the whole point is that we may hold nothing
    # for it.
    "a landmark is an event",
)

# SHORT LABELS SLIP PAST style_check.py, which needs 12 characters and 3 real
# words before a string is eligible. Every one of these was a "signal" that no
# prose check would ever have caught, so they are named here one at a time.
SHORT_LABELS = (
    (PLUGIN / "includes/sources.php", "<th>Covers</th><th>Entries</th>",
     "the per-source column counts rows, so it is Entries"),
    (PLUGIN / "includes/sources.php", 'data-label="Entries"',
     "the mobile label for that same column has to match its header"),
    (PLUGIN / "includes/shortcodes.php",
     '<th scope="col"><span class="tit-sr">Measure</span></th>',
     "the glance matrix corner names the AXIS the four kinds sit on, and a "
     "screen reader is the only reader who hears it"),
    (PLUGIN / "assets/dashboard.js",
     '<th scope="col"><span class="tit-sr">Measure</span></th>',
     "the JS renderer repaints that matrix, so it must say the same word"),
    (PLUGIN / "assets/dashboard.js", "['', 'What Happened'],",
     "the under-the-hood table's headline column, named the way the main "
     "table's identical column is already named"),
    (PLUGIN / "talent-intelligence-tracker.php", "</strong> current entries.",
     "the admin screen counts rows"),
)

# The machine-readable names, and the export contract. None of these moved.
FROZEN_IDENTIFIERS = (
    (PLUGIN / "includes/db.php", "tit_signals"),
    (PLUGIN / "includes/api.php", "signal_direction"),
    (PLUGIN / "includes/export_crm.php", "'Signal Date'"),
    (PLUGIN / "includes/export_crm.php", "'Signal Direction'"),
    (PLUGIN / "includes/shortcodes.php", "tit_signal_defs"),
    (ROOT / "pipeline/vocab.py", "leadership_change"),
    (ROOT / "pipeline/vocab.py", "rewards_comp"),
)

DEF_ID = "tit-board-def"
DEF_TEXT = ("An entry is one update about one employer. "
            "Total Raised counts dollars, and every other row counts entries.")


def reader_segments():
    root = str(ROOT)
    segs = list(style_check.collect(root))
    for rel, page in EXTRA_TARGETS:
        path = os.path.join(root, rel)
        assert os.path.isfile(path), "reader-copy target is gone: %s" % rel
        segs.extend(style_check.extract_file(path, page, root))
    assert len(segs) > 500, (
        "only %d reader-facing strings were extracted, which means the "
        "extractor stopped working and this test is checking nothing"
        % len(segs))
    return segs


class TheCopyCallsARowAnEntry(unittest.TestCase):

    def test_no_reader_facing_string_calls_a_row_a_signal(self):
        offenders = []
        for seg in reader_segments():
            if not SIGNAL_RE.search(seg.text):
                continue
            low = seg.text.lower()
            if any(p in low for p in ALLOWED_SIGNAL):
                continue
            offenders.append("%s:%s  %s" % (seg.path, seg.line,
                                            seg.text[:120]))
        self.assertEqual(
            [], offenders,
            "%d reader-facing string(s) still say \"signal\". A count of rows "
            "is entries; the axis the four kinds sit on is a kind, a line or "
            "a measure, named as such; only the subject matter keeps the "
            "word, and ALLOWED_SIGNAL is the whole list of those. Fix the "
            "copy, do not widen the list:\n  %s"
            % (len(offenders), "\n  ".join(offenders)))

    def test_event_survives_only_where_it_means_a_real_world_happening(self):
        """The recall page measures real events against the entries we hold.
        Everywhere else, a row is an entry."""
        offenders = []
        for seg in reader_segments():
            if not EVENT_RE.search(seg.text):
                continue
            if seg.page in RECALL_SURFACES:
                continue
            low = seg.text.lower()
            if any(p in low for p in ALLOWED_EVENT):
                continue
            offenders.append("%s:%s  %s" % (seg.path, seg.line,
                                            seg.text[:120]))
        self.assertEqual(
            [], offenders,
            "%d reader-facing string(s) outside the recall measurement call "
            "something an event. On the recall page an event is the "
            "real-world happening the sealed gold set is made of, and an "
            "entry is what we hold about it. Off that page there is no such "
            "pair, so a row is an entry:\n  %s"
            % (len(offenders), "\n  ".join(offenders)))

    def test_the_short_labels_a_prose_check_cannot_see(self):
        """style_check.py needs 12 characters and 3 real words before a string
        is eligible, so a table header is invisible to it. These were checked
        by eye and are pinned by name."""
        missing = []
        for path, needle, why in SHORT_LABELS:
            if needle not in path.read_text():
                missing.append("%s: %s\n      (%s)"
                               % (path.relative_to(ROOT), needle, why))
        self.assertEqual(
            [], missing,
            "%d short reader-facing label(s) are not what they should be. No "
            "prose check can see these, which is exactly why they are listed "
            "one at a time:\n  %s" % (len(missing), "\n  ".join(missing)))

    def test_the_signal_allowlist_still_describes_real_copy(self):
        blob = "\n".join(s.text.lower() for s in reader_segments())
        for phrase in ALLOWED_SIGNAL:
            self.assertIn(
                phrase, blob,
                "ALLOWED_SIGNAL still exempts %r, but no reader-facing string "
                "contains it any more. Delete the exemption rather than "
                "leaving it to catch a future sentence nobody argued for."
                % phrase)

    def test_the_four_kinds_are_still_named_separately_for_a_reader(self):
        """The reason "signal" was not simply renamed to "entry". If these
        four labels ever collapse into one word, the rename DID flatten a real
        distinction and this file's reasoning stops being true."""
        src = (PLUGIN / "includes/shortcodes.php").read_text()
        for label in ("Growing and Expanding", "Leadership Moves",
                      "Pay and Benefits", "Ways of Working"):
            self.assertIn(
                label, src,
                "the dashboard no longer names the %r kind for a reader. "
                "This tracker holds four kinds of thing, and that is exactly "
                "why the axis they sit on was not renamed to \"entries\": "
                "the kinds carry the distinction, so the axis does not have "
                "to. Losing them makes \"entry\" the flattening word this "
                "test says it is not." % label)


class NothingAConsumerReadsMoved(unittest.TestCase):

    def test_the_machine_readable_names_are_untouched(self):
        missing = []
        for path, needle in FROZEN_IDENTIFIERS:
            if needle not in path.read_text():
                missing.append("%s no longer contains %s"
                               % (path.relative_to(ROOT), needle))
        self.assertEqual(
            [], missing,
            "the copy rename reached a machine-readable name. The signals "
            "table, signal_direction, the pillar vocabulary and the CRM "
            "export's column headers are things somebody already reads.\n  %s"
            % "\n  ".join(missing))


def render_markup():
    """The real shortcode, through the real PHP, same as the other
    rendered tests here. A hand-written fixture stops describing the page the
    first time something moves, and then it passes forever."""
    php = shutil.which("php")
    if not php:
        raise unittest.SkipTest("no php on PATH: UNKNOWN, not a pass")
    dest = tempfile.mktemp(suffix=".html")
    env = dict(os.environ, TIT_DUMP_HTML=dest)
    proc = subprocess.run([php, str(HARNESS)], cwd=str(ROOT), env=env,
                          capture_output=True, text=True)
    if not os.path.exists(dest):
        raise unittest.SkipTest(
            "render_dashboard.php produced no markup, so the definition line "
            "could not be rendered. UNKNOWN, not a pass:\n%s"
            % (proc.stderr or proc.stdout)[-2000:])
    try:
        return Path(dest).read_text()
    finally:
        os.unlink(dest)


FIXTURE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>%(plugin)s</style>
<style>body{background:#fff;color:#16181d;margin:0;
             font-family:system-ui,sans-serif}</style>
</head>
<body class="wp-singular page-template-default page">
<div class="wp-site-blocks"><main class="wp-block-group has-global-padding">
<div class="wp-block-group alignfull"><div class="entry-content alignfull">
%(markup)s
</div></div></main></div>
</body></html>
"""

PROBE = r"""
(function () {
  var el = document.getElementById(%s);
  if (!el) return { found: false };
  var host = el.parentElement || document.body;
  var r = el.getBoundingClientRect();
  var cs = getComputedStyle(el);
  return {
    found: true,
    hostText: (host.innerText || '').replace(/\s+/g, ' ').trim(),
    text: (el.innerText || '').replace(/\s+/g, ' ').trim(),
    inClosedDetails: !!(el.closest('details') && !el.closest('details').open),
    w: Math.round(r.width), h: Math.round(r.height),
    display: cs.display, visibility: cs.visibility
  };
})()
""" % json.dumps(DEF_ID)


class TheDefinitionLineRenders(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not find_chrome():
            raise unittest.SkipTest(
                "no Chrome/Chromium on this machine, so the definition line "
                "could not be rendered. This is UNKNOWN, not a pass.")
        html = FIXTURE % {"plugin": CSS.read_text(),
                          "markup": render_markup()}
        try:
            with Browser(width=375, height=812) as page:
                page.call("Page.navigate", {"url": "about:blank"})
                page.eval_js(
                    "(function(){document.open();document.write(%s);"
                    "document.close();return true;})()" % json.dumps(html))
                cls.probe = page.eval_js(PROBE)
        except CDPUnavailable as exc:
            raise unittest.SkipTest("Chrome would not start: %s" % exc)

    def test_the_line_is_in_the_page_at_all(self):
        self.assertTrue(
            self.probe["found"],
            "the dashboard renders no #%s. The board is where a first-time "
            "reader meets the numbers, so that is where the definition goes."
            % DEF_ID)

    def test_the_line_is_not_sealed_inside_a_closed_disclosure(self):
        self.assertFalse(
            self.probe["inClosedDetails"],
            "the definition line sits inside a <details> that ships closed. A "
            "closed <details> still has a box and still carries textContent, "
            "so a source check would call this a pass while no reader could "
            "read it.")

    def test_the_line_has_rendered_text_a_reader_can_read(self):
        self.assertNotEqual(
            "", self.probe["text"],
            "#%s renders with EMPTY innerText (%sx%s, display:%s, "
            "visibility:%s)."
            % (DEF_ID, self.probe["w"], self.probe["h"],
               self.probe["display"], self.probe["visibility"]))
        self.assertGreater(self.probe["h"], 0,
                           "#%s has rendered text but zero height" % DEF_ID)
        self.assertEqual(
            DEF_TEXT, self.probe["text"],
            "the definition line rendered as %r. It has to say what an entry "
            "is and name the one row that is not a count of them, because "
            "Total Raised sums dollars and every other row counts records."
            % self.probe["text"])

    def test_the_rendered_ancestor_carries_the_line(self):
        self.assertIn(
            DEF_TEXT, self.probe["hostText"],
            "the definition line is not in the rendered innerText of its own "
            "parent, which means the parent is not being laid out")

    def test_the_line_uses_no_dashes(self):
        for bad in ("—", "–"):
            self.assertNotIn(
                bad, DEF_TEXT,
                "no em dashes or en dashes in UI copy. style_check.py needs "
                "12 characters and 3 real words before a string is eligible, "
                "so a short label slips past it.")


if __name__ == "__main__":
    unittest.main()
