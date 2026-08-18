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


# --- The 43-language half ----------------------------------------------------
#
# 575 national press feeds across 139 countries were wired into a parser that
# only ever read English. Turkish was the language that happened to file first:
# 'milyon' was in no list, the token fell through to no multiplier, and four
# rounds of 190, 35, 30 and 12 MILLION dollars were stored as that many dollars.
# Nothing about that is Turkish. Every other wired language had the same hole.
#
# Every string below was read off a WIRED FEED on 2026-07-30 (the fetch is
# reproducible: data/feeds.csv, one request per publisher, titles only) or is
# the stored funding_amount of a live row. None of them is invented, because a
# fixture invented to match the parser proves only that it matches the fixture.

CATALOGUE = Path(__file__).parent.parent / "data" / "sources_catalogue.csv"


def _catalogue_languages():
    """The language column of the catalogue, split on the '/' of a bilingual
    masthead, restricted to the feeds that are actually WIRED."""
    import csv

    languages = set()
    with CATALOGUE.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not (row.get("feed_kind") or "").strip():
                continue  # catalogued but not wired: nothing arrives from it
            for part in (row.get("language") or "").split("/"):
                part = part.strip()
                if part:
                    languages.add(part)
    return languages


def test_every_wired_language_either_has_scale_words_or_is_named_as_a_gap():
    """Derived from the catalogue, never from whichever string last broke.

    This is the structural half of the fix. A magnitude vocabulary that is
    missing a language does not fail loudly; it stores a nine-figure round as
    a hundred and ninety dollars and looks like sparse data, which is exactly
    what the figure-guard measurement wrote down on 2026-07-30 when it costed a
    43-language fold and declined to guess at one. So the list of languages is
    read from the catalogue at test time, and a language that is neither
    covered nor explicitly named as a gap is a failure.
    """
    from pipeline.vocab import SCALE_WORDS_BY_LANGUAGE, UNCOVERED_LANGUAGES

    missing = sorted(_catalogue_languages()
                     - set(SCALE_WORDS_BY_LANGUAGE)
                     - set(UNCOVERED_LANGUAGES))
    assert not missing, (
        "wired language(s) with no scale vocabulary and no stated reason for "
        f"not having one: {missing}. Add the words, or add the language to "
        "UNCOVERED_LANGUAGES with why."
    )


#: (string, expected dollars, where it came from). The scale word in each is
#: the one the publisher's own language uses; the US dollar marker is what the
#: parser requires before it will read any of them.
REAL_FEED_SCALE_WORDS = [
    ("$190 Milyon Dolar", 190_000_000, "Unite.AI Turkish, live row"),
    ("$12 milyon dolarlık", 12_000_000, "N24 Haber, Turkish, live row"),
    ("USD 53 millones", 53_000_000, "Infobae, Spanish, live row"),
    ("US$ 470 milhões", 470_000_000, "Brazilian press, live row"),
    ("USD 300 εκατ", 300_000_000, "Politis, Greek: εκατ is how a headline "
                                  "actually abbreviates εκατομμύρια"),
    ("USD 3 млрд", 3_000_000_000, "Bluescreen.kz, Russian"),
    ("$25 milionów", 25_000_000, "Bankier.pl, Polish genitive plural"),
    ("$8 miljonu", 8_000_000, "Dienas Bizness, Latvian: the feed carried "
                              "miljonus, miljonu, miljoni and miljoniem in "
                              "one fetch, which is why forms are enumerated"),
    ("$12 milijuna", 12_000_000, "Bug, Croatian"),
    ("$7 milliárd", 7_000_000_000, "Forbes Hungary"),
    ("USD 12 juta", 12_000_000, "CNBC Indonesia"),
    ("$2,5 miliar", 2_500_000_000, "CNBC Indonesia: miliar is 10^9, and the "
                                   "comma is the decimal separator"),
    ("USD 15 triệu", 15_000_000, "CafeF, Vietnamese: triệu appeared 132 times "
                                 "in one fetch of the Vietnamese feeds"),
    ("$1,2 tỷ", 1_200_000_000, "CafeF, Vietnamese"),
    ("USD 40 מיליון", 40_000_000, "Geektime, Hebrew"),
    ("$5 مليون", 5_000_000, "Jo24, Arabic"),
    ("$3 mld", 3_000_000_000, "Bankier.pl / Agenda Digitale: mld is 10^9 in "
                              "Polish and Italian alike"),
    ("USD 5 milliarder", 5_000_000_000, "Aftenposten, Norwegian"),
]


