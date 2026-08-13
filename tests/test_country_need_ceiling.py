"""What country-need ranking costs a saturated market, pinned as arithmetic.

THIS TEST CHANGES NOTHING AND ASKS FOR NOTHING. It is the audit of 2026-08-12
written down as executable statements, because the claim it examines — "US
recall is low partly BY DESIGN" — was one agent's reading of `candidate_rank`
and the next session deserves to inherit a check rather than a paragraph.

Everything here is deliberate behaviour, argued for in `pipeline/candidate_rank`
and in TECHLOG. Nothing here says any of it is wrong. What it says is what it
COSTS, so that a future change to the weights, the threshold or the round robin
has to walk past the price with its eyes open — and so that a session that
raises the read ration can watch these ceilings lift.

Two mechanisms, and they are NOT the same mechanism:

  the SCORE      pays 6.0 for a country holding nothing and 0 for one over
                 COUNTRY_THIN_ROWS. It decides who is ahead of whom.
  the ROUND ROBIN gives every country's best candidate a place before any
                 country's second. It decides how far down the budget reaches,
                 and at the cuts this project actually runs at it is the binding
                 one — a cut smaller than the number of countries present never
                 finishes pass one.

Free, offline, no model, no network, no database.
"""

from __future__ import annotations

import string

from pipeline import candidate_rank

#: Distinct two-letter codes. `candidate_country` only accepts a two-character
#: locale prefix, so a synthetic pool has to use real-shaped codes or every
#: item lands in the one unplaced bucket and the test measures nothing.
CODES = [a + b for a in string.ascii_uppercase[:8]
         for b in string.ascii_uppercase[:8]]

#: A corpus shaped like the real one: two saturated markets, a thin one, and a
#: country holding nothing. Numbers are the measured 2026-08-12 corpus rounded.
CONTEXT = candidate_rank.Context(
    rows_by_country={"US": 10437, "GB": 7982, "IE": 10, "KE": 0},
    known_employers=frozenset({"acme corp"}))


def item(headline, *, locale=None, collector="google_news", body=""):
    raw = f"{headline}\n\n{body}".strip()
    out = {"raw_text": raw, "headline": headline, "collector": collector,
           "source_url": "https://example.com/a/", "source_name": "Example"}
    if locale:
        out["locale"] = locale
    return out


def _codes(items):
    return [candidate_rank.candidate_country(i) for i in items]


# --- 1. the score ceiling, as arithmetic -----------------------------------

def test_a_saturated_country_cannot_outscore_an_empty_one_on_a_news_run():
    """The claim, checked against the weights rather than against a memory.

    A news candidate from a country over the threshold can earn at most
    employer_new + three keyword classes. That is 4.5 against 6.0, so no story
    about the largest market can overtake the first story about a country
    holding nothing, however completely its headline states the facts.
    """
    best_a_news_run_can_pay = (candidate_rank.W_EMPLOYER_NEW
                               + 3 * candidate_rank.W_KEYWORD_FORCE)
    assert best_a_news_run_can_pay == 4.5
    assert best_a_news_run_can_pay < candidate_rank.W_COUNTRY_EMPTY

    saturated = item("Acme Corp raises $71M in Series B, hiring 300 staff",
                     locale="US:en")
    empty = item("A company said something", locale="KE:en")
    assert candidate_rank.score(saturated, CONTEXT) < \
        candidate_rank.score(empty, CONTEXT)
    assert candidate_rank.rank([saturated, empty], CONTEXT) == [empty, saturated]


def test_the_one_arithmetic_exception_is_a_filing_and_it_is_inert_per_run():
    """`never` is false by 0.5, and the exception cannot fire in practice.

    A FILING from a saturated country reaches 6.5 and does outrank an empty
    country's news item. It is inert because a run collects from one collector
    at a time (candidate_rank's own module docstring says so of source_tier:
    "Inert in practice today"), so filings and news are never in one ordering
    outside a backfill that mixes them. Written down so nobody restates the
    ceiling as an absolute and is then surprised by a backfill.
    """
    ceiling_with_a_filing = (candidate_rank.W_SOURCE_TIER
                             + candidate_rank.W_EMPLOYER_NEW
                             + 3 * candidate_rank.W_KEYWORD_FORCE)
    assert ceiling_with_a_filing > candidate_rank.W_COUNTRY_EMPTY

    filing = item("Acme Corp raises $71M in Series B, hiring 300 staff",
                  locale="US:en", collector="sec_edgar")
    empty_news = item("A company said something", locale="KE:en")
    assert candidate_rank.rank([filing, empty_news], CONTEXT) == \
        [filing, empty_news]


def test_the_threshold_is_a_cliff_and_not_a_ramp():
    """One row either side of COUNTRY_THIN_ROWS is worth 3.0, which is more
    than every non-country signal a news run can earn except a perfect one."""
    context = candidate_rank.Context(rows_by_country={
        "AA": candidate_rank.COUNTRY_THIN_ROWS - 1,
        "BB": candidate_rank.COUNTRY_THIN_ROWS})
    just_under = item("A company said something", locale="AA:en")
    just_over = item("A company said something", locale="BB:en")
    assert (candidate_rank.score(just_under, context)
            - candidate_rank.score(just_over, context)) == \
        candidate_rank.W_COUNTRY_THIN


