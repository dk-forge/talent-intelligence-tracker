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
                          "site": SITE_OVERRIDE}
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
                       "span.tit-region-n", "span.tit-cell-p"):
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
