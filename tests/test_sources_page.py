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

    This used to assert against a hardcoded set of five names, which made it
    guard only ONE direction: it caught a name added without a collector, and
    was silent when a collector ran with no name on the page. That is the
    direction the defect actually took. On 2026-07-29 the page listed five
    sources while run_collect registered nine, so the UK pay gap service, SEC
    executive compensation and the 575-feed national press collector were all
    collecting and unlisted -- two of them among the largest contributors of
    rows in the database. Understating coverage is not the safe direction it
    looks like: the page exists so a reader can judge what the tracker runs on.

    So it asserts the PROPERTY instead: the live set equals the set of
    registered collectors, derived from run_collect rather than restated here.
    Adding either half alone now fails.
    """
    live = {s.name for s in registry.SOURCES if s.status == "live"}
    collectors = set(_registered_collector_keys())
    mapped = {registry.COLLECTOR_BY_SOURCE_NAME.get(n) for n in live}

    unlisted = collectors - mapped - _DORMANT_COLLECTORS
    assert not unlisted, (
        f"collector(s) running with no entry on the sources page: {sorted(unlisted)}"
    )
    unbacked = {n for n in live
                if registry.COLLECTOR_BY_SOURCE_NAME.get(n) not in collectors}
    assert not unbacked, (
        f"source(s) listed live with no registered collector: {sorted(unbacked)}"
    )


# Shipped dormant on purpose, so it must NOT appear as a live source until a
# run has actually stored from it. See CLAUDE.md on the dormant-source pattern.
_DORMANT_COLLECTORS = {"tripwire_chase"}


def _registered_collector_keys():
    """The collectors run_collect actually knows about, read from the source."""
    import re
    text = (Path(__file__).parent.parent / "run_collect.py").read_text()
    block = re.search(r"SOURCES\s*=\s*\{(.*?)\n\}", text, re.S)
    assert block, "could not find the SOURCES registry in run_collect.py"
    return re.findall(r"['\"]([a-z_0-9]+)['\"]\s*:", block.group(1))


def test_the_job_board_source_publishes_what_it_cannot_do():
    """The one source here that is a measurement rather than a document, so the
    page has to carry its three limits: it cannot be back-filled, its counts are
    ours rather than the employer's, and a shrinking board is not a layoff."""
    boards = next(s for s in registry.SOURCES if "job boards" in s.name)
    assert boards.status == "live"
    assert "back-fill" in boards.notes
    assert "reported and never verified" in boards.notes
    assert "job cuts" in boards.notes


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
        # A discovery-backstop row has no feed and no API and is still
        # collected twice a day: it is a country search that runs and reports
        # its own health, not a name somebody typed into a spreadsheet. The
        # rule pinned here is "do not list what we cannot read", and these we
        # can read.
        and (r.get("feed_role") or "").strip().lower() != "backstop"
    }
    assert unreachable, "fixture check: the catalogue should still contain some"

    listed = {s["name"] for s in sources_manifest()}
    # Hand-written registry entries win on a name clash and are exempt: they
    # exist because a collector reads them.
    from source_registry import SOURCES
    hand = {s.name for s in SOURCES}
    assert not ((unreachable & listed) - hand)


def test_gdelt_is_live_because_it_finally_stored_something():
    """It was retired for producing zero records in its whole life, and
    un-retired hours later when its first run after six pipeline fixes stored
    three. A source that yields nothing is either a dead source or a broken
    pipeline, and the two look identical from outside."""
    from source_registry import SOURCES

    gdelt = next(s for s in SOURCES if s.name.startswith("GDELT"))
    assert gdelt.status == "live"
    # Its weakness is published rather than glossed. The note used to claim a
    # third of queries 429; measuring it on 2026-07-28 found 8 of 8 landing
    # once the retry ladder was allowed to do its job, so the note now says
    # "erratic" instead of a fraction nobody had counted. Assert the honesty
    # rather than the wording: the page must still say the throttling exists.
    assert "throttl" in gdelt.notes.lower()
    # And the reason it earns its place, which the note used not to mention:
    # it is the only news source we read that has an archive at all.
    assert "archive" in gdelt.notes.lower()