@pytest.mark.parametrize("text,expected,where", REAL_FEED_SCALE_WORDS)
def test_the_scale_words_the_wired_feeds_actually_use(text, expected, where):
    assert parse_funding_usd(text) == expected, where


def test_milyar_is_a_billion_and_billones_is_not_a_billion():
    """The milliard family reads; the billion family refuses. Both are new.

    The table this replaces excluded `milliard` on the grounds that "a
    Scandinavian milliard is 10^9 and a Spanish billón is 10^12". The first
    clause is true and the second is about a DIFFERENT WORD. There is no
    long-scale disagreement about milliard anywhere: milliard, miliard, milyar,
    miljard, Milliarde, mia, mld, mrd, млрд, مليار and מיליארד are 10^9 in every
    language that has the word, so refusing them left the hole `milyon` left.

    `billion` is the word that means different things, and the same table had
    `billones` and `billioner` mapped to 10^9 — a Spanish billón and a Danish
    billion are 10^12, so those two entries were a thousand-fold understatement
    waiting for its first row. They refuse now.
    """
    assert parse_funding_usd("$190 Milyar Dolar") == 190_000_000_000
    assert parse_funding_usd("USD 2 miliardi") == 2_000_000_000
    assert parse_funding_usd("$4 Mrd") == 4_000_000_000
    for text in ("USD 5 billones", "$5 billioner", "USD 5 bilião",
                 "$5 Billionen"):
        assert parse_funding_usd(text) is None, text
    # English 'billion' is untouched: it is 10^9 and 3,000-odd rows rely on it.
    assert parse_funding_usd("$9.9 billion") == 9_900_000_000


def test_a_script_without_spaces_needs_no_word_boundary():
    """`\\b` is meaningless where nothing is delimited.

    Chinese, Japanese, Korean and Thai write the number, the scale word and the
    currency as one unbroken run of word characters — `1亿美元` — so a pattern
    ending in `亿\\b` can never match, because 美 is a word character too. That is
    the same class of defect as the Hebrew clitic that made `\\bגיוס\\b` miss most
    real headlines, and it is why these are matched as prefixes instead.

    The units are not translations of English ones either: 亿 is 10^8 and 万 is
    10^4, so reading 亿 as "billion" would be wrong by a factor of ten.
    """
    assert parse_funding_usd("USD 1亿") == 100_000_000
    assert parse_funding_usd("$3000万") == 30_000_000
    assert parse_funding_usd("USD 5억") == 500_000_000
    assert parse_funding_usd("$50 ล้าน") == 50_000_000
    # Longest token first, or 百万 reads as 万 and พันล้าน reads as พัน.
    assert parse_funding_usd("USD 2百万") == 2_000_000
    assert parse_funding_usd("$2 พันล้าน") == 2_000_000_000


def test_a_hebrew_clitic_does_not_hide_the_scale_word():
    """Single letters glued to the front of a noun are word characters.

    The prefilter work found this first: clitics for and/the/in/to are one
    letter and count as `\\w`, so a bare-noun match misses most real headlines.
    Stripping them is only safe because the remainder has to be a scale word we
    already know — a loose substring match is what put *salary* inside *a
    rental*.
    """
    assert parse_funding_usd("USD 40 מיליון") == 40_000_000
    assert parse_funding_usd("USD 40 כמיליון") == 40_000_000
    assert parse_funding_usd("USD 40 והמיליון") is None  # two clitics: not read


