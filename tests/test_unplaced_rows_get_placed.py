"""A row that would be stored with no country at all gets ONE free lookup.

THE DEFECT, measured on 2026-08-12 against the committed database.

1,666 current rows carry neither `country` nor `hq_country`. The site's
geographic clause is

    country IN (...) OR (country IS NULL AND hq_country IN (...))

so every one of them is invisible to every place filter on a product whose
whole organising idea is place. Of the 21 US funding events the sealed US
recall set says we hold, 13 were in exactly that state, which is why a reader
filtering the site to the United States saw 5 of 51 rather than 21 of 51.

Where the place went, in three parts, none of which is a validation bug:

  * The text does not carry it. A free scan of `headline + summary` over all
    1,666 placed exactly ZERO of them: the stored columns are exhausted.
  * 887 of the 1,666 never met a model at all. `cheap_extract` closed them for
    free and returns `headquarters_city` and `headquarters_country` as the
    empty string by construction — a regex cannot know where an employer is
    seated, and it is right not to pretend.
  * The one free mechanism that CAN answer it, `pipeline/identity.py`, ran on
    the ingestion path in cache-only mode against a cache nothing fills.
    `--backfill` is a command a human types; no workflow has ever run it; and
    12,881 of 16,597 employer keys have no cache row. So the ingestion lookup
    was not usually a hit, it was a guaranteed miss for every employer we had
    not seen before, for ever.

THE FIX under test: `identity.place_if_unplaced`, called by
`validate.build_signal` immediately after the cache-only `enrich`. It fires
for a placeless row and for nothing else, spends no money, is bounded per
process, and fails open.

WHAT IS DELIBERATELY NOT TESTED HERE, because it is deliberately not built: a
model is never asked where a company is headquartered. The identity spine's
authorities are SEC and Wikidata for exactly the reason this repo pins the
ticker authority to SEC — a confidently wrong country on a public page is
worse than an honest blank, and this project has already relabelled three rows
into the wrong country once.
"""

import os

import pytest

from pipeline import cheap_extract, identity, schema, validate


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    identity.ensure_cache(connection)
    yield connection
    connection.close()


@pytest.fixture
def lookup_on(monkeypatch):
    """The suite runs with the lookup off (tests/conftest.py). Turn it on for
    the tests that are about it, and never let one reach the real internet."""
    monkeypatch.setenv("TIT_IDENTITY_LOOKUP", "on")
    identity.reset_placement_budget()
    yield


def item(headline, teaser="", url="https://outlet.example/story-1"):
    return {
        "headline": headline,
        "raw_text": f"{headline}\n\n{teaser}".strip(),
        "source_url": url,
        "discovery_url": url,
        "source_name": "Example Wire",
        "published_date": "2026-07-29",
    }


def resolver(monkeypatch, **fields):
    """Stand in for the network half of `identity.resolve`. Counts its calls."""
    calls = []

    def fake(name, *, conn=None, allow_network=True, **kwargs):
        calls.append((name, allow_network))
        if not allow_network:
            # Faithful to the real thing: the cache is empty in these tests,
            # exactly as it is empty for 12,881 of 16,597 real employers, so a
            # cache-only read answers nothing.
            return identity.Identity(company_key=name.lower(), company=name,
                                     detail="cache miss")
        return identity.Identity(company_key=name.lower(), company=name,
                                 resolved=True, **fields)

    monkeypatch.setattr(identity, "resolve", fake)
    return calls


# --- The defect --------------------------------------------------------------

def test_a_placeless_funding_row_is_placed_by_the_identity_spine(conn, lookup_on,
                                                                 monkeypatch):
    """RED BEFORE THE FIX with:

        AssertionError: 'Mirendil Raises $200 Million Seed Round' stored with
        country=None and hq_country=None, so a reader filtering the site to
        the United States cannot see it
        assert None == 'US'

    This headline is a real one. It is the Mirendil row the US recall set
    matched, and it is one of the 13 we held and no US reader could find.
    """
    calls = resolver(monkeypatch, hq_city="New York", hq_country="US")
    raw = item("Mirendil Raises $200 Million Seed Round")
    classified = cheap_extract.extract(raw)
    assert classified is not None, "the free path is what stores this row"
    assert classified["headquarters_country"] == "", (
        "a regex cannot know an employer's seat, and it must not pretend to")

    signal = validate.build_signal(classified, raw, "google_news", conn=conn)

    assert signal.country is None, "nothing in the text states a place"
    assert signal.hq_country == "US", (
        f"{raw['headline']!r} stored with country={signal.country} and "
        f"hq_country={signal.hq_country}, so a reader filtering the site to "
        "the United States cannot see it")
    assert any(allow_network for _name, allow_network in calls), (
        "the rescue lookup must actually be allowed to use the network; a "
        "second cache read of a cache nothing fills is the original defect")


