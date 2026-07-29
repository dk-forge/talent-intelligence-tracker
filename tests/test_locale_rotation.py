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
    """Seven editions raised one run from about 25 candidates to about 215.
    Since the two-stage gate (2026-07-28) the candidate cap bounds the CHEAP
    look (one-word gate calls, ~1/40th of a read-through each) and the money
    is bounded separately by the full-classification ceiling. Both caps must
    exist and stay sane: an uncapped candidate list would still let a runaway
    feed rack up gate calls, and an uncapped read-through count is exactly the
    unbounded bill the old <=100 assertion existed to prevent."""
    from run_collect import DEFAULT_CANDIDATE_CAP
    from pipeline.classify import READTHROUGH_CAP

    # Upper bound raised 400 -> 2000 on 2026-07-29. It is a COST bound, so it
    # is derived from money rather than picked: at ~$0.00003 a gate call and
    # two runs a day, 2000 candidates is ~$3.60/month of cheap looks, which
    # sits inside the owner's ~$5 ceiling alongside the read-through spend the
    # separate cap below actually governs.
    #
    # 400 had itself become the coverage constraint the gate was built to
    # remove. The first real national_press run passed 1,018 items through the
    # FREE prefilter and sent 150 to the classifier: 868 already-judged-relevant
    # items discarded for cost, not quality - the exact sentence in
    # run_collect.py describing the superseded single-stage era. On the live
    # page it showed as 97% of rows being US or GB, with Israel on 15.
    assert 10 <= DEFAULT_CANDIDATE_CAP <= 2000
    assert 10 <= READTHROUGH_CAP <= 100
    # The gate exists precisely so the run can look at more than it classifies;
    # if the read-through ceiling ever exceeds the candidate cap, the gate is
    # dead code and every candidate is billed at the full rate again.
    assert READTHROUGH_CAP <= DEFAULT_CANDIDATE_CAP


def test_non_latin_scripts_survive_the_free_filter():
    """CJK and Arabic have no word boundaries the way \\b expects, so those
    terms are matched as substrings. Without that, adding Japanese, Korean and
    Arabic editions would have fetched correctly and dropped every candidate
    for free — the exact silent-zero shape the multilingual work exists to
    avoid. Headlines below are real, from the live editions on 2026-07-27.
    """
    from pipeline.prefilter import passes

    real = (
        "ニッケンかみそり 熊田征純氏、新社長に就任、地場産業発展へ",
        "한국유니온제약, 성광현 대표이사 선임",
        "الرئيس التنفيذي الجديد يناقش استراتيجية التوظيف",
        "Gizem Moral, Moka United’ın yeni CEO’su oldu",
        "NFZ zatrudni setki kontrolerów",
        "Han blir ny vd för ÖSK",
        "Hà Tĩnh: Hơn 23.000 việc làm tuyển dụng trong quý III",
    )
    for headline in real:
        keep, why = passes(headline)
        assert keep, f"{headline}: {why}"


def test_the_hiring_verb_that_is_also_a_football_transfer():
    """A live test of the Indonesian edition returned "Barcelona mencapai
    kesepakatan untuk merekrut bintang Man City" as a hiring signal. Several
    languages use the hiring verb for signing a player."""
    from pipeline.prefilter import passes

    for headline in (
        "Barcelona mencapai kesepakatan untuk merekrut bintang Man City",
        "Manchester City Siap Tolak Upaya Real Madrid Merekrut Rodri",
    ):
        assert not passes(headline)[0], headline


def test_public_sector_recruitment_notices_stay_out():
    """Instructions to applicants, not intelligence about an employer, in the
    same category as the Indian exam notices that were being stored."""
    from pipeline.prefilter import passes

    for headline in (
        "Thông báo tuyển dụng viên chức Trung tâm Công báo",
        "Kayseri'de KPSS fırsatı! Mülakatsız personel alımı başladı",
    ):
        assert not passes(headline)[0], headline


def test_the_recency_window_covers_the_gap_between_visits():
    """Adding eight languages made the sweep 6.2 days while the queries still
    asked `when:3d`, so every non-anchor market lost half its news and nothing
    errored: the markets simply returned less, which is indistinguishable from
    a quiet week. The window is derived from the rotation for that reason.
    """
    import math

    from run_collect import LOCALES_PER_RUN, RUNS_PER_DAY

    sweep_days = len(registry.GOOGLE_NEWS_LOCALES) / LOCALES_PER_RUN / RUNS_PER_DAY
    window = registry.recency_window_days(LOCALES_PER_RUN, RUNS_PER_DAY)
    assert window > sweep_days, (
        f"a locale comes round every {sweep_days:.1f} days but is only asked "
        f"about the last {window}: the difference is never seen"
    )


def test_adding_a_language_widens_the_window_by_itself():
    """The regression this prevents is adding locales and forgetting the
    window, which is exactly what happened on 2026-07-27."""
    narrow = registry.recency_window_days(3, 2)
    assert registry.recency_window_days(1, 2) > narrow, (
        "a slower rotation must widen the window without anyone remembering to"
    )


def test_no_phrase_carries_its_own_hardcoded_window():
    """A `when:` baked into a phrase would silently override the derived one."""
    for lang, phrases in registry.GOOGLE_NEWS_VOCAB.items():
        for phrase in phrases:
            assert "when:" not in phrase, f"{lang}: {phrase}"