# --- 2. the round robin, which is the one that actually binds ---------------

def test_a_cut_smaller_than_the_country_count_never_reaches_the_last_bucket():
    """The measured shape: 37 reads, more than 37 countries present, US last.

    This is the walker's ration (DAILY_GATE_RATION = 37 against a measured 395
    candidates a day) meeting a 52-edition sweep. Pass one is truncated, so a
    bucket that sorts after the cut receives NOTHING — not a smaller share, not
    a later place, nothing that run.
    """
    pool = [item(f"Company{i} said something", locale=f"{CODES[i]}:en")
            for i in range(40) for _ in range(3)]
    saturated = [item(f"Acme{i} raises ${i}M in Series B", locale="US:en")
                 for i in range(40)]
    context = candidate_rank.Context(
        rows_by_country={"US": 10437}, known_employers=frozenset())

    ordered = candidate_rank.rank(pool + saturated, context)
    assert "US" not in _codes(ordered[:37])
    # And it is the truncation, not the score: extend the cut past the bucket
    # count and the saturated market starts receiving one place per pass.
    assert _codes(ordered[:41]).count("US") == 1
    assert _codes(ordered[:82]).count("US") == 2


def test_raising_the_cut_past_the_bucket_count_is_what_lifts_the_floor():
    """The lever that needs no policy change: depth.

    One place per pass is the round robin's own guarantee, so a saturated
    market's share of the budget is bounded below by cut / countries once the
    cut clears pass one. That is why "raise the ration" and "change the
    weights" are alternatives to each other and not the same request.
    """
    countries = 20
    pool = [item(f"Company{i} said something", locale=f"{CODES[i]}:en")
            for i in range(countries) for _ in range(4)]
    saturated = [item(f"Acme{i} raises ${i}M", locale="US:en")
                 for i in range(countries)]
    context = candidate_rank.Context(
        rows_by_country={"US": 10437}, known_employers=frozenset())
    ordered = candidate_rank.rank(pool + saturated, context)
    for passes in (1, 2, 3):
        cut = passes * (countries + 1)
        assert _codes(ordered[:cut]).count("US") == passes


def test_the_starvation_ends_when_the_other_countries_run_out_of_stories():
    """The round robin reserves NOTHING, and that is the mercy in it.

    A bucket that is empty is skipped, so on a quiet day everywhere else the
    saturated market takes the whole remaining budget. This is why the ceiling
    is a property of the DAY's candidate mix and not a fixed percentage, and
    why no single number can be quoted as "the US share" without the mix beside
    it.
    """
    pool = [item("Company said something", locale=f"{CODES[i]}:en")
            for i in range(5)]
    saturated = [item(f"Acme{i} raises ${i}M", locale="US:en")
                 for i in range(20)]
    context = candidate_rank.Context(
        rows_by_country={"US": 10437}, known_employers=frozenset())
    ordered = _codes(candidate_rank.rank(pool + saturated, context))
    assert ordered[:6].count("US") == 1        # one place in pass one
    assert ordered[6:].count("US") == 19       # and everything after



# --- 3. what the bucket actually is, which decides who pays -----------------

def test_the_penalty_falls_on_the_PUBLISHER_country_and_not_the_story():
    """Load-bearing, and easy to state backwards.

    `candidate_country` reads the Google News edition or the publisher's own
    country, and says in its own docstring that neither is what the story is
    about. So the ranking does not deprioritise US EVENTS; it deprioritises
    candidates surfaced by US editions and US publishers. A US funding round
    written up in Sao Paulo is ranked as Brazil and gains the full country-need
    bonus. Any estimate of what a policy change would do to US recall has to
    start here: what is starved is one ROUTE to US events, not the events.
    """
    same_story_us_edition = item("Acme Corp raises $71M in Series B",
                                 locale="US:en")
    same_story_br_edition = item("Acme Corp raises $71M in Series B",
                                 locale="BR:pt")
    context = candidate_rank.Context(rows_by_country={"US": 10437, "BR": 3})
    assert candidate_rank.score(same_story_br_edition, context) > \
        candidate_rank.score(same_story_us_edition, context)
    assert candidate_rank.rank(
        [same_story_us_edition, same_story_br_edition], context) == \
        [same_story_br_edition, same_story_us_edition]


def test_an_unplaced_candidate_is_ranked_last_within_every_pass():
    """3,330 of 4,060 stored news rows carry no country hint at all (measured
    2026-08-12, `analysis.ranking.read_share --model`), and they share ONE
    bucket that goes last. Recorded here because it means the unplaced share of
    the budget is bounded by one place per pass too, and a session reading the
    tables should not mistake that bucket for a country."""
    placed = [item("Company A said something", locale="KE:en"),
              item("Company B said something", locale="BR:pt")]
    unplaced = [item("Company C said something") for _ in range(5)]
    context = candidate_rank.Context(rows_by_country={"KE": 0, "BR": 3})
    ordered = candidate_rank.rank(placed + unplaced, context)
    assert _codes(ordered[:2]) == ["KE", "BR"]
    assert candidate_rank.candidate_country(ordered[2]) is None