def test_the_reader_visible_clause_now_admits_the_row(conn, lookup_on, monkeypatch):
    """The assertion the owner actually asked about, written as the site writes it."""
    resolver(monkeypatch, hq_city="Boston", hq_country="US")
    raw = item("Databento Raises $50 Million Series B")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "google_news", conn=conn)
    visible_under_us = (signal.country == "US"
                        or (signal.country is None and signal.hq_country == "US"))
    assert visible_under_us


# --- And nothing else ---------------------------------------------------------

def test_a_row_that_already_has_a_country_buys_no_lookup(conn, lookup_on, monkeypatch):
    """Rule 1 of identity.py, at the level of whether the call happens at all.

    A sourced place is the answer. Spending a network round trip to confirm it
    would be latency for nothing, and it would put a derived value one bug
    away from a sourced one.
    """
    calls = resolver(monkeypatch, hq_city="London", hq_country="GB")
    raw = item("Boston-based Acme raised $12.5M in seed funding")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "national_press", conn=conn)
    assert signal.country == "US" and signal.city == "Boston"
    assert not any(allow_network for _n, allow_network in calls)


def test_the_lookup_can_be_switched_off_entirely(conn, monkeypatch):
    """`TIT_IDENTITY_LOOKUP=off` is the pre-2026-08-12 behaviour, exactly.

    `run_collect --offline` sets it, because a dry run that promises no
    network call must not make one.
    """
    monkeypatch.setenv("TIT_IDENTITY_LOOKUP", "off")
    calls = resolver(monkeypatch, hq_city="New York", hq_country="US")
    raw = item("Mirendil Raises $200 Million Seed Round")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "google_news", conn=conn)
    assert signal.hq_country is None
    assert not any(allow_network for _n, allow_network in calls)


def test_the_budget_bounds_a_bad_run(conn, lookup_on, monkeypatch):
    """A collect run must not become a crawl because a day was unusual."""
    monkeypatch.setattr(identity, "PLACEMENT_LOOKUP_BUDGET", 2)
    calls = resolver(monkeypatch, hq_city="New York", hq_country="US")
    for n in range(5):
        raw = item(f"Company{n} Raises $10 Million Seed Round",
                   url=f"https://outlet.example/story-{n}")
        validate.build_signal(cheap_extract.extract(raw), raw,
                              "google_news", conn=conn)
    assert len([c for c in calls if c[1]]) == 2
    assert identity.placement_lookups_used() == 2


def test_a_failing_lookup_costs_the_record_nothing(conn, lookup_on, monkeypatch):
    """Rule 3 of identity.py. Ingestion is not a nice-to-have and identity is."""
    def explode(*_a, **_k):
        raise RuntimeError("wikidata is having a day")

    monkeypatch.setattr(identity, "resolve", explode)
    raw = item("Mirendil Raises $200 Million Seed Round")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "google_news", conn=conn)
    assert signal is not None and signal.funding_amount == "$200 Million"
    assert signal.hq_country is None


def test_no_connection_means_no_lookup(conn, lookup_on, monkeypatch):
    """`build_signal` without a conn stays a pure function of two dicts, which
    is what every older test of it relies on."""
    calls = resolver(monkeypatch, hq_city="New York", hq_country="US")
    raw = item("Mirendil Raises $200 Million Seed Round")
    signal = validate.build_signal(cheap_extract.extract(raw), raw, "google_news")
    assert signal.hq_country is None
    assert not any(allow_network for _n, allow_network in calls)


