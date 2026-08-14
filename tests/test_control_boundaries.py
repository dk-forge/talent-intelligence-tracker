"""THE CONTROLS, MEASURED AS A READER MEETS THEM.

WHY THIS IS A SEPARATE FILE FROM test_rendered_contrast.py.

That file asks "can this text be read". This one asks "can this control be
SEEN AS A CONTROL", and those fail independently. Every text check in this
repository was green while the owner was reporting that the filter controls
"get lost", because a control's boundary is not text and nothing measured it:
Reset All rendered with no border on no fill, which is 1.00:1 -- not a weak
edge, no edge at all -- and every bordered control on the bar sat at 1.28:1 in
light and 1.56:1 in dark. A stylesheet read as text cannot see any of that.
Only a resolved cascade in a layout engine can.

THE BAR, AND WHY IT IS max() OF TWO RATIOS.

Crossing a control's edge a reader meets at most three colours: what is
outside, the border, and the fill. The edge is perceivable when the border
stands off ONE of its neighbours -- a border that matches the fill still shows
against the page, and one that matches the page still shows against the fill.
So the score is max(border vs outside, border vs fill), and where no border
paints the only edge left is fill against outside. Scoring the border against
one neighbour alone would fail correct designs; scoring the fill alone would
pass the transparent-border case that shipped.

THE MARKUP IS THE REAL MARKUP. It comes from tests/php/render_dashboard.php,
the harness that already renders the shipped shortcode against a synthetic
corpus, so this cannot drift from what the page emits the way a hand-copied
fixture does. That harness has an env hook for exactly this ("OPTIONAL: write
the rendered markup out, for measuring in a real browser").

NO PHP OR NO CHROME MEANS SKIP, LOUDLY. Absence of a signal is not a pass.
"""
import json
import os
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

# The bar. 3:1 is the WCAG 1.4.11 non-text contrast threshold, and it is the
# number the theme control was already held to at 1.74.2.
MIN_BOUNDARY = 3.0
# WCAG 2.5.5 / 2.5.8. Applied to the CONTROLS, not to inline text links, which
# 2.5.8 exempts and which this page has ninety-odd of inside sentences.
MIN_TAP = 44

# The reader's three real states: an explicit light choice, an explicit dark
# choice, and Auto (no choice at all) on a dark OS, which is the default and
# the combination that shipped the unreadable page in the sibling.
THEMES = (
    ("light", "light", None),
    ("dark", "dark", "dark"),
    ("auto-on-dark-os", "dark", None),
)

FREEZE_CSS = """
*, *::before, *::after { transition:none !important; animation:none !important; }
"""

# The same site-level override test_rendered_contrast.py reproduces: a rule
# that lives in the WordPress database, in neither repo, and beats every token
# this plugin owns. A fixture without it measures a page nobody is served.
SITE_OVERRIDE = """
.entry-content p,.wp-block-post-content p{font-size:1.05rem !important;line-height:1.78 !important;color:#2a2a2a !important;margin-bottom:1.2rem !important}
.entry-content h2,.wp-block-post-content h2{font-size:1.45rem !important;font-weight:700 !important;color:#1a1a1a !important;margin:2.2rem 0 .8rem !important;padding-bottom:.35rem !important;border-bottom:2px solid #eef3ee !important}
.entry-content h3,.wp-block-post-content h3{font-size:1.15rem !important;font-weight:600 !important;color:#222 !important;margin:1.5rem 0 .5rem !important}
"""

