"""The three largest private AI rounds of 2026, and every stage that lost them.

MEASURED 2026-08-04, on the live database and the live site:

  * OpenAI held 8 rows and the March 2026 close (~$122bn at ~$852bn) was not
    among them. The one OpenAI funding row on the page read
    "OpenAI capta 93.175 millones", verbatim, to an English reader.
  * Anthropic held 1 row. The February round ($30bn at $380bn) and the May
    Series H ($65bn at $965bn) were both absent from the page.

None of the three was a discovery failure: the funding query pack matched every
one of them and our own gate ledger holds the proof. They were lost downstream,
at four separate stages, and this file is one fixture per stage. Every headline
below is a REAL publisher title for one of those three rounds, and every amount
string is one a stored row would carry -- the same discipline
tests/test_funding_vocabulary.py keeps, and for the same reason: a vocabulary
tuned against invented headlines is tuned against the imagination of whoever
wrote it.

BEFORE this file's fixes, on the same corpus:
    prefilter kept 13 of 22, the amount parser read 3 of 14.
AFTER:
    22 of 22 and 14 of 14, with all seventeen must-refuse strings still refusing
    and the off-topic control unchanged.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import source_registry as registry  # noqa: E402
from pipeline import (cheap_extract, dedupe, gate_ledger, prefilter,  # noqa: E402
                      schema, store, validate, vocab)


# --------------------------------------------------------------------------
# 1. The prefilter, at billion scale
# --------------------------------------------------------------------------

#: (language, publisher, headline). Real titles, all three rounds.
MEGAROUND_HEADLINES = (
    # Anthropic Series H, 2026-05-28, ~$65bn at ~$965bn
    ("en", "anthropic.com",
     "Anthropic raises $65B in Series H funding at $965B post-money valuation"),
    ("en", "techcrunch.com",
     "Anthropic raises $65 billion, nears $1T valuation ahead of IPO"),
    ("fr", "usine-digitale.fr",
     "Anthropic lève 65 milliards de dollars en série H pour une valorisation "
     "de 965 milliards de dollars"),
    ("fr", "boursorama.com",
     "Anthropic frôle les 1 000 milliards de dollars de valorisation après une "
     "levée de fonds record"),
    ("ja", "xenospectrum.com", "Anthropic、650億ドル調達で評価額9,650億ドルに到達"),
    ("es", "extradigital.es",
     "Anthropic (Claude) supera a OpenAI (ChatGPT) en valoración tras captar "
     "65.000 millones de dólares"),
    ("he", "themarker.com",
     "עקפה את OpenAI: אנתרופיק גייסה לפי שווי של 965 מיליארד דולר"),
    # Anthropic Series G, 2026-02-12, $30bn at $380bn
    ("en", "reuters.com",
     "Anthropic clinches $380 billion valuation after $30 billion funding round"),
    ("en", "siliconrepublic.com",
     "Anthropic raises $30bn led by GIC, Coatue at $380bn valuation"),
    ("de", "computerbase.de",
     "Claude-Entwickler: Anthropic nimmt 30 Mrd. Dollar bei Finanzierungsrunde ein"),
    ("de", "qz.com", "Anthropic sammelt 65 Milliarden Dollar ein"),
    # OpenAI, March 2026 close, ~$122bn at ~$852bn
    ("en", "cnbc.com", "OpenAI closes funding round at an $852 billion valuation"),
    ("en", "bloomberg.com",
     "OpenAI Valued at $852 Billion After Completing $122 Billion Round"),
    ("en", "hl.co.uk",
     "openai clinches $122bn funding round, pushing valuation to $852bn"),
    ("en", "forbes.com",
     "OpenAI Valuation Reaches $852 Billion After Massive Funding Round"),
    ("es", "elperiodico.com",
     "OpenAI (ChatGPT) avanza en su salida a bolsa con una ronda de "
     "financiación récord de 122.000 millones de dólares"),
    ("de", "marketscreener.de", "OpenAI erreicht 840 Milliarden Dollar Bewertung"),
    ("he", "bizportal.co.il",
     "OpenAI גייסה 110 מיליארד דולר לפי שווי של 730 מיליארד"),
    # The shapes the packs demonstrably could not read, same three rounds.
    ("en", "-", "Anthropic hits $965 billion valuation"),
    ("es", "-", "Anthropic alcanza una valoración de 965.000 millones de dólares"),
    ("de", "-", "Anthropic erreicht eine Bewertung von 965 Milliarden Dollar"),
    ("en", "-", "Series H: Anthropic reaches a $965 billion post-money mark"),
)

#: The gate must still reject these. A wider vocabulary that widens into noise
#: is not a wider vocabulary, it is a more expensive one.
OFF_TOPIC_CONTROL = (
    "MLB announces expansion to two new cities",
    "World of Warcraft expansion launches next month",
    "Medicaid expansion passes in the state senate",
    "Cattle herd expansion drives beef prices up",
    # A figure with no scale word beside "valuation" must not reach the money
    # patterns. This is the one that keeps them anchored.
    "Bitcoin hits $122,000 valuation milestone as traders pile in",
    "House price valuation of 450,000 recorded in the borough",
)


@pytest.mark.parametrize("lang,host,headline", MEGAROUND_HEADLINES)
def test_every_real_megaround_headline_survives_the_free_gate(lang, host, headline):
    keep, why = prefilter.passes(headline)
    assert keep, f"[{lang}] {host}: {why}\n  {headline}"


@pytest.mark.parametrize("headline", OFF_TOPIC_CONTROL)
def test_the_widened_money_vocabulary_did_not_widen_into_noise(headline):
    keep, _ = prefilter.passes(headline)
    assert not keep, f"off-topic headline now passes the free gate: {headline}"


def test_prefilter_and_cheap_extract_agree_on_what_a_funding_stage_is():
    """Both halves read `series [a-k]`, and the round that exposed it was an H.

    Every Series pattern in prefilter.py used to stop at E or F while
    cheap_extract._STAGE has always read [a-k], so a Series H headline was
    invisible to the free gate and perfectly readable to the parser behind it.
    """
    for letter in "abcdefghijk":
        # Deliberately carries NO other funding word: "raises", "funding
        # round" and a currency symbol would each pass this on their own and
        # the stage letter is the property under test.
        headline = f"Acme completes its Series {letter.upper()} at a fresh mark"
        keep, why = prefilter.passes(headline)
        assert keep, f"Series {letter.upper()} rejected: {why}"
        assert cheap_extract._STAGE.search(f"series {letter}"), letter


def test_the_funding_query_pack_reaches_a_series_h():
    joined = " ".join(registry.GOOGLE_NEWS_QUERIES)
    for letter in "ABCDEFGH":
        assert f'"Series {letter}"' in joined, (
            f"the stage-shaped query stops before Series {letter}; Anthropic "
            "closed a Series H in May 2026")


# --------------------------------------------------------------------------
# 2. The amount parser, when the currency is written as a word
# --------------------------------------------------------------------------

#: (amount string, the dollars the SOURCE states). Nothing here is converted
#: from another currency; every one of these strings names a dollar.
STATED_IN_WORDS = (
    ("$65B", 65_000_000_000),
    ("$122 billion", 122_000_000_000),
    ("$30bn", 30_000_000_000),
    ("65.000 millones de dólares", 65_000_000_000),
    ("122.000 millones de dólares", 122_000_000_000),
    ("65 milliards de dollars", 65_000_000_000),
    ("30 Mrd. Dollar", 30_000_000_000),
    ("65 Milliarden Dollar", 65_000_000_000),
    ("650億ドル", 65_000_000_000),
    ("965 מיליארד דולר", 965_000_000_000),
    ("110 מיליארד דולר", 110_000_000_000),
    ("30 milyar dolar", 30_000_000_000),
    ("300억 달러", 30_000_000_000),
    ("300亿美元", 30_000_000_000),
)

#: The rule this parser exists to keep. A currency that is not the US dollar,
#: or no currency at all, leaves funding_amount_usd NULL: we do not convert at
#: a rate nobody published, and we do not guess.
MUST_STILL_REFUSE = (
    # Names no currency at all. This is the live OpenAI row, and it is exactly
    # why the widened vocabulary is not a relaxation: the string still states
    # nothing, so the figure is still NULL.
    "93.175 millones",
    "500 millones",
    "5.300 millones",
    # A currency that is not the dollar.
    "25 millioner kroner",
    "10,5 mio. kr.",
    "65 milliards d'euros",
    "65.000 millones de euros",
    "30 Mrd. Euro",
    "650億円",
    "300억 원",
    "300亿元",
    # SOMEBODY ELSE'S dollar, named in words. The word-shaped sibling of the
    # C$ / A$ / S$ arm of _NON_USD.
    "65 milliards de dollars canadiens",
    "50 millones de dólares australianos",
    "40 Millionen kanadische Dollar",
    "20 million Singapore dollars",
    "15 milyar Kanada doları",
    "500億カナダドル",
)


@pytest.mark.parametrize("text,expected", STATED_IN_WORDS)
def test_a_dollar_written_as_a_word_is_a_stated_dollar(text, expected):
    assert vocab.parse_funding_usd(text) == expected


@pytest.mark.parametrize("text", MUST_STILL_REFUSE)
def test_the_parser_still_refuses_everything_it_always_refused(text):
    assert vocab.parse_funding_usd(text) is None, (
        f"{text!r} produced a US dollar figure the source does not state")


def test_the_spanish_thousands_convention_is_read_the_spanish_way():
    """'122.000 millones' is a hundred and twenty-two BILLION, not 122 million.

    The factor is a thousand and the direction is the dangerous one, so this is
    pinned on its own rather than left inside the table above.
    """
    assert vocab.parse_funding_usd("122.000 millones de dólares") == 122_000_000_000
    assert vocab.parse_funding_usd("1,5 millones de dólares") == 1_500_000


# --------------------------------------------------------------------------
# 3. Dedup: two rounds in a fortnight are two rounds
# --------------------------------------------------------------------------

def _funding_signal(company, published, amount_text, headline, url):
    return validate.build_signal(
        {
            "company": company,
            "pillar": "company_development",
            "signal_direction": "hiring",
            "city": "San Francisco",
            "country": "United States",
            "confidence": "reported",
            "headline": headline,
            "summary": headline,
            "talent_readthrough": "New capital funds hiring across engineering.",
            "funding_amount": amount_text,
        },
        {
            "raw_text": headline + " " + amount_text,
            "source_url": url,
            "source_name": "Example",
            "published_date": published,
        },
        "google_news",
    )


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "megaround.db")
    yield connection
    connection.close()


def test_two_different_amounts_in_one_fortnight_are_two_developments(conn):
    """A company can raise twice in a fortnight, and in 2026 these two did.

    Before this, the employer+pillar+14-day window collapsed them and kept
    whichever arrived first.
    """
    first = _funding_signal(
        "OpenAI", "2026-02-27", "$93 billion",
        "OpenAI closes a $93 billion round",
        "https://example.com/openai-93")
    assert store.store(conn, first) == "stored"

    second = _funding_signal(
        "OpenAI", "2026-03-05", "$122 billion",
        "OpenAI completes a $122 billion round at an $852 billion valuation",
        "https://example.org/openai-122")
    assert store.store(conn, second) == "stored", (
        "a second, differently-sized round inside the window was suppressed "
        "by the first")


def test_a_quantified_round_is_not_suppressed_by_an_unquantified_one(conn):
    """The live case, exactly.

    'OpenAI capta 93.175 millones' names no currency, so its funding_amount_usd
    is NULL. It was published three days after an English story stating $110
    billion, and the window handed the survival to the row that states no
    dollars at all.
    """
    spanish = _funding_signal(
        "OpenAI", "2026-02-27", "93.175 millones",
        "OpenAI capta 93.175 millones en una ronda récord",
        "https://example.com/openai-es")
    assert store.store(conn, spanish) == "stored"
    assert spanish.funding_amount_usd is None, (
        "the fixture only means anything while this string refuses to parse")

    english = _funding_signal(
        "OpenAI", "2026-02-24", "$110 billion",
        "OpenAI raises $110 billion at a $730 billion valuation",
        "https://example.org/openai-en")
    assert store.store(conn, english) == "stored", (
        "the quantified round was dropped as a duplicate of an unquantified one")


def test_the_same_round_from_a_second_outlet_is_still_one_record(conn):
    """The widening must not cost the thing dedup is for."""
    first = _funding_signal(
        "Anthropic", "2026-02-12", "$30bn",
        "Anthropic raises $30bn led by GIC, Coatue at $380bn valuation",
        "https://example.com/anthropic-a")
    assert store.store(conn, first) == "stored"

    second = _funding_signal(
        "Anthropic", "2026-02-13", "$30 billion",
        "Anthropic clinches $380 billion valuation after $30 billion round",
        "https://example.org/anthropic-b")
    assert store.store(conn, second) == "duplicate"


def test_a_leadership_row_dedups_exactly_as_it_always_did(conn):
    """Nothing outside funding changes: neither side states an amount."""
    def leadership(published, headline, url):
        return validate.build_signal(
            {
                "company": "Acme", "pillar": "leadership_change",
                "signal_direction": "neutral", "city": "London",
                "country": "United Kingdom", "confidence": "reported",
                "headline": headline, "summary": headline,
                "talent_readthrough": "A new chief executive resets hiring.",
            },
            {"raw_text": headline, "source_url": url,
             "source_name": "Example", "published_date": published},
            "google_news")

    assert store.store(conn, leadership(
        "2026-04-01", "Acme appoints a new chief executive",
        "https://example.com/acme-a")) == "stored"
    assert store.store(conn, leadership(
        "2026-04-06", "Acme names Jane Doe as chief executive",
        "https://example.org/acme-b")) == "duplicate"


def test_the_cheap_funding_window_belongs_to_the_candidate_not_to_today(conn):
    """A round discovered late must still recognise itself.

    google_news's median discovery lag over 2,795 current rows is 130 days, so
    a window anchored on `date.today()` was dead code for most of what it
    actually delivers: every late-arriving rewrite of a round we already hold
    was bought as a full paid read.
    """
    held = _funding_signal(
        "Anthropic", "2026-02-12", "$30bn",
        "Anthropic raises $30bn at a $380bn valuation",
        "https://example.com/anthropic-held")
    assert store.store(conn, held) == "stored"

    # Anchored on today, nothing published in February is inside a 21-day
    # window in August, so this returns None and the read is bought again.
    assert dedupe.funding_event_duplicate(
        conn, "anthropic", 30_000_000_000, "$30B",
        published_date="2026-02-13") is not None

    # And a genuinely different round is still not this one.
    assert dedupe.funding_event_duplicate(
        conn, "anthropic", 65_000_000_000, "$65B",
        published_date="2026-05-28") is None


# --------------------------------------------------------------------------
# 4. A gate that ERRORED did not reject anything
# --------------------------------------------------------------------------

def test_a_gate_error_is_a_deferral_and_not_a_verdict():
    """run_collect must not count an un-judged candidate as rejected.

    On 2026-08-03 the gate errored on 85.7% of its calls for eight hours and
    every collector reported an ordinary run, because a wall of errors and a
    wall of NOs are the same number in `rejected`. All three copies of
    Anthropic's Series H arrived in that window.
    """
    source = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "run_collect.py"), encoding="utf-8").read()
    handler = source.split("except classify.ClassifyError as exc:")[1]
    handler = handler.split("continue")[0]
    assert "rejected += 1" not in handler, (
        "a gate that could not judge a candidate is counting it as a rejection")
    assert "gate_errored += 1" in handler
    assert "mostly_errored" in source and "GATE_ERROR_CEILING" in source, (
        "nothing turns a run of gate errors into a degraded health row")
    assert "or mostly_errored" in source, (
        "mostly_errored is computed and never reaches the `broken` test")


# --------------------------------------------------------------------------
# 5. A rejection that names no rule cannot be triaged
# --------------------------------------------------------------------------

def test_the_ledger_records_why_a_candidate_was_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(gate_ledger, "_BUFFER", {})
    item = {"headline": "Acme raises $10m", "source_url": "https://example.com/a"}
    gate_ledger.record(item, "google_news", gate_ledger.YES)
    gate_ledger.outcome(item, "validate_reject",
                        "no geography: the article names no place we cover")
    line = gate_ledger._BUFFER[gate_ledger.key(item)]
    assert line["outcome"] == "validate_reject"
    assert "no geography" in line["reason"]


def test_a_reason_is_bounded_and_whitespace_collapsed(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(gate_ledger, "_BUFFER", {})
    item = {"headline": "Acme raises $10m", "source_url": "https://example.com/b"}
    gate_ledger.record(item, "google_news", gate_ledger.YES)
    gate_ledger.outcome(item, "validate_reject", "  a\n\n  b  " + "x" * 500)
    reason = gate_ledger._BUFFER[gate_ledger.key(item)]["reason"]
    assert reason.startswith("a b x")
    assert len(reason) <= gate_ledger.REASON_MAX


def test_an_outcome_with_no_reason_writes_no_reason_key(tmp_path, monkeypatch):
    """The field is optional, and every line written before 2026-08-04 lacks it."""
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(gate_ledger, "_BUFFER", {})
    item = {"headline": "Acme raises $10m", "source_url": "https://example.com/c"}
    gate_ledger.record(item, "google_news", gate_ledger.YES)
    gate_ledger.outcome(item, "stored")
    assert "reason" not in gate_ledger._BUFFER[gate_ledger.key(item)]
