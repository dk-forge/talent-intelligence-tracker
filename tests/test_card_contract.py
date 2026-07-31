"""THE RESULT CARD IS DEFINED ONCE, IN docs/card-contract.json, AND THIS PINS IT.

WHY THIS TEST EXISTS, stated plainly, because the reason is the whole design.

This tracker and its sibling, the AI Layoff Tracker, render the same kind of
fact: an employer, a place, a direction, an evidence tier, an amount, a headline,
a source. The owner screenshotted THIS list, liked it, and asked for the
sibling's to match. By the time anybody looked, this repo had already changed its
own labels, so neither side could say which design was current. The mismatch was
not the defect. THE INABILITY TO SAY WHICH ONE WAS CURRENT was the defect, and
shipping matching pixels once would have fixed nothing: they would have drifted
again in a fortnight, exactly as they had already drifted once.

So the card is defined ONCE, as data, in docs/card-contract.json, and that file
is byte-identical in both repositories. Not shared code: the two products have
different tables, different REST namespaces, different plugins and different
deploy paths, and coupling them through a library would buy a smaller problem at
the price of a much worse one. A shared CONTRACT, and a test on each side that
fails when its own markup stops matching it.

THREE THINGS HOLD IT TOGETHER, and each covers what the others cannot:

  1. This test, offline, on every push. It reads the contract and asserts that
     the markup this repo actually renders satisfies it. It cannot see the
     sibling.
  2. The digest below, and a second copy of it in docs/TECHLOG.md. Editing the
     contract without meaning to fails here; editing it deliberately means
     updating the digest, which is the moment you are told this is a two-repo
     change.
  3. .github/workflows/card-contract.yml, which fetches the sibling's copy of
     the contract and goes red while the two differ. That is the only one of
     the three that can see across the repo boundary, which is why it needs a
     network and lives in CI rather than here.

CHANGING THE CARD IS THEREFORE A FOUR-STEP JOB, and the point of the design is
that you cannot do three of them and ship: edit the contract, update the digest
here and in TECHLOG, change the markup, and copy the contract into the sibling.
Miss the last step and both repos go red until somebody finishes.

This repo has TWO renderers for one card: tit_card_html() in shortcodes.php
paints it on the server for the first paint, and renderCard() in dashboard.js
repaints it on every filter change. They are both checked here, because a card
that changes shape the first time a reader touches a filter is the same defect
one layer down.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "card-contract.json"
TECHLOG_PATH = ROOT / "docs" / "TECHLOG.md"
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
DASHBOARD_JS = PLUGIN / "assets" / "dashboard.js"
DASHBOARD_CSS = PLUGIN / "assets" / "dashboard.css"
SHORTCODES_PHP = PLUGIN / "includes" / "shortcodes.php"
COMPANY_PHP = PLUGIN / "includes" / "company.php"

# The contract as this repo last agreed to it. If this fails, the file changed:
# either you meant it (update this, update TECHLOG, and copy the file to the
# sibling) or you did not (revert).
CONTRACT_SHA256 = "5ce62ea8d11073b132af83696e222f0a2c4184fba646c5f0adcb9c06f7493af2"

PREFIX = "tit"


def read(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def contract():
    return json.loads(read(CONTRACT_PATH))


def strip_comments(src):
    """
    Prose about the card is not the card. Every assertion that reads ORDER or
    asks whether a string is rendered runs on the code only, or a comment
    explaining why a badge was removed would count as the badge.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


def _js_slice(js, opener, closer):
    start = js.index(opener)
    return js[start : js.index(closer, start)]


@pytest.fixture(scope="module")
def js_card():
    """
    renderCard(), plus the two helpers it composes the card out of. whenCell()
    builds the card's <time> and archivedLink() the second source link, so a
    check that asks whether the card renders something has to see them: they are
    the card, just not in one function.
    """
    js = read(DASHBOARD_JS)
    parts = [
        _js_slice(js, "function renderCard(r) {",
                  "\n  // --- The rest of the page follows the filters"),
        _js_slice(js, "function whenCell(r) {", "\n  // The archived copy"),
        _js_slice(js, "function archivedLink(r) {", "\n  // ONE RESULT CARD"),
    ]
    return strip_comments("\n".join(parts))


