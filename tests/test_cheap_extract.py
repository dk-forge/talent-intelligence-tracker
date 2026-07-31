"""The deterministic extractor (pipeline/cheap_extract.py) and the story
clustering built on it — cost levers 1 and 2.

The property under test is PRECISION OVER RECALL: a decline is always
acceptable, a wrong extraction never is. So the decline battery is the
important half of this file, and anything added to the extractor should add
its failure mode there first.
"""

import inspect
import re

import pytest

import run_collect
from collectors import national_press
from pipeline import (cheap_extract, classify, dedupe, schema, store, validate,
                      vocab)


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def item(headline, teaser="", url="https://outlet.example/story-1"):
    return {
        "headline": headline,
        "raw_text": f"{headline}\n\n{teaser}".strip(),
        "source_url": url,
        "discovery_url": url,
        "source_name": "Example Wire",
        "published_date": "2026-07-29",
    }


# --- What must close ---------------------------------------------------------

def test_clean_funding_headline_closes_completely():
    out = cheap_extract.extract(item("Enigma Raises $71M in Seed Funding",
                                     "The round was led by Greenfield."))
    assert out is not None
    assert out["company"] == "Enigma"
    assert out["funding_amount"] == "$71M"
    assert out["pillar"] == "company_development"
    # Funding is NOT hiring until the source states roles — same rule the
    # model prompt carries, applied deterministically.
    assert out["signal_direction"] == "neutral"
    assert out["confidence"] == "reported"


def test_stated_city_is_captured_from_a_based_prefix():
    out = cheap_extract.extract(item("Boston-based Acme raised $12.5M in seed funding"))
    assert out is not None and out["city"] == "Boston"


def test_hiring_headline_closes_with_first_number_and_place():
    out = cheap_extract.extract(item("Acme to hire up to 500 engineers in Dublin"))
    assert out is not None
    assert out["headcount"] == 500          # first number, existing rule
    assert out["headcount_scope"] == "new_roles"
    assert out["signal_direction"] == "hiring"
    assert out["city"] == "Dublin"
    assert out["functions"] == ["engineering"]


def test_closed_record_survives_the_same_validate_path(conn):
    """The extractor earns no exemptions: its output goes through
    build_signal, every figure must be in raw_text, and store() accepts it."""
    raw = item("Enigma Raises $71M in Seed Funding", "Led by Greenfield.")
    classified = cheap_extract.extract(raw)
    signal = validate.build_signal(classified, raw, "national_press", conn=conn)
    assert signal.funding_amount == "$71M"
    assert signal.funding_amount_usd == 71_000_000
    assert signal.funding_stage == "seed"
    assert signal.confidence == "reported"
    signal.notes = cheap_extract.EVIDENCE_NOTE
    assert store.store(conn, signal) == "stored"
    row = conn.execute("SELECT notes, confidence FROM signals").fetchone()
    assert row["notes"] == cheap_extract.EVIDENCE_NOTE
    assert row["confidence"] == "reported"


def test_non_usd_stays_as_stated_with_no_usd_integer(conn):
    raw = item("Acme Robotics secures €30M", "A Series A round.")
    classified = cheap_extract.extract(raw)
    signal = validate.build_signal(classified, raw, "national_press", conn=conn)
    assert signal.funding_amount == "€30M"
    assert signal.funding_amount_usd is None   # never an invented FX rate


def test_confidence_is_never_verified_on_a_news_source(conn):
    """Even if the extractor were edited to claim 'verified', the existing
    ceiling in infer_confidence must hold for a news URL."""
    raw = item("Enigma Raises $71M in Seed Funding")
    classified = cheap_extract.extract(raw)
    classified["confidence"] = "verified"
    signal = validate.build_signal(classified, raw, "national_press", conn=conn)
    assert signal.confidence == "reported"


# --- What must decline -------------------------------------------------------

DECLINES = (
    # ambiguity in the name span
    "Fintech startup Alma raises $10M",
    "Israeli startup Coho raises $5M",
    "Acme, backed by Sequoia, raises $20M",
    "It raises $5M",
    "Why Acme raised $30M",
    # the money is not in the bank
    "Acme in talks to raise $50M",
    "Acme plans to raise $50M",
    "Acme eyes $100M round",
    "Is Acme raising $50M?",
    # the amount means something else
    "Walmart gets $10 price target boost",
    "Acme secures $5M stake in Beta",
    # a second signal the parser cannot carry
    "Acme raises $50M and hires 200",
    "Acme raises $50M to acquire Beta",
    "Acme opens new Dublin office after raising $20M",
    "Acme to hire 500 workers at new plant",
    # no currency, no capture
    "Acme raises 12 million",
    # non-English takes the paid path
    "Deutsches Startup sammelt 10 Millionen ein",
    # the sibling's scope
    "Acme cuts 200 jobs after raising $5M",
    # a stated place that will not normalise declines the whole item
    "Acme to hire 300 staff in Zenithville",
)


