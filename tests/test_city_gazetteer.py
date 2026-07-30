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
    in tests/test_identity.py). Those rows are wrong, this table is right, and
    a backfill is the owner's call — so the exception is named here rather
    than hidden by a loose assertion.
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
    assert contradicted == [("Toronto", "US", "CA")], contradicted
