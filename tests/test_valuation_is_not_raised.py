"""A valuation is never money raised.

On 2026-11-11 the money-basis check failed on two published rows summed as
company_raise at their VALUATION: Kalepa "mit 470 Millionen Dollar bewertet"
($470M valuation; its real raise, $75M, is a separate row) and Percent Labs
"closes seed round at over $1 million valuation" (the round was undisclosed).
The check caught them after publish; the write path must never store them.
"""

from pipeline import money_raised, validate

KALEPA_HEAD = ("Das KI-gestützte Versicherungs-Start-up Kalepa wird in einer neuen "
               "Finanzierungsrunde mit 470 Millionen Dollar bewertet")
KALEPA_SUM = ("The AI-powered insurance startup Kalepa is valued at $470 million in a "
              "new funding round. The article does not specify the funding stage.")
PERCENT_HEAD = "Syrian foodtech Percent Labs closes seed round at over $1 million valuation"
PERCENT_SUM = ("Syrian foodtech startup Percent Labs has closed an undisclosed seed funding "
               "round at a valuation exceeding $1 million, backed by Syrian angel investors.")


def test_the_two_published_valuations_are_valuation_only():
    assert money_raised.figure_is_valuation(470_000_000, KALEPA_HEAD, KALEPA_SUM)
    assert money_raised.figure_is_valuation(1_000_000, PERCENT_HEAD, PERCENT_SUM)


def test_a_raise_stated_beside_its_valuation_is_still_a_raise():
    head = "Kalepa Raises $75M Series B at $470M Valuation to Expand Insurance AI"
    assert not money_raised.figure_is_valuation(75_000_000, head, "")
    # ...and the valuation in that same headline is not the raise.
    assert money_raised.figure_is_valuation(470_000_000, head, "")


def test_a_raise_equal_to_no_valuation_is_untouched():
    assert not money_raised.figure_is_valuation(
        20_000_000, "Acme raises $20 million Series A", "Acme raised $20 million.")


def _build(head, summary, amount):
    raw = {"raw_text": f"{head}. {summary}", "headline": head,
           "source_url": "https://www.example-news.com/2026/11/10/kalepa/",
           "source_name": "Example News", "published_date": "2026-11-10"}
    cls = {"company": "Kalepa", "pillar": "company_development",
           "signal_direction": "hiring", "city": "New York", "country": "United States",
           "confidence": "reported", "headline": head, "summary": summary,
           "talent_readthrough": "Funding supports hiring.", "funding_amount": amount}
    return validate.build_signal(cls, raw, "google_news")


def test_the_write_path_never_stores_a_valuation_as_the_raise():
    signal = _build(KALEPA_HEAD, KALEPA_SUM, "$470 million")
    assert signal.funding_amount_usd is None
    assert signal.money_basis is None


def test_the_write_path_keeps_a_real_raise():
    head = "Kalepa Raises $75M Series B at $470M Valuation"
    signal = _build(head, "Kalepa has raised $75M in Series B funding.", "$75M")
    assert signal.funding_amount_usd == 75_000_000
    assert signal.money_basis == money_raised.COMPANY_RAISE


def test_the_stored_valuations_are_cleared_by_the_rederivation():
    """The rows stored before the write path learned this are fixed by the
    repo's standing correction, correct-funding-amount.yml, which re-derives
    funding_amount_usd with the write path's own rules and clears it live."""
    import correct_funding_amount as correct

    kalepa = {"funding_amount": "$470 million", "funding_amount_usd": 470_000_000,
              "country": "US", "headline": KALEPA_HEAD, "summary": KALEPA_SUM}
    percent = {"funding_amount": "over $1 million", "funding_amount_usd": 1_000_000,
               "country": "SY", "headline": PERCENT_HEAD, "summary": PERCENT_SUM}
    assert correct.rederivation(kalepa) == (470_000_000, None)
    assert correct.rederivation(percent) == (1_000_000, None)


def test_the_rederivation_leaves_a_real_raise_alone():
    import correct_funding_amount as correct

    row = {"funding_amount": "$75M", "funding_amount_usd": 75_000_000, "country": "US",
           "headline": "Kalepa Raises $75M Series B at $470M Valuation",
           "summary": "Kalepa has raised $75M in Series B funding."}
    assert correct.rederivation(row) is None


def test_a_corrected_raise_beside_a_valuation_string_is_kept():
    """Velaura's string is its valuation; its stored figure is the corrected
    raise. Re-deriving from the string must neither restore the valuation nor
    clear the raise."""
    import correct_funding_amount as correct

    row = {"funding_amount": "more than $1 billion", "funding_amount_usd": 110_000_000,
           "country": "US", "money_basis": "company_raise",
           "headline": "Chip designer Velaura AI valued at more than $1 billion in funding round",
           "summary": "Velaura AI has been valued at more than $1 billion in a recent funding round."}
    assert correct.rederivation(row) is None


def test_only_a_raise_is_judged_for_a_valuation():
    """'a donation valued at $250 million' is not a company's price; the row is
    not summed as raised anyway, so its figure is left alone."""
    import correct_funding_amount as correct

    row = {"funding_amount": "$250 million", "funding_amount_usd": 250_000_000,
           "country": "US", "money_basis": "outbound_investment",
           "headline": "NVIDIA Commits $250 Million to UK Research",
           "summary": "NVIDIA announced a compute donation valued at $250 million."}
    assert correct.rederivation(row) is None