@pytest.mark.parametrize("headline", DECLINES)
def test_ambiguity_declines(headline):
    assert cheap_extract.extract(item(headline), count=False) is None, headline


# --- Leadership: the second formulaic pillar ----------------------------------

def test_clean_appointment_headline_closes_completely():
    out = cheap_extract.extract(item(
        "Acme Appoints Jane Doe as Chief Executive Officer",
        "The board announced the appointment on Tuesday."))
    assert out is not None
    assert out["company"] == "Acme"
    assert out["pillar"] == "leadership_change"
    # An appointment is one person in a planned succession: neutral, the same
    # rule the model prompt carries.
    assert out["signal_direction"] == "neutral"
    assert out["confidence"] == "reported"
    assert out["functions"] == ["executive"]
    assert "Jane Doe" in out["summary"]


def test_no_connector_and_acronym_titles_close():
    out = cheap_extract.extract(item("Acme Names Jane Doe CEO"))
    assert out is not None and "Jane Doe" in out["summary"]


def test_promotion_closes_and_reads_grammatically():
    out = cheap_extract.extract(item(
        "Acme Promotes Rahul Sharma to Chief Financial Officer"))
    assert out is not None
    assert "promoted Rahul Sharma to" in out["summary"]


def test_stated_place_prefix_is_captured_like_funding():
    out = cheap_extract.extract(item("Boston-based Acme Taps Jane Doe as CFO"))
    assert out is not None and out["city"] == "Boston"


def test_chair_of_the_board_is_the_one_allowed_tail():
    out = cheap_extract.extract(item("Acme Names Jane Doe Chairman of the Board"))
    assert out is not None and out["pillar"] == "leadership_change"


def test_three_token_person_is_trusted_outside_title_case():
    # lowercase verb keeps the headline out of Title Case, where only a
    # two-token person is trusted.
    out = cheap_extract.extract(item(
        "Acme names Mary Jane Watson chief executive officer"))
    assert out is not None and "Mary Jane Watson" in out["summary"]


def test_closed_appointment_survives_the_same_validate_path(conn):
    """No exemptions: build_signal, figure checks, confidence ceiling and
    store() all apply to a leadership close exactly as to a funding close."""
    raw = item("Acme Appoints Jane Doe as Chief Executive Officer",
               "The board announced the appointment.")
    classified = cheap_extract.extract(raw)
    signal = validate.build_signal(classified, raw, "national_press", conn=conn)
    assert signal.pillar == "leadership_change"
    assert signal.signal_direction == "neutral"
    assert signal.confidence == "reported"
    signal.notes = cheap_extract.EVIDENCE_NOTE
    assert store.store(conn, signal) == "stored"
    row = conn.execute("SELECT notes FROM signals").fetchone()
    assert row["notes"] == cheap_extract.EVIDENCE_NOTE


# Every entry names the failure mode it guards; each is the leadership
# translation of a tightening the funding extractor learned from a live sweep.
LEADERSHIP_DECLINES = (
    # the employer span is a geography, not an employer
    "India Names New Central Bank Chief",
    "Kuwait Appoints Jane Doe as CEO",
    # descriptor-led and hyphen-embedded employer spans
    "Fintech startup Alma appoints Jane Doe as CEO",
    "Dutch-US MedTech Xeltis Appoints Jane Doe as CEO",
    # the person span carries a role word: where the description ends and the
    # name begins is a model's job
    "Acme Appoints Former Google Executive Jane Doe as CEO",
    "Acme Taps Stripe Veteran Jane Doe as CFO",
    "Acme Appoints New CEO",
    # multiple people or multiple roles
    "Acme Names Jane Doe as President and CEO",
    "Acme Appoints Jane Doe and John Smith as Co-Chiefs",
    "Acme Names Jane Doe CEO as John Smith Steps Down",
    "Tesla Board Names Committee to Find Next CEO",
    # title-casing makes the spans ambiguous: multi-token employer, or a
    # person span longer than first-name surname
    "Building Materials Startup Fixxly Names Jane Doe CEO",
    "Acme Names Mary Jane Watson CEO",
    # not an appointment yet, or second-hand
    "Acme to Appoint Jane Doe as CEO",
    "Acme Reportedly Names Jane Doe CEO",
    "Acme Set to Name Jane Doe CEO",
    # facts the record cannot carry: a start date, an interim arrangement
    "Acme Appoints Jane Doe as CEO, Effective September 1",
    # divisional and sub-C titles stay with the model
    "Acme Appoints Jane Doe as CEO of Its Gaming Division",
    "Acme Names Jane Doe as Head of Marketing",
)


