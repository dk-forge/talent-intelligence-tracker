"""LIGHT AND DARK, PINNED TO THE ARITHMETIC RATHER THAN TO A SCREENSHOT.

The stylesheet used to carry `color-scheme: only light` and a hard white
background on the wrapper, with two comment blocks explaining why dark mode
could not work here. The reasoning was real: re-inking TEXT while the parent
theme kept painting a white page produced light text on white, which is worse
than either scheme. What it was missing is that the surface has to be owned
too, not that the preference has to be refused.

So the file now carries three token blocks and nothing else knows which scheme
is in force. This test pins the four properties that make that safe, and every
one of them is a real failure mode somebody has shipped:

  1. THE DARK BLOCKS HOLD ONLY TOKENS. A component rule inside one is a second
     description of the same layout that only one scheme ever reads, and the
     two stop agreeing the first time either is edited.

  2. THE TOKEN SETS ARE IDENTICAL. A token defined light-only silently keeps
     its light value in dark. That is not a missing style, it is a white card
     in a dark page, and it is invisible until a reader reports it.

  3. AN EXPLICIT CHOICE BEATS THE MEDIA QUERY IN BOTH DIRECTIONS. Without the
     :not([data-theme="light"]) on the media block, a reader who picked light
     on a dark device gets dark anyway and the control looks broken.

  4. THE CONTRAST CLAIM IS COMPUTED, NOT ASSERTED. The ratios are read out of
     the values this file actually ships, so the palette cannot regress into a
     comment that still says it passes.

A NOTE ON MATCHING. Every assertion runs against text with comments STRIPPED.
A comment describing a call must never satisfy a test looking for the call --
this file is dense with prose about the very things it checks, and a naive
substring search would pass on the documentation of a feature that had been
deleted.

And on the semantic hues: they are compared by deltaE2000, not by contrast
ratio. The categorical palette is built to separate at similar lightness, so a
contrast ratio near 1.0 between two of them means only that they are equally
light. It is the wrong instrument and it reports false collisions.
"""
import math
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "wordpress-plugin" / "talent-intelligence-tracker"
CSS_PATH = PLUGIN / "assets" / "dashboard.css"
JS_PATH = PLUGIN / "assets" / "dashboard.js"
PAGE_PATH = PLUGIN / "includes" / "page.php"
SHORTCODES_PATH = PLUGIN / "includes" / "shortcodes.php"


# --- comment stripping ------------------------------------------------------

def strip_block_comments(text):
    """Remove /* ... */ everywhere. Used for CSS and for PHP."""
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def strip_js_comments(text):
    """Remove /* ... */ and // line comments.

    The // pass runs line by line and skips a // that sits inside a quoted
    string, because a URL in a string literal is not a comment and cutting it
    would silently delete the rest of that line from everything downstream.
    """
    text = strip_block_comments(text)
    out = []
    for line in text.split("\n"):
        quote = None
        i = 0
        cut = None
        while i < len(line):
            c = line[i]
            if quote:
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    quote = None
            elif c in "'\"":
                quote = c
            elif c == "/" and line[i + 1:i + 2] == "/":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


CSS = CSS_PATH.read_text(encoding="utf-8")
CSS_CODE = strip_block_comments(CSS)
JS_CODE = strip_js_comments(JS_PATH.read_text(encoding="utf-8"))
PAGE_CODE = strip_block_comments(PAGE_PATH.read_text(encoding="utf-8"))
SHORTCODES_CODE = strip_block_comments(SHORTCODES_PATH.read_text(encoding="utf-8"))


# --- block extraction -------------------------------------------------------

def _balanced(text, start):
    """The text of the brace-delimited block whose opening { follows `start`."""
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    raise AssertionError("unbalanced braces from offset %d" % start)


def root_light_block():
    m = re.search(r"(?m)^:root\s*\{", CSS_CODE)
    assert m, "no :root block: the light tokens have no single home"
    return _balanced(CSS_CODE, m.start())


