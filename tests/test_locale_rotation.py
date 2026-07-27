"""Google News must ask each country's own edition.

The sibling hardcoded US:en and it was wrong for a global product: asking the
US English edition about hiring in Germany returns whatever US outlets happened
to cover it, which is almost nothing.
"""

from __future__ import annotations

import source_registry as registry
from run_collect import LOCALES_PER_RUN, build_locales, fair_share


def test_the_us_anchor_is_always_present():
    for run_index in range(2):
        assert registry.GOOGLE_NEWS_ANCHOR in build_locales(run_index)


def test_a_run_asks_more_than_one_edition():
    locales = build_locales(0)
    assert len(locales) == LOCALES_PER_RUN + 1
    assert len(set(locales)) == len(locales), "an edition should not repeat in one run"


def test_the_rotation_sweeps_every_edition():
    """Deterministic rotation, so the whole list is reached rather than a
    lucky subset. If this ever fails, coverage of some country silently
    stopped."""
    seen = set()
    for day in range(1, 200):
        for run_index in range(2):
            start = ((day * 2 + run_index) * LOCALES_PER_RUN) % len(registry.GOOGLE_NEWS_LOCALES)
            for i in range(LOCALES_PER_RUN):
                seen.add(registry.GOOGLE_NEWS_LOCALES[
                    (start + i) % len(registry.GOOGLE_NEWS_LOCALES)])
    assert seen == set(registry.GOOGLE_NEWS_LOCALES)


def test_the_cap_does_not_starve_the_last_query():
    """The sibling's MAX_ITEMS was a flat head slice, so with a broad sweep
    first the targeted company queries filled the cap and never fired."""
    items = (
        [{"query": "leadership", "id": i} for i in range(50)]
        + [{"query": "funding", "id": i} for i in range(50)]
        + [{"query": "hiring", "id": i} for i in range(50)]
    )
    kept = fair_share(items, 9)
    assert len(kept) == 9
    assert {k["query"] for k in kept} == {"leadership", "funding", "hiring"}


def test_fair_share_returns_everything_when_under_the_cap():
    items = [{"query": "a", "id": 1}, {"query": "b", "id": 2}]
    assert fair_share(items, 10) == items


def test_a_locale_is_a_real_country_code():
    for lang, country in registry.GOOGLE_NEWS_LOCALES:
        assert len(country) == 2 and country.isupper(), country
        assert lang and lang == lang.strip()


def test_every_rotated_locale_has_a_phrase_set_in_its_own_language():
    """The measured failure this prevents: English phrases in the German
    edition returned 2 items where German phrases returned 20, and Brazil
    returned 0. A locale without its own vocabulary is a silent zero dressed
    up as coverage."""
    for lang, country in registry.GOOGLE_NEWS_LOCALES:
        assert lang in registry.GOOGLE_NEWS_VOCAB, f"{country}:{lang} has no phrases"
        assert registry.google_news_queries(lang) is not registry.google_news_queries("en") \
            or lang == "en"


def test_the_free_filter_understands_those_languages_too():
    """Without this the queries would fetch correctly and every candidate would
    be dropped before the model ever saw it."""
    from pipeline.prefilter import passes

    real_headlines = (
        "SAP ernennt neuen Vorstandsvorsitzenden und schafft 300 Arbeitsplätze",
        "Nubank vai contratar 500 funcionários em São Paulo",
        "Renault nomme un nouveau directeur général",
        "Ferrari annuncia nuove assunzioni a Maranello",
        "ASML zoekt personeel en opent vestiging in Eindhoven",
        "Telefónica contratará 200 empleados en Madrid",
    )
    for headline in real_headlines:
        keep, why = passes(headline)
        assert keep, f"{headline}: {why}"

    # And still rejects what it always rejected.
    assert not passes("Bayern München gewinnt gegen Dortmund")[0]


def test_the_run_caps_itself_without_being_told_to():
    """Seven editions raised one run from about 25 candidates to about 215, and
    candidates are what cost money."""
    from run_collect import DEFAULT_CANDIDATE_CAP

    assert 10 <= DEFAULT_CANDIDATE_CAP <= 100
