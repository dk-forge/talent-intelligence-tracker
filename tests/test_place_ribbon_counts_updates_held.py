"""THE PLACE RIBBON COUNTS WHAT ITS CAPTION SAYS, MEASURED IN A LAYOUT ENGINE.

WHY THIS FILE EXISTS.

tests/test_place_ribbon_names_its_unit.py pinned the WORDS over the two place
rows: neither caption may say "Top", both must name their unit, and the basis
must be visible prose above the rows. It pinned nothing about the NUMBERS
underneath, and the numbers were counting something else.

Both rows were built under ``is_current = 1 AND tit_notable_where()``, which is
the default view, while the captions read "Countries by Updates Held" and
"Cities by Updates Held". Measured on the live table on 2026-08-13:

    ribbon  (notable clause)   GB 8,033   US 7,547
    /aggregate (no filter)     US 10,570  GB 8,047

3,023 United States rows were dropped by the clause, 3,020 of them routine
``sec_edgar`` ``leadership_change`` officer changes. The United Kingdom holds
14 routine rows in total, because its bulk is Companies House pay gap filings
graded ``medium``. So the ordering on that ribbon was decided by how each
country's dominant collector happens to be graded, and the page's own API
reported the pair the other way round. A routine officer change is an update we
hold; the caption is the contract; the count is now every current row.

WHAT IS PINNED, AND WHY EACH ASSERTION IS SHAPED THE WAY IT IS.

  * THE LOAD-BEARING ONE. Every country pill's number equals what /aggregate
    reports for that country, and every city pill's equals /aggregate's by_city.
    The endpoint is not re-implemented here: tests/php/render_dashboard.php
    invokes ``tit_api_aggregate`` out of the shipped api.php against the same
    fixture the page rendered from, and writes the response out. A test that
    rebuilt the GROUP BY by hand would only prove two copies agree.

  * NOTHING IS READ OUT OF MARKUP. The count is taken from ``innerText`` of the
    rendered button, so it is the string a reader actually sees after the
    cascade and the script have run, and every pill is required to have a real
    box: a control that measures 0x0, or that is clipped to nothing, prints a
    correct number nobody can read. This is the same reason
    test_control_boundaries.py exists.

  * THE CAPTION AND THE COUNT ARE CHECKED TOGETHER. Reading the caption from
    the same rendered ancestor as the numbers is what stops the pair drifting:
    if some future pass narrows the count again, the assertion that fails names
    the caption it now contradicts.

NO PHP OR NO CHROME MEANS SKIP, LOUDLY. Absence of a signal is not a pass.
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

from cdp import Browser, CDPUnavailable, find_chrome  # noqa: E402

PLUGIN = ROOT / "wordpress-plugin/talent-intelligence-tracker"
CSS = PLUGIN / "assets/dashboard.css"
JS = PLUGIN / "assets/dashboard.js"
HARNESS = ROOT / "tests/php/render_dashboard.php"

# The same theme shim and site override the other two browser tests use. A
# fixture without the site rule measures a page nobody is served.
THEME_SHIM = """
:root { --wp--preset--color--base:#fff; --wp--preset--color--contrast:#111; }
body { background-color: var(--wp--preset--color--base);
       color: var(--wp--preset--color--contrast);
       margin:0; font-family: system-ui, sans-serif; }
"""

SITE_OVERRIDE = """
.entry-content p,.wp-block-post-content p{font-size:1.05rem !important;line-height:1.78 !important;color:#2a2a2a !important;margin-bottom:1.2rem !important}
.entry-content h2,.wp-block-post-content h2{font-size:1.45rem !important;font-weight:700 !important;color:#1a1a1a !important;margin:2.2rem 0 .8rem !important;padding-bottom:.35rem !important;border-bottom:2px solid #eef3ee !important}
.entry-content h3,.wp-block-post-content h3{font-size:1.15rem !important;font-weight:600 !important;color:#222 !important;margin:1.5rem 0 .5rem !important}
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>%(theme)s</style><style>%(plugin)s</style><style>%(site)s</style>
</head><body class="wp-singular page">
<div class="wp-site-blocks"><main class="wp-block-group has-global-padding">
<div class="wp-block-group alignfull"><div class="entry-content alignfull">
%(frag)s
</div></div></main></div><script>%(js)s</script></body></html>
"""

# The ribbon as a reader meets it: the caption's own text, and for every pill
# its rendered text, its rendered box, and the key it filters by. Read off the
# ribbon's container so a stray .tit-cbtn elsewhere on the page cannot be
# mistaken for one of these, and so the caption travels with its own numbers.
RIBBON_JS = r"""
(function () {
  function box(el) {
    var r = el.getBoundingClientRect();
    return {w: r.width, h: r.height, left: r.left, right: r.right};
  }
  function pills(label, attr) {
    var group = document.querySelector('.tit-countries[aria-label="' + label + '"]');
    if (!group) return null;
    var cap = group.querySelector('.tit-countries-label');
    var out = {caption: cap ? (cap.innerText || '').trim() : '',
               box: box(group), items: []};
    Array.prototype.forEach.call(group.querySelectorAll('.tit-cbtn'), function (b) {
      out.items.push({key: b.getAttribute(attr),
                      text: (b.innerText || '').trim(),
                      box: box(b)});
    });
    return out;
  }
  return JSON.stringify({
    country: pills('Filter by country', 'data-code'),
    city: pills('Filter by city', 'data-city'),
    basis: (function () {
      var p = document.querySelector('.tit-places-basis');
      if (!p) return null;
      var r = p.getBoundingClientRect();
      return {text: (p.innerText || '').trim(), w: r.width, h: r.height};
    })()
  });
})()
"""


def _run_harness():
    """The real shortcode and the real /aggregate, off one fixture, one run."""
    php = shutil.which('php')
    if not php:
        raise unittest.SkipTest('no php on PATH: UNKNOWN, not a pass')
    frag_path = tempfile.mktemp(suffix='.html')
    agg_path = tempfile.mktemp(suffix='.json')
    env = dict(os.environ, TIT_DUMP_HTML=frag_path, TIT_DUMP_AGGREGATE=agg_path)
    proc = subprocess.run([php, str(HARNESS)], cwd=str(ROOT), env=env,
                          capture_output=True, text=True)
    if not os.path.exists(frag_path) or not os.path.exists(agg_path):
        raise unittest.SkipTest(
            'render_dashboard.php produced no markup or no /aggregate dump: %s'
            % (proc.stderr[-400:],))
    frag = open(frag_path, encoding='utf-8').read()
    agg = json.load(open(agg_path, encoding='utf-8'))
    os.unlink(frag_path)
    os.unlink(agg_path)
    return frag, agg


def _count(text):
    """The number a reader sees on the pill, out of its rendered text.

    Last numeric run, because the pill reads flag, place, count, and a place
    name can carry digits long before a count does.
    """
    numbers = re.findall(r'[\d,]*\d', text)
    if not numbers:
        return None
    return int(numbers[-1].replace(',', ''))


class PlaceRibbonCountsUpdatesHeld(unittest.TestCase):
    """One browser, one render, every assertion off the same measurement."""

    @classmethod
    def setUpClass(cls):
        if not find_chrome():
            raise unittest.SkipTest(
                'no Chrome: this is UNKNOWN, not a pass (CLAUDE.md)')
        frag, cls.agg = _run_harness()
        html = PAGE % {'theme': THEME_SHIM,
                       'plugin': CSS.read_text(encoding='utf-8'),
                       'site': SITE_OVERRIDE,
                       'frag': frag,
                       'js': JS.read_text(encoding='utf-8')}
        cls.page_path = tempfile.mktemp(suffix='.html')
        open(cls.page_path, 'w', encoding='utf-8').write(html)
        try:
            with Browser(width=1280, height=900) as page:
                page.navigate('file://' + cls.page_path, settle=1.5)
                cls.ribbon = json.loads(page.eval_js(RIBBON_JS))
        except CDPUnavailable as exc:
            raise unittest.SkipTest('Chrome would not drive: %s' % (exc,))

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.page_path)
        except Exception:
            pass

    def _aggregate(self, key):
        return {row['k']: int(row['n']) for row in self.agg.get(key, [])}

    def _row(self, which):
        row = self.ribbon.get(which)
        self.assertIsNotNone(
            row, "the %s ribbon did not render at all, so nothing below this "
                 "is asserting about anything" % which)
        self.assertTrue(
            row['items'],
            "the %s ribbon rendered no pills, so its counts cannot be checked "
            "against /aggregate" % which)
        return row

    # ---- the load-bearing pair -------------------------------------------

    def test_every_country_pill_equals_what_aggregate_reports(self):
        """The page's own ribbon and the page's own API, on one number each.

        They disagreed by 3,023 rows on the United States alone, and the
        disagreement decided which country the ribbon put first.
        """
        row = self._row('country')
        truth = self._aggregate('by_country')
        for item in row['items']:
            printed = _count(item['text'])
            self.assertIsNotNone(
                printed,
                "the %s pill renders no number at all: %r"
                % (item['key'], item['text']))
            self.assertIn(
                item['key'], truth,
                "the ribbon carries a pill for %s and /aggregate reports no "
                "such country. The two are reading different sets of rows."
                % (item['key'],))
            self.assertEqual(
                printed, truth[item['key']],
                "the %s pill reads %s under the caption %r, and /aggregate "
                "reports %s for the same country. A ribbon that counts "
                "something narrower than its own caption is the defect; the "
                "count is every current row, routine filings included."
                % (item['key'], f"{printed:,}", row['caption'],
                   f"{truth[item['key']]:,}"))

    def test_every_city_pill_equals_what_aggregate_reports(self):
        """Same contract on the row below, which had the same clause on it."""
        row = self._row('city')
        truth = self._aggregate('by_city')
        for item in row['items']:
            printed = _count(item['text'])
            self.assertIsNotNone(
                printed,
                "the %s pill renders no number at all: %r"
                % (item['key'], item['text']))
            self.assertIn(
                item['key'], truth,
                "the ribbon carries a pill for %s and /aggregate reports no "
                "such city" % (item['key'],))
            self.assertEqual(
                printed, truth[item['key']],
                "the %s pill reads %s under the caption %r, and /aggregate "
                "reports %s for the same city."
                % (item['key'], f"{printed:,}", row['caption'],
                   f"{truth[item['key']]:,}"))

    def test_the_ribbon_leads_with_the_place_aggregate_leads_with(self):
        """Order, not only arithmetic.

        Equal counts in a different order is still a ribbon that answers "where
        do we hold the most" differently from the endpoint beside it, and the
        order is the part a reader takes away.

        TIES ARE SORTED BY NAME ON BOTH SIDES BEFORE COMPARING. The ribbon's
        query breaks them with `ORDER BY n DESC, k ASC` and /aggregate's does
        not, so four fixture cities holding 480 rows each come back in whatever
        order the engine reached them. Comparing raw lists would fail on a
        difference no reader can see, and a test that fails for a reason that is
        not a defect is how a red run stops being read.
        """
        for which, key in (('country', 'by_country'), ('city', 'by_city')):
            row = self._row(which)
            truth = [r['k'] for r in
                     sorted(self.agg.get(key, []),
                            key=lambda r: (-int(r['n']), r['k']))]
            printed = [item['key'] for item in row['items']]
            self.assertEqual(
                printed, truth[:len(printed)],
                "the %s ribbon is ordered %s and /aggregate ranks them %s"
                % (which, printed, truth[:len(printed)]))

    # ---- the caption is checked with the numbers it sits over -------------

    def test_each_caption_names_updates_held_where_its_numbers_are(self):
        """Read off the rendered ancestor, so the words and the numbers that
        drift apart are caught in one measurement rather than two files."""
        for which in ('country', 'city'):
            caption = self._row(which)['caption']
            # Case-insensitive because this is the RENDERED string and the
            # stylesheet uppercases it. Matching the source casing here would
            # be reading the markup with extra steps.
            self.assertIn(
                'updates held', caption.lower(),
                "the %s ribbon's rendered caption is %r. These counts are "
                "every current row, and the caption is what makes that legible."
                % (which, caption))

    def test_the_basis_says_routine_filings_are_counted(self):
        """The one thing a reader cannot work out from the numbers.

        The table below opens on the default view, which sets routine filings
        aside, and the region strip above counts that same default view. So a
        country pill can read higher than the region containing it and higher
        than the rows a click returns, and the only honest answer to that is to
        say on the page what the ribbon counts.
        """
        basis = self.ribbon.get('basis')
        self.assertIsNotNone(basis, 'the basis line did not render')
        self.assertGreater(basis['w'] * basis['h'], 0,
                           'the basis line renders with no box, so it is prose '
                           'nobody is served: %r' % (basis['text'],))
        self.assertIn(
            'routine filings', basis['text'].lower(),
            "the basis line does not say that these counts include the routine "
            "filings the table sets aside, which is the whole reason a pill can "
            "read higher than the rows behind it: %r" % (basis['text'],))
        for ch, name in (('—', 'em dash'), ('–', 'en dash')):
            self.assertNotIn(ch, basis['text'],
                             'the basis line contains an %s' % name)

    # ---- a correct number nobody can read is not a correct number ---------

    def test_every_pill_has_a_real_box_inside_its_own_ribbon(self):
        """Geometry, because the count is only true if it is rendered.

        Both halves matter. A pill measuring 0x0 prints a right number into
        nothing, and a pill whose box escapes its ribbon is the horizontal
        bleed this page is held to on a phone.
        """
        for which in ('country', 'city'):
            row = self._row(which)
            for item in row['items']:
                box = item['box']
                self.assertGreater(
                    box['w'] * box['h'], 0,
                    "the %s pill in the %s ribbon renders a %gx%g box"
                    % (item['key'], which, box['w'], box['h']))
                self.assertLessEqual(
                    round(box['right']), round(row['box']['right']) + 1,
                    "the %s pill overflows its own ribbon to the right "
                    "(pill %g, ribbon %g)"
                    % (item['key'], box['right'], row['box']['right']))
