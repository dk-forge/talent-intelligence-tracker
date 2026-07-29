"""The country vocabulary must cover everywhere we ask.

The bug this exists to prevent: the collector was querying 25 national editions
while `normalize_country` knew 23 countries. A live dry run correctly identified
a Philippine story and an Egyptian one, and both were silently filed as unknown
because the vocabulary had no entry to normalise them to. Nothing errored. The
records just arrived with no geography, in a product whose whole premise is
segmenting by geography.
"""

from __future__ import annotations

import source_registry as registry
from phpsource import balanced_block
from pipeline import vocab


def test_every_edition_we_query_has_a_country_entry():
    for lang, code in (registry.GOOGLE_NEWS_ANCHOR,) + tuple(registry.GOOGLE_NEWS_LOCALES):
        assert code in vocab.COUNTRY_NAMES, f"querying {code}:{lang} with no vocabulary for {code}"


def test_every_region_tab_code_resolves():
    """The dashboard's region tabs send country lists to the API. A code the
    vocabulary cannot produce is a tab that can never match anything."""
    tabs = (
        "US CA GB IE DE FR NL ES IT SE PL CH BE DK NO FI AT PT CZ GR RO HU "
        "IN SG JP CN HK AU NZ KR MY PH ID TH VN TW BR MX AR CL CO PE UY CR "
        "AE SA IL QA KW BH OM TR ZA NG KE EG MA GH ET"
    ).split()
    missing = [c for c in tabs if c not in vocab.COUNTRY_NAMES]
    assert not missing, f"region tabs reference countries with no entry: {missing}"


def test_the_countries_that_were_silently_dropped():
    assert vocab.normalize_country("Philippines") == "PH"
    assert vocab.normalize_country("Egypt") == "EG"


def test_aliases_are_derived_so_a_new_country_works_immediately():
    """Every country's own name must normalise without anyone remembering to
    add it to a second list. The hand-maintained alias list is exactly how the
    two above went missing."""
    for code, name in vocab.COUNTRY_NAMES.items():
        assert vocab.normalize_country(name) == code, f"{name} does not map to {code}"
        assert vocab.normalize_country(code) == code


def test_the_variants_a_newsroom_actually_writes():
    for text, code in (("USA", "US"), ("U.S.", "US"), ("UK", "GB"),
                       ("Britain", "GB"), ("UAE", "AE"), ("South Korea", "KR"),
                       ("Ivory Coast", "CI"), ("Czech Republic", "CZ"),
                       ("Türkiye", "TR"), ("Holland", "NL")):
        assert vocab.normalize_country(text) == code, text


def test_it_still_refuses_things_that_are_not_countries():
    """A fixed vocabulary that accepts anything is not a vocabulary. A US state
    is not a country, and neither is a company name."""
    for text in ("Colorado", "South Carolina", "Sidus Space", "", "   ", "XX"):
        assert vocab.normalize_country(text) is None, text


def _region_tabs():
    """Parse the region strip out of `tit_regions` as {name: set of codes}.

    This reads the ONE structure it is testing — the `$defs` list inside
    `tit_regions` — and is anchored on that function, not on whatever entry
    happens to sit next to it.

    The previous version of these assertions sliced the file from the literal
    "array('Europe'" to the literal "array('India'". India was a top-level tab
    at the time. When the strip was rebuilt into one exhaustive, non-overlapping
    taxonomy (1.36.0), India became a country inside Asia rather than a region
    of its own, the closing delimiter stopped existing, and the test died with a
    bare `ValueError: substring not found` — naming neither Europe nor India nor
    anything a reader could act on. The taxonomy change was correct and the
    property under test still held; only the delimiter was wrong.

    So: no assertion below may depend on a region's position in the list, on how
    many regions there are, or on the name of a region other than the one it is
    actually making a claim about.
    """
    import re
    from pathlib import Path

    php = (Path(__file__).parent.parent / "wordpress-plugin"
           / "talent-intelligence-tracker" / "includes" / "shortcodes.php").read_text()

    assert "function tit_regions" in php, (
        "tit_regions is gone from shortcodes.php; the region strip moved and "
        "these tests need to point at wherever it lives now"
    )
    # Anchor on the function under test, then let the brackets say where its
    # region list ends. The only literal either step depends on is the name of
    # the thing being asserted about.
    body = php[php.index("function tit_regions"):]
    defs = balanced_block(body, "$defs = array(", what="the region list in tit_regions")

    regions = {}
    # array('Name', 'AA,BB' . 'CC,DD') — the code lists are split across several
    # concatenated string literals purely for line length, so rejoin them.
    for match in re.finditer(r"array\(\s*'([^']*)'\s*,\s*((?:'[^']*'\s*\.?\s*)+)\)", defs):
        codes = "".join(re.findall(r"'([^']*)'", match.group(2)))
        regions[match.group(1)] = {c for c in codes.split(",") if c}

    assert len(regions) >= 3, f"parsed too few regions to be plausible: {regions}"
    return regions


def test_every_country_we_can_normalise_is_reachable_from_some_region_tab():
    """A Latvian employer was landing outside every region tab because LV was on
    no list. That is the failure mode worth guarding, and it is not specific to
    Latvia or to Europe: a country the vocabulary can PRODUCE but no tab can
    SELECT is a row that exists in the database and cannot be found in the UI.
    Nothing errors and nothing looks wrong. The row is just gone.

    Stated over the whole vocabulary, this keeps holding when regions are
    renamed, reordered, split or merged.
    """
    regions = _region_tabs()
    covered = set().union(*regions.values())
    unreachable = sorted(set(vocab.COUNTRY_NAMES) - covered)
    assert not unreachable, (
        f"these countries normalise fine but sit in no region tab, so their rows "
        f"cannot be filtered to: {unreachable}"
    )


def test_no_country_sits_in_two_region_tabs():
    """The strip's contract is one exhaustive, non-overlapping taxonomy, so the
    per-region counts sum to the total and a reader can trust them.

    The old strip put the UK beside Europe and India beside Asia, so GB and IN
    were each counted twice and the tabs added up to more than the world. This
    is what stops that returning.
    """
    regions = _region_tabs()
    seen, doubled = {}, {}
    for name, codes in regions.items():
        for code in codes:
            if code in seen:
                doubled.setdefault(code, [seen[code]]).append(name)
            seen[code] = name
    assert not doubled, f"countries claimed by more than one region tab: {doubled}"


def test_europe_is_the_whole_continent_not_a_shortlist_of_the_big_names():
    """The Latvia bug again, at the level the mistake was actually made: LV was
    not absent by accident, it was absent because the list was the well known
    European economies rather than Europe.

    This asserts that every one of these countries lands in the SAME region as
    the others, without naming that region — renaming the Europe tab is a
    presentation decision this test should not own. What must stay true is that
    a European employer is filed with the rest of Europe.
    """
    regions = _region_tabs()
    europe = {"GB", "IE", "DE", "FR", "NL", "ES", "IT", "SE", "PL", "BE",
              "DK", "NO", "FI", "AT", "PT", "CZ", "GR", "RO", "HU", "CH",
              "LV", "LT", "EE", "SK", "SI", "HR", "BG"}

    placement = {}
    for country in sorted(europe):
        home = [name for name, codes in regions.items() if country in codes]
        assert home, f"{country} is in no region tab at all"
        placement.setdefault(home[0], []).append(country)

    assert len(placement) == 1, (
        "European countries are split across different region tabs, so a reader "
        f"filtering to Europe misses some of it: {placement}"
    )
