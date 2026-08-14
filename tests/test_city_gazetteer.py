"""The city vocabulary must cover the markets we claim, and must never place a
city in the wrong country.

The bug this exists to prevent is the one measured on 2026-07-29: 969 of 15,711
current rows carried a city, in 25 distinct cities, and Tel Aviv, Dubai, Sao
Paulo, Seoul, Lagos, Nairobi and Jakarta could not be stored AT ALL. Nothing
errored. `normalize_city` simply returned None, `build_signal` left the column
NULL by design, and a product whose whole premise is segmenting by geography
reported "location not stated" for 93.8% of what it held.

The sibling failure mode is worse and is what most of this file guards: a
vocabulary that grows carelessly starts ANSWERING when it should decline.
Toronto sat in this table mapped to the United States for months. Cambridge
belongs to two countries. So every assertion below is either "we can now
receive a place a source states" or "we still refuse to guess which place it
was".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline import vocab

DB = Path(__file__).resolve().parent.parent / "data" / "talent_intel.db"


# --- The invariants ----------------------------------------------------------

def test_one_region_per_country():
    """`validate._region_for_country` finds a region by scanning this table for
    the first city with a matching code, so two cities in one country
    disagreeing about their region makes the region a dictionary-order
    accident rather than a fact."""
    regions: dict[str, set[str]] = {}
    for _city, region, code in vocab._CITY_ALIASES.values():
        regions.setdefault(code, set()).add(region)
    split = {code: sorted(rs) for code, rs in regions.items() if len(rs) > 1}
    assert not split, f"one country, two regions: {split}"


def test_every_region_is_a_real_region():
    used = {region for _c, region, _code in vocab._CITY_ALIASES.values()}
    assert not used - set(vocab.REGIONS), used - set(vocab.REGIONS)


def test_every_country_code_can_be_named():
    """A code missing from COUNTRY_NAMES stores a city whose country label
    renders empty — the record looks placed and reads unplaced."""
    codes = {code for _c, _r, code in vocab._CITY_ALIASES.values()}
    missing = sorted(codes - set(vocab.COUNTRY_NAMES))
    assert not missing, f"cities in countries with no name: {missing}"


def test_no_city_name_belongs_to_two_countries():
    """The display name is what a reader filters on and what a URL slug is
    built from. One name, one country, or the filter lies."""
    countries: dict[str, set[str]] = {}
    for city, _region, code in vocab._CITY_ALIASES.values():
        countries.setdefault(city, set()).add(code)
    split = {city: sorted(cs) for city, cs in countries.items() if len(cs) > 1}
    assert not split, f"one city name, two countries: {split}"


def test_every_alias_round_trips_to_its_own_display_name():
    for alias, (city, _region, _code) in vocab._CITY_ALIASES.items():
        hit = vocab.normalize_city(alias)
        assert hit is not None, alias
        assert hit[0] == city, f"{alias} -> {hit[0]}, expected {city}"
        # The display name must itself normalise, or a stored value cannot be
        # read back — which is how the Toronto correction was found.
        assert vocab.normalize_city(city) == hit, city


def test_the_state_table_is_us_cities_only_and_unambiguous():
    displays = {c for c, _r, _code in vocab._CITY_ALIASES.values()}
    for city, state in vocab._CITY_STATE.items():
        assert city in displays, f"{city} is in _CITY_STATE but not a city"
        hit = vocab.normalize_city(city)
        assert hit[2] == "US", f"{city} is not in the US, so it has no state"
        assert state in set(vocab.US_STATES.values()), state
    # A city that exists in two states must not be here, because the state
    # facet is where guessing between them is visibly wrong.
    for both in ("Portland", "Columbus", "Kansas City"):
        assert vocab.normalize_city(both) is not None, both
        assert both not in vocab._CITY_STATE, both
        assert not vocab.state_for_city(both)


# --- Coverage: the hubs a hiring-side reader expects ------------------------

# The named gap, from the 2026-07-29 measurement. Each of these was absent
# from the DATA; the ones that were also absent from the vocabulary could not
# have arrived however plainly a source stated them.
NAMED_GAP = {
    "Tel Aviv": "IL", "Berlin": "DE", "Dubai": "AE", "Sao Paulo": "BR",
    "Seoul": "KR", "Barcelona": "ES", "Munich": "DE", "Lagos": "NG",
    "Nairobi": "KE", "Jakarta": "ID", "Mexico City": "MX", "Bangkok": "TH",
    "Istanbul": "TR", "Cairo": "EG", "Riyadh": "SA", "Mumbai": "IN",
    "New Delhi": "IN", "Shanghai": "CN", "Beijing": "CN", "Hong Kong": "HK",
    "Taipei": "TW", "Kuala Lumpur": "MY", "Manila": "PH",
    "Ho Chi Minh City": "VN", "Cape Town": "ZA", "Johannesburg": "ZA",
    "Buenos Aires": "AR", "Bogota": "CO", "Santiago": "CL", "Lima": "PE",
    "Kyiv": "UA", "Tallinn": "EE", "Vilnius": "LT", "Riga": "LV",
    "Budapest": "HU", "Athens": "GR", "Vienna": "AT", "Geneva": "CH",
    "Los Angeles": "US", "Chicago": "US", "Denver": "US", "Atlanta": "US",
    "Miami": "US", "Vancouver": "CA", "Montreal": "CA", "Auckland": "NZ",
    "Brisbane": "AU", "Accra": "GH", "Kigali": "RW", "Casablanca": "MA",
}


@pytest.mark.parametrize("city,code", sorted(NAMED_GAP.items()))
def test_the_named_gap_is_covered(city, code):
    hit = vocab.normalize_city(city)
    assert hit is not None, f"{city} still cannot be stored"
    assert hit[2] == code, f"{city} -> {hit[2]}, expected {code}"


@pytest.mark.parametrize("alias,city", [
    ("Bengaluru", "Bangalore"), ("Bangalore", "Bangalore"),
    ("Tel Aviv-Yafo", "Tel Aviv"), ("Tel Aviv-Jaffa", "Tel Aviv"),
    ("São Paulo", "Sao Paulo"), ("Sao Paulo", "Sao Paulo"),
    ("Kiev", "Kyiv"), ("Kyiv", "Kyiv"),
    ("Bombay", "Mumbai"), ("Mumbai", "Mumbai"),
    ("Gurgaon", "Gurugram"), ("Gurugram", "Gurugram"),
    ("Calcutta", "Kolkata"), ("Saigon", "Ho Chi Minh City"),
    ("Delhi", "New Delhi"), ("Wien", "Vienna"), ("Köln", "Cologne"),
    ("CDMX", "Mexico City"), ("Ciudad de México", "Mexico City"),
    ("Göteborg", "Gothenburg"), ("Cluj", "Cluj-Napoca"),
])
def test_two_spellings_collapse_to_one_city(alias, city):
    """One market, one row on the dashboard. A reader filtering Bengaluru and a
    reader filtering Bangalore are looking at the same labour market."""
    hit = vocab.normalize_city(alias)
    assert hit is not None, alias
    assert hit[0] == city


# --- The way the city's own newsroom writes it -------------------------------
#
# The sibling tracker's 2026-08 defect was 45 news editions configured with a
# local UI language and English-only search phrases, so non-English markets
# returned global stories. This repo does not have that defect — GOOGLE_NEWS_VOCAB
# carries sixteen language packs and source_registry admits a locale only once
# its language has one. It has the defect ONE LAYER DOWN instead.
#
# `normalize_city` was a plain dict lookup on lowercase+whitespace, and of 422
# alias keys exactly 27 were non-ASCII — every one of them Latin script with a
# diacritic. No Japanese, Korean, Chinese, Hebrew, Arabic, Thai or Cyrillic
# spelling of any city was in the table at all. So we ask Google News in ja, ko,
# ar, he, vi and tr, get articles back that name their city in their own script,
# and the column comes out NULL. Measured on the committed corpus 2026-08-13,
# news rows by market: TR 0 of 71 placed, IL 0 of 70, VN 0 of 49, ID 0 of 47,
# JP 1 of 56, KR 1 of 77.
#
# The second half is folding. 'münchen' was in the table and 'munchen' was not,
# so a publisher that strips its own diacritics missed, and Turkish 'İzmir'
# lowercases to a dotted i that matched nothing.

@pytest.mark.parametrize("native,city,code", [
    # CJK — the frame's biggest absent cities
    ("東京", "Tokyo", "JP"), ("大阪", "Osaka", "JP"), ("京都", "Kyoto", "JP"),
    ("ソウル", "Seoul", "KR"), ("서울", "Seoul", "KR"), ("부산", "Busan", "KR"),
    ("上海", "Shanghai", "CN"), ("北京", "Beijing", "CN"),
    ("深圳", "Shenzhen", "CN"), ("杭州", "Hangzhou", "CN"),
    ("台北", "Taipei", "TW"), ("香港", "Hong Kong", "HK"),
    # Hebrew and Arabic — both have live Google News editions here
    ("תל אביב", "Tel Aviv", "IL"), ("ירושלים", "Jerusalem", "IL"),
    ("دبي", "Dubai", "AE"), ("القاهرة", "Cairo", "EG"),
    ("الرياض", "Riyadh", "SA"),
    # Vietnamese, Thai — live editions, zero placed rows today
    ("Hà Nội", "Hanoi", "VN"),
    ("Thành phố Hồ Chí Minh", "Ho Chi Minh City", "VN"),
    ("กรุงเทพ", "Bangkok", "TH"),
    # Latin script, local spelling
    ("Napoli", "Naples", "IT"), ("Warszawa", "Warsaw", "PL"),
    ("København", "Copenhagen", "DK"), ("Bucureşti", "Bucharest", "RO"),
    ("Bruselas", "Brussels", "BE"), ("İstanbul", "Istanbul", "TR"),
])
def test_a_city_written_in_its_own_language_is_the_same_city(native, city, code):
    """We ask sixteen languages for news and then only accept English answers.

    Every locale below is in GOOGLE_NEWS_LOCALES, so these spellings are what
    the articles we already pay to read actually contain."""
    hit = vocab.normalize_city(native)
    assert hit is not None, f"{native} ({city}) still cannot be stored"
    assert (hit[0], hit[2]) == (city, code), f"{native} -> {hit}"


@pytest.mark.parametrize("stripped,city", [
    ("Munchen", "Munich"), ("Muenchen", "Munich"), ("Zuerich", "Zurich"),
    ("Goteborg", "Gothenburg"), ("Kobenhavn", "Copenhagen"),
    ("Dusseldorf", "Dusseldorf"), ("Malmo", "Malmo"), ("Krakow", "Krakow"),
    ("Izmir", "Izmir"), ("Sao Paulo", "Sao Paulo"), ("Bogota", "Bogota"),
])
def test_a_diacritic_a_publisher_dropped_does_not_drop_the_city(stripped, city):
    """Half the wires transliterate their own accents. 'münchen' resolved and
    'munchen' did not, which is a spelling difference deciding whether a market
    exists on the dashboard."""
    hit = vocab.normalize_city(stripped)
    assert hit is not None, stripped
    assert hit[0] == city, f"{stripped} -> {hit[0]}"


def test_folding_never_puts_two_different_cities_on_one_key():
    """The fold is a FALLBACK and it must stay unambiguous. If two cities in
    the table fold to the same key, neither may be reachable through it —
    guessing between them is the exact failure the ambiguous-name refusals
    exist to prevent."""
    folded: dict[str, set[str]] = {}
    for alias, (city, _r, code) in vocab._CITY_ALIASES.items():
        folded.setdefault(vocab._fold(alias), set()).add((city, code))
    for key, cities in folded.items():
        if len(cities) > 1:
            assert vocab._CITY_FOLDED.get(key) is None, (
                f"folded key {key!r} answers for {sorted(cities)}")


# --- Still refusing to guess -------------------------------------------------

@pytest.mark.parametrize("name", sorted(vocab.AMBIGUOUS_CITY_NAMES))
def test_a_name_belonging_to_two_countries_is_refused_bare(name):
    """Cambridge is in England and in Massachusetts, Birmingham in England and
    in Alabama, San Jose in California and in Costa Rica. Storing either
    without the source's own qualifier would be inventing a country."""
    assert vocab.normalize_city(name) is None, name