def media_dark_block():
    """Selected by its CONDITION text, never by position.

    Indexing media blocks positionally is how a test starts asserting about
    the reduced-motion rule the day somebody adds one above this.
    """
    m = re.search(r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{", CSS_CODE)
    assert m, "no prefers-color-scheme: dark block"
    inner = _balanced(CSS_CODE, m.start())
    sel = re.search(r"^\s*([^{]+)\{", inner)
    assert sel, "the dark media block contains no rule"
    return sel.group(1).strip(), _balanced(inner, 0)


def attr_dark_block():
    m = re.search(r'(?m)^:root\[data-theme="dark"\]\s*\{', CSS_CODE)
    assert m, 'no :root[data-theme="dark"] block, so an explicit choice cannot win'
    return _balanced(CSS_CODE, m.start())


def declarations(block):
    """{name: value} for the custom properties declared in a block."""
    out = {}
    for name, value in re.findall(r"(--[A-Za-z0-9-]+)\s*:\s*([^;]+);", block):
        out[name] = value.strip()
    return out


def non_custom_declarations(block):
    """Everything in a block that is not a custom property or a nested rule."""
    flat = re.sub(r"\{[^{}]*\}", "", block)
    found = []
    for chunk in flat.split(";"):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("--"):
            continue
        if ":" in chunk:
            found.append(chunk.split(":", 1)[0].strip())
    return found


# --- (a) (b) (c) the three blocks exist and are selected correctly ----------

def test_a_light_tokens_live_in_one_root_block():
    tokens = declarations(root_light_block())
    assert len(tokens) > 60, (
        "the :root block holds %d tokens, which is too few to be the whole "
        "palette. Token-first means every colour has one home." % len(tokens)
    )
    assert "color-scheme" in root_light_block(), (
        "the light block must state color-scheme, or a user agent in dark mode "
        "repaints form controls and scrollbars underneath us"
    )


def test_b_the_media_block_stands_aside_for_an_explicit_light_choice():
    selector, _ = media_dark_block()
    assert '[data-theme="light"]' in selector and ":not(" in selector, (
        "the prefers-color-scheme block must exclude an explicit light choice "
        "(:root:not([data-theme=\"light\"])). Without it a reader who pressed "
        "Light on a dark device stays dark and the control reads as broken. "
        "Got: %s" % selector
    )


def test_c_an_explicit_dark_choice_has_its_own_block():
    block = attr_dark_block()
    assert "color-scheme" in block
    assert declarations(block), "the attribute block declares no tokens"


def test_c2_the_explicit_block_comes_after_the_media_block():
    """Equal specificity, so source order is what makes an explicit dark choice
    beat a light device."""
    media_at = CSS_CODE.index("@media")
    media_at = re.search(r"@media\s*\(\s*prefers-color-scheme", CSS_CODE).start()
    attr_at = re.search(r'(?m)^:root\[data-theme="dark"\]', CSS_CODE).start()
    assert attr_at > media_at


# --- (d) the dark blocks are tokens and nothing else ------------------------

def test_d_the_dark_blocks_contain_only_token_declarations():
    for label, block in (("media", media_dark_block()[1]), ("attribute", attr_dark_block())):
        assert "{" not in block, (
            "the %s dark block contains a nested rule. Only token "
            "redefinitions belong in there." % label
        )
        stray = [p for p in non_custom_declarations(block) if p != "color-scheme"]
        assert not stray, (
            "the %s dark block declares component styles (%s). A component rule "
            "only one scheme reads is a second description of the layout, and "
            "the two stop agreeing." % (label, ", ".join(stray))
        )


# --- (e) the opt-out is gone ------------------------------------------------

def test_e_the_hard_light_opt_out_is_gone():
    flat = re.sub(r"\s+", " ", CSS_CODE)
    assert "color-scheme: only light" not in flat and "color-scheme:only light" not in flat, (
        "`color-scheme: only light` pins the whole subtree to one scheme and "
        "makes every token block below it decorative"
    )


# --- (f) the token sets match -----------------------------------------------

def test_f_every_light_token_is_redefined_in_both_dark_blocks():
    light = set(declarations(root_light_block()))
    for label, block in (("media", media_dark_block()[1]), ("attribute", attr_dark_block())):
        dark = set(declarations(block))
        missing = sorted(light - dark)
        extra = sorted(dark - light)
        assert not missing, (
            "%s dark block does not redefine %s. A token defined light-only "
            "keeps its LIGHT value in dark, which paints a white card in a dark "
            "page and is invisible until a reader reports it."
            % (label, ", ".join(missing))
        )
        assert not extra, (
            "%s dark block defines %s, which has no light value at all"
            % (label, ", ".join(extra))
        )


def test_f2_the_two_dark_blocks_agree_with_each_other():
    a = declarations(media_dark_block()[1])
    b = declarations(attr_dark_block())
    differing = sorted(k for k in a if a[k] != b.get(k))
    assert not differing, (
        "the OS-asked and reader-asked dark palettes disagree on %s, so the "
        "page looks different depending on HOW it got to dark"
        % ", ".join(differing)
    )


# --- (g) no literal survives outside the token blocks -----------------------

def _outside_token_blocks():
    end = re.search(r'(?m)^:root\[data-theme="dark"\]', CSS_CODE).start()
    end = CSS_CODE.index("}", CSS_CODE.index("{", end)) if False else None
    m = re.search(r'(?m)^:root\[data-theme="dark"\]\s*\{', CSS_CODE)
    block = _balanced(CSS_CODE, m.start())
    after = CSS_CODE[CSS_CODE.index(block) + len(block):]
    before = CSS_CODE[:re.search(r"(?m)^:root\s*\{", CSS_CODE).start()]
    return before + after


def test_g_no_white_background_literal_outside_the_token_block():
    body = _outside_token_blocks()
    bad = re.findall(r"background(?:-color)?\s*:\s*(#fff\b|#ffffff\b)", body, re.I)
    assert not bad, (
        "%d background declarations still name white directly. In dark mode "
        "each of those is a white panel in a dark page." % len(bad)
    )


def test_g2_no_colour_literal_at_all_outside_the_token_blocks():
    body = _outside_token_blocks()
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(\s*\d", body)
    assert not literals, (
        "colour literals survive outside the token blocks: %s. Every value that "
        "participates in light or dark has to go through a token, or half the "
        "palette flips and half of it does not."
        % ", ".join(sorted(set(literals))[:12])
    )


# --- (h) the no-flash stamp -------------------------------------------------

def test_h_the_no_flash_stamp_is_inline_in_the_head():
    assert "add_action('wp_head', 'tit_theme_head'" in PAGE_CODE, (
        "nothing stamps the theme into <head>, so a reader who chose dark gets "
        "a white page painted first"
    )
    body = PAGE_CODE[PAGE_CODE.index("function tit_theme_head("):]
    body = body[: body.index("\nadd_action")]
    assert "localStorage" in body, "the stamp must read the reader's stored choice"
    assert "data-theme" in body, "the stamp must write the attribute the CSS reads"
    assert "tit-theme" in body, "the stamp must read the same storage key the control writes"
    assert "catch" in body, (
        "a browser with storage disabled throws on the read; the stamp must "
        "degrade to auto rather than erroring in the head of every page"
    )


def test_h2_the_stamp_reaches_the_recall_page_too():
    """Gated on the stylesheet, not on a list of route names."""
    body = PAGE_CODE[PAGE_CODE.index("function tit_theme_head("):]
    body = body[: body.index("\nadd_action")]
    assert "wp_style_is" in body and "tit-dashboard" in body, (
        "the gate must be the stylesheet, so every surface this plugin styles "
        "is covered without a route list to keep in step"
    )
    assert "is_page(" not in body and "get_query_var(" not in body


# --- the control itself -----------------------------------------------------

def test_the_control_is_three_real_buttons_with_a_named_state():
    assert "tit-theme" in JS_CODE, "no theme control is built"
    assert "'tit-theme'" in JS_CODE, "the storage key is not the one the head stamp reads"
    assert "createElement('button')" in JS_CODE, (
        "the control must be real buttons, so it is tab-reachable with no "
        "tabindex or key handling of ours"
    )
    assert "aria-pressed" in JS_CODE
    assert "aria-label" in JS_CODE and "Colour theme" in JS_CODE, (
        "the group must state the current theme in its own accessible name"
    )
    assert "removeAttribute('data-theme')" in JS_CODE, (
        "auto must REMOVE the attribute, or it is a third pinned theme rather "
        "than following the device"
    )
    assert "role', 'group'" in JS_CODE.replace('"', "'")


def test_the_control_has_a_visible_focus_state():
    assert re.search(r"\.tit-theme-b:focus-visible\s*\{[^}]*outline", CSS_CODE), (
        "the control must show keyboard focus"
    )


# --- the control that changes the theme is the last thing allowed to vanish -

def rule_body(selector):
    """The declarations of one rule, matched in the COMMENT-STRIPPED CSS.

    Comments are stripped before any of this matches, so a rule cannot be
    satisfied by a paragraph promising that it is satisfied. Every one of
    these files is more comment than code and that is the trap.

    Selectors are named WITHOUT whatever ancestor happens to prefix them, so
    a rename of the prefix cannot turn "this declaration is wrong" into "this
    rule is missing" and quietly pass for a new reason.
    """
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS_CODE)
    assert m, "no rule for %s, so nothing styles it" % selector
    return re.sub(r"\s+", "", m.group(1))


def test_the_theme_control_owns_its_colours_in_every_scheme():
    """It borrowed --tit-surface and --tit-line, and those are page furniture:
    tuned to be quiet against the page, which is the opposite of what the one
    control that rescues a reader from an unreadable theme needs to be."""
    blocks = {
        "light :root": declarations(root_light_block()),
        "the dark media block": declarations(media_dark_block()[1]),
        "the explicit dark block": declarations(attr_dark_block()),
    }
    missing = [
        "%s in %s" % (name, where)
        for where, T in blocks.items()
        for name in THEME_CONTROL_TOKENS
        if name not in T
    ]
    assert not missing, (
        "the theme control is not fully coloured in every scheme: %s"
        % ", ".join(missing)
    )


def test_the_theme_control_does_not_borrow_the_tokens_that_vanished():
    """--tit-line is 1.56:1 against the dark ground and --tit-surface is
    1.19:1. Naming either one here is the defect coming back."""
    for selector in (".tit-theme", ".tit-theme-b"):
        body = rule_body(selector)
        for borrowed in ("var(--tit-line)", "var(--tit-surface)", "var(--tit-surface-1)"):
            assert borrowed not in body, (
                "%s uses %s, which does not clear 3:1 against the dark page"
                % (selector, borrowed)
            )


def test_every_theme_button_has_a_fill_and_an_edge_of_its_own():
    """`border:0; background:none` is not a quiet button, it is no button:
    there is nothing on the page whose contrast could be measured, which is
    why the three of them read as loose words in the dark scheme."""
    body = rule_body(".tit-theme-b")
    assert "border:0" not in body and "border:none" not in body, (
        "a theme button with no border has no boundary to see"
    )
    assert "background:none" not in body and "background:transparent" not in body, (
        "a theme button with no fill leans entirely on the well behind it"
    )
    assert "background:var(--tit-theme-b-bg)" in body, (
        "each theme button needs a fill of its own"
    )
    assert "border:1pxsolidvar(--tit-theme-b-line)" in body, (
        "each theme button needs an edge of its own"
    )
    well = rule_body(".tit-theme")
    assert "background:var(--tit-theme-bg)" in well, "the well needs a fill"
    assert "border:1pxsolidvar(--tit-theme-line)" in well, "the well needs an edge"


def test_the_selected_theme_button_is_marked_by_shape_as_well():
    """The palette carries state by shape elsewhere (the watchlist star fills
    in), and the selected theme has to as well: fill plus weight is two colour
    cues wearing a hat. A dot that is present or absent is neither."""
    mark = rule_body(".tit-theme-b::before")
    assert "content:" in mark, "the state marker must be a painted box"
    assert "background:currentColor" in mark, (
        "the marker must be the label's own ink, or it is one more colour that "
        "can fail on its own"
    )
    on = rule_body('.tit-theme-b[aria-pressed="true"]::before')
    assert "opacity:1" in on, "the marker must appear only on the pressed button"
    assert "opacity:0" in mark, "the marker must be absent on the others"


def test_the_selected_marker_cannot_change_the_control_s_width():
    """375px: the group is flex-end against the page edge, so anything that
    widens it on press pushes the whole control out of the viewport. The box
    is in the layout in EVERY state and only the paint changes, so pressing a
    button reflows nothing."""
    mark = rule_body(".tit-theme-b::before")
    for geometry in ("width:7px", "height:7px", "margin-right:6px", "display:inline-block"):
        assert geometry in mark, (
            "the marker must reserve %s on every button, pressed or not" % geometry
        )
    on = rule_body('.tit-theme-b[aria-pressed="true"]::before')
    for geometry in ("width", "height", "margin", "padding", "content", "display"):
        assert geometry not in on, (
            "the pressed marker may only change paint, never layout, and it "
            "sets %s" % geometry
        )


def test_the_focus_ring_is_the_control_s_own_colour():
    body = rule_body(".tit-theme-b:focus-visible")
    assert "outline:2pxsolidvar(--tit-theme-focus)" in body, (
        "the ring must be the control's own token, measured against the well "
        "and against the page"
    )
    assert "outline-offset:2px" in body, (
        "the ring sits outside the chip, which is what puts it on a surface "
        "the test measures it against"
    )


def test_the_control_reaches_the_recall_page():
    assert "#tit-recall" in JS_CODE, (
        "the control is built inside the dashboard-only closure, so the recall "
        "page (#tit-recall) never gets one"
    )


def test_the_transition_is_dropped_for_reduced_motion():
    blocks = [
        CSS_CODE[m.start(): CSS_CODE.index("\n}", m.start()) + 2]
        for m in re.finditer(r"@media\s*\(\s*prefers-reduced-motion", CSS_CODE)
    ]
    mine = [b for b in blocks if "tit-theme-b" in b]
    assert mine, "the theme control animates with nothing dropping it for reduced motion"
    assert "transition:none" in mine[0].replace(" ", "")


# --- (i) the server-rendered trend plot -------------------------------------

def test_i_the_trend_svg_emits_no_inline_colour():
    assert 'stroke="#' not in SHORTCODES_CODE, (
        "the trend plot still writes a hex stroke into the markup. The server "
        "cannot know the reader's scheme, and dashboard.js swaps this same "
        "server HTML in on every filter change, so the wrong hue arrives twice."
    )
    trend = SHORTCODES_CODE[SHORTCODES_CODE.index("function tit_trend_svg("):]
    trend = trend[:20000]
    assert "$s['colour']" not in trend, (
        "the plot still reads the hex out of the series and prints it"
    )
    assert "tit_trend_series_class" in trend, "the series carry no class to paint through"


def test_i2_every_series_class_the_server_can_emit_has_a_rule():
    keys = re.findall(r"'(\w+)'\s*=>\s*'#[0-9a-fA-F]{6}'", SHORTCODES_CODE)
    keys = keys[:5]
    assert len(keys) == 5, "could not read the trend palette keys"
    for key in keys:
        for part in ("s", "d", "w"):
            assert ".tit-tc-%s-%s" % (part, key) in CSS_CODE, (
                "tit-tc-%s-%s is emitted by the server and painted by nothing, "
                "so that series draws in the inherited colour" % (part, key)
            )


# ===========================================================================
# THE CONTRAST CLAIM, COMPUTED FROM THE SHIPPED VALUES.
# ===========================================================================

def _srgb(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(hexs):
    h = hexs.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(hexs):
    r, g, b = _rgb(hexs)
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def ratio(fg, bg):
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _lab(hexs):
    r, g, b = (_srgb(v) for v in _rgb(hexs))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def deltaE(c1, c2):
    """CIEDE2000. Contrast ratio is the wrong instrument for "are these two
    hues tellable apart": a palette built to separate at equal lightness reads
    as a collision to it."""
    L1, a1, b1 = _lab(c1)
    L2, a2, b2 = _lab(c2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) if Cb > 0 else 0
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dhp = 0.0
    if C1p * C2p != 0:
        dh = h2p - h1p
        dhp = dh - 360 if dh > 180 else (dh + 360 if dh < -180 else dh)
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2)
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    else:
        d = abs(h1p - h2p)
        hbp = (h1p + h2p) / 2 + (0 if d <= 180 else (180 if h1p + h2p < 360 else -180))
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dTh = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp > 0 else 0
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dTh)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def over(fg, bg, alpha):
    """A tinted cell composited onto its surface, which is what a reader sees.

    The heat map paints rgba(<channel>, alpha) over the card, so the ink is
    never on either colour on its own and checking against the surface alone
    overstates the ratio.
    """
    f, b = _rgb(fg), _rgb(bg)
    return "#" + "".join("%02x" % round(alpha * f[i] + (1 - alpha) * b[i]) for i in range(3))


def rgba_parts(value):
    """(hex, alpha) for an rgba() token, so a tint can be composited."""
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)", value)
    assert m, "not an rgba value: %s" % value
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    return "#%02x%02x%02x" % (r, g, b), float(m.group(4) or 1)


