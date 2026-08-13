"""The structural facts the 2026-08-13 bucket measurement rests on.

`analysis/ranking/gold_bucket.py` concludes that a US funding event surfaced by
the Google News walker lands in the US bucket rather than a foreign one, and
that the walker's bucket count is bounded by its editions rather than by the 77
buckets the stored-population model showed. Neither is an opinion: both follow
from three lines of code that could be changed by accident, so they are pinned
here as arithmetic.

Nothing here touches a network, a model or a cent.
"""

from __future__ import annotations

import sqlite3

import pytest

import backfill_gnews_2026 as walker
import source_registry as registry
from analysis.ranking import gold_bucket
from pipeline import candidate_rank


# --- why a US-sourced article cannot land in a foreign bucket --------------

def test_the_us_edition_is_the_anchor_and_is_swept_first():
    """`fetch_day` keeps the FIRST edition that returned a `discovery_url`, so
    whichever edition leads the list stamps the locale on every article it
    surfaces. That is the whole reason a US round written up in Sao Paulo is
    bucketed US and not BR: the US edition answered first."""
    assert registry.GOOGLE_NEWS_ANCHOR == ("en", "US")
    assert walker.all_locales()[0] == registry.GOOGLE_NEWS_ANCHOR
    assert registry.GOOGLE_NEWS_ANCHOR not in registry.GOOGLE_NEWS_LOCALES, \
        "the anchor listed twice would sweep the US edition twice"


def test_a_google_news_item_is_bucketed_by_its_edition_never_its_publisher():
    item = {"locale": "US:en", "source_country": "Brazil",
            "headline": "Bland raises $50 million Series C", "raw_text": "x"}
    assert candidate_rank.candidate_country(item) == "US"


def test_an_edition_that_answers_second_never_gets_the_article():
    """Two editions, one article, anchor first. The bucket is decided by the
    sweep order and not by anything about the story."""
    seen: set[str] = set()
    out = []
    for _lang, country in [("en", "US"), ("pt", "BR")]:
        item = {"discovery_url": "https://news.google.com/rss/articles/AAA"}
        if item["discovery_url"] in seen:
            continue
        seen.add(item["discovery_url"])
        item["locale"] = f"{country}:en"
        out.append(item)
    assert [i["locale"] for i in out] == ["US:en"]


def test_the_walkers_bucket_count_is_bounded_by_its_editions():
    """The stored-population model in `read_share.py` reads `source_country`
    from the catalogue and finds 77 buckets. A google_news day cannot: every
    item carries a `locale`, and there are only this many editions."""
    editions = {country for _lang, country in walker.all_locales()}
    assert len(editions) <= len(walker.all_locales()), \
        "a country reached in two languages is still one bucket"
    assert len(editions) < 77


def test_only_two_edition_countries_hold_no_rows_at_all(tmp_path):
    """The round robin's visiting order is by best score, and the biggest term
    in that score is `W_COUNTRY_EMPTY`. It can only lift an edition whose
    country holds nothing — so on the walker's own population the term is
    almost always inert, which is why the US bucket is not pushed to the back.
    Pinned against a synthetic context so the test needs no database."""
    context = candidate_rank.Context(
        rows_by_country={"US": 10_376, "BR": 300, "SN": 0}, known_employers=())
    us = {"locale": "US:en", "headline": "Acme raises $10 million Series A",
          "raw_text": "Acme raises $10 million Series A"}
    br = {"locale": "BR:pt", "headline": "Empresa capta milhoes",
          "raw_text": "Empresa capta milhoes"}
    sn = {"locale": "SN:fr", "headline": "Societe leve des fonds",
          "raw_text": "Societe leve des fonds"}
    order = candidate_rank.rank([br, sn, us], context)
    assert order[0] is sn, "a country holding nothing still goes first"
    assert order.index(us) < order.index(br), \
        "against a country that merely holds rows, US keyword force wins"