@pytest.fixture(scope="module")
def js_template():
    """
    The return expression of renderCard(). This, and not the order the local
    variables happen to be declared in, is the order a reader meets the card in.
    """
    card = strip_comments(
        _js_slice(js_source(), "function renderCard(r) {",
                  "\n  // --- The rest of the page follows the filters")
    )
    return card[card.index("return '<li class=\"tit-card\">'") :]


def js_source():
    return read(DASHBOARD_JS)


@pytest.fixture(scope="module")
def php_card():
    """The body of tit_card_html(), the only place the server builds a card."""
    php = read(SHORTCODES_PHP)
    start = php.index("function tit_card_html($r) {")
    end = php.index("\n    return ob_get_clean();", start)
    return strip_comments(php[start:end])


# What each contract slot looks like inside each renderer. Most are the class
# itself; a couple are a local variable because the element is built a few lines
# above. If a rename makes one of these miss, the test says so and this map is
# the one place to fix it.
JS_TOKEN = {
    "card": r'<li class="tit-card"',
    "card-rail": r'class="tit-card-rail"',
    "card-employer": r'class="tit-card-employer"',
    "card-industry": r"\bindustry\b",
    "card-where": r'class="tit-card-where"',
    "card-body": r'class="tit-card-body"',
    "card-badges": r'class="tit-card-badges"',
    "card-dir": r"tit-card-dir",
    "card-ev": r"tit-card-ev",
    "card-amt": r"\bamount\b",
    "card-h": r'class="tit-card-h',
    "card-rt": r'class="tit-card-rt',
    "card-foot": r'class="tit-card-foot"',
    "card-when": r"\bwhenCell\(",
    "card-src": r'class="tit-card-src"',
}
PHP_TOKEN = dict(JS_TOKEN, **{
    "card-industry": r'class="tit-card-industry"',
    "card-amt": r"tit-card-amt",
    "card-when": r"tit-card-when",
})


def required_suffixes(contract):
    s = contract["structure"]
    out = [s["list"]["suffix"], s["card"]["suffix"]] + list(s["card"]["children"])
    for region in ("rail", "body", "foot"):
        out += s[region]["required_children"]
    out += contract["badges"]["order"]
    out.append(contract["not_stated"]["class"])
    return out


# --- The file itself, before anything that reads it -------------------------

def test_contract_parses_and_matches_its_digest():
    raw = CONTRACT_PATH.read_bytes()
    json.loads(raw)  # a contract that does not parse is not a contract
    assert hashlib.sha256(raw).hexdigest() == CONTRACT_SHA256, (
        "docs/card-contract.json changed. This file is byte-identical in the "
        "sibling ai-layoff-tracker repo and a change here is a change there: "
        "update CONTRACT_SHA256, update the digest recorded in docs/TECHLOG.md, "
        "and copy the file across. card-contract.yml goes red in BOTH repos "
        "until the two copies agree."
    )


def test_techlog_records_the_same_digest():
    """A spec doc that can disagree with the file it specifies is decoration."""
    assert CONTRACT_SHA256 in read(TECHLOG_PATH)


def test_contract_names_this_product(contract):
    assert PREFIX in [p["prefix"] for p in contract["products"]]


# --- The markup, read out of both renderers ---------------------------------

@pytest.mark.parametrize("renderer", ["js", "php"])
def test_every_required_class_is_rendered(contract, js_card, php_card, renderer):
    source = js_card if renderer == "js" else php_card
    for suffix in required_suffixes(contract):
        if suffix == "cards":
            # The list container is the shortcode's, not the card's.
            assert 'class="tit-cards"' in read(SHORTCODES_PHP)
            continue
        cls = "%s-%s" % (PREFIX, suffix)
        assert cls in source, (
            "the shared card contract requires a %s element and the %s renderer "
            "builds none" % (cls, renderer)
        )


@pytest.mark.parametrize("renderer", ["js", "php"])
def test_badges_render_in_the_contract_order(contract, js_template, php_card, renderer):
    """Direction, then evidence, then the amount. Always, and in that order."""
    source, tokens = (js_template, JS_TOKEN) if renderer == "js" else (php_card, PHP_TOKEN)
    at = []
    for suffix in contract["badges"]["order"]:
        m = re.search(tokens[suffix], source)
        assert m, "the %s renderer does not build %s" % (renderer, suffix)
        at.append(m.start())
    assert at == sorted(at), (
        "the badge row must render %s in that order (docs/card-contract.json -> "
        "badges.order)" % ", ".join(contract["badges"]["order"])
    )