def star_ground(T, surface):
    tint, alpha = rgba_parts(T["--tit-star-bg"])
    return over(tint, surface, alpha)


def channels_to_hex(triplet):
    return "#%02x%02x%02x" % tuple(int(v) for v in triplet.split(","))


# Read lazily, and per test. Computing these at import time turns any single
# missing block into a COLLECTION error, which hides the other twenty-odd
# results behind the first thing that broke -- and the whole point of a suite
# like this is to say which properties hold and which do not.
def light_tokens():
    return declarations(root_light_block())


def dark_tokens():
    return declarations(attr_dark_block())


THEME_CONTROL_TOKENS = (
    "--tit-theme-bg", "--tit-theme-line",
    "--tit-theme-b-bg", "--tit-theme-b-line", "--tit-theme-b-fg",
    "--tit-theme-on-bg", "--tit-theme-on-fg", "--tit-theme-focus",
)


def _theme_control_pairs(T):
    """The control that CHANGES the theme, measured in the theme it lands in.

    This is the pass that was missing. Every ratio in _pairs is text on a
    surface, and the theme control never failed a text ratio: its labels were
    7.4:1 and its selected label 8.9:1 in the dark scheme while the control as
    an object was invisible, because the well was 1.19:1 against the page and
    its edge 1.56:1, and the three buttons had `border:0; background:none` and
    so had no boundary to measure at all. A control a reader cannot find is
    not saved by the contrast of the words inside it. So the BOUNDARIES are
    pairs here too, at the 3:1 that non-text UI has to clear, and each is
    checked against BOTH things it sits between -- an edge that clears the
    page but not its own fill has not survived, it has moved.
    """
    for name in THEME_CONTROL_TOKENS:
        assert name in T, (
            "%s is not defined: the theme control has no colour of its own and "
            "is borrowing tokens that vanish against a dark page" % name
        )
    ground = T["--tit-ground"]
    well, well_line = T["--tit-theme-bg"], T["--tit-theme-line"]
    chip, chip_line = T["--tit-theme-b-bg"], T["--tit-theme-b-line"]
    on = T["--tit-theme-on-bg"]
    return [
        # The label on the fill it actually sits on, in both states.
        ("theme chip label", T["--tit-theme-b-fg"], chip, 4.5),
        ("theme chip label on hover", T["--tit-fg"], chip, 4.5),
        ("theme selected label", T["--tit-theme-on-fg"], on, 4.5),
        # The well, against the page and against its own fill.
        ("theme well edge vs the page", well_line, ground, 3.0),
        ("theme well edge vs the well", well_line, well, 3.0),
        # Each button, so three controls read as three controls.
        ("theme chip edge vs the well", chip_line, well, 3.0),
        ("theme chip edge vs the chip", chip_line, chip, 3.0),
        # Selected, told apart from the well behind it and from its neighbours.
        ("theme selected fill vs the well", on, well, 3.0),
        ("theme selected fill vs an unselected chip", on, chip, 3.0),
        # The ring sits outside the chip, so it lands on the well or the page.
        ("theme focus ring vs the well", T["--tit-theme-focus"], well, 3.0),
        ("theme focus ring vs the page", T["--tit-theme-focus"], ground, 3.0),
    ]


