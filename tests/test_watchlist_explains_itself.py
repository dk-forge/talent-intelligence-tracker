# -*- coding: utf-8 -*-
"""The star says what it does, on the page, without being opened.

THE DEFECT. The owner asked three times what the star on an employer does and
could not tell from the dashboard. The answer was written down twice and both
copies were unreachable: a block comment in dashboard.js, and #tit-help-watch
inside <details id="tit-help">, which ships CLOSED. A control whose only
explanation is behind a disclosure has no explanation for the reader who does
not already know there is one to open.

WHY THESE ASSERTIONS AND NOT A SCREENSHOT. The suite cannot execute the plugin
or run a browser, so this file pins the two structural properties that the
failure had:

  ONE. The sentence exists in the served markup.
  TWO. It is not inside a container the script shuts on load. That is the exact
       shape of the older place-caveat bug (see
       test_chart_titles_and_basis.py): valid markup, correct prose, passed as
       tit_chart_head()'s note_html into .tit-chart-note, which dashboard.js
       closes on load, so it computed display:none on every browser that ran
       the script and a live audit found it measuring 0x0.

Plus the three properties that make the control legible once it is found: the
star is a real button with a plain-words name in both states, the 50-row limit
is stated where a reader meets it, and the two states differ by more than
colour.

RUN AGAINST THE PRE-FIX TREE BEFORE TRUSTING ANY OF IT. Two guards this week
passed against the defective code for the wrong reason, one by matching a
line-wrapped sentence and one by measuring a CSS width as a font size. Every
assertion below was executed against the tree at 0e17e640 and failed there.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
SHORTCODES_PHP = PLUGIN / "includes" / "shortcodes.php"
DASHBOARD_JS = PLUGIN / "assets" / "dashboard.js"
DASHBOARD_CSS = PLUGIN / "assets" / "dashboard.css"


def strip_php_comments(src: str) -> str:
    """Comments out, so every assertion reads the CODE and not the prose.

    The same reason test_chart_titles_and_basis.py does it: the notes beside
    this markup quote the very strings and ids being asserted, so a test that
    read the comments would pass on a tree where the markup had been deleted
    and only the explanation of it survived.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def flat(src: str) -> str:
    """Whitespace collapsed, because the markup wraps.

    A sentence in this file is written across three source lines. Matching it
    literally is how a guard passes against a tree it was written to reject.
    """
    return re.sub(r"\s+", " ", src)


def joined(src: str) -> str:
    """Adjacent JS string literals spliced back into one sentence.

    A message written as 'part one ' + 'part two' is one sentence to the
    reader and two literals to a search. Asserting on the literal as typed is
    the same class of mistake as matching a line-wrapped one: the guard then
    passes or fails on where somebody happened to break the line.
    """
    return re.sub(r"'\s*\+\s*'", "", flat(src))


def php() -> str:
    return strip_php_comments(SHORTCODES_PHP.read_text(encoding="utf-8"))


