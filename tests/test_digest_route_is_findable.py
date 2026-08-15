"""THE EMAIL DIGEST HAS A ROUTE ON THE FIRST SCREEN OF THE DASHBOARD.

WHY THIS FILE EXISTS. tests/test_digest_signup_embed.py already pins that the
dashboard RENDERS the shared signup. It renders it at the foot of the page,
and until 2026-08-14 nothing above it said so. Measured off the live
dashboard, bare URL, browser User-Agent, no cache buster:

    viewport     signup section top    document height
    1280 x 900   13,995px              15,093px
     375 x 812   32,224px              33,895px

Sixteen screens down on a desktop and forty on a phone. The sibling learned
this defect on its press kit (13,252px and 31,707px down) and the fix that
worked was a control in the hero, beside the actions that were already there.
This is that fix, here.

WHAT IS PINNED, all of it reader-visible and all of it measured off a real
headless Chrome render of the real shortcode:

  * the digest route renders WITHIN THE FIRST VIEWPORT at 375x812 and at
    1280x900. This is the assertion that fails on the pre-fix tree;
  * it is a control a thumb can hit: 44x44 at 375 and at 414, 8px clear of
    its neighbours;
  * it says "Email digest", which is the h2 of the thing it opens, behind a
    tag saying how often. Found by rendered innerText, never by class;
  * its boundary clears 3:1 and its two pieces of text clear 4.5:1, measured
    COMPOSITED against what is actually painted behind them, in light,
    dark-by-choice and dark-by-OS, with the pointer off the control and with
    the pointer on it. The hover is not decoration: the neighbouring
    .tit-cta-how:hover repaints its edge in --tit-blue, and inheriting that
    on a blue-tinted fill would dissolve this control's outline at the moment
    somebody was using it, with every text check on the page still green;
  * the hero heading stays above it, so no width lets this push the thesis
    the page opens with;
  * no horizontal document overflow at 375, 414, 768, 1024 or 1280.

WHAT THIS FILE DELIBERATELY DOES NOT MEASURE, and where it is measured
instead. The signup itself is the SIBLING plugin's component: this repo holds
a `function_exists`-guarded call and no markup, by the isolation promise at
the top of talent-intelligence-tracker.php, so the harness renders a page with
no #alt-digest on it and any landing assertion here would be measuring a
fixture nobody is served. The landing was measured on the LIVE dashboard
instead, at 2026-08-14, by following the hash with the page settled:

    375 x 812   section top 0px, email field ends 697px down
    1280 x 900  section top 0px, email field ends 474px down

That is the check the sibling's press page failed when its own jump menu
ended 847px down an 812px screen, and it is why the anchor takes no scroll
offset here: nothing on this page declares one, and the existing "How this is
built" jump to #tit-trust behaves identically. The signup's own height budget
is pinned in the sibling's test of the same name.

NO PHP OR NO CHROME MEANS SKIP, LOUDLY. Absence of a signal is not a pass.

PROVEN TO FAIL ON THE PRE-FIX TREE (8149fad), with these assertions:

    at 375x812 nothing a reader can see and click says 'Email digest'
    at 414x896 nothing a reader can see and click says 'Email digest'
    at 1280x900 nothing a reader can see and click says 'Email digest'
    dashboard.css declares no rule '.tit-wrap .tit-cta-digest'
    dashboard.css declares no rule '.tit-wrap .tit-cta-digest:hover'

The no-overflow test and the hero-heading test passed there and are named
rather than left to look like proof they are not: both describe something the
old tree already had and this change had to preserve.
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

from test_control_boundaries import (  # noqa: E402
    CSS, FREEZE_CSS, JS, PAGE, SITE_OVERRIDE, THEME_SHIM, THEMES,
    _render_markup,
)

MIN_TAP = 44.0
MIN_GAP = 8.0
MIN_BOUNDARY = 3.0    # WCAG 1.4.11
MIN_TEXT = 4.5        # WCAG 1.4.3

#: What the button has to say. The first is the h2 of the shared signup this
#: opens (sibling plugin, includes/subscribe.php); the second is the answer to
#: the first question anybody asks of a signup, in the words its own radio
#: buttons use. Cross-repo, so it is a constant here and the sibling's test of
#: the same name reads it off the component.
LABEL = "email digest"
CADENCE = "weekly or daily"
ANCHOR = "#alt-digest"

#: Every laid-out control on the page, with the two things a reader has: where
#: it is and what it says. Selection by RENDERED TEXT, never by class, so a
#: rename cannot make this file measure nothing and report a pass. innerText,
#: never textContent: a collapsed panel still carries textContent for text
#: nobody can read.
FIND_JS = r"""
(function () {
  var out = [];
  Array.prototype.forEach.call(
    document.querySelectorAll('.tit-wrap a[href], .tit-wrap button,'
                              + ' .tit-wrap [role="button"]'),
    function (el) {
      var cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      var r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      out.push({text: (el.innerText || '').replace(/\s+/g, ' ').trim(),
                href: el.getAttribute('href') || '',
                x: +r.x.toFixed(1), y: +(r.y + window.scrollY).toFixed(1),
                w: +r.width.toFixed(1), h: +r.height.toFixed(1),
                display: cs.display});
    });
  var head = document.querySelector('.tit-hero-head h2');
  var hr = head ? head.getBoundingClientRect() : null;
  var doc = document.documentElement;
  return JSON.stringify({
    controls: out, viewport: window.innerHeight,
    docOverflow: +(doc.scrollWidth - doc.clientWidth).toFixed(1),
    heading: hr ? {y: +(hr.y + window.scrollY).toFixed(1),
                   h: +hr.height.toFixed(1)} : null});
})()
"""

#: The composited truth for one control, found by what it says. Same
#: compositing walk test_control_boundaries.py uses: alpha-blend down the
#: ancestor chain until something opaque, because a token cannot tell you what
#: a translucent fill is actually sitting on.
PAINT_JS = r"""
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
  function ground(el) {
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
  var btn = null;
  Array.prototype.forEach.call(document.querySelectorAll('.tit-hero-cta a,'
      + ' .tit-hero-cta button'), function (el) {
    if (!btn && (el.innerText||'').toLowerCase().indexOf(%(label)s) >= 0)
      btn = el;
  });
  if (!btn) return JSON.stringify({missing: true});
  var cs = getComputedStyle(btn);
  var outside = ground(btn.parentElement);
  var fill = over(parse(cs.backgroundColor) || {r:0,g:0,b:0,a:0}, outside);
  var bw = parseFloat(cs.borderTopWidth) || 0;
  var bc = parse(cs.borderTopColor);
  var paints = bw > 0 && bc && bc.a > 0 && cs.borderTopStyle !== 'none';
  var boundary, how;
  if (paints) {
    var border = over(bc, outside);
    boundary = Math.max(ratio(border, outside), ratio(border, fill));
    how = 'border';
  } else {
    boundary = ratio(fill, outside);
    how = 'no border painted, so fill vs outside';
  }
  var tag = btn.querySelector('span');
  var out = {boundary: Math.round(boundary*100)/100, how: how,
             label: Math.round(ratio(over(parse(cs.color), fill), fill)*100)/100};
  if (tag) out.tag = Math.round(
    ratio(over(parse(getComputedStyle(tag).color), fill), fill)*100)/100;
  var r = btn.getBoundingClientRect();
  out.centre = [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)];
  return JSON.stringify(out);
})()
"""


def paint_js():
    return PAINT_JS % {"label": json.dumps(LABEL)}


def build_page():
    html = PAGE % {"theme": THEME_SHIM,
                   "plugin": CSS.read_text(encoding="utf-8"),
                   "site": SITE_OVERRIDE,
                   "frag": _render_markup(),
                   "js": JS.read_text(encoding="utf-8")}
    path = tempfile.mktemp(suffix=".html")
    open(path, "w", encoding="utf-8").write(html)
    return path


class _Rendered(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not shutil.which("php"):
            raise unittest.SkipTest("no php on PATH: UNKNOWN, not a pass")
        if not find_chrome():
            raise unittest.SkipTest(
                "no Chrome: this is UNKNOWN, not a pass (CLAUDE.md)")
        cls.page_path = build_page()
        cls.url = "file://" + cls.page_path
        cls._cache = {}

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.page_path)
        except Exception:
            pass

    def rendered(self, width, height):
        key = (width, height)
        if key in self._cache:
            return self._cache[key]
        try:
            with Browser(width=width, height=height) as p:
                p.navigate(self.url, settle=1.0)
                p.eval_js("(function(){var s=document.createElement('style');"
                          "s.textContent=%s;document.head.appendChild(s);"
                          "return 1})()" % json.dumps(FREEZE_CSS))
                data = json.loads(p.eval_js(FIND_JS))
        except CDPUnavailable as exc:
            raise unittest.SkipTest("could not launch Chrome: %s" % exc)
        self._cache[key] = data
        return data

    def route(self, width, height):
        data = self.rendered(width, height)
        hits = sorted([c for c in data["controls"]
                       if LABEL in c["text"].lower()], key=lambda c: c["y"])
        self.assertTrue(
            hits,
            "at %dx%d nothing a reader can see and click says %r. The route "
            "to the signup is either absent or renders no text."
            % (width, height, LABEL))
        first = hits[0]
        on_screen = [c for c in hits if c["y"] + c["h"] < data["viewport"]]
        self.assertLessEqual(
            len(on_screen), 1,
            "at %dpx the first screen offers %d controls all saying %r. One "
            "destination under one name, twice on one screen, reads as two."
            % (width, len(on_screen), LABEL))
        return first, data


class TheDigestRouteIsOnTheFirstScreen(_Rendered):

    def test_a_reader_meets_it_on_the_first_screen_on_a_phone(self):
        ctl, data = self.route(375, 812)
        self.assertLess(
            ctl["y"] + ctl["h"], data["viewport"],
            "at 375x812 the digest route's bottom edge is %.0fpx down, so a "
            "reader has to scroll to be offered the emails. Measured live on "
            "2026-08-14 the signup itself was 32,224px down a 33,895px "
            "document, which is forty screens." % (ctl["y"] + ctl["h"]))

    def test_a_reader_meets_it_on_the_first_screen_on_a_desktop(self):
        ctl, data = self.route(1280, 900)
        self.assertLess(
            ctl["y"] + ctl["h"], data["viewport"],
            "at 1280x900 the digest route's bottom edge is %.0fpx down, so it "
            "is below the fold on the widest layout this page has. Measured "
            "live on 2026-08-14 the signup was 13,995px down."
            % (ctl["y"] + ctl["h"]))

    def test_the_hero_heading_stays_above_it(self):
        """A regression bar: the thesis the page opens with cannot be pushed
        down by an action placed under it."""
        for width, height in ((375, 812), (1280, 900)):
            ctl, data = self.route(width, height)
            head = data["heading"]
            self.assertIsNotNone(
                head, "at %dpx the hero heading did not render" % width)
            self.assertLessEqual(
                head["y"] + head["h"], ctl["y"],
                "at %dpx the digest route starts at %.0fpx, above the bottom "
                "of the hero heading at %.0fpx"
                % (width, ctl["y"], head["y"] + head["h"]))


class TheDigestRouteIsAControlAThumbCanHit(_Rendered):

    def test_it_clears_the_tap_floor_on_both_phone_widths(self):
        for width, height in ((375, 812), (414, 896)):
            ctl, _ = self.route(width, height)
            self.assertGreaterEqual(
                ctl["h"], MIN_TAP,
                "the digest route is %.1fpx tall at %dpx, under the %.0fpx "
                "floor (WCAG 2.5.5): %r" % (ctl["h"], width, MIN_TAP,
                                            ctl["text"]))
            self.assertGreaterEqual(
                ctl["w"], MIN_TAP,
                "the digest route is %.1fpx wide at %dpx, under the %.0fpx "
                "floor" % (ctl["w"], width, MIN_TAP))
            self.assertNotEqual(
                "inline", ctl["display"],
                "the digest route lays out as an inline box, which is what a "
                "link inside a sentence does: %r" % ctl["text"])

    def test_it_is_8px_clear_of_its_neighbours_at_375(self):
        ctl, data = self.route(375, 812)
        bad = []
        for other in data["controls"]:
            if (other["x"], other["y"]) == (ctl["x"], ctl["y"]):
                continue
            dx = max(0.0, max(ctl["x"] - (other["x"] + other["w"]),
                              other["x"] - (ctl["x"] + ctl["w"])))
            dy = max(0.0, max(ctl["y"] - (other["y"] + other["h"]),
                              other["y"] - (ctl["y"] + ctl["h"])))
            if dx > 0 and dy > 0:
                continue           # diagonal: a missed tap lands on the page
            if dx + dy < MIN_GAP - 0.05:
                bad.append("%.1fpx from %r" % (dx + dy, other["text"][:40]))
        self.assertEqual(
            [], bad,
            "the digest route sits under %.0fpx from a neighbouring control, "
            "so a thumb aimed at it takes the other one:\n  %s"
            % (MIN_GAP, "\n  ".join(bad)))


class TheDigestRouteSaysWhatItOpens(_Rendered):

    def test_it_carries_the_signups_own_heading(self):
        ctl, _ = self.route(1280, 900)
        self.assertIn(
            LABEL, ctl["text"].lower(),
            "the digest route reads %r. The thing it opens is headed 'Email "
            "digest'; a second name for one destination is how a reader "
            "concludes they are two." % ctl["text"])

    def test_it_says_how_often_without_renaming_the_signup(self):
        ctl, _ = self.route(1280, 900)
        self.assertIn(
            CADENCE, ctl["text"].lower(),
            "the button reads %r. 'How often will you email me' is the first "
            "question anybody asks of a signup, and the answer is a choice "
            "the form already offers." % ctl["text"])

    def test_it_points_at_the_shared_signups_anchor(self):
        ctl, _ = self.route(1280, 900)
        self.assertEqual(
            ANCHOR, ctl["href"],
            "the digest route points at %r. The shared signup renders as "
            "<section id=\"alt-digest\"> (sibling plugin, "
            "includes/subscribe.php), so any other target is a jump to "
            "nothing." % ctl["href"])

    def test_the_copy_carries_no_dash_a_style_check_would_miss(self):
        """style_check.py needs a length and a word count before a string is
        eligible, so a short button label slips past it entirely."""
        ctl, _ = self.route(1280, 900)
        for ch, name in (("—", "em dash"), ("–", "en dash")):
            self.assertNotIn(
                ch, ctl["text"],
                "the digest button copy %r carries an %s" % (ctl["text"], name))


class NothingBleedsSidewaysAtAnyWidth(_Rendered):
    """A regression bar. The hero row gained a third action; a control that
    cannot wrap widens the document instead of wrapping."""

    def test_no_horizontal_document_overflow(self):
        bad = []
        for width in (375, 414, 768, 1024, 1280):
            data = self.rendered(width, 812 if width < 768 else 900)
            if data["docOverflow"] > 0.5:
                bad.append("%dpx: document is %.1fpx wider than the viewport"
                           % (width, data["docOverflow"]))
        self.assertEqual([], bad,
                         "the page bleeds sideways:\n  " + "\n  ".join(bad))


class TheDigestRouteHasAVisibleBoundaryInEveryTheme(unittest.TestCase):
    """1.4.11 and 1.4.3, composited, pointer off AND pointer on.

    Read from the painted pixels rather than from the tokens: a token cannot
    tell you what a translucent fill ends up sitting on, and this repo has
    already shipped a control whose declared edge was correct and whose
    rendered edge was 1.00:1.
    """

    RULES = (".tit-wrap .tit-cta-digest", ".tit-wrap .tit-cta-digest:hover")

    @classmethod
    def setUpClass(cls):
        if not shutil.which("php"):
            raise unittest.SkipTest("no php on PATH: UNKNOWN, not a pass")
        if not find_chrome():
            raise unittest.SkipTest(
                "no Chrome: this is UNKNOWN, not a pass (CLAUDE.md)")
        cls.page_path = build_page()
        cls.url = "file://" + cls.page_path

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.page_path)
        except Exception:
            pass

    def test_the_rules_exist_at_all(self):
        """A missing rule must fail here rather than quietly measuring the
        inherited .tit-cta, which would pass on the wrong control."""
        css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"),
                     flags=re.S)
        for rule in self.RULES:
            self.assertIn(
                rule + " ", css.replace("{", " {"),
                "dashboard.css declares no rule %r, so the digest button is "
                "whatever .tit-cta gives it" % rule)

    def _measure(self, width, hover):
        rows = {}
        for name, os_scheme, attr in THEMES:
            try:
                with Browser(width=width, height=900) as p:
                    p.call("Emulation.setEmulatedMedia", {
                        "features": [{"name": "prefers-color-scheme",
                                      "value": os_scheme}]})
                    p.navigate(self.url, settle=1.0)
                    p.eval_js(
                        "(function(){var s=document.createElement('style');"
                        "s.textContent=%s;document.head.appendChild(s);"
                        "return 1})()" % json.dumps(FREEZE_CSS))
                    if attr is None:
                        p.eval_js("(function(){document.documentElement"
                                  ".removeAttribute('data-theme');return 1})()")
                    else:
                        p.eval_js("(function(){document.documentElement"
                                  ".setAttribute('data-theme',%s);return 1})()"
                                  % json.dumps(attr))
                    first = json.loads(p.eval_js(paint_js()))
                    self.assertFalse(
                        first.get("missing"),
                        "%s: no control in the hero says %r" % (name, LABEL))
                    if hover:
                        x, y = first["centre"]
                        p.call("Input.dispatchMouseEvent", {
                            "type": "mouseMoved", "x": x, "y": y,
                            "buttons": 0})
                        first = json.loads(p.eval_js(paint_js()))
                    rows[name] = first
            except CDPUnavailable as exc:
                raise unittest.SkipTest("could not launch Chrome: %s" % exc)
        return rows

    def _check(self, rows, when):
        bad = []
        for name, r in sorted(rows.items()):
            if r["boundary"] < MIN_BOUNDARY - 0.005:
                bad.append("%s %s: boundary %.2f:1 (%s), need %.1f"
                           % (name, when, r["boundary"], r["how"],
                              MIN_BOUNDARY))
            if r["label"] < MIN_TEXT - 0.005:
                bad.append("%s %s: the label reads %.2f:1 on its own fill, "
                           "need %.1f" % (name, when, r["label"], MIN_TEXT))
            if r.get("tag") is not None and r["tag"] < MIN_TEXT - 0.005:
                bad.append("%s %s: the cadence tag reads %.2f:1 on its own "
                           "fill, need %.1f" % (name, when, r["tag"],
                                                MIN_TEXT))
        self.assertEqual(
            [], bad,
            "the digest button is not visible as a control:\n  "
            + "\n  ".join(bad))

    def test_it_is_visible_as_a_control_with_the_pointer_away(self):
        self._check(self._measure(1280, hover=False), "at rest")

    def test_it_does_not_dissolve_under_the_pointer(self):
        """.tit-cta-how:hover repaints its edge in --tit-blue. On a
        blue-tinted fill that is a wash, and a control that loses its outline
        the moment a pointer arrives fails 1.4.11 while somebody is using
        it."""
        self._check(self._measure(1280, hover=True), "hovered")
