"""funding_amount_usd is the only figure on this tracker that is ARITHMETIC.

Every other number on the page is a count of rows. This one is a quantity read
out of a string a publisher wrote, it is summed into a headline total, and it is
the input to the guardrail that decides whether a single amount is implausible.
So a parse error here does not show up as a missing row; it shows up as a total
that is wrong by a factor of a million, and nothing about the page looks broken.

Five live rows were wrong that way on 2026-07-29, all of them by the same
mechanism: the parser knew English scale words and treated "no foreign currency
word I recognise" as "US dollars". Both halves are asserted here.

Sourced entirely from strings the database actually holds. A fixture invented to
match the parser proves the parser matches the fixture.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline.vocab import parse_funding_usd

DB = Path(__file__).parent.parent / "data" / "talent_intel.db"


# --- the five rows the audit found, verbatim from the database ---------------
#
# Each carries its publisher and what the row's own summary says the currency
# was, because the correct answer for three of them is "refuse", and "refuse"
# is only obviously right once you can see the article was not in dollars.
THE_FIVE = [
    # BetaKit. The multiplier was hyphenated to the number, \s* does not match
    # '-', so a $20M Series A was stored as twenty dollars.
    ("$20-million USD", 20_000_000, "BetaKit, stated USD"),
    # TechSavvy, Danish. _NON_USD's kron[ao]r? does not match "kroner".
    ("25 millioner kroner", None, "TechSavvy, Danish kroner"),
    # Infobae, Spanish, but the amount states USD outright, so it is summable.
    ("USD 53 millones", 53_000_000, "Infobae, stated USD"),
    # Bootstrapping.dk. "kr." was in no currency list, and "mio." in no
    # multiplier list, so 10.5m DKK became one hundred and five dollars.
    ("10,5 mio. kr.", None, "Bootstrapping.dk, Danish kroner"),
    # Expansion. The string names no currency at all; the row's summary says
    # euros. Absence of a currency is not a dollar.
    ("500 millones", None, "Expansion, euros per the summary"),
]


@pytest.mark.parametrize("text,expected,why", THE_FIVE)
def test_the_five_amounts_that_were_off_by_a_million(text, expected, why):
    assert parse_funding_usd(text) == expected, why


def test_a_dollar_has_to_be_stated_rather_than_merely_not_contradicted():
    """The rule that makes the next unknown currency safe.

    A denylist of foreign currency words is short by exactly the currencies
    nobody has met yet, which is how Danish kroner ended up in a US dollar
    column twice. The test is positive now: no '$', 'US$' or 'USD' in the
    string, no number.
    """
    for text in ("500 millones", "25 millioner kroner", "10,5 mio. kr.",
                 "1,5 milionu eur", "200 miljoen", "3 000 000",
                 "40 crore", "5 milliarder"):
        assert parse_funding_usd(text) is None, text
    # And the same figures WITH a stated dollar are read.
    assert parse_funding_usd("USD 500 millones") == 500_000_000
    assert parse_funding_usd("$200 miljoen") == 200_000_000


def test_a_european_decimal_comma_is_not_a_thousands_separator():
    """The trap that widening the multiplier vocabulary opens.

    Before this, every comma-decimal string was refused as a foreign currency
    before its number was read, so the ambiguity never surfaced. Now that
    'millones' is understood, 'USD 1,5 millones' has to be one and a half
    million rather than fifteen.
    """
    assert parse_funding_usd("USD 1,5 millones") == 1_500_000
    assert parse_funding_usd("$10,5 mio") == 10_500_000
    # A three-digit group after the comma is still a thousands separator, and a
    # string carrying both separators is read as English.
    assert parse_funding_usd("$1,500 million") == 1_500_000_000
    assert parse_funding_usd("$600,000") == 600_000
    assert parse_funding_usd("$1,000.0 million") == 1_000_000_000


def test_an_ambiguous_scale_word_refuses_instead_of_guessing():
    """'mil' is a million in Singapore English and a thousand in Spanish.

    It used to fall through to NO multiplier, so 'US$22 mil in pre-Series A'
    (Singapore, in the 2026-07-29 sweep) parsed as twenty-two dollars: wrong
    under every reading of the word. A thousand-fold error on a summed total is
    worse than an absent figure, and the verbatim string stays on the row.
    """
    assert parse_funding_usd("US$22 mil") is None
    assert parse_funding_usd("USD 22 mil") is None
    # Unambiguous neighbours still parse.
    assert parse_funding_usd("US$22 million") == 22_000_000
    assert parse_funding_usd("$22M") == 22_000_000


def test_the_shapes_that_make_up_the_corpus_still_parse():
    """The regression half. These are the forms 3,000-odd stored rows use, and
    a stricter currency rule must not have quietly emptied the money charts."""
    for text, expected in (
        ("$3.6M", 3_600_000), ("$1.45 Million", 1_450_000),
        ("$130 Million", 130_000_000), ("$6.0 million", 6_000_000),
        ("$71M", 71_000_000), ("$1.8bn", 1_800_000_000),
        ("USD 20.6 Million", 20_600_000), ("US$22 million", 22_000_000),
    ):
        assert parse_funding_usd(text) == expected, text
    # And the currencies that were already refused stay refused.
    for text in ("EUR 1.2 million", "R$ 5,3 milhoes", "C$5M", "S$10 million",
                 "HK$40M", "Tk200cr", "40 lakh", "250 crore"):
        assert parse_funding_usd(text) is None, text


@pytest.mark.skipif(not DB.exists(), reason="the committed database is the fixture")
def test_no_stored_amount_parses_to_an_absurdly_small_figure():
    """The property that would have caught all five without anyone auditing.

    A funding round of less than a thousand dollars is not a funding round. Every
    one of the five defects showed up here as a two- or three-digit dollar
    figure, and the whole class is visible in one query. Read from what the
    parser says about the strings we hold, not from the stored column, so the
    test passes as soon as the parser is right and does not wait on the
    correction run.
    """
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT funding_amount FROM signals "
            " WHERE is_current = 1 AND funding_amount IS NOT NULL "
            "   AND funding_amount <> ''").fetchall()
    assert rows, "fixture check: the corpus should hold funding amounts"

    absurd = []
    for (text,) in rows:
        parsed = parse_funding_usd(text)
        if parsed is not None and parsed < 1_000:
            absurd.append((text, parsed))
    assert not absurd, (
        "amount(s) that parse to less than a thousand US dollars, which is a "
        f"parse failure rather than a small round: {absurd}"
    )