def js() -> str:
    return strip_js_comments(DASHBOARD_JS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# ONE: the explanation is on the page
# ---------------------------------------------------------------------------

def test_the_page_says_what_the_star_does():
    """In plain words, in the markup, whitespace collapsed first."""
    src = flat(php())
    assert 'id="tit-watch-hint"' in src, (
        "the standing explanation of the star is gone from the dashboard "
        "markup. The star's meaning then exists only in dashboard.js comments "
        "and the closed (i) panel, which is the state the owner reported."
    )
    assert "Star an employer on any update to follow it." in src, (
        "the sentence naming what pressing the star does is gone. It has to be "
        "on the page in words a first-time reader understands, not the word "
        "'watchlist' on its own."
    )
    assert "Turn Watchlist on to show just the employers you starred." in src, (
        "the markup no longer says what the Watchlist chip then does, so the "
        "star reads as an action with no visible consequence."
    )


def test_the_explanation_is_not_inside_a_container_the_script_closes():
    """THE property, and the one the older place-caveat bug violated.

    dashboard.js closes every .tit-chart-note on load, and <details> ships
    closed by definition, so an explanation inside either computes display:none
    on every browser that runs the script.
    """
    src = php()
    # Asserted rather than indexed, so a tree that simply lost the element fails
    # with the reason instead of a ValueError somebody has to go and decode.
    assert 'id="tit-watch-hint"' in src, (
        "there is no standing explanation to place, so this property is vacuous."
    )
    at = src.index('id="tit-watch-hint"')

    # Not inside the (i) disclosure, which is where the old copy lived.
    help_open = src.index('<details class="tit-help" id="tit-help">')
    help_close = src.index("</details>", help_open)
    assert not (help_open < at < help_close), (
        "the standing explanation has been moved back inside "
        "<details id='tit-help'>. That element ships closed, so the sentence "
        "reaches only readers who already know to open it, which is the defect."
    )

    # Not handed to tit_chart_head() as note markup, and not inside a chart note.
    assert "tit_chart_head" not in src[max(0, at - 400):at], (
        "the explanation is being printed through tit_chart_head(), which puts "
        "it inside .tit-chart-note. dashboard.js closes every one of those on "
        "load."
    )
    assert "tit-chart-note" not in src[max(0, at - 400):at + 400], (
        "the explanation has landed inside a .tit-chart-note panel."
    )

    # And it is not marked hidden by anything other than the one pass that
    # reveals it together with the chip.
    body = js()
    paint = body[body.index("function paintWatch("):]
    paint = paint[: paint.index("\n  }\n")]
    assert "watchHint.hidden = false" in paint, (
        "paintWatch() no longer reveals the explanation. The element ships "
        "hidden so that a browser without localStorage is not told about a "
        "control it does not have; if nothing unhides it, no reader ever sees "
        "it and the page is back to explaining the star nowhere."
    )
    assert re.search(r"watchHint\.hidden\s*=\s*true", body) is None, (
        "something hides the explanation again after load."
    )


# ---------------------------------------------------------------------------
# TWO: the control names itself
# ---------------------------------------------------------------------------

def test_the_star_is_a_button_with_a_plain_words_name_in_both_states():
    src = js()
    star = src[src.index("function decorateCards("):]
    star = star[: star.index("\n  }\n")]
    assert "createElement('button')" in star and "btn.type = 'button'" in star, (
        "the star must stay a real <button>: keyboard reachable, with the "
        "focus-visible outline the stylesheet gives it."
    )
    assert "aria-pressed" in star, "the star must report its pressed state."
    flat_star = flat(star)
    for phrase in ("Star this company to follow it", "Following. Click to unfollow."):
        assert phrase in flat_star, (
            f"the star's tooltip no longer says {phrase!r}. Both states need a "
            f"title AND an accessible name in words that do not assume the "
            f"reader knows the feature is called a watchlist."
        )
    assert "'Star ' + name + ' to follow it.'" in flat_star, (
        "the unstarred accessible name must name the employer and the action."
    )
    assert "'Following ' + name + '. Press to unfollow.'" in flat_star, (
        "the starred accessible name must say the state and how to undo it."
    )
    assert "'Watch this employer'" not in flat_star, (
        "the old label is back. 'Watch' alone was the wording the owner read "
        "three times without learning what it did."
    )


def test_the_two_states_differ_by_more_than_colour():
    star = js()[js().index("function decorateCards("):]
    star = star[: star.index("\n  }\n")]
    assert "★" in star and "☆" in star, (
        "one of the two glyphs is gone, so the state is carried by colour "
        "alone and is unreadable in monochrome or with a colour vision "
        "difference."
    )
    css = DASHBOARD_CSS.read_text(encoding="utf-8")
    rule = css[css.index(".tit-watch-star.is-on"):]
    rule = rule[: rule.index("}")]
    assert "background" in rule, (
        "the starred star has lost its second, non-colour signal. The glyph "
        "shape is the primary one and this tinted pill is the reinforcement; "
        "dropping it leaves a reader who cannot separate the two shades with "
        "one cue where the design promised two."
    )


# ---------------------------------------------------------------------------
# THREE: the 50-row limit is stated where a reader meets it
# ---------------------------------------------------------------------------

def test_the_fifty_row_limit_is_stated_when_the_filter_comes_up_short():
    """Not only when the list is empty.

    Star four employers, filter, see one card: the page looks like it lost
    three stars. The shortfall line has to name both numbers, and the empty
    case has to explain itself in the list where the missing cards would be.
    """
    body = joined(js())
    fn = js()[js().index("function applyWatchFilter("):]
    fn = fn[: fn.index("\n  }\n")]
    assert "matched < starred" in fn, (
        "the partial case is unhandled again: the shortfall message now shows "
        "only when NOTHING matches, so a watchlist that matches one of four "
        "starred employers silently drops three and says nothing."
    )
    assert "seen[name]" in fn, (
        "the shortfall must count DISTINCT starred employers on the page. "
        "Counting rows says 'showing 5 of your 3' as soon as one employer has "
        "three updates in the window."
    )
    assert flat("'Showing ' + matched + ' of the ' + starred + ' employers you starred. '") in flat(js()), (
        "the shortfall sentence no longer reports both numbers."
    )
    assert body.count("The watchlist narrows the newest 50 updates of this view") >= 2, (
        "the 50-row limit must be stated in BOTH states: the empty list and "
        "the short one. It is the reason the feature looks broken and it "
        "belongs where the reader meets it, not in the (i) panel."
    )
    assert "widen the filters to reach further back" in body.lower(), (
        "the message must say what to do next. An explanation with no remedy "
        "leaves the reader exactly as stuck."
    )


# ---------------------------------------------------------------------------
# FOUR: the confirmation on click
# ---------------------------------------------------------------------------

def test_starring_confirms_and_says_where_it_is_saved():
    body = flat(js())
    assert "Added to your watchlist. Saved in this browser only." in body, (
        "starring no longer confirms itself. The owner assumed the star wrote "
        "a cookie; the confirmation is where the page corrects that."
    )
    assert "Removed from your watchlist." in body, (
        "unstarring must confirm too, or removing feels like a misfire."
    )
    assert "cookie" not in body.lower(), (
        "the confirmation must not say 'cookie'. It would be inaccurate and it "
        "would weaken a real privacy claim: a cookie travels to the server on "
        "every request, and localStorage never leaves the browser."
    )


def test_the_confirmation_is_announced_and_does_not_stack():
    src = php()
    assert 'id="tit-watch-toast"' in src and 'aria-live="polite"' in flat(src), (
        "the confirmation region is gone from the markup or is no longer a "
        "live region, so the message is a purely visual effect."
    )
    at = src.index('id="tit-watch-toast"')
    assert "hidden" not in src[at:src.index(">", at)], (
        "the live region ships hidden again. A region that is display:none "
        "until the moment it is written is one several screen readers never "
        "announce; it must ship empty and permanent, with :empty carrying the "
        "cost of not being there."
    )
    body = js()
    fn = body[body.index("function sayWatch("):]
    fn = fn[: fn.index("\n  }\n")]
    assert "clearTimeout(watchToastTimer)" in fn and "setTimeout(" in fn, (
        "the confirmation must replace itself on one timer. Without the clear, "
        "starring six employers quickly leaves six overlapping timers and the "
        "message disappears while the last one is still being read."
    )
    assert "focus()" not in fn, (
        "the confirmation must not move focus: the keyboard stays on the star "
        "the reader just pressed."
    )


def test_reduced_motion_drops_the_animation_and_keeps_the_message():
    css = DASHBOARD_CSS.read_text(encoding="utf-8")
    # The stylesheet has several reduced-motion blocks. Find the one that
    # governs the confirmation rather than assuming it is the first.
    blocks = [
        css[m.start(): css.index("\n}", m.start()) + 2]
        for m in re.finditer(r"@media\s*\(prefers-reduced-motion", css)
    ]
    mine = [b for b in blocks if "tit-watch-toast" in b]
    assert mine, (
        "the confirmation is no longer covered by a reduced-motion rule, so a "
        "reader who asked for no motion still gets a fade."
    )
    block = mine[0]
    assert "transition:none" in block.replace(" ", ""), (
        "reduced motion must remove the transition."
    )
    assert "display:none" not in block.replace(" ", ""), (
        "reduced motion must drop the ANIMATION, never the message. A reader "
        "who asks for no motion still gets the confirmation."
    )
