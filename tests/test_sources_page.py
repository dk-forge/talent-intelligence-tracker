"""The sources page is generated, so it cannot drift from what actually runs."""

import json
import sqlite3
from pathlib import Path

import source_registry as registry

ROOT = Path(__file__).parent.parent
MANIFEST = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker" /
            "data" / "sources.json")
SOURCES_PHP = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker" /
               "includes" / "sources.php")
DB = ROOT / "data" / "talent_intel.db"

# Collectors that report health and are NOT sources, each with the reason it is
# not one. A source is a place documents come FROM; these four read nothing new:
#
#   archive_sources   captures a Wayback fallback for URLs we already cite
#   link_check        re-checks those same URLs and records whether they still live
#   recall            grades what we hold against a sealed gold set
#   sec_form_d_bulk   backfills a source that IS listed (SEC EDGAR Form D), so
#                     listing it again would double-count one source as two
#
# Listing any of them would inflate "running now" with work that produces no
# document, which is the exact overstatement this page exists to avoid. The set
# is asserted to be disjoint from the manifest below, so it cannot quietly become
# a place to hide a real source that nobody wants to write a row for.
_NOT_SOURCES = {
    "archive_sources": "archives URLs we already cite",
    "link_check": "re-checks URLs we already cite",
    "recall": "measures what we miss",
    # One row per measured population, for the same reason `recall` is here: it
    # grades what we hold and reads nothing new. Two rows and not one, because
    # they are separate reference sets with separate floors, and a single row
    # would let one population's staleness hide behind the other's freshness.
    "recall_us": "measures what we miss in the United States",
    "sec_form_d_bulk": "backfills SEC EDGAR Form D, which is listed",
    # It asks a model what we are missing and emits a WORK LIST. Every field it
    # returns is prefixed `claimed_` and dies there; a lead becomes a record
    # only when collectors/tripwire_chase.py finds the publisher's own article
    # and THAT goes through classify -> validate -> store. So the tripwire has
    # never been the source of a stored row and must never be named as one —
    # "a model is a discovery pointer too" is the same rule that keeps
    # commercial funding databases and Google News off this page. It first appeared here on
    # 2026-08-02, when the first non-dry run filed its first health row.
    "tripwire": "asks a model where to look; stores nothing itself",
    # The benchmark-diff chase (collectors/benchmark_chase.py, dormant until a
    # BENCHMARK_* secret is armed). An external reference list is a discovery
    # pointer exactly as a model or an aggregator is: it points at an
    # employer, the chase finds that employer's OWN article or filing, and the
    # stored source is the publisher or the registry. The pointer is never
    # cited, so it must never be named on this page, and the health row it
    # files once armed is bookkeeping about a chase, not a source of
    # documents.
    "benchmark_chase": "chases a reference-list diff; rows cite the publisher",
}


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
# benchmark_chase is doubly excused: dormant here, and in _NOT_SOURCES above
# because even armed it is a pointer-chaser whose rows cite the publisher.
_DORMANT_COLLECTORS = {"tripwire_chase", "benchmark_chase"}


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


# --- the join between a health row and a name on the page -------------------
#
# The page said "not yet reported" for three of the nine live collectors,
# including `national_press` (9,305 items found on its last run) and
# `uk_paygap` (4,761 of the United Kingdom's 4,793 rows). The cause was a
# five-entry map hand-typed in PHP beside a nine-entry one in Python. There is
# now one map, in the registry, and it rides into the plugin on each row of
# sources.json. These tests assert the property in both directions.

def test_every_live_source_carries_its_collector_in_the_manifest():
    """The page cannot join a source to its health row without this field."""
    for s in json.loads(MANIFEST.read_text()):
        assert "collector" in s, f"{s['name']}: no collector key at all"
        if s["status"] == "live":
            assert s["collector"], f"{s['name']} is live with no collector named"
        else:
            assert s["collector"] == "", (
                f"{s['name']} is {s['status']} and names collector "
                f"{s['collector']!r}; only a live source has one"
            )


def test_every_collector_that_is_a_source_resolves_to_a_name():
    """Read the health ledger the page actually renders from, not a fixture.

    Every collector that has ever reported health either resolves to a source
    name or is one of the four that is not a source. A new collector lands in
    neither bucket and fails here, which is the point: the alternative is it
    rendering as "not yet reported" for months while running twice a day.
    """
    if not DB.exists():
        return  # nothing to check against; the manifest tests still hold

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        reporting = {r[0] for r in conn.execute("SELECT collector FROM source_health")}
    assert reporting, "fixture check: the health ledger should not be empty"

    by_collector = {s["collector"]: s["name"]
                    for s in json.loads(MANIFEST.read_text())
                    if s["status"] == "live" and s["collector"]}

    unresolved = reporting - set(by_collector) - set(_NOT_SOURCES) - _DORMANT_COLLECTORS
    assert not unresolved, (
        "collector(s) reporting health that resolve to no name on the sources "
        f"page: {sorted(unresolved)}. Either add a Source() for it in "
        "source_registry.SOURCES and rerun build_sources_json.py, or add it to "
        "_NOT_SOURCES here WITH the reason it is not a source."
    )