# --- the probe's own machinery --------------------------------------------

def test_the_name_matcher_is_literal_and_punctuation_tolerant():
    assert gold_bucket.name_pattern("logcat.ai").search("Logcat.ai raises $4M")
    assert gold_bucket.name_pattern("logcat.ai").search("logcat ai raises $4M")
    assert gold_bucket.name_pattern("Norm Ai").search("Norm Ai lands $48M")
    assert not gold_bucket.name_pattern("Norm Ai").search("normal AI is fine")
    assert not gold_bucket.name_pattern("Queue").search("Queues are long")


def test_every_gold_day_and_the_day_after_are_swept():
    events = [{"event_date": "2026-06-16"}, {"event_date": "2026-06-17"}]
    assert gold_bucket.sweep_dates(events) == [
        "2026-06-16", "2026-06-17", "2026-06-18"]


def test_the_as_of_context_counts_the_same_rows_as_the_live_one(tmp_path):
    """`context_as_of(conn, None)` must be `Context.for_conn(conn)` exactly, or
    the historical lens and the live one are measuring different corpora."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE signals (is_current INT, country TEXT, "
                 "company_key TEXT, captured_at TEXT)")
    conn.executemany("INSERT INTO signals VALUES (1, ?, ?, ?)", [
        ("US", "acme", "2026-07-01"), ("US", "beta", "2026-09-01"),
        ("BR", "gamma", "2026-07-01")])
    conn.commit()

    live = gold_bucket.context_as_of(conn, None)
    assert live.rows_by_country == candidate_rank.Context.for_conn(
        conn).rows_by_country

    historical = gold_bucket.context_as_of(conn, "2026-08-04")
    assert historical.rows_by_country == {"US": 1, "BR": 1}
    assert "beta" not in historical.known_employers


def test_a_row_with_no_capture_date_is_counted_rather_than_dropped(tmp_path):
    """The conservative direction: an undated row can only ADD to a country and
    so only ever LOWERS its need bonus. Dropping it would invent a need."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE signals (is_current INT, country TEXT, "
                 "company_key TEXT, captured_at TEXT)")
    conn.execute("INSERT INTO signals VALUES (1, 'BR', 'x', NULL)")
    conn.commit()
    assert gold_bucket.context_as_of(conn, "2026-08-04").rows_by_country == {
        "BR": 1}


def test_the_probe_opens_the_database_read_only():
    """A measurement that migrates the schema is not a measurement. `connect()`
    in pipeline.schema runs `_migrate`; this one cannot."""
    conn = gold_bucket.read_only_conn()
    if conn is None:                      # no committed database in this tree
        pytest.skip("no data/talent_intel.db")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE probe_should_not_be_able_to (x INT)")
    conn.close()


def test_no_free_text_survives_into_the_committed_sweep():
    """The first attempt to commit this sweep went red on
    `test_no_provider_names.py`: two matched HEADLINES named a commercial data
    service, which is banned repo-wide. Free text is dropped at write time
    rather than filtered — a filter is only as good as the list behind it, and
    this file grows every time the sweep is run."""
    payload = {"days": {"2026-06-16": {"hits": {"x": [{
        "headline": "Acme raises $10M, says a data service",
        "source_name": "Example Wire", "bucket": "US", "rank": 3}]}}}}
    out = gold_bucket.scrub(payload)
    hit = out["days"]["2026-06-16"]["hits"]["x"][0]
    assert "headline" not in hit and "source_name" not in hit
    assert hit["bucket"] == "US" and hit["rank"] == 3
    assert len(hit["id"]) == 12 and "acme" not in hit["id"].lower()


def test_the_committed_sweep_carries_no_free_text_key():
    if not gold_bucket.CACHE.exists():
        pytest.skip("no committed sweep in this tree")
    raw = gold_bucket.CACHE.read_text()
    assert '"headline"' not in raw and '"source_name"' not in raw
