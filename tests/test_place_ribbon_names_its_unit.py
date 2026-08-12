"""The place ribbon names what it counts, and says why the order looks so.

WHY THIS FILE EXISTS.

On 2026-08-05 every chart title on the dashboard was made to name its unit,
because "Where the Jobs Are" over a ranking of record counts is a wrong number
written in words. tests/test_chart_titles_and_basis.py is the guard from that
pass and it holds: the geography card is titled "Updates by Country" and says
every bar is a count of updates we hold.

The rename was applied to the CHART and not to its TWIN. The filter ribbon
directly above it kept the captions "Top Countries" and "Top Cities" over the
same numbers, with flags and a descending sort, so the page said both things at
once. The owner read the ribbon as a market ranking and asked why the United
Kingdom outranks the United States. It does not: Companies House publishes
structured filings for very nearly every UK company and we ingest all of them,
while the US equivalent reaches public companies only, so the ordering is a
picture of our collection method. That is the exact misreading the chart rename
existed to prevent, reached through the control sitting above the chart.

WHAT IS PINNED. Three properties, and none of them is the ordering: the numbers
and the sort are correct and this file must never be read as licence to change
them.

  1. Neither caption says "Top". A caption of that shape over a descending list
     of flags is a leaderboard, whatever the numbers underneath it mean.
  2. Both captions name the unit, in the vocabulary the chart already uses.
  3. The basis is stated as VISIBLE prose, above the rows, and it names the
     cause rather than just hedging.

COMMENTS ARE STRIPPED BEFORE ANYTHING IS MATCHED, and that is not a formality
here. The commit that fixes this defect adds a long comment ABOVE the ribbon
which quotes "Top Countries" verbatim to explain what was wrong with it. A
checker that read comments would find the quotation, fail against the fixed
tree, and pass against the broken one the moment somebody deleted the note.
That is the mistake test_chart_titles_and_basis.py records making once already.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHORTCODES_PHP = (
    ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / "includes" / "shortcodes.php"
)


def strip_comments(src):
    """PHP block and line comments out, so assertions read code, not prose."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def code():
    return strip_comments(SHORTCODES_PHP.read_text(encoding="utf-8"))


def ribbon_captions(src):
    """Every caption printed over a row of place filter chips."""
    return re.findall(r'<span class="tit-countries-label">([^<]+)</span>', src)


def test_the_ribbon_has_both_captions():
    """If this fails the rest of the file is asserting about nothing."""
    caps = ribbon_captions(code())
    assert len(caps) == 2, (
        f"expected the country caption and the city caption, found {caps!r}. "
        f"If a row was deliberately added or removed, read this file rather "
        f"than renumbering it."
    )


def test_no_place_caption_is_framed_as_a_leaderboard():
    for cap in ribbon_captions(code()):
        assert not re.search(r"\btop\b", cap, re.I), (
            f"place ribbon caption {cap!r} is framed as a ranking. These rows "
            f"are sorted by how many updates we hold, and a country with a "
            f"national company registry we ingest in full produces far more of "
            f"them per employer than one we reach through press and filings. "
            f"'Top' over that, with flags and a descending sort, is read as a "
            f"market ranking, which is the misreading that reached the owner."
        )


def test_every_place_caption_names_its_unit():
    """Same bar the chart titles clear, and the same word, so the page has one voice."""
    for cap in ribbon_captions(code()):
        assert "Updates" in cap, (
            f"place ribbon caption {cap!r} names no unit. The chart directly "
            f"below it is titled 'Updates by Country' and says every bar is a "
            f"count of updates we hold; a control above it that names only the "
            f"place makes the page say two different things about one number."
        )


def test_no_place_caption_claims_to_count_jobs_or_people():
    for cap in ribbon_captions(code()):
        for word in ("Jobs", "People", "Hiring", "Market"):
            assert word not in cap, (
                f"place ribbon caption {cap!r} contains {word!r}. Nothing in "
                f"this ribbon counts jobs, people or market activity; every "
                f"figure in it is a count of records we hold."
            )


def _places_block(src):
    """The ribbon's own container, from its opening div to its closing note."""
    start = src.index('<div class="tit-places">')
    return src[start : src.index('class="tit-places-note"', start)]


def _basis_or_fail(block):
    """The basis paragraph, or an assertion that NAMES what is missing.

    Written this way after running the file against the pre-fix tree: two of
    these tests reached for the basis line with .index() and died with a bare
    ``ValueError: substring not found``, which tells a reader of CI nothing
    about what is wrong with the page. A red run has to say what to go and
    look at.
    """
    assert "tit-places-basis" in block, (
        "the place ribbon prints no basis line at all, so there is nothing to "
        "check the wording of. The rows are ordered by how many updates we "
        "hold, and nothing on the control says so."
    )
    return block[block.index("tit-places-basis"):]


def test_the_basis_is_stated_and_is_visible_prose():
    """A caveat inside a disclosure is one nobody reads.

    Every .tit-chart-note panel on this page is closed by dashboard.js on load,
    so an element put there computes display:none and measures 0x0 in any
    browser that ran the script. Three caveats on this dashboard have already
    shipped that way. The basis line must therefore be a plain paragraph in the
    flow, carrying .tit-places-note, which is the class the contrast pass gave
    an explicit colour in both themes.
    """
    src = code()
    block = _places_block(src)
    assert "tit-places-basis" in block, (
        "the place ribbon prints no basis line. The captions alone say what is "
        "being counted but not why the United Kingdom leads it, which is the "
        "question the ordering actually provokes."
    )
    assert "tit-chart-note" not in block, (
        "the basis line is inside a .tit-chart-note. dashboard.js closes every "
        "one of those on load, so it would render display:none and be read by "
        "nobody."
    )
    assert "hidden" not in block.split("tit-places-basis")[0].rsplit("<p", 1)[-1], (
        "the basis paragraph ships hidden."
    )


def test_the_basis_sits_above_the_rows_it_qualifies():
    """A correction printed after a long descending list arrives too late.

    The misread country is by definition near the front of the list, which is
    forty rows long on a phone. This is the same placement argument the place
    chart's own caveat is built on.
    """
    src = code()
    block = _places_block(src)
    _basis_or_fail(block)
    assert block.index("tit-places-basis") < block.index('aria-label="Filter by country'), (
        "the basis line is printed below the country row. It must sit above "
        "both rows: the country a reader is surprised by is one of the first "
        "chips, so a note underneath arrives after the misreading."
    )


def test_the_basis_names_the_cause_and_not_only_the_caveat():
    """'These are our counts' without the mechanism does not answer the question.

    The owner's question was why one country outranks another. The answer is
    that we ingest a national company registry wholesale where one exists, so
    the line has to carry that, not merely disclaim the ranking.
    """
    src = code()
    basis = _basis_or_fail(_places_block(src))
    flat = re.sub(r"\s+", " ", basis).lower()
    assert "registry" in flat, (
        "the basis line does not name the mechanism. Countries where a national "
        "company registry can be read in full yield far more updates per "
        "employer than countries reached through press and filings, and that "
        "is the whole reason the order looks the way it does."
    )
    assert "not a ranking of the market" in flat, (
        "the basis line does not say what the order is NOT. Naming the cause "
        "without denying the leaderboard reading leaves a reader to draw the "
        "same conclusion with more detail."
    )


def test_the_basis_carries_no_dash_punctuation():
    """House style, and this file adds new reader-facing copy."""
    basis = _places_block(code())
    for ch, name in (("—", "em dash"), ("–", "en dash")):
        assert ch not in basis, f"the place ribbon copy contains an {name}"
