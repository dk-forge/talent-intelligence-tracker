"""THE SECONDARY ROUTES ARE IN THE SITE NAVIGATION, AND THEY RENDER.

WHY THIS FILE EXISTS. /sources/, /places/, /recall/ and /press/ have been live
for months while the site header carried "Talent Intelligence Tracker" as one
flat item with nothing under it. A reader who wants to know where the data
comes from, or how much of the market we miss, had to already be on the
dashboard and find a link in a footer.

WHAT IS PINNED:

  * the four routes are under the tracker's own menu item, in reader order;
  * every label is the ROUTE'S OWN <h1>, read from the file that renders it,
    so a rename reaches the menu rather than leaving it describing a page that
    no longer calls itself that;
  * the sync is IDEMPOTENT: two registrations, one item per route, and no
    second write;
  * a route that stops being offered LOSES ITS ITEM instead of lingering on a
    404, while a child the owner added elsewhere survives;
  * the items RENDER: real geometry and real innerText at 1280 and at 375,
    with no horizontal bleed at either. An item in the DOM at 0x0 is not a
    menu item, and reading the markup cannot tell the two apart.

HOW THE RENDERED HALF MEASURES, AND WHAT IT IS NOT. This plugin does not
render the menu; WordPress core does, from a `wp_navigation` post, through a
renderer in neither repo. So tests/fixtures/site_nav.json is CAPTURED, not
written: the live header nav's markup plus the CSS core prints for it
(wp-block-navigation, its link sheet, global styles, block supports and the
Twenty Twenty-Five inline sheet), taken from the bare tracker URL with a
browser User-Agent on 2026-08-13.

The submenu is then built IN THE BROWSER by cloning the "Blog" item out of
that same captured markup -- an item core itself rendered on that page, as a
submenu with children -- and substituting only the labels and hrefs this
plugin produces. The shape under test is core's own output; the only invented
part is the text, which is the part this plugin owns. What this cannot catch
is core changing how it renders a submenu. What it does catch is every way
this plugin can put the wrong thing, or nothing, into one.

The labels are executed out of includes/nav_submenu.php against the real route
files, so this file holds no copy of any route's name. The EXPECTATION is read
from the <h1> tags directly, not from the plugin's output: an expectation
derived from the thing under test passes on a submenu that has lost half of
itself.

Where php is not installed the executed half skips and the source-level half
still runs, the same split tests/test_feed_and_crm.py uses. No Chrome, no
geometry: those skip loudly rather than pass. Absence of a signal is not a
pass.

PROVEN TO FAIL, TWICE OVER. On the pre-fix tree (origin/main@48cd3a0, 1.80.0)
includes/nav_submenu.php does not exist and every test here fails, the first
with "the plugin does not load includes/nav_submenu.php, so nothing puts the
secondary routes in the site navigation". Because "the file is missing" is a
weak proof, the fix was also taken back out behaviourally, with the include
present and tit_nav_routes() reduced to sources and press:

    at 1280x900 the submenu under "Talent Intelligence Tracker" reads
    ['Where this data comes from', 'Press and Media Kit'] but the routes head
    themselves ['Where this data comes from',
    'Countries, Cities And Industries We Cover', 'How much do we miss?',
    'Press and Media Kit']
"""

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cdp import Browser, CDPUnavailable, find_chrome  # noqa: E402

PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
NAV = PLUGIN / "includes" / "nav_submenu.php"
MAIN = PLUGIN / "talent-intelligence-tracker.php"
HARNESS = ROOT / "tests" / "php" / "nav_submenu.php"
NAV_FIXTURE = ROOT / "tests" / "fixtures" / "site_nav.json"

PHP = shutil.which("php")
PARENT = "https://example.test/blog/talent-intelligence-tracker/"

# The four, in reader order, and the file whose <h1> names each. Named here so
# a change to either is a decision somebody made in a diff, not a drift.
ROUTES = [
    ("sources", "includes/sources.php"),
    ("places", "includes/places.php"),
    ("recall", "includes/recall.php"),
    ("press", "includes/press.php"),
]

# Reader-facing and deliberately out, with the reason.
EXCLUDED = {
    "corrections": "a report-an-error form, reached from where the error is",
}


def route_heading(relative):
    """The <h1> the route renders, read straight out of the file."""
    src = (PLUGIN / relative).read_text(encoding="utf-8")
    m = re.search(r"<h1[^>]*\bdata-tit-route-heading\b[^>]*>(.*?)</h1>", src, re.S | re.I)
    assert m, (
        "%s has no <h1 data-tit-route-heading>, so nothing names its menu item "
        "and the label would be a second typed copy" % relative)
    text = m.group(1)
    for ent, ch in (("&amp;", "&"), ("&#038;", "&"), ("&rsquo;", "’"),
                    ("&#8217;", "’"), ("&quot;", '"')):
        text = text.replace(ent, ch)
    return re.sub(r"\s+", " ", text).strip()