@pytest.mark.parametrize("renderer", ["js", "php"])
@pytest.mark.parametrize("region", ["rail", "body", "foot"])
def test_each_region_renders_in_the_contract_reading_order(
    contract, js_template, php_card, renderer, region
):
    source, tokens = (js_template, JS_TOKEN) if renderer == "js" else (php_card, PHP_TOKEN)
    at = []
    for suffix in contract["structure"][region]["reading_order"]:
        m = re.search(tokens[suffix], source)
        assert m, "the %s renderer does not build %s" % (renderer, suffix)
        at.append(m.start())
    assert at == sorted(at), "the %s renders out of contract order" % region


@pytest.mark.parametrize("renderer", ["js", "php"])
def test_the_amount_badge_is_absent_when_there_is_no_amount(js_card, php_card, renderer):
    """
    Not a pill reading "no funding stated". The direction badge already says what
    the source did and did not tell us, and two badges saying one thing is the
    duplicate the contract removed.
    """
    source = js_card if renderer == "js" else php_card
    assert re.search(r"(usd > 0|\$usd > 0)", source), (
        "the amount badge must be built only when there is an amount"
    )


def test_the_two_renderers_agree_on_the_class_list(js_card, php_card):
    """
    A card that changes shape the first time a reader touches a filter is the
    same drift this contract exists to stop, one layer down.
    """
    def classes(src):
        return {c for c in re.findall(r"tit-card[a-z-]*", src)}
    js, php = classes(js_card), classes(php_card)
    assert js == php, (
        "renderCard() in dashboard.js and tit_card_html() in shortcodes.php must "
        "produce the same classes. Only in one of them: %s"
        % sorted(js.symmetric_difference(php))
    )


# --- The words. This is the part that drifted -------------------------------

def js_direction_labels():
    js = read(DASHBOARD_JS)
    block = re.search(r"var DIRECTION_LABEL = \{(.*?)\};", js, re.S)
    assert block, "dashboard.js declares no DIRECTION_LABEL"
    return dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", strip_comments(block.group(1))))


def php_direction_labels():
    php = read(SHORTCODES_PHP)
    block = re.search(
        r"function tit_direction_labels\(\) \{\s*return array\((.*?)\);", php, re.S
    )
    assert block, "shortcodes.php declares no tit_direction_labels()"
    return dict(re.findall(r"'(\w+)'\s*=>\s*'([^']*)'", block.group(1)))


@pytest.mark.parametrize("labels", [js_direction_labels, php_direction_labels])
def test_direction_labels_are_exactly_the_shared_four(contract, labels):
    assert labels() == contract["direction_labels"], (
        "the direction vocabulary must equal direction_labels in "
        "docs/card-contract.json exactly. These four strings are shared with the "
        "sibling AI Layoff Tracker and changing one here changes it there."
    )


def test_there_is_only_one_direction_vocabulary_in_this_plugin():
    """
    There were two. The dashboard said "Cutting Roles" and the employer page a
    click away said "Cutting back", and a reader had to work out that the two
    were one thing. tit_company_direction_labels() defers to the shared map now
    and must keep deferring.
    """
    php = strip_comments(read(COMPANY_PHP))
    body = re.search(
        r"function tit_company_direction_labels\(\) \{(.*?)\n\}", php, re.S
    )
    assert body, "tit_company_direction_labels() is gone"
    assert "tit_direction_labels()" in body.group(1)
    for retired in ("Hiring up", "Cutting back", "Update reported"):
        assert retired not in body.group(1)


def test_confidence_labels_are_the_ones_the_contract_records(contract):
    php = read(SHORTCODES_PHP)
    block = re.search(
        r"function tit_confidence_labels\(\) \{\s*return array\((.*?)\);", php, re.S
    )
    found = dict(re.findall(r"'(\w+)'\s*=>\s*'([^']*)'", block.group(1)))
    assert found == contract["evidence_labels"][PREFIX]

    js = read(DASHBOARD_JS)
    block = re.search(r"var CONFIDENCE_LABEL = \{(.*?)\};", js, re.S)
    found_js = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", block.group(1)))
    assert found_js == contract["evidence_labels"][PREFIX]