def _pairs(T):
    """(name, foreground, background, minimum) for one scheme's tokens."""
    s1, s2, ground = T["--tit-surface-1"], T["--tit-surface"], T["--tit-ground"]
    body = [
        ("fg on surface-1", T["--tit-fg"], s1, 4.5),
        ("fg on surface", T["--tit-fg"], s2, 4.5),
        ("fg on ground", T["--tit-fg"], ground, 4.5),
        ("fg on quiet", T["--tit-fg"], T["--tit-quiet"], 4.5),
        ("fg on hover", T["--tit-fg"], T["--tit-hover"], 4.5),
        ("fg on blue-tint", T["--tit-fg"], T["--tit-blue-tint"], 4.5),
        ("fg on zone", T["--tit-fg"], T["--tit-zone-bg"], 4.5),
        ("mut on surface-1", T["--tit-mut"], s1, 4.5),
        ("mut on surface", T["--tit-mut"], s2, 4.5),
        ("mut on ground", T["--tit-mut"], ground, 4.5),
        ("mut on blue-tint", T["--tit-mut"], T["--tit-blue-tint"], 4.5),
        ("mut on zone", T["--tit-mut"], T["--tit-zone-bg"], 4.5),
        ("link on surface-1", T["--tit-link"], s1, 4.5),
        ("link-hover on surface-1", T["--tit-link-hover"], s1, 4.5),
        ("ink-invert on blue", T["--tit-ink-invert"], T["--tit-blue"], 4.5),
        ("accent-ink on blue-tint", T["--tit-accent-ink"], T["--tit-blue-tint"], 4.5),
        ("ink-blue on blue-tint", T["--tit-ink-blue"], T["--tit-blue-tint"], 4.5),
        ("hiring on green-tint", T["--tit-hiring"], T["--tit-green-tint"], 4.5),
        ("cutting on red-tint", T["--tit-cutting"], T["--tit-red-tint"], 4.5),
        ("ink-violet on violet-tint", T["--tit-ink-violet"], T["--tit-violet-tint"], 4.5),
        ("ink-teal on teal-tint", T["--tit-ink-teal"], T["--tit-teal-tint"], 4.5),
        ("ink-ochre on surface-1", T["--tit-ink-ochre"], s1, 4.5),
        ("neutral-fg on track-2", T["--tit-neutral-fg"], T["--tit-track-2"], 4.5),
        ("warn on surface-1", T["--tit-warn"], s1, 4.5),
        ("trust-k on ground", T["--tit-trust-k"], ground, 4.5),
        ("degraded", T["--tit-degraded-fg"], T["--tit-degraded-bg"], 4.5),
        ("partial", T["--tit-partial-fg"], T["--tit-partial-bg"], 4.5),
        ("toast", T["--tit-toast-fg"], T["--tit-toast-bg"], 4.5),
        # --tit-faint is used at 10px to 15px in eleven rules and at no size
        # that qualifies as large text, so it is body text and the bar is 4.5.
        # It was checked at 3.0 here, which is the bar that excused a 3.29:1
        # value in the light theme rather than reporting it.
        ("faint on ground", T["--tit-faint"], ground, 4.5),
        ("faint on surface-1", T["--tit-faint"], s1, 4.5),
        ("faint on surface", T["--tit-faint"], T["--tit-surface"], 4.5),
        ("faint on quiet", T["--tit-faint"], T["--tit-quiet"], 4.5),
    ]
    # Non-text UI: bars, ticks and swatches carry meaning by their extent.
    large = [
        ("blue bar", T["--tit-blue"], s1, 3.0),
        ("orange bar", T["--tit-orange"], s1, 3.0),
        ("violet bar", T["--tit-violet"], s1, 3.0),
        ("teal bar", T["--tit-teal"], s1, 3.0),
        ("ochre bar", T["--tit-ochre"], s1, 3.0),
        ("neutral bar", T["--tit-neutral-bar"], s1, 3.0),
        ("stat 5 stripe", T["--tit-stat-5"], s1, 3.0),
        ("recall held line", T["--tit-rc-held"], s1, 3.0),
        ("recall clean line", T["--tit-rc-clean"], s1, 3.0),
        ("roo deep on roo surface", T["--roo-deep"], T["--roo-surface"], 3.0),
        # The watchlist star is a GLYPH USED AS AN ICON, and its state is
        # carried by shape first (hollow becomes filled), so 3:1 is the bar it
        # has to clear rather than 4.5:1 -- and it is checked against the
        # tinted pill it actually sits on, not against the bare card.
        ("watch star on its pill", T["--tit-star"], star_ground(T, s1), 3.0),
    ]
    for key in ("hiring", "funded", "leadership", "pay", "total"):
        large.append(("trend " + key, T["--tit-tc-" + key], s1, 3.0))
    # The heat map composites before anything is read on it.
    heat = over(channels_to_hex(T["--tit-heat-rgb"]), s1, 0.30)
    heat_hover = over(channels_to_hex(T["--tit-heat-rgb"]), s1, 0.42)
    money = over(channels_to_hex(T["--tit-heat-money-rgb"]), s1, 0.30)
    body += [
        ("fg on the strongest heat cell", T["--tit-fg"], heat, 4.5),
        ("fg on a hovered heat cell", T["--tit-fg"], heat_hover, 4.5),
        ("ink-ochre on the money row", T["--tit-ink-ochre"], money, 4.5),
    ]
    return body + large + _theme_control_pairs(T)