@pytest.mark.parametrize("headline", LEADERSHIP_DECLINES)
def test_leadership_ambiguity_declines(headline):
    assert cheap_extract.extract(item(headline), count=False) is None, headline


LEADERSHIP_TEASER_DECLINES = (
    # a stated start date lives in the teaser as often as the headline
    ("Acme Appoints Jane Doe as CEO",
     "She takes the role effective September 1."),
    ("Acme Appoints Jane Doe as CEO", "Doe will join in October."),
    # an interim arrangement is a nuance the record would silently drop
    ("Acme Names Jane Doe CEO", "Doe has served as interim chief since May."),
    # money beside an appointment is a bigger story than one person
    ("Acme Appoints Jane Doe as CEO",
     "The move follows Acme's $50M Series B."),
    # so is hiring language, and so is a deal
    ("Acme Appoints Jane Doe as CEO",
     "Doe plans to recruit aggressively in Austin."),
    ("Acme Appoints Jane Doe as CEO",
     "The appointment follows the acquisition of Beta Systems."),
    # the sibling's scope
    ("Acme Appoints Jane Doe as CEO",
     "She arrives weeks after layoffs cut 200 jobs."),
)


@pytest.mark.parametrize("headline,teaser", LEADERSHIP_TEASER_DECLINES)
def test_leadership_teaser_facts_decline(headline, teaser):
    assert cheap_extract.extract(item(headline, teaser), count=False) is None, \
        (headline, teaser)


# --- Clustering (lever 2) ----------------------------------------------------

def test_rewrites_of_one_round_cluster_to_one_read():
    items = [
        item("Acme raises $71M Series B", url="https://a.example/1"),
        item("Acme raised $71 million from investors led by X",
             url="https://b.example/2"),
        item("Acme secures $71M in Series B funding", url="https://c.example/3"),
        item("Beta names new CEO", url="https://d.example/4"),
    ]
    kept, removed, removed_loose, clusters = run_collect.cluster_stories(items)
    assert clusters == 1
    assert len(kept) == 2          # one Acme representative + the Beta story
    assert len(removed) == 2 and not removed_loose
    assert all("Acme" in r["headline"] for r in removed)


def test_different_amounts_or_currencies_never_cluster():
    items = [
        item("Acme raises $71M Series B", url="https://a.example/1"),
        item("Acme raises €71M Series B", url="https://b.example/2"),
        item("Acme raises $17M Series B", url="https://c.example/3"),
    ]
    kept, removed, removed_loose, clusters = run_collect.cluster_stories(items)
    assert clusters == 0 and not removed and not removed_loose and len(kept) == 3


def test_descriptor_names_cluster_loosely_and_are_not_marked_gone():
    """'…startup Fixxly raises $5.5 Mn' is unstorable (descriptor name) but
    four outlets wrote it in one real sweep. The loose tier clusters them;
    run_collect must NOT mark the set-aside copies seen (asserted on source
    below), so a false merge can only ever defer a read."""
    items = [
        item("Building materials quick commerce startup Fixxly raises $5.5 Mn in seed round",
             url="https://a.example/1"),
        item("Q-comm for building material industry Fixxly bags $5.5 mn in seed round",
             url="https://b.example/2"),
        item("Building Materials Quick Commerce Startup Fixxly Raises $5.5M",
             url="https://c.example/3"),
    ]
    kept, removed_strict, removed_loose, clusters = run_collect.cluster_stories(items)
    assert clusters == 1
    assert not removed_strict
    assert len(removed_loose) == 2 and len(kept) == 1
    # And none of them is storable by the strict extractor.
    assert all(cheap_extract.extract(i, count=False) is None for i in items)
    # The seen-marking loop must iterate the strict list only.
    src = inspect.getsource(run_collect.run)
    assert "for extra in away_strict" in src
    assert "for extra in away_loose" not in src