@pytest.mark.parametrize("text,city,code", [
    ("Cambridge, MA", "Cambridge MA", "US"),
    ("Cambridge, Massachusetts", "Cambridge MA", "US"),
    ("Cambridge, UK", "Cambridge UK", "GB"),
    ("Birmingham, Alabama", "Birmingham AL", "US"),
    ("Birmingham, UK", "Birmingham UK", "GB"),
    ("San Jose, CA", "San Jose CA", "US"),
    ("San Jose, Costa Rica", "San Jose, Costa Rica", "CR"),
    ("London, Ontario", "London, Ontario", "CA"),
    ("Washington, DC", "Washington DC", "US"),
])
def test_the_qualified_spelling_is_accepted(text, city, code):
    """Where the SOURCE resolved the ambiguity, we can store it. That is the
    whole difference between reading and guessing."""
    hit = vocab.normalize_city(text)
    assert hit is not None, text
    assert (hit[0], hit[2]) == (city, code)


def test_bare_london_is_still_the_english_one_and_ontario_is_not():
    assert vocab.normalize_city("London")[2] == "GB"
    assert vocab.normalize_city("London, Ontario")[2] == "CA"


def test_a_country_is_not_a_city_unless_it_is_a_city_state():
    for country_only in ("Mexico", "Brazil", "India", "Germany", "Israel",
                         "Nigeria", "Kenya", "United Kingdom"):
        assert vocab.normalize_city(country_only) is None, country_only
    # Singapore and Luxembourg are both, and have been since before this work.
    assert vocab.normalize_city("Singapore") == ("Singapore", "Asia", "SG")
    assert vocab.normalize_city("Luxembourg")[2] == "LU"