def headings():
    return [route_heading(f) for _slug, f in ROUTES]


def run(*args):
    out = subprocess.run([PHP, str(HARNESS), *args], capture_output=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    return out.stdout.decode("utf-8")


def run_json(*args):
    return json.loads(run(*args))


# --- source-level, no php needed --------------------------------------------

def test_the_plugin_loads_the_include():
    assert NAV.exists(), (
        "includes/nav_submenu.php does not exist, so nothing puts the "
        "secondary routes in the site navigation")
    assert "includes/nav_submenu.php" in MAIN.read_text(encoding="utf-8"), (
        "the plugin does not load includes/nav_submenu.php, so nothing puts "
        "the secondary routes in the site navigation")


def test_the_include_is_loaded_after_every_route_it_reads():
    """It reads each route's url helper, so it must load after all of them."""
    main = MAIN.read_text(encoding="utf-8")
    mine = main.index("includes/nav_submenu.php")
    for _slug, relative in ROUTES:
        assert main.index(relative) < mine, (
            "%s is loaded after nav_submenu.php, so its url helper is undefined "
            "when the submenu is built and the whole set is withheld forever"
            % relative)


def test_the_lock_name_matches_the_sibling_plugin():
    """Both trackers write the same wp_navigation post.

    The literal is the only thing serialising them. A rename on one side
    removes the serialisation and neither plugin would notice: each verifies
    its own subtree and sets its own done-flag.
    """
    src = NAV.read_text(encoding="utf-8")
    assert "'atr_nav_children_lock'" in src, (
        "TIT_NAV_LOCK_OPTION is no longer 'atr_nav_children_lock'. The sibling "
        "plugin's ALT_NAV_LOCK_OPTION uses that literal, and two plugins "
        "editing one menu post with different lock names can drop each other's "
        "children with both of them reporting success.")
    assert "alt" not in src.split("const TIT_NAV_LOCK_OPTION")[1].split(";")[0]


def test_every_route_names_itself_exactly_once():
    for _slug, relative in ROUTES:
        src = (PLUGIN / relative).read_text(encoding="utf-8")
        assert src.count("data-tit-route-heading") == 1, (
            "%s marks %d headings as its route heading. Exactly one <h1> may "
            "author the menu label, or the reader picks whichever comes first."
            % (relative, src.count("data-tit-route-heading")))


def test_the_route_list_holds_no_copy_of_a_heading_or_a_url():
    src = NAV.read_text(encoding="utf-8")
    body = src[src.index("function tit_nav_routes("):]
    body = body[:body.index("\n}\n")]
    for heading in headings():
        assert heading not in body, (
            "tit_nav_routes() carries a typed copy of %r. The label has one "
            "author and it is the <h1> the route renders." % heading)
    assert "http" not in body, "tit_nav_routes() types a URL instead of asking the route"


def test_the_excluded_routes_are_named_with_a_reason():
    src = NAV.read_text(encoding="utf-8")
    body = src[:src.index("function tit_nav_routes(")]
    for slug, reason in EXCLUDED.items():
        assert "/%s/" % slug in body, (
            "/%s/ is a live reader-facing route that is not in the submenu and "
            "the file does not say why. The reason is: %s" % (slug, reason))
    for slug, relative in ROUTES:
        assert relative in src, slug


def test_no_dash_in_any_label():
    # style_check needs 12 characters and 3 real words before a string is
    # eligible, so a short menu label slips past it.
    for heading in headings():
        for dash in ("—", "–"):
            assert dash not in heading, (
                "the menu label %r carries a dash the UI copy rule forbids and "
                "the style check is too short to see" % heading)


def test_the_harness_runs_in_ci():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert "php tests/php/nav_submenu.php" in workflow, (
        "the executed half of this file never runs unattended, so a broken "
        "menu sync would only be caught by somebody running php locally")


# --- executed ---------------------------------------------------------------

php_only = pytest.mark.skipif(PHP is None, reason="php not installed; CI runs the harness")


@php_only
def test_the_harness_self_check_passes():
    assert "ok:" in run()


@php_only
def test_the_block_parser_agrees_with_cores_grammar():
    assert run_json("--round-trip") is True, (
        "the harness's block parser does not round-trip core-shaped markup, so "
        "every executed assertion here is measuring a fiction")


@php_only
def test_every_label_is_the_routes_own_h1():
    got = [c["label"] for c in run_json("--desired")]
    assert got == headings(), (
        "the menu would read %r while the routes head themselves %r" % (got, headings()))


@php_only
def test_every_child_points_at_its_own_route():
    got = [c["url"] for c in run_json("--desired")]
    assert got == [PARENT + slug + "/" for slug, _f in ROUTES]


@php_only
def test_registering_twice_writes_once_and_leaves_one_item_per_route():
    r = run_json("--twice")
    assert r["writes_one"] == 1, "the first registration did not write once"
    assert r["writes_total"] == 1, (
        "registering twice wrote the menu %d times. The second run must find "
        "the item already correct and perform no write at all." % r["writes_total"])
    assert r["one"] == r["two"], "the second registration changed the stored menu"
    urls = [k["url"] for k in r["item"]["kids"]]
    assert urls == [PARENT + slug + "/" for slug, _f in ROUTES], (
        "after two registrations the submenu holds %r" % (urls,))
    assert len(urls) == len(set(urls)), "a destination appears twice"
    assert r["item"]["block"] == "core/navigation-submenu"
    assert r["synced"] == "test", (
        "the sync never verified, so it would rewrite the menu on every "
        "request forever")


@php_only
def test_a_retired_route_loses_its_item_and_an_owners_child_survives():
    r = run_json("--retired")
    urls = [k["url"] for k in r["item"]["kids"]]
    assert PARENT + "corrections/" not in urls, (
        "a route that is no longer offered kept its menu item. A retired or "
        "renamed route's item must follow it, not linger pointing at something "
        "the navigation still claims is there.")
    assert "https://example.test/blog/contact/" in urls, (
        "a child the owner put under the tracker pointing outside it was "
        "deleted. This plugin manages the items below its own page, nothing else.")
    assert '"label":"AI Layoff Tracker"' in r["content"], (
        "the sibling tracker's menu item was lost; both plugins write this one post")


@php_only
def test_a_tracker_in_no_menu_writes_nothing():
    r = run_json("--not-in-any-menu")
    assert r["writes"] == 0, (
        "the tracker is in no menu on this site and the sync wrote anyway. A "
        "menu we are not in is not a menu we may create items in.")


@php_only
def test_the_children_survive_serialisation():
    # serialize_block() substitutes an innerBlock for each null in innerContent
    # and never reads innerBlocks directly, so a container built with an empty
    # innerContent serialises as a submenu with no children at all.
    content = run("--serialised")
    for slug, _f in ROUTES:
        assert PARENT + slug + "/" in content, (
            "/%s/ is missing from the SERIALISED menu. The block array may hold "
            "it while innerContent does not, and innerContent is what core "
            "writes out." % slug)


# --- rendered ---------------------------------------------------------------

BUILD = r"""
(function (children) {
  var nav = document.querySelector('nav.wp-block-navigation');
  var blog = null, ours = null;
  Array.prototype.forEach.call(nav.querySelectorAll('li'), function (li) {
    var a = li.querySelector(':scope > a.wp-block-navigation-item__content');
    if (!a) return;
    var t = a.textContent.trim();
    if (t === 'Blog' && li.classList.contains('has-child')) blog = li;
    if (t === 'Talent Intelligence Tracker') ours = li;
  });
  if (!blog || !ours) return 'no seed item: blog=' + !!blog + ' ours=' + !!ours;

  // Core rendered this submenu on this page. Clone its exact shape and change
  // only the text and the hrefs, so no markup is invented here.
  var next = blog.cloneNode(true);
  var head = next.querySelector(':scope > a.wp-block-navigation-item__content');
  head.setAttribute('href', ours.querySelector('a').getAttribute('href'));
  head.querySelector('.wp-block-navigation-item__label').textContent =
      'Talent Intelligence Tracker';
  next.querySelector(':scope > button.wp-block-navigation-submenu__toggle')
      .setAttribute('aria-label', 'Talent Intelligence Tracker submenu');

  var sub = next.querySelector(':scope > ul.wp-block-navigation__submenu-container');
  var seed = sub.querySelector('li');
  sub.textContent = '';
  children.forEach(function (c) {
    var li = seed.cloneNode(true);
    li.querySelector('a').setAttribute('href', c.url);
    li.querySelector('.wp-block-navigation-item__label').textContent = c.label;
    sub.appendChild(li);
  });
  next.setAttribute('data-tit-tracker-item', '1');
  ours.parentNode.replaceChild(next, ours);
  return 'ok';
})(%s)
"""

OPEN_DESKTOP = """
(function () {
  document.querySelector('[data-tit-tracker-item] > button.wp-block-navigation-submenu__toggle')
          .setAttribute('aria-expanded', 'true');
  return true;
})()
"""

OPEN_MOBILE = """
(function () {
  var c = document.querySelector('.wp-block-navigation__responsive-container');
  c.classList.add('is-menu-open', 'has-modal-open');
  document.documentElement.classList.add('has-modal-open');
  return true;
})()
"""

MEASURE = r"""
(function () {
  var item = document.querySelector('[data-tit-tracker-item]');
  if (!item) return { error: 'the tracker item is not in the menu' };
  var sub = item.querySelector(':scope > ul.wp-block-navigation__submenu-container');
  var cs = getComputedStyle(sub), sr = sub.getBoundingClientRect();
  var out = [];
  Array.prototype.forEach.call(sub.querySelectorAll(':scope > li'), function (li) {
    var r = li.querySelector('a').getBoundingClientRect();
    out.push({
      // innerText off the rendered ancestor, never textContent: a hidden
      // submenu still carries textContent for text no reader can read.
      text: (li.innerText || '').trim().replace(/\s+/g, ' '),
      href: li.querySelector('a').getAttribute('href'),
      w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
      right: Math.round(r.right * 10) / 10
    });
  });
  return { visibility: cs.visibility, opacity: cs.opacity,
           subRight: Math.round(sr.right * 10) / 10,
           docW: document.documentElement.scrollWidth, winW: window.innerWidth,
           items: out };
})()
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>%(css)s</style>
<style>body{margin:0;background:#fff;color:#16181d;font-family:system-ui,sans-serif}</style>
</head><body class="wp-singular page"><div class="wp-site-blocks"><header class="wp-block-template-part">
%(nav)s
</header></div></body></html>
"""

rendered_only = pytest.mark.skipif(
    PHP is None or not find_chrome(),
    reason="php or Chrome missing: geometry cannot be measured, and absence of "
           "a signal is not a pass")


def _render(width, height, opener):
    fx = json.loads(NAV_FIXTURE.read_text(encoding="utf-8"))
    html = PAGE % {"css": fx["css"], "nav": fx["nav_html"]}
    children = run_json("--desired")
    url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)
    try:
        with Browser(width=width, height=height) as b:
            b.navigate(url, settle=1.0)
            built = b.eval_js(BUILD % json.dumps(children))
            assert built == "ok", built
            b.eval_js(opener)
            # The container carries `transition: opacity .1s linear`, so a
            # same-tick read reports 0 on a submenu that is opening correctly.
            time.sleep(0.4)
            return b.eval_js(MEASURE)
    except CDPUnavailable as exc:
        pytest.skip("Chrome unavailable: %s" % exc)