THEME_SHIM = """
:root { --wp--preset--color--base:#fff; --wp--preset--color--contrast:#111; }
body { background-color: var(--wp--preset--color--base);
       color: var(--wp--preset--color--contrast);
       margin:0; font-family: system-ui, sans-serif; }
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

# WHAT THE PAGE MEASURES AS ON A NARROW SCREEN. Three questions in one pass,
# because each needs the same rendered tree: what paints past the right edge
# with nothing to scroll it, which chart wrappers still scroll, and which
# controls a thumb cannot land on.
GEOMETRY_JS = r"""
(function () {
  // The LAYOUT viewport, not window.innerWidth. A document that overflows
  // sideways widens innerWidth to cover the overflow, so measuring against it
  // reports a clean page at the exact moment the page is broken.
  var doc = document.documentElement;
  var W = Math.min(window.innerWidth, doc.clientWidth);
  var out = {bleed: [], small: [], chartScroll: [],
             docOverflow: doc.scrollWidth - doc.clientWidth};
  function cs(el) { return getComputedStyle(el); }
  function sig(el) {
    var c = (typeof el.className === 'string') ? el.className : '';
    return el.tagName.toLowerCase() + (c ? '.' + c.trim().split(/\s+/)
      .slice(0, 2).join('.') : '');
  }
  function clippedByAncestor(el) {
    for (var p = el.parentElement; p && p !== document.body; p = p.parentElement)
      if (/(auto|scroll|hidden|clip)/.test(cs(p).overflowX)) return true;
    return false;
  }
  function shown(el) {
    var s = cs(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    return el.getClientRects().length > 0;
  }
  Array.prototype.forEach.call(document.querySelectorAll('.tit-wrap, .tit-wrap *'),
    function (el) {
      if (!shown(el)) return;
      var r = el.getBoundingClientRect();
      if (r.width <= 2 || r.height <= 2) return;
      if (r.right <= W + 2) return;
      if (clippedByAncestor(el)) return;          // scrolls inside its own box
      // Report the OUTERMOST offender only: a cell and the six spans inside it
      // are one defect, not seven.
      if (el.parentElement &&
          el.parentElement.getBoundingClientRect().right > W + 2) return;
      out.bleed.push({sel: sig(el), l: Math.round(r.left), r: Math.round(r.right)});
    });
  Array.prototype.forEach.call(document.querySelectorAll('.tit-chart-scroll'),
    function (el) {
      if (!shown(el)) return;
      out.chartScroll.push({ovx: cs(el).overflowX,
                            w: Math.round(el.getBoundingClientRect().width),
                            scrollW: el.scrollWidth});
    });
  Array.prototype.forEach.call(document.querySelectorAll(
      '.tit-wrap a, .tit-wrap button, .tit-wrap select, .tit-wrap summary,'
      + ' .tit-wrap input:not([type=hidden])'), function (el) {
    if (!shown(el)) return;
    var r = el.getBoundingClientRect();
    if (!r.height || !r.width) return;
    if (r.height >= 44) return;
    // 2.5.8 exempts a target inside a sentence. The place directory is a run
    // of links that are each their own line, so it is not exempt.
    var inline = cs(el).display === 'inline'
      && el.closest('p, li, td, th, figcaption, small, dd, dt');
    if (inline && !el.closest('.tit-place-list')) return;
    out.small.push({sel: sig(el), w: Math.round(r.width), h: Math.round(r.height)});
  });
  return JSON.stringify(out);
})()
"""

BOUNDARY_JS = r"""
(function () {
  function parse(c) {
    var m = /^rgba?\(([^)]+)\)$/.exec(c || '');
    if (!m) return null;
    var p = m[1].split(',').map(parseFloat);
    return {r:p[0], g:p[1], b:p[2], a:p.length>3?p[3]:1};
  }
  function over(f, b) {
    var a = f.a + b.a*(1-f.a);
    if (!a) return {r:0,g:0,b:0,a:0};
    return {r:(f.r*f.a+b.r*b.a*(1-f.a))/a, g:(f.g*f.a+b.g*b.a*(1-f.a))/a,
            b:(f.b*f.a+b.b*b.a*(1-f.a))/a, a:a};
  }
  function lum(c) {
    var v = [c.r,c.g,c.b].map(function(x){ x/=255;
      return x<=0.03928 ? x/12.92 : Math.pow((x+0.055)/1.055, 2.4); });
    return 0.2126*v[0] + 0.7152*v[1] + 0.0722*v[2];
  }
  function ratio(a, b) {
    var l1 = lum(a), l2 = lum(b);
    return (Math.max(l1,l2)+0.05) / (Math.min(l1,l2)+0.05);
  }
  function ground(el) {           // composite down to something opaque
    var stack = [], n = el;
    while (n && n.nodeType === 1) {
      var bg = parse(getComputedStyle(n).backgroundColor);
      if (bg && bg.a > 0) { stack.push(bg); if (bg.a === 1) break; }
      n = n.parentElement;
    }
    var out = {r:255,g:255,b:255,a:1};
    for (var i = stack.length-1; i >= 0; i--) out = over(stack[i], out);
    return out;
  }
  var panel = document.getElementById('tit-panel');
  if (!panel) return JSON.stringify({error:'no filter panel in the markup'});
  var out = [];
  Array.prototype.forEach.call(
    panel.querySelectorAll('select, input:not([type=hidden]), textarea, button'),
    function (el) {
      var r = el.getBoundingClientRect(), cs = getComputedStyle(el);
      if (!r.width || !r.height || cs.visibility === 'hidden' ||
          cs.display === 'none') return;
      var outside = el.parentElement ? ground(el.parentElement)
                                     : {r:255,g:255,b:255,a:1};
      var fill = over(parse(cs.backgroundColor) || {r:0,g:0,b:0,a:0}, outside);
      var bw = parseFloat(cs.borderTopWidth) || 0;
      var bc = parse(cs.borderTopColor);
      var paints = bw > 0 && bc && bc.a > 0 && cs.borderTopStyle !== 'none';
      var score, how;
      if (paints) {
        var border = over(bc, outside);
        score = Math.max(ratio(border, outside), ratio(border, fill));
        how = 'border';
      } else {
        score = ratio(fill, outside);
        how = 'no border painted, so fill vs outside';
      }
      out.push({name: el.id || String(el.className||'').slice(0,30) || el.tagName,
                how: how, score: Math.round(score*100)/100,
                w: Math.round(r.width*10)/10, h: Math.round(r.height*10)/10,
                fontSize: cs.fontSize, radius: cs.borderTopLeftRadius});
    });
  return JSON.stringify(out);
})()
"""

# What a reader can actually READ. A display:none subtree returns textContent
# from its own innerText, so asking the hidden element is a lie -- this reads
# innerText off the RENDERED panel, which excludes non-rendered descendants.
STATE_JS = r"""
(function () {
  var panel = document.getElementById('tit-panel');
  var body  = document.getElementById('tit-panel-body');
  function vis(el){ if(!el) return false; var r=el.getBoundingClientRect(),
    cs=getComputedStyle(el);
    return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&cs.display!=='none'; }
  var n = 0;
  Array.prototype.forEach.call(
    panel.querySelectorAll('select,input:not([type=hidden]),textarea,button'),
    function(e){ if (vis(e)) n++; });
  return JSON.stringify({
    bodyRendered: vis(body),
    collapsed: /is-collapsed/.test(panel.className),
    visibleControls: n,
    panelInnerTextLen: (panel.innerText||'').trim().length
  });
})()
"""


def _render_markup():
    """The real shortcode, through the real PHP."""
    php = shutil.which('php')
    if not php:
        raise unittest.SkipTest('no php on PATH: UNKNOWN, not a pass')
    dest = tempfile.mktemp(suffix='.html')
    env = dict(os.environ, TIT_DUMP_HTML=dest)
    p = subprocess.run([php, str(HARNESS)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True)
    if not os.path.exists(dest):
        raise unittest.SkipTest(
            'render_dashboard.php produced no markup: %s' % (p.stderr[-400:],))
    frag = open(dest, encoding='utf-8').read()
    os.unlink(dest)
    return frag


def _build_page():
    html = PAGE % {'theme': THEME_SHIM,
                   'plugin': CSS.read_text(encoding='utf-8'),
                   'site': SITE_OVERRIDE,
                   'frag': _render_markup(),
                   'js': JS.read_text(encoding='utf-8')}
    path = tempfile.mktemp(suffix='.html')
    open(path, 'w', encoding='utf-8').write(html)
    return path


class ControlBoundaries(unittest.TestCase):
    """One browser, reused: launching Chrome per assertion is most of the
    runtime and none of the evidence."""

    @classmethod
    def setUpClass(cls):
        if not find_chrome():
            raise unittest.SkipTest(
                'no Chrome: this is UNKNOWN, not a pass (CLAUDE.md)')
        cls.page_path = _build_page()
        cls.url = 'file://' + cls.page_path

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.page_path)
        except Exception:
            pass

    def _measure(self, width, height, os_scheme, attr):
        with Browser(width=width, height=height) as p:
            p.call('Emulation.setEmulatedMedia', {
                'features': [{'name': 'prefers-color-scheme',
                              'value': os_scheme}]})
            p.navigate(self.url, settle=1.5)
            p.eval_js("(function(){var s=document.createElement('style');"
                      "s.textContent=%s;document.head.appendChild(s);"
                      "return 1})()" % json.dumps(FREEZE_CSS))
            if attr is None:
                p.eval_js("(function(){document.documentElement"
                          ".removeAttribute('data-theme');return 1})()")
            else:
                p.eval_js("(function(){document.documentElement"
                          ".setAttribute('data-theme',%s);return 1})()"
                          % json.dumps(attr))
            rows = json.loads(p.eval_js(BOUNDARY_JS))
            state = json.loads(p.eval_js(STATE_JS))
        return rows, state

    def test_every_control_has_a_perceivable_edge(self):
        """The defect the owner reported, as a number, in all three states."""
        for width, height in ((1280, 900), (375, 812)):
            for name, os_scheme, attr in THEMES:
                rows, _ = self._measure(width, height, os_scheme, attr)
                self.assertNotIsInstance(
                    rows, dict, 'the probe found no filter panel')
                self.assertTrue(rows, 'no controls measured at %dpx' % width)
                worst = min(rows, key=lambda r: r['score'])
                self.assertGreaterEqual(
                    worst['score'], MIN_BOUNDARY,
                    '%s at %dpx: the control %r has a boundary of %.2f:1 '
                    '(%s), under the %.1f:1 a control edge has to clear. '
                    'Crossing its edge a reader meets at most three colours, '
                    'and none of the pairs stand apart. This is the check '
                    'every TEXT contrast test passes while the control is '
                    'still invisible as a control.'
                    % (name, width, worst['name'], worst['score'],
                       worst['how'], MIN_BOUNDARY))

    def test_the_controls_are_one_size_and_not_five(self):
        """A standard nobody can check is a standard that regresses. Before
        this pass the bar carried four type sizes and three heights across
        controls doing the same job."""
        rows, _ = self._measure(1280, 900, 'light', None)
        sizes = sorted({r['fontSize'] for r in rows})
        radii = sorted({r['radius'] for r in rows})
        self.assertLessEqual(
            len(sizes), 2,
            'the filter controls use %d type sizes (%s). One control standard '
            'means one size; a second is only tolerated because the loading '
            'retry button is not a filter.' % (len(sizes), ', '.join(sizes)))
        self.assertLessEqual(
            len(radii), 2,
            'the filter controls use %d corner radii (%s), so the bar reads '
            'as several kinds of thing.' % (len(radii), ', '.join(radii)))

    def test_the_filter_panel_ships_open(self):
        """The owner: "i like when both filters are just showing so it's
        obvious and not hidden on both trackers."

        Asserted on what a reader can READ, not on what the markup contains.
        A collapsed panel still has a box and still has textContent, and
        innerText falls back to textContent on a non-rendered subtree, so the
        only honest reading is innerText off the RENDERED ancestor."""
        for width, height in ((1280, 900), (375, 812)):
            _, state = self._measure(width, height, 'light', None)
            self.assertFalse(
                state['collapsed'],
                'the filter panel ships collapsed at %dpx' % width)
            self.assertTrue(
                state['bodyRendered'],
                'the filter panel body is not rendered at %dpx, so a reader '
                'has to know the filters are there before they can find out '
                'what they filter' % width)
            self.assertGreaterEqual(
                state['visibleControls'], 8,
                'only %d controls are visible at %dpx with the panel as '
                'served; the panel reads as empty chrome'
                % (state['visibleControls'], width))
            self.assertGreater(
                state['panelInnerTextLen'], 100,
                'the panel renders %d characters a reader can actually read '
                'at %dpx. A closed panel still has textContent, so this is '
                'read as innerText off the rendered element.'
                % (state['panelInnerTextLen'], width))

    def _geometry(self, width, height):
        with Browser(width=width, height=height) as p:
            p.navigate(self.url, settle=1.5)
            return json.loads(p.eval_js(GEOMETRY_JS))

    def test_nothing_bleeds_past_the_screen_edge(self):
        """A DEVICE SWEEP ON 2026-08-14 FOUND THE CHART PAINTED OFF-SCREEN.

        `.tit-table-scroll` drops its scrollbar under 860px because a TABLE
        becomes cards there. The market chart is wrapped in the same shared
        box and keeps min-width:520px, so on a 375px phone 174px of it was
        painted past the right edge with nothing to scroll: not a table that
        had become cards, a drawing with half of itself unreachable.

        Measured, not read: an element is only a defect here if NO ancestor
        clips or scrolls it. A wide table inside its own scroll box is the
        correct answer and must not be reported as a failure."""
        for width, height in ((375, 812), (414, 896), (768, 1024)):
            bleed = self._geometry(width, height)['bleed']
            self.assertEqual(
                bleed, [],
                'at %dpx these paint past the right edge of the screen with '
                'no ancestor that scrolls or clips them, so a reader cannot '
                'reach what is cut off: %s'
                % (width, ', '.join('%s (%d..%d)' % (b['sel'], b['l'], b['r'])
                                    for b in bleed[:6])))

    def test_a_drawing_keeps_its_scroll_box_on_a_phone(self):
        """The fix above, stated as the rule rather than as its effect: the
        chart's wrapper still scrolls at a phone width. If a later pass takes
        the opt-in class off the wrapper, this fails before the bleed does."""
        for width, height in ((375, 812), (768, 1024)):
            g = self._geometry(width, height)
            self.assertTrue(
                g['chartScroll'],
                'no .tit-chart-scroll wrapper rendered at %dpx: the chart '
                'is back inside a box that stops scrolling under 860px'
                % width)
            for box in g['chartScroll']:
                self.assertIn(
                    box['ovx'], ('auto', 'scroll'),
                    'the chart wrapper has overflow-x:%s at %dpx, so the part '
                    'of the drawing wider than the screen cannot be reached'
                    % (box['ovx'], width))

    def test_a_long_token_cannot_widen_a_card_off_the_screen(self):
        """ONE WORD DECIDED THE WIDTH OF THE CARD.

        A flex item's automatic minimum size is its min-content width. One
        source note carries the query string
        `dept=innovationsciencesandeconomicdevelopmentcanada`, and at 375px
        that single 51-character word made its cell 362px wide inside a 307px
        row, pushing the card and the link beside it off the right of the
        screen (/sources/, measured live 2026-08-14).

        The fixture is the real class names in the real nesting; the token is
        the real token. `overflow-wrap:anywhere` is what fixes it, and only
        `anywhere` counts toward min-content, which is the measurement that
        was wrong."""
        frag = (
            '<div class="tit-wrap tit-sources"><div class="tit-table-scroll">'
            '<table class="tit-table"><thead><tr><th>Source</th></tr></thead>'
            '<tbody><tr><td class="tit-headline">'
            '<span class="tit-h"><a href="#">Government of Canada newsroom '
            '(all depts)</a></span> <span class="tit-rt">LIVE. Atom API over '
            'all departments; the per-department feed is '
            'dept=innovationsciencesandeconomicdevelopmentcanada and it is '
            'one word.</span></td></tr></tbody></table></div></div>')
        html = PAGE % {'theme': THEME_SHIM,
                       'plugin': CSS.read_text(encoding='utf-8'),
                       'site': SITE_OVERRIDE, 'frag': frag, 'js': ''}
        path = tempfile.mktemp(suffix='.html')
        open(path, 'w', encoding='utf-8').write(html)
        try:
            for width in (375, 414):
                with Browser(width=width, height=812) as p:
                    p.navigate('file://' + path, settle=0.8)
                    g = json.loads(p.eval_js(GEOMETRY_JS))
                self.assertEqual(
                    g['docOverflow'], 0,
                    'at %dpx one long token in a source note makes the whole '
                    'document %dpx wider than the screen, so the page scrolls '
                    'sideways' % (width, g['docOverflow']))
                self.assertEqual(
                    g['bleed'], [],
                    'at %dpx one long token in a source note pushes the card '
                    'past the screen edge: %s'
                    % (width, ', '.join('%s (%d..%d)' % (b['sel'], b['l'],
                                                         b['r'])
                                        for b in g['bleed'][:4])))
        finally:
            os.unlink(path)

    def test_every_control_in_the_content_is_thumb_sized_on_a_phone(self):
        """The test below holds the FILTER controls to 44px. This holds every
        other control a reader drives, which is where the 2026-08-14 sweep
        found them: ranking rows at 34px, at-a-glance cells at 36px, chart
        icon buttons at 28px, disclosures at 21px, the export links at 30px.
        Links inside a sentence keep 2.5.8's inline exception and are not
        counted here; the place directory is a run of links that are each
        their own line, so it is."""
        for width, height in ((375, 812), (414, 896)):
            small = self._geometry(width, height)['small']
            self.assertEqual(
                small, [],
                'these controls are under %dpx tall at %dpx: %s'
                % (MIN_TAP, width,
                   ', '.join('%s (%dx%d)' % (s['sel'], s['w'], s['h'])
                             for s in small[:12])))

    def test_every_control_is_a_thumb_sized_target_on_a_phone(self):
        rows, _ = self._measure(375, 812, 'light', None)
        small = [r for r in rows if r['w'] < MIN_TAP or r['h'] < MIN_TAP]
        self.assertEqual(
            small, [],
            'these filter controls are under %dx%d on a 375px phone: %s'
            % (MIN_TAP, MIN_TAP,
               ', '.join('%s (%.0fx%.0f)' % (r['name'], r['w'], r['h'])
                         for r in small)))


if __name__ == '__main__':
    unittest.main()