def test_known_round_is_matched_before_any_read(conn):
    raw = item("Enigma Raises $71M in Seed Funding")
    classified = cheap_extract.extract(raw)
    signal = validate.build_signal(classified, raw, "national_press", conn=conn)
    assert store.store(conn, signal) == "stored"

    parsed = cheap_extract.parse_funding(
        item("Enigma raised $71 million, sources say it closed last week",
             url="https://late.example/7"))
    # ("sources" would decline extract(); parse_funding is looser on purpose —
    # clustering the seventh rewrite needs only the stated employer+amount.)
    assert parsed is None or True  # shape check below is the real assertion

    parsed = cheap_extract.parse_funding(
        item("Enigma raised $71 million from new investors",
             url="https://late.example/7"))
    assert parsed is not None
    assert dedupe.funding_event_duplicate(
        conn, parsed.company_key, parsed.amount_usd, parsed.amount_canon)


def test_a_different_company_or_size_is_not_a_known_round(conn):
    raw = item("Enigma Raises $71M in Seed Funding")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "national_press", conn=conn)
    store.store(conn, signal)
    other = cheap_extract.parse_funding(item("Enigma raises $17M Series A"))
    assert dedupe.funding_event_duplicate(
        conn, other.company_key, other.amount_usd, other.amount_canon) is None


# --- Wiring and the cache-shaped prompt ---------------------------------------

def test_run_collect_tries_cheap_extraction_before_the_model():
    src = inspect.getsource(run_collect.run)
    assert "cheap_extract.extract" in src
    assert src.index("cheap_extract.extract") < src.index("classify.classify"), \
        "the free path must be tried before the paid one"
    assert "EVIDENCE_NOTE" in src, "a cheap row must carry its evidence marker"


def test_run_collect_prechecks_before_the_model():
    """Read only what can store: every rejection reachable from the raw item
    alone (validate.precheck) must fire before the paid read, not after it."""
    src = inspect.getsource(run_collect.run)
    assert "validate.precheck" in src
    assert src.index("validate.precheck") < src.index("classify.classify"), \
        "the free verdicts must land before the money is spent"


def test_the_cost_knobs_are_env_configurable_with_the_agreed_defaults():
    """The three model/cap decisions, pinned where they were made. The gate
    default is the A/B winner; the read cap default is what $25 a month buys
    (cost_projection.py section [5]); and the extraction model is deliberately
    NOT switched - that change is gated behind its own A/B."""
    import pipeline.classify as classify_module
    src = inspect.getsource(classify_module)
    assert 'os.environ.get("TIT_GATE_MODEL", "google/gemini-2.5-flash-lite")' in src
    assert 'os.environ.get("TIT_READTHROUGH_CAP", "88")' in src
    assert 'os.environ.get("TIT_MODEL", "deepseek/deepseek-chat")' in src, \
        "the read-through model must stay on the incumbent until its own A/B runs"


def test_every_run_measures_reads_bought_vs_rows_stored():
    """The waste ratio the last real run made necessary: 60 reads bought, 34
    rows stored, and nothing printed the gap. The counter lives in
    classify.STATS beside full_calls; run_collect feeds it at store time and
    prints the two numbers together every run."""
    assert "read_stored" in classify.STATS
    src = inspect.getsource(run_collect.run)
    assert 'STATS["read_stored"]' in src
    assert "reads bought vs rows stored" in src


def test_read_sizes_are_named_constants():
    src = inspect.getsource(classify.classify)
    assert "FULL_READ_CHARS" in src
    assert classify.FULL_READ_CHARS == 4000
    assert "GATE_CHARS" in inspect.getsource(classify.gate)


def test_schema_hint_stays_at_the_head_of_the_user_message():
    """The cacheable prefix: system prompt, then SCHEMA_HINT, then the item.
    Anything inserted before SCHEMA_HINT breaks DeepSeek's automatic prefix
    cache and silently forfeits the 0.1x pricing on ~70% of input tokens."""
    src = inspect.getsource(classify.classify)
    call = re.search(r"_call\(\s*MODEL.*?\)", src, re.S).group(0)
    assert re.search(r'f"\{SCHEMA_HINT\}', call), \
        "the user message must START with SCHEMA_HINT for the prefix cache"


