"""The placement bar applies to every writer of hq_city / hq_country, not one.

THE DEFECT, measured on the committed database 2026-09-02.

`identity.is_placeable` refuses two classes of resolution: a country with no
headquarters city behind it (P17 of the entity itself, the fallback where
Premier Lacrosse League became Canadian) and a name that two organisations
share. It was the bar for `place_if_unplaced` and `place_backfill`, and for
nothing else. `enrich()` on the ingestion path and `apply_identity()` under
`--backfill` / `--apply-cache` copied every non-empty field off the cached
row, so the refused class walked in as soon as an employer had a cache row:

    current rows carrying a cityless-cache hq_country written AFTER the
    cache row resolved (so through enrich at ingestion)          276
      of which it is the ONLY place on the row                     37
    rows one --apply-cache run would have stamped the same way  1,694
      from an ambiguous cache row                                  36

`reverse_cityless_hq.py` says "nothing new joins the list". These tests make
that true: a resolution that does not clear the bar may still fill a ticker,
a CIK or an employer type, and writes no geography anywhere.
"""
import sqlite3

import pytest

from pipeline import cheap_extract, identity, schema, validate


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute("ATTACH DATABASE ':memory:' AS cache")
    connection.executescript(schema.TABLES)
    connection.executescript(schema.CACHE_TABLES)
    identity.ensure_cache(connection)
    yield connection
    connection.close()


def cached(conn, company, **fields):
    ident = identity.Identity(company_key=identity.vocab.company_key(company),
                              company=company, resolved=True, **fields)
    identity.cache_put(conn, ident)
    return ident


def item(headline, url="https://outlet.example/story-1"):
    return {
        "headline": headline,
        "raw_text": headline,
        "source_url": url,
        "discovery_url": url,
        "source_name": "Example Wire",
        "published_date": "2026-09-01",
    }


def stored_row(conn, company, **cols):
    base = {"signal_id": "s", "headline": "h", "summary": "s",
            "talent_readthrough": "t", "company": company,
            "company_key": identity.vocab.company_key(company),
            "pillar": "leadership", "signal_direction": "neutral",
            "confidence": "reported", "source_url": "https://e.com/a",
            "source_name": "E", "captured_at": "2026-09-01",
            "as_of": "2026-09-01", "content_hash": f"hash-{company}-{len(cols)}",
            "collector": "test"}
    base.update(cols)
    conn.execute(
        f"INSERT INTO signals ({', '.join(base)}) VALUES ({', '.join('?' * len(base))})",
        tuple(base.values()))


# --- writable_fields is the one rule ------------------------------------------

def test_a_cityless_resolution_may_write_everything_but_geography():
    cityless = identity.Identity(company_key="x", ticker="LAX", cik="1",
                                 employer_type="private", hq_country="CA",
                                 detail="wikidata Q60750165")
    assert set(identity.writable_fields(cityless)) == {"ticker", "cik", "employer_type"}


def test_an_ambiguous_resolution_may_write_everything_but_geography():
    ambiguous = identity.Identity(company_key="x", hq_city="Prague", hq_country="CZ",
                                  detail=f"wikidata Q1, 2 {identity.AMBIGUOUS_MARKER}")
    assert "hq_country" not in identity.writable_fields(ambiguous)
    assert "hq_city" not in identity.writable_fields(ambiguous)


def test_a_resolution_that_clears_the_bar_may_write_all_of_it():
    clean = identity.Identity(company_key="x", hq_city="Cupertino", hq_country="US",
                              detail="wikidata Q312")
    assert set(identity.writable_fields(clean)) == set(identity.ENRICHED_FIELDS)


# --- enrich(), the ingestion path -----------------------------------------------

def test_ingestion_does_not_place_a_row_from_a_cityless_cache_row(conn):
    """The 276. The suite runs with the network lookup off (conftest), so this
    is exactly the cache-only read every collector run makes."""
    cached(conn, "Lacrosse", ticker="LAX", hq_country="CA", detail="wikidata Q60750165")
    raw = item("Lacrosse Raises $35M in Series B")
    signal = validate.build_signal(cheap_extract.extract(raw), raw, "google_news", conn=conn)
    assert signal.hq_country is None
    assert signal.ticker == "LAX"          # identity still enriched the rest


def test_ingestion_does_not_place_a_row_from_an_ambiguous_cache_row(conn):
    cached(conn, "Synthesia", hq_city="Prague", hq_country="CZ",
           detail=f"wikidata Q1, 2 {identity.AMBIGUOUS_MARKER}")
    raw = item("Synthesia Raises $50M in Series C")
    signal = validate.build_signal(cheap_extract.extract(raw), raw, "google_news", conn=conn)
    assert signal.hq_country is None
    assert signal.hq_city is None


def test_ingestion_still_places_a_row_from_a_clean_cache_row(conn):
    cached(conn, "Groq", hq_city="Mountain View", hq_country="US", detail="wikidata Q126050137")
    raw = item("Groq Raises $640M in Series D")
    signal = validate.build_signal(cheap_extract.extract(raw), raw, "google_news", conn=conn)
    assert (signal.hq_city, signal.hq_country) == ("Mountain View", "US")


# --- apply_identity / apply_cache, the backfill path ----------------------------

def test_apply_cache_does_not_stamp_a_cityless_country_onto_history(conn):
    """The 1,694. `--apply-cache` reads every resolved cache row and UPDATEs by
    company_key; before this it carried no bar at all."""
    cached(conn, "Lacrosse", ticker="LAX", hq_country="CA", detail="wikidata Q60750165")
    stored_row(conn, "Lacrosse")
    stats = identity.apply_cache(conn)
    ticker, hq_country = conn.execute("SELECT ticker, hq_country FROM signals").fetchone()
    assert ticker == "LAX"
    assert hq_country is None
    assert stats["rows"]["hq_country"] == 0


def test_apply_cache_carries_the_ambiguity_marker_off_the_cached_row(conn):
    """The 36. `detail` is where the cache keeps the marker; a SELECT that
    drops it would place a two-organisation name with a city behind it."""
    cached(conn, "Synthesia", hq_city="Prague", hq_country="CZ",
           detail=f"wikidata Q1, 2 {identity.AMBIGUOUS_MARKER}")
    stored_row(conn, "Synthesia")
    identity.apply_cache(conn)
    assert conn.execute("SELECT hq_country FROM signals").fetchone()[0] is None


def test_apply_cache_still_places_from_a_clean_cache_row(conn):
    cached(conn, "Groq", hq_city="Mountain View", hq_country="US", detail="wikidata Q126050137")
    stored_row(conn, "Groq")
    stats = identity.apply_cache(conn)
    assert conn.execute("SELECT hq_city, hq_country FROM signals").fetchone() == ("Mountain View", "US")
    assert stats["rows"]["hq_country"] == 1


def test_every_writer_of_geography_goes_through_the_one_rule():
    """Mutation guard on the shape: the three loops that write ENRICHED_FIELDS
    onto a row or a signal must iterate `writable_fields(...)`, never the raw
    tuple. `place_backfill` is the exception because it writes PLACEMENT_FIELDS
    only after its own `is_placeable` check."""
    source = open(identity.__file__, encoding="utf-8").read()
    for writer in ("def enrich(", "def apply_identity("):
        body = source.split(writer, 1)[1].split("\ndef ", 1)[0]
        assert "for field in writable_fields(ident)" in body, writer
        assert "for field in ENRICHED_FIELDS" not in body, writer