def test_the_non_sources_stay_off_the_page():
    """_NOT_SOURCES must never overlap the manifest, or it becomes a hiding place."""
    listed = {s["collector"] for s in json.loads(MANIFEST.read_text()) if s["collector"]}
    overlap = listed & set(_NOT_SOURCES)
    assert not overlap, (
        f"{sorted(overlap)} is listed as a source AND excused as a non-source; "
        "one of the two is wrong"
    )


def test_the_php_derives_the_map_and_does_not_retype_it():
    """A second copy of this map is the defect, not the fix.

    Asserted as text because there is no php binary here. If the join ever moves
    back into a literal array of collector keys, this fails.
    """
    php = SOURCES_PHP.read_text()
    assert "tit_sources_collector_map" in php, (
        "the sources page no longer derives its collector map from the manifest"
    )
    assert "$s['collector']" in php, "the derived map is not reading the collector field"
    for collector in ("google_news", "sec_edgar", "national_press", "uk_paygap"):
        assert f"'{collector}'" not in php, (
            f"{collector} is hand-typed in sources.php again; the map is derived "
            "from data/sources.json, so a collector name has no business being "
            "written here"
        )


def test_no_em_dash_reaches_the_generated_sources_page():
    """The catalogue is an engineering log AND public copy, in one file.

    Em-dashes are fine in "13 of 15 candidate paths answer 403" and banned in
    anything rendered. Nothing distinguished the two, so on 2026-07-31 two of
    the thirteen dashes in data/sources_catalogue.csv were live on the sources
    page. This asserts the shipped artifact, not the catalogue, because the
    catalogue is allowed to keep its dashes in fields that never render.
    """
    import json
    from pathlib import Path

    out = (Path(__file__).parent.parent / "wordpress-plugin"
           / "talent-intelligence-tracker" / "data" / "sources.json")
    manifest = json.loads(out.read_text())
    offenders = [
        (s.get("name"), field)
        for s in manifest
        for field, value in s.items()
        if isinstance(value, str) and ("—" in value or "–" in value)
    ]
    assert not offenders, (
        "em or en dash in the shipped sources manifest: %r. Rewrite the text "
        "in data/sources_catalogue.csv and rerun build_sources_json.py"
        % (offenders,))


def test_the_builder_refuses_rather_than_substituting():
    """A silent repair would put words on a public page that nobody wrote.

    The build fails and names the field. It does not swap in a comma, because
    one of the two real offences wanted a full stop and the other wanted a
    comma, and guessing wrong is a sentence the author never approved.
    """
    import build_sources_json as builder

    clean = [{"name": "Fine", "notes": "a - b, and c"}]
    assert builder.dash_offences(clean) == []

    dirty = [{"name": "Presseportal", "notes": "a release portal — the "
                                               "German equivalent"}]
    offences = builder.dash_offences(dirty)
    assert len(offences) == 1
    label, field, fragment = offences[0]
    assert label == "Presseportal" and field == "notes"
    assert "—" in fragment, "the fragment must show the author the dash"


def test_every_collector_with_stored_rows_is_named_on_the_page():
    """The third direction: rows in the database from a path nobody listed.

    `test_live_sources_are_only_the_ones_with_collectors` derives its collector
    set from `run_collect.SOURCES`, so it can only see the DAILY collectors. A
    backfill script stores through its own name and never appears there, which
    is how `sec_form_d_bulk` came to hold 2,682 current rows while resolving to
    no source at all -- its rows say "SEC EDGAR (Form D)" and the map holds
    "SEC EDGAR Form D".

    Understating provenance is not the safe direction. The page exists so a
    reader can judge what the tracker runs on, and a reader cannot audit an
    ingest path that is not named. So this asserts against what is ACTUALLY
    STORED rather than what is registered.
    """
    import sqlite3
    from pathlib import Path as _Path

    db = _Path(__file__).parent.parent / "data" / "talent_intel.db"
    if not db.exists():  # a checkout without the database is not a failure
        import pytest
        pytest.skip("no local database to audit")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        stored = {r[0] for r in conn.execute(
            "select distinct collector from signals where is_current = 1")}
    finally:
        conn.close()

    live = {s.name for s in registry.SOURCES if s.status == "live"}
    named = {registry.COLLECTOR_BY_SOURCE_NAME.get(n) for n in live}

    unnamed = {c for c in stored
               if registry.resolve_collector(c) not in named} - _DORMANT_COLLECTORS
    assert not unnamed, (
        "collector(s) hold current rows but resolve to no live source on the "
        f"sources page: {sorted(unnamed)}. Either add the source, or -- if it "
        "reads the same publisher as an existing one -- add it to "
        "registry.COLLECTOR_ALIASES."
    )


def test_an_alias_must_point_at_a_real_live_source():
    """An alias that resolves to nothing would silence the check above."""
    live = {s.name for s in registry.SOURCES if s.status == "live"}
    named = {registry.COLLECTOR_BY_SOURCE_NAME.get(n) for n in live}
    for alias, target in registry.COLLECTOR_ALIASES.items():
        assert target in named, (
            f"COLLECTOR_ALIASES maps {alias!r} to {target!r}, which is not a "
            "live source on the page"
        )
        assert alias not in named, (
            f"{alias!r} is both an alias and a listed source; pick one"
        )