def test_it_still_refuses_things_that_are_not_cities():
    for text in ("Atlantis", "Zenithville", "", "   ", "AI", "the company",
                 "Remote", "Anywhere", "EMEA", "Europe"):
        assert vocab.normalize_city(text) is None, text


def test_words_a_headline_uses_as_words_are_deliberately_absent():
    """Admitting one costs a real employer: cheap_extract._valid_name declines
    any span that IS a city, so a company called Reading could never close."""
    for word in ("Reading", "Bath", "Mobile", "Nice", "Orange", "LA"):
        assert vocab.normalize_city(word) is None, word


# --- The qualifier reader ----------------------------------------------------

@pytest.mark.parametrize("text,code", [
    ("Ireland", "IE"), ("United States", "US"), ("Ohio", "US"),
    ("Massachusetts", "US"), ("Ontario", "CA"), ("Quebec", "CA"),
    ("England", "GB"), ("Scotland", "GB"), ("New South Wales", "AU"),
    ("Karnataka", "IN"), ("Bavaria", "DE"), ("Catalonia", "ES"),
    ("", None), ("Nowhere", None), ("Acme Inc", None),
])
def test_place_qualifier_country(text, code):
    assert vocab.place_qualifier_country(text) == code


# --- Against the database we actually hold ----------------------------------

