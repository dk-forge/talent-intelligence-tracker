"""The sources page is generated, so it cannot drift from what actually runs."""

import json
from pathlib import Path

import source_registry as registry

MANIFEST = (Path(__file__).parent.parent / "wordpress-plugin" /
            "talent-intelligence-tracker" / "data" / "sources.json")


def test_manifest_is_in_sync_with_the_registry():
    """Run build_sources_json.py if this fails — never hand-edit the JSON."""
    assert json.loads(MANIFEST.read_text()) == registry.sources_manifest()


def test_every_source_has_a_real_url():
    for s in registry.SOURCES:
        assert s.url.startswith("https://"), s.name


def test_status_is_only_live_or_candidate():
    for s in registry.SOURCES:
        assert s.status in ("live", "candidate"), f"{s.name}: {s.status}"


def test_live_sources_are_only_the_ones_with_collectors():
    """A source is live when a collector reads it, not when it looks easy.
    Adding a name here without a collector is the failure this guards."""
    live = {s.name for s in registry.SOURCES if s.status == "live"}
    # GDELT was here and is not any more: it produced zero records in its whole
    # life, so counting it made the page claim four running things when three
    # run. See test_a_source_that_has_never_yielded_is_not_counted_as_running.
    assert live == {
        "SEC EDGAR 8-K (Item 5.02)",
        "SEC EDGAR Form D",
        "Google News RSS",
    }


def test_google_news_is_live_because_urls_now_resolve():
    """It was listed candidate on the belief that publisher URLs were
    unrecoverable. They are recoverable through Google's own resolution
    endpoint, so it is a real source and the page says so."""
    gn = next(s for s in registry.SOURCES if "Google News" in s.name)
    assert gn.status == "live"
    assert "resolution endpoint" in gn.notes


def test_catalogue_only_lists_sources_we_could_actually_connect_to():
    """A name is not a roadmap item.

    The imported catalogue carried 383 rows, 272 of which had no feed, no API
    and no filing system behind them. Listing those as "researched" made the
    page read as coverage we do not have, and reading them would have meant
    scraping homepages. The prune is a rule, not a one-off edit, so it is
    pinned here.
    """
    import csv

    from source_registry import CATALOGUE_CSV, sources_manifest

    with CATALOGUE_CSV.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    unreachable = {
        r["name"]
        for r in rows
        if not r["rss"].startswith("http")
        and not r["api"].startswith("http")
        and r["source_type"] not in {
            "Government Agency", "Government Open Data", "Regulatory Body",
            "Stock Exchange", "Statistical Agency",
        }
    }
    assert unreachable, "fixture check: the catalogue should still contain some"

    listed = {s["name"] for s in sources_manifest()}
    # Hand-written registry entries win on a name clash and are exempt: they
    # exist because a collector reads them.
    from source_registry import SOURCES
    hand = {s.name for s in SOURCES}
    assert not ((unreachable & listed) - hand)


def test_a_source_that_has_never_yielded_is_not_counted_as_running():
    """Coverage is earned. GDELT was listed as live for its whole life and
    produced no stored record; on the day it was retired a fair last test
    returned 429 on two of three requests at 8-second spacing."""
    from source_registry import SOURCES

    gdelt = next(s for s in SOURCES if s.name.startswith("GDELT"))
    assert gdelt.status == "candidate", "GDELT is retired, not running"
    assert "Retired" in gdelt.notes, "the page must say why, not just drop it"