def test_two_organisations_of_the_same_name_place_nothing(conn, lookup_on,
                                                          monkeypatch):
    """The guard that keeps this cheap fix from becoming the documented incident.

    `_names_agree` already throws out every hit whose name merely BEGINS with
    the employer's, so two survivors are two organisations with the SAME name
    and sitelinks hand it to the better-known one. Measured on the committed
    corpus that is not an occasional slip: the AI video company Synthesia
    resolves to the Czech chemical works, Fluidstack to a French namesake, BKV
    Corporation to a Hungarian political party, Capital Bancorp to a Nigerian
    bank. Every one would render on a public page exactly like a right answer.
    """
    def ambiguous(name, *, conn=None, allow_network=True, **kwargs):
        if not allow_network:
            return identity.Identity(company_key=name.lower(), company=name,
                                     detail="cache miss")
        return identity.Identity(
            company_key=name.lower(), company=name, resolved=True,
            hq_country="CZ", hq_city="Pardubice",
            detail=f"wikidata Q123, 2 {identity.AMBIGUOUS_MARKER}")

    monkeypatch.setattr(identity, "resolve", ambiguous)
    raw = item("Synthesia Raises $180 Million Series D")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "google_news", conn=conn)
    assert signal.hq_country is None, "a coin flip is not a country"
    assert signal.hq_city is None


def test_a_country_with_no_headquarters_city_behind_it_places_nothing(
        conn, lookup_on, monkeypatch):
    """The near miss that bought this bar, written down as the row it was.

    `hq_country` is read from P17 of the entity's HEADQUARTERS and falls back
    to P17 of the entity itself. The fallback is where the errors live: on the
    committed corpus the 108 cityless resolutions include Premier Lacrosse
    League as Canada, and that row is one of the 13 US funding events a reader
    could not find. Filing it under Canada is not an improvement on filing it
    nowhere.
    """
    def cityless(name, *, conn=None, allow_network=True, **kwargs):
        if not allow_network:
            return identity.Identity(company_key=name.lower(), company=name,
                                     detail="cache miss")
        return identity.Identity(company_key=name.lower(), company=name,
                                 resolved=True, hq_country="CA", hq_city=None,
                                 detail="wikidata Q60750165")

    monkeypatch.setattr(identity, "resolve", cityless)
    raw = item("Lacrosse Raises $35M in Series B")
    signal = validate.build_signal(cheap_extract.extract(raw), raw,
                                   "google_news", conn=conn)
    assert signal.hq_country is None
    assert not identity.is_placeable(
        identity.Identity(company_key="x", hq_country="CA"))
    assert identity.is_placeable(
        identity.Identity(company_key="x", hq_country="CA", hq_city="Toronto"))


def test_the_ambiguity_is_recorded_where_the_cache_keeps_it():
    """`detail` is cached, so the marker survives a cache hit. Without that the
    guard would only work on the run that resolved the employer."""
    props = {
        "Q1": {"roots": ["Q4830453"], "sitelinks": 40, "instances": [],
               "places": [], "hq_country": "Czechia", "country": "Czechia"},
        "Q2": {"roots": ["Q4830453"], "sitelinks": 1, "instances": [],
               "places": [], "hq_country": "United Kingdom", "country": "United Kingdom"},
    }
    ident = identity._identity_from_props("Synthesia", ["Q1", "Q2"], props)
    assert identity.AMBIGUOUS_MARKER in ident.detail
    assert identity.is_ambiguous(ident)
    assert identity.is_ambiguous(identity.Identity(company_key="x", detail=ident.detail))

    only_one = identity._identity_from_props("Synthesia", ["Q1"], props)
    assert not identity.is_ambiguous(only_one)


def test_the_spine_is_never_allowed_to_ask_a_model():
    """The guard that keeps the cheap fix from becoming the expensive mistake.

    `identity.py` resolves from SEC and Wikidata. If a future edit reaches for
    the classifier to fill a headquarters from a company name, this goes red,
    and it should: that value would be indistinguishable on the page from a
    sourced one and wrong an unknown fraction of the time.
    """
    source = open(identity.__file__, encoding="utf-8").read()
    body = source.split('"""', 2)[-1]          # skip the module docstring
    for banned in ("openrouter", "classify.", "import classify", "chat/completions"):
        assert banned not in body.lower(), (
            f"pipeline/identity.py must never call a model; found {banned!r}")


def test_the_suite_runs_with_the_lookup_off():
    """conftest.py is load-bearing: five older tests hand build_signal a real
    connection, and any of them could otherwise reach Wikidata."""
    assert os.environ.get("TIT_IDENTITY_LOOKUP") == "off"
