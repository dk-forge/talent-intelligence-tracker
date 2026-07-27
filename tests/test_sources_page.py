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
    assert live == {
        "SEC EDGAR 8-K (Item 5.02)",
        "SEC EDGAR Form D",
        "GDELT DOC 2.0",
        "Google News RSS",
    }


def test_google_news_is_live_because_urls_now_resolve():
    """It was listed candidate on the belief that publisher URLs were
    unrecoverable. They are recoverable through Google's own resolution
    endpoint, so it is a real source and the page says so."""
    gn = next(s for s in registry.SOURCES if "Google News" in s.name)
    assert gn.status == "live"
    assert "resolution endpoint" in gn.notes