def test_a_language_cannot_silently_change_what_a_token_means():
    """Two languages that disagree about a token do not settle it by dict order.

    The vocabulary is declared per language and flattened at import; a token
    claimed with two different multipliers joins the refusal set unless
    RESOLVED_SCALE_COLLISIONS names the winner and the reason. That is the
    machinery that would have caught `billones` on the day Spanish was wired,
    and it is what stops the next language wired from moving a figure that
    3,000 rows already depend on.
    """
    from pipeline import vocab

    for token in vocab.AMBIGUOUS_SCALE_WORDS:
        assert token in vocab._AMBIGUOUS_SCALE, token
        assert token not in vocab._SCALE, token
    for token, (multiplier, why) in vocab.RESOLVED_SCALE_COLLISIONS.items():
        assert vocab._SCALE.get(token) == multiplier, token
        assert why, token
    # 'mil' and 'mi' keep refusing, from the sweeps that found them.
    assert parse_funding_usd("US$22 mil") is None
    assert parse_funding_usd("US$ 544 mi") is None


def test_a_three_digit_group_the_publishers_language_contradicts_refuses():
    """Where shape and locale disagree by a thousand-fold, decline.

    A lone separator with exactly three digits after it is a thousands group
    under BOTH conventions — Spanish writes '1,5 millones' and '1.500 millones'
    and never '1,500 millones' for one and a half — so shape decides, and that
    is what makes the Indonesian '$150.000' a hundred and fifty thousand.

    The exception is a three-digit group written with the separator that the
    scale word's own language uses for decimals. Then the two readings are a
    thousand apart and nothing in the string chooses, so no figure is stored
    and the verbatim amount stays on the row.
    """
    assert parse_funding_usd("$150.000") == 150_000
    assert parse_funding_usd("$1.500.000") == 1_500_000
    assert parse_funding_usd("USD 1 500 000") == 1_500_000
    assert parse_funding_usd("$1.000,50 million") == 1_000_500_000
    # 'milhões' is Portuguese and Portuguese writes decimals with a comma.
    assert parse_funding_usd("US$ 1,500 milhões") is None
    # 'million' is spelled the same in five wired languages that do not agree
    # about the separator, so it names no locale and shape decides.
    assert parse_funding_usd("$1,500 million") == 1_500_000_000


#: Strings the plausibility FLOOR is currently swallowing, and why each is a
#: parse failure rather than a small round. An allowlist rather than an exact
#: set, so that correcting a row keeps the build green while a NEW one turns it
#: red.
FLOOR_REFUSALS = {
    "$1": "Pluang, Indonesian. The headline reads 'pendanaan non-dilutif "
          "$1...', truncated mid-figure by the publisher; the article slug "
          "says 15 juta USD. Nothing in the string we hold states the round.",
    "$1 mili": "Pluang again, Indonesian, arrived 2026-08-02 (Rocket Lab: "
               "'pendanaan $1 mili...'). Truncated mid-SCALE-WORD by the "
               "publisher — 'mili' is the stem of 'miliar' (billion), but the "
               "string we hold does not finish the word, and reading a "
               "truncated stem as a known scale would be a guess. Refusing "
               "is correct.",
}

# QpiAI's '$20–25 million' (YourStory, 2026-08-17) was here for one day and is
# deliberately gone. It was never a truncation or an unknown scale word — it was
# a RANGE, and it only landed under the floor because the reader took '20' while
# the scale word sat at the far end. Documenting it here would have made this
# dict a treadmill: every '$X to $Y million' headline needs its own line, and a
# list everybody appends to is a list nobody reads. Ranges are detected before
# the number now (vocab.FUNDING_RANGE_POLICY) and cannot reach the floor at all,
# which test_a_range_never_arrives_as_a_floor_refusal holds.