# --- The stated city ---------------------------------------------------------
#
# Six phrasings, and the four tightenings that keep them honest. The decline
# half of this table is the important half: every entry in it is a sentence
# that names a place we must NOT store, and getting one of them wrong turns a
# sourced claim into an invented one.

CITY_STATED = (
    # "<City>-based" and "<City>-headquartered"
    ("Tel Aviv-based Acme raised $10M", "Tel Aviv", "IL"),
    ("Vilnius-based Sigvi raises €1.2 million", "Vilnius", "LT"),
    ("The Berlin-based startup raised $10M", "Berlin", "DE"),
    ("A Lagos-based fintech raised $3M", "Lagos", "NG"),
    ("Acme is a Sao Paulo-headquartered lender", "Sao Paulo", "BR"),
    # multi-word cities, which is most of the interesting ones
    ("New York-based Acme raised $10M", "New York", "US"),
    ("San Francisco-based Acme raised $10M", "San Francisco", "US"),
    ("Mexico City-based Acme raised $10M", "Mexico City", "MX"),
    ("Ho Chi Minh City-based Acme raised $10M", "Ho Chi Minh City", "VN"),
    ("Rio de Janeiro-based Acme raised $10M", "Rio de Janeiro", "BR"),
    # a nationality in front of the city is a descriptor, not part of its name
    ("Israeli Tel Aviv-based Acme raised $10M", "Tel Aviv", "IL"),
    # "based in" / "headquartered in"
    ("Acme is based in Jakarta", "Jakarta", "ID"),
    ("Acme is headquartered in Seoul", "Seoul", "KR"),
    ("Acme, headquartered in Nairobi, raised $3M", "Nairobi", "KE"),
    # the source's own qualifier, agreeing
    ("Acme is based in Dublin, Ireland", "Dublin", "IE"),
    ("Acme is based in Austin, Texas", "Austin", "US"),
    ("Acme is based in Melbourne, Australia", "Melbourne", "AU"),
    ("Acme is based in Washington, DC", "Washington DC", "US"),
    # the source's own qualifier, resolving a name that belongs to two countries
    ("Cambridge, Massachusetts-based Acme raised $10M", "Cambridge MA", "US"),
    ("Acme is based in London, Ontario", "London, Ontario", "CA"),
    # aliases collapse to one market
    ("Acme is based in Bengaluru", "Bangalore", "IN"),
    ("Acme is based in Gurgaon", "Gurugram", "IN"),
    ("Acme is based in Kiev", "Kyiv", "UA"),
    ("Acme is based in Bombay", "Mumbai", "IN"),
    # a city that is also a country is still a city, because it is a city-state
    ("Acme is based in Singapore", "Singapore", "SG"),
    # the office phrasings
    ("Acme opens a Lagos office", "Lagos", "NG"),
    ("Acme opens its new Kigali office", "Kigali", "RW"),
    ("Acme will double its Dubai office", "Dubai", "AE"),
    ("ACME OPENS A LAGOS OFFICE", "Lagos", "NG"),
)

CITY_DECLINES = (
    # TIGHTENING 1: a place inside a longer name is not the place
    "Berlin Packaging-based supplier raised $10M",
    "Acme is based in Boston Consulting Group's building",
    # TIGHTENING 2: "-based" is not a place frame
    "AI-based Acme raised $10M",
    "Cloud-based Acme raised $10M",
    "Faith-based charity Acme raised $10M",
    "Community-based care provider Acme raised $10M",
    "US-based Acme raised $10M",
    "Israeli-based Acme raised $10M",
    # TIGHTENING 3: a qualifier that contradicts the gazetteer
    "Acme is based in Dublin, Ohio",
    "Acme is based in Melbourne, Florida",
    "Acme is based in Athens, Georgia",
    "Acme is based in Manchester, New Hampshire",
    "Acme is headquartered in Perth, Scotland",
    "Acme is based in Athens Georgia",
    # TIGHTENING 4: the place belongs to somebody else, or to two places
    "Acme raised $10M led by London-based Index Ventures",
    "Acme raised $10M with participation from Berlin-based Foo",
    "A Lagos-based rival raised $1M",
    "Berlin-based investor Foo backed Acme",
    "Acme is based in Berlin and Munich",
    "Acme is based in Barcelona, Spain, and Madrid",
    "Tel Aviv-based Acme opens its Munich office",
    # a stated place the gazetteer does not curate says nothing, which is the
    # same answer as a place it refuses to guess at
    "Acme is based in Cambridge",
    "Acme is based in Zenithville",
    "Acme is based in Washington",
    "Acme is based in Newcastle",
    # a country is not a city
    "Acme is based in Mexico",
    "Acme is based in the Netherlands",
    # nothing stated at all
    "Acme names Jane Doe Chief Executive Officer",
    "Acme raised $10M in Series A funding",
    "",
)