@rendered_only
def test_the_four_render_as_the_routes_head_themselves_at_1280():
    r = _render(1280, 900, OPEN_DESKTOP)
    assert "error" not in r, r.get("error")
    got = [i["text"] for i in r["items"]]
    assert got == headings(), (
        'at 1280x900 the submenu under "Talent Intelligence Tracker" reads %r '
        "but the routes head themselves %r" % (got, headings()))
    assert r["visibility"] == "visible", (
        "the submenu computes visibility:%s, so none of its items is on the "
        "page" % r["visibility"])
    assert float(r["opacity"]) > 0, (
        "the open submenu computes opacity:%s, so it has geometry a reader "
        "cannot see" % r["opacity"])
    for i in r["items"]:
        assert i["w"] * i["h"] > 0, (
            "at 1280x900 %r renders %sx%s. An item in the DOM at zero size is "
            "not a menu item." % (i["text"], i["w"], i["h"]))


@rendered_only
def test_the_four_render_as_the_routes_head_themselves_at_375():
    r = _render(375, 812, OPEN_MOBILE)
    got = [i["text"] for i in r["items"]]
    assert got == headings(), (
        "at 375x812 the submenu reads %r but the routes head themselves %r"
        % (got, headings()))
    for i in r["items"]:
        assert i["w"] * i["h"] > 0, (
            "at 375x812 %r renders %sx%s" % (i["text"], i["w"], i["h"]))


@rendered_only
def test_nothing_bleeds_horizontally_at_375():
    r = _render(375, 812, OPEN_MOBILE)
    assert r["docW"] <= r["winW"], (
        "the document scrolls to %spx inside a %spx viewport once the menu is "
        "open. Nothing here may bleed horizontally on a phone."
        % (r["docW"], r["winW"]))
    for i in r["items"]:
        assert i["right"] <= r["winW"] + 0.5, (
            "at 375px %r ends at %spx, past the %spx viewport"
            % (i["text"], i["right"], r["winW"]))


@rendered_only
def test_nothing_bleeds_horizontally_at_1280():
    r = _render(1280, 900, OPEN_DESKTOP)
    assert r["docW"] <= r["winW"], (
        "the document scrolls to %spx inside a %spx viewport with the submenu "
        "open" % (r["docW"], r["winW"]))
    assert r["subRight"] <= r["winW"] + 0.5, (
        "the open submenu's right edge is %spx, past the %spx viewport, so the "
        "longest label pushes it off screen" % (r["subRight"], r["winW"]))