@pytest.mark.skipif(not DB.exists(), reason="the committed database is the fixture")
def test_the_plausibility_floor_is_not_allowed_to_stand_in_for_the_guard():
    """The floor makes the test above unfailable. This is the guard restored.

    `parse_funding_usd` refuses anything under a thousand dollars, which is
    right — a sub-thousand figure means the string was cut short, the scale word
    was one we do not know, or a separator was misread, and refusing beats
    guessing. But it also means the property 'no stored amount parses absurdly
    small' can no longer fail, and the property anyone actually wanted checked
    was never about the parser's output range: it was that no string we HOLD is
    being read that way.

    So this reads the UNCLAMPED figure and names every string the floor is
    catching. The six Turkish and Indonesian rows of 2026-07-30 would have
    arrived here as six unexplained entries rather than as silence.
    """
    from pipeline.vocab import read_funding_figure

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT funding_amount FROM signals "
            " WHERE is_current = 1 AND funding_amount IS NOT NULL "
            "   AND funding_amount <> ''").fetchall()
    assert rows, "fixture check: the corpus should hold funding amounts"

    swallowed = []
    for (text,) in rows:
        figure = read_funding_figure(text)
        if figure is not None and figure < 1_000 and text not in FLOOR_REFUSALS:
            swallowed.append((text, figure))
    assert not swallowed, (
        "amount(s) the plausibility floor is quietly refusing, with no entry "
        "in FLOOR_REFUSALS saying why they are a parse failure rather than a "
        f"small round: {swallowed}. This is how a scale word we do not know "
        "arrives: read the string, find the language, add the word."
    )


# --- A stated range is a named outcome, not an accident ----------------------
#
# Every string below is a shape a publisher actually writes. The point of the
# block is not which answer they get — that is FUNDING_RANGE_POLICY and it is
# the owner's line to change — it is that they all get the SAME one. Before
# 2026-08-17 they did not: four of them stored a low end nothing marked as a low
# end, the rest hit the plausibility floor, and which half a headline landed in
# depended on whether its writer repeated the scale word.
RANGE_SHAPES = [
    ("$20–25 million", 20_000_000),        # QpiAI/YourStory, the live row
    ("$20-25 million", 20_000_000),        # the same, with an ASCII hyphen
    ("$20 to 25 million", 20_000_000),
    ("between $20 and $25 million", 20_000_000),
    ("USD 20-25 millones", 20_000_000),    # the scale word in another language
    ("$1.5-2 billion", 1_500_000_000),
    ("$5M to $10M", 5_000_000),            # first end carries its own scale
    ("$5M-$10M", 5_000_000),
    ("$20M–$25M", 20_000_000),
    ("$20 million to $25 million", 20_000_000),
    # The joiner is a word in 43 languages, not just 'to'.
    ("USD 20 a 25 millones", 20_000_000),      # Spanish
    ("$20 bis 25 Millionen", 20_000_000),      # German
    ("$20 ile 25 milyon", 20_000_000),         # Turkish
]

#: Shapes that are NOT ranges however much they look like one. A dash between a
#: number and its scale word is BetaKit's house style, and a parenthetical year
#: span is not a second end.
NOT_RANGES = [
    ("$20-million USD", 20_000_000),
    ("$1,500 million", 1_500_000_000),
    ("$1,000.0 million", 1_000_000_000),
    ("$600,000", 600_000),
    # A short joiner word with no second NUMBER behind it is not a range. This
    # is what keeps 'a', 'e' and 'do' from eating ordinary sentences.
    ("$5 million a year", 5_000_000),
    ("$12 milyon dolarlık", 12_000_000),
]


@pytest.mark.parametrize("text,low_end", RANGE_SHAPES)
def test_a_range_answers_the_same_whatever_its_typography(text, low_end):
    from pipeline import vocab

    if vocab.FUNDING_RANGE_POLICY == "refuse":
        assert vocab.parse_funding_usd(text) is None, text
    elif vocab.FUNDING_RANGE_POLICY == "low_end":
        assert vocab.parse_funding_usd(text) == low_end, text
    else:  # pragma: no cover - the constant documents its two values
        pytest.fail(f"unknown FUNDING_RANGE_POLICY {vocab.FUNDING_RANGE_POLICY!r}")


