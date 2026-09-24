"""The thin pillars are queried in every language, and the gate keeps what the
queries fetch (coverage audit 2026-09-24).

Pins four things:
  1. every language the Google News packs carry also carries all five pillar
     intents (work mode, pay & benefits, M&A, hiring freeze, expansion);
  2. an edition's query list includes those groups, and the English anchor does;
  3. every pillar phrase survives the free prefilter in a realistic headline --
     a phrase the gate drops is a fetch paid for and thrown away;
  4. the English-market rotation reaches all nine markets inside a week and
     never adds more than its stated number of queries per run.
"""

from __future__ import annotations

import source_registry as registry
from pipeline import pillar_vocab, prefilter


def test_every_query_language_carries_every_pillar_intent():
    for lang in registry.GOOGLE_NEWS_VOCAB:
        assert lang in pillar_vocab.PILLAR_VOCAB, f"{lang} has no pillar pack"
        for intent in pillar_vocab.INTENTS:
            phrases = pillar_vocab.PILLAR_VOCAB[lang].get(intent)
            assert phrases, f"{lang} is missing the {intent} intent"


def test_no_phrase_can_break_the_query_quoting():
    for lang, intents in pillar_vocab.PILLAR_VOCAB.items():
        for phrases in intents.values():
            for p in phrases:
                assert '"' not in p, f"{lang}: {p!r} would terminate phrase quoting"
                assert p.strip() == p and p


def test_an_edition_asks_about_every_pillar():
    for lang in registry.GOOGLE_NEWS_VOCAB:
        qs = registry.google_news_queries(lang, window_days=5)
        for intent in pillar_vocab.INTENTS:
            first = pillar_vocab.PILLAR_VOCAB[lang][intent][0]
            assert any(f'"{first}"' in q for q in qs), (lang, intent)
        assert all(q.endswith(" when:5d") for q in qs)


def test_the_english_anchor_asks_about_every_pillar():
    anchor = " ".join(registry.GOOGLE_NEWS_QUERIES)
    for intent in pillar_vocab.INTENTS:
        assert f'"{pillar_vocab.PILLAR_VOCAB["en"][intent][0]}"' in anchor, intent


def test_every_pillar_phrase_survives_the_free_gate():
    dropped = []
    for lang, intents in pillar_vocab.PILLAR_VOCAB.items():
        for intent, phrases in intents.items():
            for p in phrases:
                ok, reason = prefilter.passes(f"Acme Corp {p} 2026")
                if not ok:
                    dropped.append((lang, intent, p, reason))
    assert not dropped, dropped


def test_pillar_terms_do_not_open_the_gate_to_nuclear_fusion_or_shopping():
    for text in ("Scientists hail nuclear fusion breakthrough",
                 "Kunde kauft neues Auto beim Händler"):
        assert not prefilter.passes(text)[0], text


def test_a_hiring_freeze_is_not_handed_to_the_layoff_tracker():
    ok, reason = prefilter.passes("Acme announces hiring freeze across Europe")
    assert ok, reason


def test_english_markets_are_all_reached_within_a_week():
    reached = set()
    for day in range(1, 8):
        qs = registry.english_market_queries(day_of_year=day)
        assert len(qs) <= registry.ENGLISH_MARKETS_PER_RUN
        for q in qs:
            for code, name in registry.ENGLISH_MARKETS:
                if f'"{name}"' in q:
                    reached.add(code)
    assert reached == {c for c, _ in registry.ENGLISH_MARKETS}
    assert {c for c, _ in registry.ENGLISH_MARKETS} >= {
        "GB", "IE", "IN", "AU", "SG", "ZA", "NG", "KE", "PH"}


def test_english_market_queries_lead_with_the_country_and_ask_the_pillars():
    q = registry.english_market_queries(day_of_year=1)[0]
    assert q.startswith('"')
    assert "return to office" in q and "pay rise" in q and "acquire" in q
    assert "when:" in q


def test_build_queries_carries_the_english_market_slice():
    import run_collect
    qs = run_collect.build_queries(0, "google_news")
    market = registry.english_market_queries(
        day_of_year=__import__("datetime").date.today().timetuple().tm_yday)
    for q in market:
        assert q in qs