def _report(scheme, T):
    failures = []
    for name, fg, bg, need in _pairs(T):
        got = ratio(fg, bg)
        if got < need:
            failures.append("%s: %s on %s is %.2f:1, needs %.1f"
                            % (name, fg, bg, got, need))
    assert not failures, (
        "the %s palette does not meet AA:\n  %s" % (scheme, "\n  ".join(failures))
    )


def test_the_light_palette_meets_aa():
    _report("light", light_tokens())


def test_the_dark_palette_meets_aa():
    _report("dark", dark_tokens())


def _separation(scheme, T, group, names):
    """Categorical hues have to stay tellable apart FROM EACH OTHER."""
    close = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            d = deltaE(T[a], T[b])
            if d < 15.0:
                close.append("%s vs %s: dE %.1f" % (a, b, d))
    assert not close, (
        "%s %s hues are not perceptually separated:\n  %s"
        % (scheme, group, "\n  ".join(close))
    )


TREND = ["--tit-tc-hiring", "--tit-tc-funded", "--tit-tc-leadership",
         "--tit-tc-pay", "--tit-tc-total"]
PALETTE = ["--tit-blue", "--tit-orange", "--tit-violet", "--tit-teal", "--tit-ochre"]
DIRECTION = ["--tit-hiring", "--tit-cutting", "--tit-warn", "--tit-neutral"]


def test_the_trend_series_stay_apart_in_both_schemes():
    _separation("light", light_tokens(), "trend series", TREND)
    _separation("dark", dark_tokens(), "trend series", TREND)


def test_the_chart_palette_stays_apart_in_both_schemes():
    _separation("light", light_tokens(), "chart palette", PALETTE)
    _separation("dark", dark_tokens(), "chart palette", PALETTE)


def test_the_direction_hues_stay_apart_in_both_schemes():
    _separation("light", light_tokens(), "direction", DIRECTION)
    _separation("dark", dark_tokens(), "direction", DIRECTION)


def test_dark_really_is_dark_and_light_really_is_light():
    """The cheapest possible check that the two blocks were not copy-pasted."""
    light, dark = light_tokens(), dark_tokens()
    assert _lum(light["--tit-surface-1"]) > 0.7, "the light surface is not light"
    assert _lum(dark["--tit-surface-1"]) < 0.1, "the dark surface is not dark"
    assert _lum(light["--tit-fg"]) < 0.1 and _lum(dark["--tit-fg"]) > 0.6