@pytest.mark.parametrize("renderer", ["js", "php"])
def test_the_not_stated_strings_are_shared_verbatim(contract, js_card, php_card, renderer):
    source = js_card if renderer == "js" else php_card
    for key in ("location", "date"):
        assert contract["not_stated"][key] in source


def test_no_em_dash_in_any_contract_label(contract):
    groups = [
        contract["direction_labels"],
        contract["evidence_labels"][PREFIX],
        contract["not_stated"],
    ]
    for group in groups:
        for key, text in group.items():
            if key in ("note", "class"):
                continue
            assert "—" not in text and "–" not in text


# --- Accessibility, and a real regression each -------------------------------

@pytest.mark.parametrize("renderer", ["js", "php"])
def test_no_aria_label_overrides_visible_card_text(js_card, php_card, renderer):
    """
    An aria-label on an element that already has text REPLACES that text for a
    screen reader. This product shipped longer, invisible, differently worded
    labels over its visible ones, and they read as a different page. Inside a
    card an aria-label is only ever allowed on an element with no text of its
    own, and no element in the card is icon-only today.
    """
    source = js_card if renderer == "js" else php_card
    assert "aria-label" not in source, (
        "no element inside the card carries an aria-label today, and none should "
        "be added over visible text. If you need one on an icon-only control, "
        "allowlist it here with the reason."
    )


@pytest.mark.parametrize("renderer", ["js", "php"])
def test_the_source_link_opens_out_safely(js_card, php_card, renderer):
    source = js_card if renderer == "js" else php_card
    assert 'rel="nofollow noopener"' in source
    assert 'target="_blank"' in source


def test_the_date_is_a_real_time_element(js_card, php_card):
    for source in (js_card, php_card):
        assert "<time" in source or "whenCell" in source
    js = read(DASHBOARD_JS)
    assert "<time class=\"tit-card-when\" datetime=" in js


# --- Nothing a reader could reach became unreachable -------------------------

def test_every_column_header_ordering_is_still_offered():
    """
    Four sortable <th> buttons went with the table. Their orderings did not: each
    is a server-rendered option on the one sort control, so an old share link
    still lands on the ordering it names.
    """
    php = read(SHORTCODES_PHP)
    block = php[php.index('<select id="tit-f-sort"') : php.index("</select>", php.index('<select id="tit-f-sort"'))]
    for value in ("employer", "employer_desc", "place", "place_desc",
                  "evidence", "evidence_desc", "newest", "oldest",
                  "notable", "raised"):
        assert 'value="%s"' % value in block, (
            "%s was reachable before the table became cards and must stay "
            "reachable" % value
        )


def test_the_retired_sort_headers_left_nothing_behind():
    js = read(DASHBOARD_JS)
    for dead in ("syncSortHeads", "tit-th-sort", "tit-th-arrow", "COL_SORT"):
        assert dead not in js
    assert "SORT_OPTION_LABEL" in js, (
        "applyUrlState() still needs it: a shared link can carry a sort value "
        "that arrives before its option does"
    )


# --- 375px, and not by measuring the symptom --------------------------------

def test_no_card_rule_pins_a_width():
    """
    NOT by comparing scrollWidth to innerWidth: that comparison passes on a
    clipped page, and an overflow-x rule on a narrow ancestor already guillotined
    a hero headline here once. What is checkable offline is the cause rather than
    the symptom: nothing inside a card may pin a width the viewport cannot give
    it. The table this replaced set min-width:760px, which is exactly the thing.
    """
    css = read(DASHBOARD_CSS)
    block = css[css.index(".tit-cards {") : css.index("/* Colour never carries meaning alone")]
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("/*") or stripped.startswith("*"):
            continue
        # A breakpoint is the opposite of the failure this guards: it is how the
        # rail becomes a column ABOVE 700px and stays stacked below it.
        if stripped.startswith("@media"):
            continue
        assert "min-width:" not in stripped.replace("min-width:0", ""), (
            "a card may not pin a width a 375px viewport cannot give it: %s"
            % stripped
        )


def test_long_values_wrap_rather_than_overflow():
    css = read(DASHBOARD_CSS)
    for selector in (".tit-card-employer", ".tit-card-h", ".tit-card-src"):
        at = css.index(selector)
        assert "overflow-wrap:anywhere" in css[at : at + 400].replace(" ", ""), (
            "%s holds arbitrary-length values and must wrap" % selector
        )