@pytest.mark.skipif(not DB.exists(), reason="no committed database in this tree")
def test_the_gazetteer_agrees_with_every_city_already_stored():
    """Expanding a vocabulary must never re-file history. Every city in the
    committed database has to read back as itself, in the country the row says.

    The one accepted disagreement is the Toronto/US legacy: two rows written
    before the table was corrected (the correction is recorded in vocab.py and
    in tests/test_identity.py). Those rows are wrong and this table is right.

    Named as an allowance rather than pinned as an equality, because
    correct_city_country.py exists to remove it: pinned, the correction landing
    would turn this green test red and read as a regression. The allowance can
    only ever SHRINK — anything not in it still fails — so a third city
    contradicting the table is as loud as it ever was.
    """
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT city, country FROM signals "
            "WHERE city IS NOT NULL AND city != ''").fetchall()
    finally:
        conn.close()
    assert rows, "the database holds no placed rows at all"

    unreadable, contradicted = [], []
    for city, country in rows:
        hit = vocab.normalize_city(city)
        if hit is None or hit[0] != city:
            unreadable.append((city, country))
        elif hit[2] != country:
            contradicted.append((city, country, hit[2]))
    assert not unreadable, f"stored cities the vocabulary cannot read: {unreadable}"
    assert set(contradicted) <= {("Toronto", "US", "CA")}, contradicted
