"""THE GUARD THAT WOULD HAVE CAUGHT THE UNREADABLE DARK PAGE.

Nothing in this repository evaluated what a page RENDERS AS in a non-default
theme. Every front-end check here reads CSS, PHP or a version string as TEXT,
and the defect they all missed does not exist in any of those: a site-level
rule stored in the WordPress database, attached to WordPress core's
`wp-block-library` handle and present in NEITHER repository, declares
`color:#2a2a2a !important` on `.entry-content p` and two siblings of it, and
beats every token the plugin owns. In light that is 14.6:1 and invisible as a
problem. In dark it put 62 text elements on the dashboard between 1.04:1 and
1.4:1, which does not read as low contrast, it reads as a page that failed to
load. Every check was green the entire time, because the defect only exists
once the cascade resolves, and nothing resolved a cascade.

So this file does two different jobs, and the second is the one that matters.

  1. It measures the CONTRACT: given the real dashboard.css and a faithful
     reproduction of the site override, does every text element clear WCAG AA,
     in dark and in light?

  2. It measures the CHECKER: with each of the four winning declarations taken
     back out one at a time, does the audit actually FAIL? A guard that cannot
     be made to fail is not evidence of anything.

Both halves strip CSS COMMENTS before matching. The fix's own comment block
quotes the site rule verbatim, braces and all, so a matcher that reads the file
as text would find the defect inside the prose explaining it and report a pass
it did not earn.

Everything runs against a LOCAL fixture, so it needs no network and is safe in
CI. The live-site sweep is `contrast_audit.py`, the same probe pointed at the
real pages; see .github/workflows/contrast-audit.yml.

No Chrome, no measurement: this SKIPS loudly rather than passing. Absence of a
signal is not a pass.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cdp import Browser, CDPUnavailable, find_chrome  # noqa: E402
import contrast_audit  # noqa: E402

CSS = ROOT / "wordpress-plugin/talent-intelligence-tracker/assets/dashboard.css"

# Copied verbatim off the live document head on 2026-08-11, out of the
# `wp-block-library-inline-css` <style> tag. This is the rule that lives in the
# WordPress database, and reproducing it exactly is the whole point: a fixture
# that omits it measures a page nobody is served.
SITE_OVERRIDE = """
.entry-content p,.wp-block-post-content p{font-size:1.05rem !important;line-height:1.78 !important;color:#2a2a2a !important;margin-bottom:1.2rem !important}
.entry-content h2,.wp-block-post-content h2{font-size:1.45rem !important;font-weight:700 !important;color:#1a1a1a !important;margin:2.2rem 0 .8rem !important;padding-bottom:.35rem !important;border-bottom:2px solid #eef3ee !important}
.entry-content h3,.wp-block-post-content h3{font-size:1.15rem !important;font-weight:600 !important;color:#222 !important;margin:1.5rem 0 .5rem !important}
"""

# The theme states its two palette colours as custom properties on :root and
# reads them back for the post title and the navigation. Reproduced because
# that indirection is exactly what let the site chrome stay black on a dark
# page: the plugin painted the ground and the theme kept inking #111 over it.
THEME_SHIM = """
:root { --wp--preset--color--base:#fff; --wp--preset--color--contrast:#111; }
body { background-color: var(--wp--preset--color--base);
       color: var(--wp--preset--color--contrast);
       margin:0; font-family: system-ui, sans-serif; }
h1.wp-block-post-title { color: var(--wp--preset--color--contrast);
       font-size:36px; margin:0 0 12px; }
.wp-block-navigation-item__label { color: var(--wp--preset--color--contrast);
       font-size:15px; }
"""

# THE SHARED DIGEST SIGNUP, which the dashboard renders by calling into the
# SIBLING plugin (tests/test_digest_signup_embed.py pins that call). Copied
# verbatim off the live document on 2026-08-16, out of the <style> the sibling
# emits beside the form, and trimmed to the declarations that carry a colour.
#
# It is here because this component is the second thing to prove that reading
# our own CSS is not reading the page. Every colour it paints is a component
# token reading a SITE token with the sibling's own light literal as the
# fallback, and no `--alt-` name has ever been declared on a talent page, so
# every one of them fell through to a light value on a #14161b ground. Nothing
# in either repository is wrong when read as text: the sibling declares a
# sensible fallback and this stylesheet declares a complete palette. The defect
# exists only where the two meet, which is in a browser.
SIGNUP_COMPONENT_CSS = """
.alt-digest{--alt-dg-edge:var(--alt-border,#e2e3e8);--alt-dg-ink:var(--alt-ink,#16181d);--alt-dg-field-edge:var(--alt-control-border,#838893);--alt-dg-field-bg:var(--alt-surface,#fff);--alt-dg-btn-bg:var(--alt-blue,#1f6fd0);--alt-dg-btn-bg-hover:var(--alt-blue-dark,#1c5cab);--alt-dg-btn-ink:var(--alt-on-accent,#fff);--alt-dg-ok-bg:var(--alt-ok-bg,#dff3df);--alt-dg-ok-ink:var(--alt-ok-ink,#165d28);--alt-dg-ok-edge:var(--alt-tint-border,#cfdad0);--alt-dg-err-bg:var(--alt-red-tint,#fdeeee);--alt-dg-err-ink:var(--alt-crit,#b3261e);--alt-dg-err-edge:var(--alt-crit-border,#e6b6b3);margin:40px 0;padding:20px;border:1px solid var(--alt-dg-edge);border-radius:12px;color:var(--alt-dg-ink)}
.alt-digest p.alt-digest-intro{margin:0 0 12px !important;font-size:14px !important;line-height:1.55 !important}
.alt-digest-form fieldset{border:none;margin:0 0 12px;padding:0}
.alt-digest-form legend{font-weight:600;margin-bottom:6px;padding:0}
.alt-digest-lists{display:flex;flex-direction:column;gap:8px}
.alt-digest-lists label{display:flex;align-items:center;gap:10px;min-height:44px;margin:0;font-size:14px}
.alt-digest-freq label{display:inline-flex;align-items:center;gap:10px;min-height:44px;margin:0 16px 0 0;font-size:14px}
.alt-digest-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.alt-digest-row label{font-weight:600;flex-basis:100%;margin:0}
.alt-digest-row input[type=email]{flex:1 1 220px;min-width:0;min-height:44px;padding:8px 10px;font:inherit;font-size:16px;line-height:1.3;border:1px solid var(--alt-dg-field-edge);border-radius:8px;background:var(--alt-dg-field-bg);color:var(--alt-dg-ink)}
.alt-digest-submit{display:inline-flex;align-items:center;justify-content:center;min-height:44px;min-width:44px;padding:8px 18px;font:inherit;font-size:14px;font-weight:600;line-height:1.2;border:1px solid var(--alt-dg-btn-bg);border-radius:8px;background:var(--alt-dg-btn-bg);color:var(--alt-dg-btn-ink);cursor:pointer}
.alt-digest-privacy{margin-top:14px;font-size:13px}
.alt-digest-privacy summary{display:flex;align-items:center;min-height:44px;cursor:pointer}
"""

# The form itself, off the same live document, with the nonce and the referer
# dropped because they are per-request and carry no colour. The honeypot label
# is KEPT: it is offscreen rather than hidden, so it is a text element a
# measurement sees, and it was one of the ten the live audit flagged.
#
# `<details open>` where the live page ships it closed. A closed disclosure
# renders none of its children, and its four paragraphs are the control group
# that shows the wrapper ink block still wins inside this component while the
# summary line beside them does not.
SIGNUP_MARKUP = """
<section class="alt-digest" id="alt-digest"><h2>Email digest</h2>
<p class="alt-digest-intro">A plain email summary of what changed on these
trackers. Details in the <a href="#alt-digest-privacy">privacy note</a> below.</p>
<form class="alt-digest-form" data-alt-context="talent" method="post" action="#">
<div aria-hidden="true" style="position:absolute;left:-9999px;top:-9999px;height:1px;width:1px;overflow:hidden;"><label>Website<input type="text" name="alt_website" tabindex="-1" autocomplete="off"></label></div>
<fieldset class="alt-digest-lists"><legend>What would you like?</legend>
<label><input type="checkbox" name="alt_list_layoff" value="1"> AI Layoff Tracker digest: verified layoffs, headline totals, largest new entries.</label>
<label><input type="checkbox" name="alt_list_talent" value="1"> Talent Intelligence Tracker digest: hiring, leadership and compensation signals.</label>
<label><input type="checkbox" name="alt_list_articles" value="1"> Occasional articles and product news.</label></fieldset>
<fieldset class="alt-digest-freq"><legend>How often for the digests?</legend>
<label><input type="radio" name="alt_freq" value="weekly" checked> Weekly</label>
<label><input type="radio" name="alt_freq" value="daily"> Daily</label></fieldset>
<div class="alt-digest-row"><label for="alt-digest-email">Your email</label>
<input type="email" id="alt-digest-email" name="alt_email" required autocomplete="email" placeholder="you@example.com">
<button type="submit" class="alt-digest-submit">Subscribe</button></div></form>
<details class="alt-digest-privacy" id="alt-digest-privacy" open>
<summary>Privacy note: what we store and how to erase it</summary>
<p><strong>What we store:</strong> your email address, the choices above, and
timestamps. Nothing else about you.</p>
<p><strong>How to erase it:</strong> click the unsubscribe link in any email.</p>
</details></section>
"""

# Every element here exists because a real one like it was measured below AA on
# 2026-08-11. The two outside .entry-content are site chrome and are the reason
# the fix cannot be a single wrapper-scoped rule.
FIXTURE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<!-- Without this, Chrome lays a mobile emulation out at its 980px fallback
     width, every max-width:860px rule stays inert, and the phone-only markup
     this fixture exists to measure renders display:none. The first draft of
     this file had no viewport meta and reported the matrix label as absent. -->
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>%(theme)s</style>
<style>%(plugin)s</style>
<style>%(site)s</style>
</head>
<body class="wp-singular page-template-default page">
<div class="wp-site-blocks">
<header class="wp-block-template-part"><nav class="wp-block-navigation">
  <a class="wp-block-navigation-item__content" href="#"><span
     class="wp-block-navigation-item__label">Talent tracker</span></a>
</nav></header>
<main class="wp-block-group has-global-padding">
<h1 class="wp-block-post-title">Talent Intelligence Tracker</h1>
<div class="wp-block-group alignfull"><div class="entry-content alignfull">
<div class="tit-wrap" id="tit-dashboard">
  <h2 class="tit-section-h">Where the hiring is</h2>
  <h3 class="tit-board-title">How it is trending</h3>
  <p class="tit-hero-sub">Every update links to the document it came from.</p>
  <p id="tit-detail-note">Rows overlap, so the columns do not add up.</p>
  <p>A plain paragraph with <strong>a bold lead in</strong> after it.</p>
  <div class="tit-regions">
    <button class="tit-region is-on"><span class="tit-region-name">Europe</span>
      <span class="tit-region-n">26,036</span></button>
    <button class="tit-region"><span class="tit-region-name">Americas</span>
      <span class="tit-region-n">7,809</span></button>
  </div>
  <div class="tit-matrix-scroll"><table class="tit-matrix"><tbody>
    <tr class="tit-matrix-row" data-signal="hiring">
      <th scope="row">Hiring</th>
      <td><button class="tit-cell" style="--i:1"><span
          class="tit-cell-p">This quarter</span><span class="tit-cell-n">412</span>
      </button></td>
    </tr>
    <tr class="tit-matrix-row" data-signal="money">
      <th scope="row">Money</th>
      <td><button class="tit-cell" style="--i:1"><span
          class="tit-cell-p">This quarter</span><span class="tit-cell-n">88</span>
      </button></td>
    </tr>
  </tbody></table></div>
  <!-- The sibling emits its <style> beside the form, in the body, AFTER this
       stylesheet has loaded in the head. Reproduced in that order: the two
       rules that meet here have equal specificity (.alt-digest against
       .alt-digest), so which one is later is exactly the sort of thing a
       fixture that tidied it into the head would stop measuring. -->
  <style>%(signupcss)s</style>
  %(signup)s
</div></div></div></main></div>
</body></html>
"""


def _decomment(css):
    """Drop CSS comments before any structural matching.

    The fix's comment block quotes the site rule it defeats, braces and all, so
    a brace scanner run over the raw file finds a rule inside the prose. That
    is not a hypothetical: the block below this line contains the exact string
    `{ color:#2a2a2a !important }` in a comment.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _strip_wrapper_ink(css):
    """Remove the declarations that beat the site rule on p, h2 and h3.

    Removes whole rule blocks whose selector list reaches
    `.tit-wrap p|h2|h3` inside the site rule's own scope: exactly what was
    added to win, and nothing else.
    """
    css = _decomment(css)
    out, i, removed = [], 0, 0
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        sel = m.group(1)
        if re.search(r"\.(entry-content|wp-block-post-content)\s+\.tit-wrap\s+"
                     r"(p|h2|h3)\b", sel):
            out.append(css[i:m.start()])
            i = m.end()
            removed += 1
    out.append(css[i:])
    assert removed >= 2, "found %d blocks to strip; the fix moved" % removed
    return "".join(out)


def _strip_preset_repoint(css):
    """Remove the re-pointing of the theme's own two palette variables."""
    css = _decomment(css)
    before = css
    css = re.sub(r"--wp--preset--color--(base|contrast)\s*:[^;]*;", "", css)
    assert css != before, "nothing re-points the theme presets any more"
    return css


def _restore_white_wash(css):
    """Put --tit-on-accent back to the white wash that measured 3.25:1."""
    css = _decomment(css)
    out, n = re.subn(r"--tit-on-accent\s*:\s*rgba\(0,0,0,\.28\)\s*;",
                     "--tit-on-accent:rgba(255,255,255,.26);", css)
    assert n == 1, "the light --tit-on-accent value moved (%d matches)" % n
    return out


def _restore_muted_cell_label(css):
    """Put the phone matrix label back on --tit-mut, which read 4.40:1."""
    css = _decomment(css)
    out, n = re.subn(r"color\s*:\s*var\(--tit-cell-p-ink\)",
                     "color:var(--tit-mut)", css)
    assert n == 1, "the .tit-cell-p ink declaration moved (%d matches)" % n
    return out


def _strip_alt_bridge(css):
    """Remove the `.alt-digest` rule that answers the sibling's var() calls.

    Taking this back out is a byte-exact reproduction of what was live between
    2026-08-15 and 2026-08-16: the sibling asks for `--alt-ink` and nothing on
    a talent page has ever declared one, so the form paints its own light
    fallback palette onto whatever ground the page is wearing.
    """
    css = _decomment(css)
    out, i, removed = [], 0, 0
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        if re.search(r"(^|[\s,])\.alt-digest\s*$", m.group(1)) and \
                "--alt-ink" in m.group(2):
            out.append(css[i:m.start()])
            i = m.end()
            removed += 1
    out.append(css[i:])
    assert removed == 1, (
        "expected exactly one .alt-digest bridge rule declaring --alt-ink, "
        "found %d; the fix moved" % removed)
    return "".join(out)


class _Measured(unittest.TestCase):
    """Loads a fixture in headless Chrome and returns the audit's own rows."""

    @classmethod
    def setUpClass(cls):
        if not find_chrome():
            raise unittest.SkipTest(
                "no Chrome/Chromium on this machine, so rendered contrast could "
                "not be measured. This is UNKNOWN, not a pass: run "
                "`python3 contrast_audit.py` where a browser exists.")

    def _rows(self, css, theme="dark", width=375):
        html = FIXTURE % {"plugin": css, "theme": THEME_SHIM,
                          "site": SITE_OVERRIDE,
                          "signupcss": SIGNUP_COMPONENT_CSS,
                          "signup": SIGNUP_MARKUP}
        try:
            with Browser(width=width, height=900) as page:
                page.call("Emulation.setEmulatedMedia", {
                    "features": [{"name": "prefers-color-scheme",
                                  "value": theme}]})
                page.call("Page.navigate", {"url": "about:blank"})
                page.eval_js(
                    "(function(){document.open();document.write(%s);"
                    "document.close();return true;})()" % json.dumps(html))
                # Freeze first, then flip: reading a computed background in the
                # same task that changed the theme returns the PREVIOUS colour
                # while a transition is still running.
                page.eval_js(
                    "(function(){var s=document.createElement('style');"
                    "s.textContent=%s;document.head.appendChild(s);"
                    "return true;})()" % json.dumps(contrast_audit.FREEZE_CSS))
                page.eval_js("document.documentElement.setAttribute("
                             "'data-theme', %s)" % json.dumps(theme))
                rows = page.eval_js(contrast_audit.PROBE_JS)
        except CDPUnavailable as exc:
            raise unittest.SkipTest("could not launch Chrome: %s" % exc)
        self.assertTrue(rows, "the fixture rendered no text at all, so this "
                              "measured nothing")
        return rows

    def _bad(self, css, theme="dark", width=375):
        return contrast_audit.violations(self._rows(css, theme, width))


class DarkContrastTests(_Measured):

    def test_the_shipped_stylesheet_clears_aa_under_the_site_override(self):
        for width in (1280, 375):
            bad = self._bad(CSS.read_text(), "dark", width)
            self.assertEqual(
                [], [(b["sel"], b["color"], b["bg"], b["ratio"]) for b in bad],
                "text below WCAG AA in dark at %dpx, with the site override "
                "applied" % width)

    def test_light_is_not_regressed_by_the_dark_fix(self):
        for width in (1280, 375):
            bad = self._bad(CSS.read_text(), "light", width)
            self.assertEqual(
                [], [(b["sel"], b["color"], b["bg"], b["ratio"]) for b in bad],
                "the dark fix moved light text below AA at %dpx" % width)

    def test_every_element_the_fix_covers_is_actually_being_measured(self):
        # Guards the fixture itself. If a selector or a template change ever
        # stops these from rendering, the two tests above would pass by
        # measuring nothing at all.
        sels = " ".join(r["sel"] for r in self._rows(CSS.read_text(), "dark"))
        for needle in ("h2.tit-section-h", "h3.tit-board-title",
                       "p.tit-hero-sub", "h1.wp-block-post-title",
                       "span.wp-block-navigation-item__label",
                       "span.tit-region-n", "span.tit-cell-p",
                       "label", "legend", "summary",
                       "button.alt-digest-submit"):
            self.assertIn(needle, sels, "fixture no longer renders %s" % needle)


class TheGuardCanActuallyFailTests(_Measured):
    """With each fix taken back out, the audit MUST report that defect.

    This is the half that makes the contract tests mean something. Each case is
    a reproduction of what was live on 2026-08-11: same override, same tokens,
    same markup, one winning declaration removed.
    """

    def test_removing_the_wrapper_ink_reproduces_the_unreadable_page(self):
        bad = self._bad(_strip_wrapper_ink(CSS.read_text()), "dark")
        flagged = {b["sel"] for b in bad}
        for needle in ("h2.tit-section-h", "p.tit-hero-sub"):
            self.assertIn(needle, flagged,
                          "%s vanished for the reader and the audit did not "
                          "flag it" % needle)
        worst = min(b["ratio"] for b in bad)
        self.assertLess(worst, 1.5, "expected the reproduction to land near "
                                    "1:1, got %.2f" % worst)

    def test_removing_the_preset_repoint_reproduces_the_black_site_chrome(self):
        flagged = {b["sel"] for b in
                   self._bad(_strip_preset_repoint(CSS.read_text()), "dark")}
        for needle in ("h1.wp-block-post-title",
                       "span.wp-block-navigation-item__label"):
            self.assertIn(needle, flagged,
                          "%s was black on the dark page and the audit did "
                          "not flag it" % needle)

    def test_restoring_the_white_wash_reproduces_the_light_mode_failure(self):
        # The one defect that was in LIGHT, where nobody was looking.
        flagged = {b["sel"]: b["ratio"] for b in
                   self._bad(_restore_white_wash(CSS.read_text()), "light")}
        self.assertIn("span.tit-region-n", flagged,
                      "the count inside a selected region chip measured "
                      "3.25:1 in light and the audit did not flag it")
        self.assertLess(flagged["span.tit-region-n"], 4.5)

    def test_removing_the_alt_bridge_reproduces_the_unreadable_signup(self):
        """The 2026-08-16 audit, reproduced: ten elements at 1.02:1 in dark.

        Seven labels, two legends and one summary, which are the only text in
        that form with no ink of its own. The paragraphs and the h2 beside them
        stay readable, and that asymmetry is the tell: a rule that re-inks `p`
        inside .tit-wrap does not reach a <label>.
        """
        bad = {b["sel"]: b["ratio"] for b in
               self._bad(_strip_alt_bridge(CSS.read_text()), "dark", 375)}
        for needle in ("label", "legend", "summary"):
            self.assertIn(needle, bad,
                          "the signup's <%s> was 1.02:1 on the dark ground "
                          "and the audit did not flag it" % needle)
        worst = min(bad.values())
        self.assertLess(worst, 1.1, "expected the reproduction to land at "
                                    "about 1.02:1, got %.2f" % worst)
        self.assertNotIn("p.alt-digest-intro", bad,
                         "the intro paragraph failed too, so this reproduces "
                         "something wider than the token bridge and the test "
                         "below would pass for the wrong reason")

    def test_restoring_the_muted_label_reproduces_the_heat_cell_failure(self):
        flagged = {b["sel"] for b in
                   self._bad(_restore_muted_cell_label(CSS.read_text()),
                             "dark", 375)}
        self.assertIn("span.tit-cell-p", flagged,
                      "the phone matrix label on the hottest heat cell was "
                      "under AA and the audit did not flag it")


class TheFixIsWhereItSaysItIsTests(unittest.TestCase):
    """Structure, read off the file with comments stripped. Cheap, no browser."""

    def setUp(self):
        self.css = _decomment(CSS.read_text())

    def test_the_wrapper_rules_never_reach_wider_than_the_site_rule(self):
        # One class more specific, and never wider: a selector that drops
        # .tit-wrap would flatten colours that are currently surviving.
        for m in re.finditer(r"([^{}]*)\{[^{}]*\}", self.css):
            for part in m.group(1).split(","):
                if "--wp--preset" in m.group(0):
                    continue
                if re.search(r"\.(entry-content|wp-block-post-content)\s+"
                             r"(p|h2|h3)\b", part):
                    self.fail("this stylesheet inks a bare .entry-content "
                              "element: %s" % part.strip())

    def test_the_dark_rules_cover_both_the_attribute_and_the_media_query(self):
        # Auto is the default and Auto is the ABSENCE of the attribute, so a
        # fix written only as [data-theme=dark] reaches almost nobody.
        self.assertIn('.entry-content .tit-wrap p', self.css)
        attr = self.css.count(':root[data-theme="dark"] .entry-content '
                              '.tit-wrap p')
        media = self.css.count(':root:not([data-theme="light"]) '
                               '.entry-content .tit-wrap p')
        self.assertEqual(attr, 1)
        self.assertEqual(media, 1)

    def test_every_site_token_the_signup_asks_for_is_answered(self):
        """No browser needed, and it catches the NEXT one.

        The rendered half above can only fail on a colour a reader can see, so
        the two status panels (hidden until the form is submitted) and any
        token the sibling adds tomorrow would slip past it in silence and get
        found by a reader. This reads the component's own var() calls out of
        the pinned copy of its stylesheet and insists each name is declared.
        """
        asked = set(re.findall(r"var\((--alt-[A-Za-z0-9-]+)\s*,",
                               SIGNUP_COMPONENT_CSS))
        # The component's own `--alt-dg-*` names are declared by the component
        # itself; the SITE tokens are the ones it is asking this page for.
        asked = {n for n in asked if not n.startswith("--alt-dg-")}
        self.assertGreaterEqual(len(asked), 10, "the pinned copy of the "
                                "sibling's stylesheet no longer reads site "
                                "tokens, so this test measures nothing")
        declared = set(re.findall(r"(--alt-[A-Za-z0-9-]+)\s*:", self.css))
        missing = sorted(asked - declared)
        self.assertEqual([], missing, (
            "the shared digest signup reads %s and this stylesheet declares "
            "none of them, so each one falls through to the sibling's LIGHT "
            "literal on a page whose dark ground is #14161b" % missing))

    def test_the_bridge_is_theme_aware_by_reference_and_not_by_literal(self):
        """A hex in there is a colour that cannot follow the theme.

        The whole point of the bridge is that `--tit-*` are already redefined
        under both the media query and the attribute selector. A literal here
        would need doing twice and would be wrong in one theme the first time
        somebody forgot.
        """
        m = re.search(r"\.alt-digest\s*\{([^{}]*--alt-ink[^{}]*)\}", self.css)
        self.assertIsNotNone(m, "no .alt-digest rule declares --alt-ink")
        for name, value in re.findall(r"(--alt-[A-Za-z0-9-]+)\s*:\s*([^;]+);",
                                      m.group(1)):
            self.assertRegex(value.strip(), r"^var\(--tit-[A-Za-z0-9-]+\)$",
                             "%s is %s, which is a fixed colour in a rule that "
                             "is declared once and read in both themes"
                             % (name, value.strip()))

    def test_light_keeps_its_own_values(self):
        # The dark block must not be the only definition of these, or light
        # inherits a dark ink the day the media query is edited.
        self.assertIn("--tit-cell-p-ink:#4a4d55", self.css)
        self.assertIn("--tit-cell-p-ink:#c3c9d3", self.css)


class ProbeArithmeticTests(unittest.TestCase):
    """The ratio maths and the audit's own contract. No browser needed."""

    def test_the_thresholds_are_the_wcag_aa_ones(self):
        self.assertEqual(contrast_audit.AA_NORMAL, 4.5)
        self.assertEqual(contrast_audit.AA_LARGE, 3.0)

    def test_violations_uses_the_large_text_threshold_for_large_text(self):
        rows = [
            {"sel": "h1", "ratio": 3.2, "large": True, "color": "", "bg": ""},
            {"sel": "p", "ratio": 3.2, "large": False, "color": "", "bg": ""},
        ]
        got = [v["sel"] for v in contrast_audit.violations(rows)]
        self.assertEqual(got, ["p"])

    def test_the_audit_disables_transitions_before_measuring(self):
        self.assertIn("transition: none !important", contrast_audit.FREEZE_CSS)
        self.assertIn("animation: none !important", contrast_audit.FREEZE_CSS)

    def test_the_freeze_is_installed_before_the_theme_is_flipped(self):
        # Reading a computed background in the same task that flipped the theme
        # returns the PREVIOUS colour, which is a guard inventing failures.
        src = (ROOT / "contrast_audit.py").read_text()
        body = src[src.index("def audit_page("):]
        body = body[:body.index("\ndef ")]
        self.assertLess(body.index("_freeze(page)"),
                        body.index("_apply_theme(page"),
                        "the theme is flipped before transitions are frozen")

    def test_it_measures_both_matched_and_mismatched_theme_combinations(self):
        combos = {(os_s, attr) for _, os_s, attr in contrast_audit.THEMES}
        self.assertIn(("dark", None), combos, "the default for a dark-OS "
                                              "reader is not being measured")
        self.assertIn(("dark", "light"), combos)
        self.assertIn(("light", "dark"), combos)

    def test_it_measures_the_permalink_templates_too(self):
        names = {n for n, _ in contrast_audit.SURFACES}
        for needed in ("dashboard", "recall", "sources", "company", "country"):
            self.assertIn(needed, names)

    def test_an_unmeasurable_run_is_not_a_pass(self):
        src = (ROOT / "contrast_audit.py").read_text()
        self.assertIn("return 3", src, "no UNKNOWN exit path")
        self.assertRegex(src, r"RESULT: UNKNOWN")

    def test_a_simulated_run_can_never_report_a_clean_pass(self):
        # --inject-css measures a page no reader can load. If that path could
        # return 0, a preview of an undeployed fix would read as live.
        src = (ROOT / "contrast_audit.py").read_text()
        tail = src[src.index("if inject_css:\n        # A simulated run"):]
        self.assertIn("return 3", tail[:tail.index("return 0")])


if __name__ == "__main__":
    unittest.main()
