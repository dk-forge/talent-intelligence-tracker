"""The funding query, pinned against the headlines it was measured on.

Funding is the pillar with no filing regime anywhere in the world, so the query
is the whole route. On 2026-07-30 the query was `("raises" OR "raised")
("Series A" OR "Series B" OR "seed funding")` — Google News AND-s the groups, so
a round nobody called a Series or a seed could not match however many times the
article said "raises".

The headlines below are REAL: they are the publishers' own titles for funding
events the 2026-07-28 recall run recorded as misses. They are the fixture on
purpose. A vocabulary tuned against invented headlines is tuned against the
imagination of whoever wrote it, which is how the sibling's euphemism list took
many iterations to become useful and why this one starts from misses instead.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import source_registry as registry  # noqa: E402

# Real headlines, real misses, none of which the old query could reach.
MISSED_FUNDING_HEADLINES = (
    "Young Group closes a €22.5 million capital increase, led by Azimut",
    "Nexu secures $143M credit line from HSBC",
    "European defencetech leader Helsing secures $1.8B Series E at $18B valuation",
    "InsideDesk closes $12.6 million USD to grow its dental payment management platform",
    "Monumental secures $32M Series B to accelerate construction automation",
    "Senra Systems Announces $65 Million Series B, Plans for Third Manufacturing Facility",
    "Swissto12 Secures $70M in Series C Round",
    "UK robotics startup Humanoid hits $1.35B valuation with $152M Series A",
    "Augustus Announces $180M Series B at $1B Valuation",
    "AI security startup Glow emerges from stealth with $180 million at $1.2 billion valuation",
    "Sofab Inks announces $6 million funding to advance novel perovskite ETL materials",
    "Starlight Engine Raises ¥60.6 Billion Led by Global Brain",
    "Think gears up for GCC expansion with $8 million pre-seed funding",
    "Float raises €4.5M Series A to bridge Europe's funding gap",
)

# The class the widened query STILL cannot reach on the headline alone: a raise
# verb, an abbreviated amount and no noun anywhere. Kept as a fixture rather
# than deleted, because the honest ceiling of this change is a fact about it.
# Google News matches article text and not only headlines, so these are
# probably reached by the body copy — probably, because a direct RSS probe on
# 2026-07-30 returned zero rows for every query including the control, which
# settles nothing in either direction.
STILL_OUT_OF_HEADLINE_REACH = (
    "Israeli B2B sales AI agent co Aligned raises $60m",
    "Alva Industries lands €16M to scale next-generation electric motors",
    "BIZAY secures $55M to fuel US growth and industry consolidation",
    "Siigo secures $103.5M to support Latin America expansion",
)

# The negatives come from the same gold set: leadership events, which the
# funding query must not drag in. A query that widens by matching everything is
# not a wider query, it is a more expensive one.
LEADERSHIP_HEADLINES = (
    "Bunge Global SA Appoints Gregory Heckman's Successor as Chief Executive",
    "PwC Cyprus appoints new Territory Senior Partner",
    "Oman Duty Free announces new Chief Executive Officer",
    "Keitaro Inada appointed President of Tsuruya Yoshinobu",
    "Sabancı Holding announces board change",
)


def _groups(query: str) -> list:
    """The AND-groups of a Google News query string, as the engine reads them:
    each parenthesised OR-list is a group, and groups are AND-ed."""
    out, depth, buf = [], 0, ""
    for char in query:
        if char == "(":
            depth += 1
            if depth == 1:
                buf = ""
                continue
        if char == ")":
            depth -= 1
            if depth == 0:
                out.append([p.strip().strip('"') for p in buf.split(" OR ")])
                continue
        if depth:
            buf += char
    return out


def _matches(headline: str, query: str) -> bool:
    low = headline.lower()
    groups = _groups(query)
    if not groups:
        return False
    return all(any(term.lower() in low for term in group) for group in groups)


def _funding_queries() -> list:
    """Every English query that is about funding, wherever it is defined."""
    marks = ("series", "funding", "seed", "round", "stealth", "capital",
             "investment", "raises", "valuation")
    return [q for q in registry.GOOGLE_NEWS_QUERIES
            if any(m in q.lower() for m in marks)] + \
           [q for q in registry.GOOGLE_NEWS_VOCAB["en"]
            if any(m in q.lower() for m in marks)]


def _reaches(headline: str) -> bool:
    return any(_matches(headline, q) for q in _funding_queries())


def test_a_round_that_is_never_called_a_series_is_still_reachable():
    """The structural defect, and the one that does not depend on whether
    Google News reads the body as well as the headline: an AND with a stage
    word excludes growth rounds, debt facilities, credit lines and capital
    increases outright, in every language."""
    for headline in ("Nexu secures $143M credit line from HSBC",
                     "Young Group closes a €22.5 million capital increase, led by Azimut",
                     "AI security startup Glow emerges from stealth with $180 million"):
        assert _reaches(headline), headline


def test_a_stage_word_with_any_other_verb_is_still_reachable():
    """"Announces a Series B" and "Secures a Series C" are how half the market
    writes it, and the old query required the word "raises"."""
    for headline in ("Augustus Announces $180M Series B at $1B Valuation",
                     "Swissto12 Secures $70M in Series C Round",
                     "UK robotics startup Humanoid hits $1.35B valuation with $152M Series A"):
        assert _reaches(headline), headline


def test_most_of_the_measured_misses_are_now_within_reach():
    """37 of the 54 real missed funding headlines (69%), against 13 (24%)
    before. The bar below is set under what was measured, so ordinary rewording
    does not fail the suite, and far above the old query, so a revert does."""
    reached = [h for h in MISSED_FUNDING_HEADLINES if _reaches(h)]
    assert len(reached) >= 12, [h for h in MISSED_FUNDING_HEADLINES
                                if h not in reached]


def test_the_ceiling_of_this_change_is_stated_rather_than_hidden():
    """A widening that claims everything is a widening nobody can check."""
    assert not any(_reaches(h) for h in STILL_OUT_OF_HEADLINE_REACH)


def test_the_widening_does_not_swallow_leadership_news():
    for headline in LEADERSHIP_HEADLINES:
        assert not _reaches(headline), headline


def test_no_funding_query_is_a_bare_high_frequency_token():
    """The lesson the first live run paid for with "expansion", and the sibling
    paid for again with Czech "investice": an unanchored common word returns
    everything. Every OR-group here is either multi-word or sits in an AND."""
    for query in _funding_queries():
        groups = _groups(query)
        if len(groups) > 1:
            continue
        for term in groups[0]:
            # A single word is allowed only when it is rare enough that it
            # cannot be the "expansion" mistake again. `oversubscribed` appears
            # in almost nothing but funding copy; `funding`, `investment` and
            # `capital` never stand alone and always sit inside an AND.
            assert " " in term or term.lower() in {"oversubscribed"}, \
                f"{term!r} stands alone in {query!r}"


def test_gdelt_asks_about_funding_too():
    """GDELT is the only route into countries with no Google News edition worth
    the name, and a funding gap there is a funding gap in exactly the places
    recall measured at zero."""
    funding = [q for q in registry.GDELT_QUERIES
               if "funding" in q.lower() or "stealth" in q.lower()]
    assert len(funding) >= 2


def test_the_backstop_asks_about_funding_too():
    for term in ("raises", "funding round", "seed funding"):
        assert term in registry.BACKSTOP_INTENTS


def test_base_vocabulary_is_documented_as_off_the_query_path():
    """It carries no funding term, which reads as a hole in the largest pillar
    until you find that nothing queries it. The comment is the fix; padding the
    tuple would have looked like work and changed nothing that runs."""
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "source_registry.py"),
            encoding="utf-8") as handle:
        text = handle.read()
    head = " ".join(text.split("BASE_VOCABULARY = (")[0].replace("#", " ").split())
    assert "not on any live query path" in head
    assert "GOOGLE_NEWS_QUERIES" in head and "GDELT_QUERIES" in head
