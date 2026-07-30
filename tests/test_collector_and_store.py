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


def test_a_market_with_terms_has_its_papers_of_record_wired():
    """Spec 14.2: a local-language term without that country's outlets is pure
    waste — the term can only surface a story if somebody publishes one we
    read. Every market that carries `terms` must therefore have at least two
    wired publisher feeds for its country in the catalogue (two, because one
    feed for a whole country is a single point of failure the catalogue
    already refuses elsewhere)."""
    import csv

    with registry.CATALOGUE_CSV.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    wired_by_country: dict[str, int] = {}
    for row in rows:
        if (row.get("rss") or "").startswith("http"):
            country = (row.get("country") or "").strip()
            wired_by_country[country] = wired_by_country.get(country, 0) + 1

    for market in registry.MARKETS:
        if not market.terms:
            continue
        assert wired_by_country.get(market.name, 0) >= 2, (
            f"{market.iso2} carries local terms but {market.name} has fewer "
            f"than two wired feeds in the catalogue — the terms have no "
            f"papers of record to surface stories in"
        )


def test_every_market_is_reachable_by_some_discovery_route():
    """A market in MARKETS that nothing queries and nothing reads claims 'we
    monitor news here' while no collector ever asks. Two legitimate routes
    exist: a Google News edition in the locale rotation, or wired publisher
    feeds in the catalogue (Luxembourg has no dedicated Google News edition
    and is read through six national feeds instead). A market with neither is
    a name on a page."""
    import csv

    rotated = {country for _, country in registry.GOOGLE_NEWS_LOCALES}
    rotated.add(registry.GOOGLE_NEWS_ANCHOR[1])

    with registry.CATALOGUE_CSV.open(newline="") as fh:
        fed = {row["country"].strip() for row in csv.DictReader(fh)
               if (row.get("rss") or "").startswith("http")}

    for market in registry.MARKETS:
        assert market.iso2 in rotated or market.name in fed, (
            f"{market.iso2} is listed as a market but no locale queries its "
            f"edition and no catalogue feed covers it"
        )


def test_the_segment_matrix_still_sweeps_inside_the_recency_window():
    """The coupling that broke once already: queries asked `when:3d` while the
    rotation took 6.2 days, and the gap was invisible — the markets simply
    returned less. The locale window is derived (recency_window_days) and
    tested in test_locale_rotation.py; this guards the SEGMENT matrix the same
    way, so that widening MARKETS cannot quietly stretch its sweep past what a
    derived window would cover. At 4 segments a run, twice a day, the matrix
    must sweep inside the window the locale rotation derives."""
    import math

    from run_collect import LOCALES_PER_RUN, RUNS_PER_DAY, SEGMENTS_PER_RUN

    segments = registry.build_segments()
    sweep_days = math.ceil(len(segments) / SEGMENTS_PER_RUN / RUNS_PER_DAY)
    window = registry.recency_window_days(LOCALES_PER_RUN, RUNS_PER_DAY)
    assert sweep_days <= window, (
        f"{len(segments)} segments at {SEGMENTS_PER_RUN}/run x "
        f"{RUNS_PER_DAY}/day sweep in {sweep_days}d, outside the {window}d "
        f"recency window: a segment's stories can age out before its turn "
        f"comes round"
    )


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


def test_duplicate_verdict_and_store_cannot_disagree(conn, signal):
    """`run_collect` asks the dedup layers BEFORE buying the read-through, and
    `store()` asks them again on the way in. Two implementations of "is this
    already held" would eventually answer differently and either double-store a
    record or charge for one that never lands, so there is exactly one."""
    import inspect

    assert "duplicate_verdict(conn, signal)" in inspect.getsource(store.store)

    assert store.duplicate_verdict(conn, signal) is None
    assert store.store(conn, signal) == "stored"
    conn.commit()

    assert store.duplicate_verdict(conn, signal) == "duplicate"
    assert store.store(conn, signal) == "duplicate"
