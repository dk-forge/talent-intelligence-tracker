"""Offline tests for the collector, the store and the registry.

No network. The RSS fixture is a real-shaped payload captured to disk.

Note on stubbing (spec 8): nothing here stubs a real module into sys.modules.
A fake that persists there shadows the real module for every test that loads
afterwards, so tests pass alone and fail in the suite.
"""

from pathlib import Path

import pytest

import source_registry as registry
from collectors import google_news
from pipeline import dedupe, schema, store, validate

FIXTURE = Path(__file__).parent / "fixtures" / "google_news_sample.xml"


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def signal():
    return validate.build_signal(
        {
            "company": "Stripe",
            "pillar": "company_development",
            "signal_direction": "hiring",
            "city": "Dublin",
            "country": "Ireland",
            "confidence": "reported",
            "headline": "Stripe to create 300 new jobs in Dublin",
            "summary": "Stripe will add 300 roles in Dublin.",
            "talent_readthrough": "300 engineering roles entering the Dublin market.",
        },
        {
            "raw_text": "Stripe to create 300 new jobs in Dublin",
            "source_url": "https://www.irishtimes.com/stripe/",
            "source_name": "The Irish Times",
            "published_date": "2026-07-20",
        },
        "google_news",
    )


# --- collector -------------------------------------------------------------

def test_parses_every_well_formed_item():
    items = google_news.parse(FIXTURE.read_bytes())
    assert len(items) == 4


def test_every_item_carries_raw_text():
    for item in google_news.parse(FIXTURE.read_bytes()):
        assert item["raw_text"].strip()


def test_source_element_becomes_source_url_and_name():
    first = google_news.parse(FIXTURE.read_bytes())[0]
    assert first["source_url"].startswith("https://www.irishtimes.com/business/")
    assert first["source_name"] == "The Irish Times"
    assert "news.google.com" in first["discovery_url"]


def test_item_without_a_source_element_falls_back_to_the_google_link():
    """And validate.py then rejects it rather than crediting Google."""
    last = google_news.parse(FIXTURE.read_bytes())[3]
    assert "news.google.com" in last["source_url"]

    with pytest.raises(validate.Rejected, match="aggregator"):
        validate.build_signal(
            {
                "company": "X", "pillar": "company_development",
                "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
                "confidence": "reported", "headline": "h", "summary": "s",
                "talent_readthrough": "t",
            },
            last,
            "google_news",
        )


def test_query_url_is_well_formed():
    url = google_news.build_query_url('"new jobs" OR "hiring spree"', country="IE", lang="en")
    assert url.startswith("https://news.google.com/rss/search?")
    assert "ceid=IE%3Aen" in url


# --- store -----------------------------------------------------------------

def test_store_then_exact_duplicate_is_caught(conn, signal):
    assert store.store(conn, signal) == "stored"
    assert store.store(conn, signal) == "duplicate"


def test_fuzzy_duplicate_same_company_same_pillar_within_window(conn, signal):
    store.store(conn, signal)

    restated = validate.build_signal(
        {
            "company": "Stripe", "pillar": "company_development",
            "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
            "confidence": "reported",
            "headline": "Stripe confirms 300 roles for Dublin office",
            "summary": "Stripe confirmed 300 roles.",
            "talent_readthrough": "Same development, different outlet.",
        },
        {
            "raw_text": "Stripe confirms 300 roles for Dublin office",
            "source_url": "https://www.rte.ie/stripe/",
            "source_name": "RTE",
            "published_date": "2026-07-23",
        },
        "google_news",
    )
    assert dedupe.fuzzy_duplicate(conn, restated) is not None


def test_seen_urls_short_circuits_before_the_llm(conn):
    url = "https://www.irishtimes.com/stripe/"
    assert not store.already_seen(conn, url)
    store.mark_seen(conn, url, "google_news", "stored")
    assert store.already_seen(conn, url)


def test_zero_items_is_degraded_not_ok(conn):
    store.report_health(conn, "google_news", status="ok", items_found=0)
    row = conn.execute("SELECT status FROM source_health").fetchone()
    assert row["status"] == "degraded"


def test_revision_preserves_the_original_row(conn, signal):
    store.store(conn, signal)

    corrected = validate.build_signal(
        {
            "company": "Stripe", "pillar": "company_development",
            "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
            "confidence": "reported",
            "headline": "Stripe to create 300 new jobs in Dublin",
            "summary": "Stripe will add 300 roles in Dublin over two years.",
            "talent_readthrough": "Corrected read-through.",
        },
        {
            "raw_text": "Stripe to create 300 new jobs in Dublin over two years",
            "source_url": "https://www.irishtimes.com/stripe/",
            "source_name": "The Irish Times",
            "published_date": "2026-07-20",
        },
        "google_news",
    )
    store.revise(conn, signal.signal_id, corrected, note="clarified timeframe")

    rows = conn.execute(
        "SELECT revision, is_current FROM signals WHERE signal_id = ? ORDER BY revision",
        (signal.signal_id,),
    ).fetchall()

    assert [(r["revision"], r["is_current"]) for r in rows] == [(1, 0), (2, 1)]


def test_outcome_columns_exist_from_day_one(conn):
    """Impossible to retrofit, so they ship unused (spec 18)."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    assert {"predicted_outcome", "check_after_date",
            "outcome_observed", "outcome_source_url"} <= columns


# --- registry --------------------------------------------------------------

def test_no_market_claims_more_than_its_tier_earns():
    for market in registry.MARKETS:
        assert market.status in registry.TIER_PUBLIC_CLAIM
        if market.status == registry.DISCOVERY_ONLY:
            assert market.live_sources == ("google_news",), market.iso2


def test_coverage_manifest_renders_every_market():
    manifest = registry.coverage_manifest()
    assert len(manifest) == len(registry.MARKETS)
    assert all(entry["public_claim"] for entry in manifest)


def test_rotation_sweeps_the_whole_matrix():
    segments = registry.build_segments()
    seen = set()
    for day in range(1, 22):
        for run_index in range(2):
            seen.update(registry.rotate(segments, day, run_index, 2, 4))
    assert seen == set(segments)


def test_rotation_is_deterministic():
    segments = registry.build_segments()
    assert registry.rotate(segments, 200, 1, 2, 4) == registry.rotate(segments, 200, 1, 2, 4)


def test_euphemisms_are_standalone_never_segments():
    """A euphemism AND-ed with the base vocabulary can only ever match articles
    that also use the obvious word. That bug made 16 sibling terms dead on
    arrival (spec 14)."""
    segments = set(registry.build_segments())
    assert not (segments & set(registry.STANDALONE_QUERIES))


def test_the_publisher_reaches_the_classifier():
    """The outlet is the best geography hint in the item and it was being
    dropped. "USTA SC names new CEO" places nowhere on its own; the same story
    from the Post and Courier is South Carolina. Five dry runs stored nine of
    eleven records with no location while source_name sat in the item, unused.
    """
    import inspect

    from pipeline import classify

    src = inspect.getsource(classify.classify)
    assert 'raw.get("source_name")' in src
    assert "Published by:" in src