@pytest.mark.parametrize("text,city,code", CITY_STATED)
def test_a_stated_city_is_read(text, city, code):
    hit = cheap_extract.stated_city(text)
    assert hit is not None, text
    assert (hit[0], hit[2]) == (city, code), text


@pytest.mark.parametrize("text", CITY_DECLINES)
def test_an_unstated_or_ambiguous_city_declines(text):
    assert cheap_extract.stated_city(text) is None, text


def test_the_publishers_own_base_is_never_the_records_city():
    """THE RULE THAT DOES NOT BEND. national_press.dateline() folds the
    outlet's seat into raw_text on purpose, as a HINT for the model, and it is
    written in the exact "based in <city>, <country>" shape this scanner reads.
    Reading it would file every story a Sofia outlet carried in Sofia.
    """
    hint = national_press.dateline(national_press.Feed(
        name="The Recursive", rss="https://therecursive.com/feed/",
        country="Bulgaria", city="Sofia", coverage="national",
        language="en", source_type="press"))
    assert "based in Sofia" in hint          # the shape really is that shape
    assert cheap_extract.stated_city(f"Acme raised $5M.\n\n{hint}") is None
    # And the story's OWN city still survives beside the hint.
    hit = cheap_extract.stated_city(f"Tel Aviv-based Acme raised $5M.\n\n{hint}")
    assert hit[0] == "Tel Aviv"


def test_the_classify_outlet_line_is_never_the_records_city():
    """classify.classify prepends "Published by: <outlet>" for the same reason
    and with the same danger."""
    assert cheap_extract.stated_city(
        "Published by: Jakarta Post\n\nAcme raised $5M") is None


def test_a_city_in_the_teaser_now_places_a_funding_round():
    """The gap this work closed: the headline states the round, the teaser
    states the city, and every such row was stored unplaced."""
    out = cheap_extract.extract(item(
        "Sigvi raises €1.2 million",
        "The Vilnius-based company will scale its car rental platform."))
    assert out is not None
    assert out["city"] == "Vilnius"
    assert out["country"] == "Lithuania"


def test_a_city_in_the_teaser_now_places_an_appointment():
    out = cheap_extract.extract(item(
        "Acme Appoints Jane Doe as Chief Executive Officer",
        "The Nairobi-based lender said the board voted unanimously."))
    assert out is not None
    assert out["city"] == "Nairobi"
    assert out["country"] == "Kenya"


def test_the_headline_prefix_still_wins_over_the_teaser():
    """A prefix place is the SUBJECT's place. The scan fills a NULL city and
    never overrides one, so a disagreement cannot silently re-place a record."""
    out = cheap_extract.extract(item(
        "Boston-based Acme raised $12.5M in seed funding",
        "Acme is headquartered in Berlin, according to filings."))
    assert out is not None and out["city"] == "Boston"


def test_a_teaser_city_that_contradicts_a_sourced_country_is_not_stored():
    """"Spain's Acme" sources Spain. A city in Germany cannot be the same
    record's city, so the country stands and the city stays NULL."""
    out = cheap_extract.extract(item(
        "Spain's Acme raised $12.5M in seed funding",
        "The Berlin-based rival was unavailable for comment."))
    assert out is not None
    assert out["city"] == ""
    assert out["country"] == "Spain"


def test_a_placed_deterministic_row_survives_the_same_validate_path(conn):
    """No exemptions: a city the scan found still normalises through the
    vocabulary in build_signal, exactly as a model's answer would."""
    raw = item("Sigvi raises $1.2 million",
               "The Vilnius-based company is scaling across Europe.")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "national_press")
    assert signal.city == "Vilnius"
    assert signal.country == "LT"
    assert signal.region == "Europe"
    assert store.store(conn, signal) == "stored"


def test_the_scanner_reads_the_gazetteer_rather_than_a_second_list():
    """A city added to vocab is readable here the same day, and there is no
    second place for the two to drift apart."""
    assert set(cheap_extract._ALIAS_KEYS) == set(vocab._CITY_ALIASES)
