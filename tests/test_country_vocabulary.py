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