@pytest.mark.parametrize("text,expected", NOT_RANGES)
def test_a_hyphen_before_a_scale_word_is_not_a_range(text, expected):
    """The regression half. '$20-million USD' was the 2026-07-29 defect that
    added hyphen-attached multipliers; a range detector that ate it again would
    put that round back to twenty dollars, which is the failure this whole file
    exists about."""
    assert parse_funding_usd(text) == expected, text


def test_both_policies_are_typography_independent():
    """The property, checked against the OTHER policy too.

    Whichever line the owner picks, it has to mean the same thing for every
    shape in RANGE_SHAPES. Flipping the constant and re-reading is how this test proves the
    unchosen branch has not rotted, because the day it is chosen is the day
    nobody is looking at the branch that was not.
    """
    from pipeline import vocab

    original = vocab.FUNDING_RANGE_POLICY
    try:
        vocab.FUNDING_RANGE_POLICY = "low_end"
        for text, low_end in RANGE_SHAPES:
            assert vocab.parse_funding_usd(text) == low_end, text
        vocab.FUNDING_RANGE_POLICY = "refuse"
        for text, _ in RANGE_SHAPES:
            assert vocab.parse_funding_usd(text) is None, text
        for text, expected in NOT_RANGES:
            assert vocab.parse_funding_usd(text) == expected, text
    finally:
        vocab.FUNDING_RANGE_POLICY = original


def test_a_range_never_arrives_as_a_floor_refusal():
    """FLOOR_REFUSALS is for a language we cannot read, not for a shape we can.

    The allowlist above stays a signal only while every entry in it is a string
    somebody has to go and understand. A range is understood; it needs a policy,
    not a line. If a range can reach the floor again, the treadmill is back.
    """
    from pipeline.vocab import read_funding_figure

    for text, _ in RANGE_SHAPES:
        figure = read_funding_figure(text)
        assert figure is None or figure >= 1_000, (
            f"{text!r} reads as {figure}, which lands under the plausibility "
            "floor and would need a FLOOR_REFUSALS entry. Ranges are decided by "
            "vocab.FUNDING_RANGE_POLICY before the number is read; a range that "
            "reaches the floor means that detection missed a typography."
        )


@pytest.mark.skipif(not DB.exists(), reason="the committed database is the fixture")
def test_no_stored_range_is_carrying_a_figure_the_publisher_did_not_state():
    """The corpus half, and the one that would have caught the silent shapes.

    The floor guard above can only see a range that parses ABSURDLY SMALL, which
    is the accidental half. '$20M to $25M' parses to a perfectly plausible
    twenty million and would never have appeared anywhere. This reads the stored
    strings, finds the ones that state two ends, and holds them to the policy.
    """
    from pipeline import vocab

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT funding_amount FROM signals "
            " WHERE is_current = 1 AND funding_amount IS NOT NULL "
            "   AND funding_amount <> ''").fetchall()
    assert rows, "fixture check: the corpus should hold funding amounts"

    stored_ranges = []
    for (text,) in rows:
        normalised = str(text).strip()
        match = vocab._NUMBER.search(normalised)
        if not match or vocab._range_far_end(normalised, match) is None:
            continue
        if vocab.parse_funding_usd(text) is not None:
            stored_ranges.append((text, vocab.parse_funding_usd(text)))

    if vocab.FUNDING_RANGE_POLICY == "refuse":
        assert not stored_ranges, (
            "stored amount(s) that state a RANGE and are nonetheless carrying a "
            f"single figure: {stored_ranges}. Under FUNDING_RANGE_POLICY "
            "'refuse' a range has no figure, and one that has acquired one is a "
            "typography the detector does not recognise."
        )
