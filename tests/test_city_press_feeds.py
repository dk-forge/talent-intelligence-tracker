"""The US city tech press set: what it reads, what it refuses, what it costs.

Every test here runs offline against a recorded fixture or the committed
catalogue. Nothing touches the network or the model.

WHY THIS SET EXISTS
-------------------
Worldwide discovery is edition-led and mostly national, so a metro funding
round or a 200 job expansion reaches this tracker only when a national desk
picks it up. The publishers that carry those with receipts are the metro
business press, and they serve ordinary first-party feeds.

WHAT IT MUST NEVER BECOME
-------------------------
A compiler. A publisher's weekly funding-roundup column is somebody else's
reporting under a masthead we trust, and one item names eight employers and
eight figures, so `company` can be at best one of them.
"""

import csv
from pathlib import Path
from urllib.parse import urlparse

import pytest

from collectors import national_press as press
from pipeline import classify, validate

FIXTURES = Path(__file__).parent / "fixtures"
METRO = (FIXTURES / "city_press_metro.xml").read_bytes()

NYBJ = press.Feed(
    name="New York Business Journal",
    rss="https://feeds.bizjournals.com/newyork",
    country="United States", city="New York", coverage="Local",
    language="English", source_type="News Organization",
    site="https://www.bizjournals.com/newyork",
    category=press.CITY_PRESS_CATEGORY)

#: The cities this set was wired to reach, and the publisher reaching each.
#: Named rather than counted, because "8 feeds" stays true while the city it
#: was wired for quietly loses its only feed.
WIRED_CITIES = {
    "Seattle": "GeekWire",
    "Miami": "Refresh Miami",
    "Boston": "Boston Business Journal",
    "New York": "New York Business Journal",
    "San Francisco": "San Francisco Business Times",
    "Los Angeles": "L.A. Business First",
    "Chicago": "Chicago Business Journal",
    "Austin": "Austin Business Journal",
}


def _catalogue_rows():
    with press.CATALOGUE_CSV.open(newline="") as fh:
        return list(csv.DictReader(fh))


# --- The shape survives to a storable candidate ----------------------------

def test_the_metro_feed_shape_parses_into_candidates_with_a_receipt():
    """The bizjournals shape: CDATA titles, a <guid> repeating the link, an
    RFC-822 pubDate. A homepage is not a receipt, so every item must carry the
    publisher's own ARTICLE url."""
    items = press.parse(METRO, NYBJ)
    assert len(items) == 2, "the third fixture item has no link and is dropped"
    first = items[0]
    assert first["source_url"] == (
        "https://www.bizjournals.com/newyork/news/2026/08/13/"
        "withcoverage-expected-to-create-200-jobs.html")
    assert urlparse(first["source_url"]).path.strip("/"), "a bare domain is not an article"
    assert first["collector"] == "national_press"
    assert first["published_date"].startswith("Thu, 13 Aug 2026")


def test_raw_text_carries_the_figures_the_gate_and_the_extractor_need():
    """The classifier reads ONLY raw_text. A collector that forgets it posts
    zero records and reports no error. The teaser is also where the jobs number
    lives when the headline rounds it."""
    item = press.parse(METRO, NYBJ)[0]
    assert item["raw_text"]
    assert "WithCoverage" in item["raw_text"]
    assert "205 jobs" in item["raw_text"], "the teaser figure must survive the trim"
    assert "$25 million" in item["raw_text"]
    assert "<" not in item["raw_text"] and "&lt;" not in item["raw_text"]


def test_the_metro_dateline_is_context_and_never_a_sourced_country():
    """Same rule as every other feed: the publisher's seat is a hint for the
    model, never `raw["country"]`, which validate.py would take as sourced."""
    item = press.parse(METRO, NYBJ)[0]
    assert "New York" in item["raw_text"] and "United States" in item["raw_text"]
    assert "country" not in item
    assert item["source_country"] == "United States"


def test_a_metro_item_reaches_a_storable_signal():
    """End of the funnel, offline: a classified metro item must build a Signal
    with its receipt intact. If this breaks, the feeds are fetching for
    nothing, which is exactly the silent-zero failure the house rules name."""
    item = press.parse(METRO, NYBJ)[0]
    classified = {
        "company": "WithCoverage", "pillar": "how_we_work",
        "signal_direction": "hiring",
        "headline": "WithCoverage to create 205 jobs in New York",
        "summary": "WithCoverage will create 205 jobs and invest $25 million.",
        "talent_readthrough": "205 roles in the New York metro.",
        "confidence": "reported", "city": "New York",
    }
    signal = validate.build_signal(classified, item, "national_press")
    assert signal.source_url.startswith("https://www.bizjournals.com/newyork/news/")
    assert signal.published_date == "2026-08-13"
    assert signal.company


def test_the_gate_payload_stays_small_because_it_is_charged_per_token():
    """This set adds 150 items a run and the gate is billed per token, so the
    payload size IS the price. Priced at 400 teaser characters; a regression
    here is a bill, not a style question."""
    for item in press.parse(METRO, NYBJ):
        assert len(item["raw_text"]) < 700


# --- Request-URL guard -----------------------------------------------------

def test_every_wired_city_feed_requests_exactly_the_url_we_catalogued():
    """A collector that constructs a URL can construct a wrong one. These are
    read verbatim out of the catalogue, https, on the publisher's own host,
    and with no query string of ours bolted on."""
    for feed in (f for f in press.load_feeds() if f.is_city_press):
        assert feed.rss.startswith("https://"), feed.name
        parsed = urlparse(feed.rss)
        assert not parsed.query, f"{feed.name} carries a query string we invented"
        assert press.registrable_domain(feed.rss) in feed.expected_domains, (
            f"{feed.name} would fetch from a domain the catalogue does not name")
        assert press.registrable_domain(feed.rss) not in press._AGGREGATOR_DOMAINS


def test_a_hijacked_city_domain_is_refused_before_a_row_is_stored():
    """The documented hazard for any catalogue: a domain expires and somebody
    else serves a perfectly green feed from it."""
    press._ROBOTS_CACHE.clear()

    class Resp:
        def __init__(self, body=METRO, status=200,
                     url="https://metro-deals-blog.example/feed"):
            self.content, self.status_code, self.url = body, status, url
            self.text = body.decode()

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            # robots.txt answers 404 (no restriction); the feed answers from a
            # host nobody catalogued.
            if url.endswith("/robots.txt"):
                return Resp(b"", 404, url)
            return Resp()

    items = press.collect(feeds=[NYBJ], session=Session(), pause=0, dry_run=True)
    assert items == []
    assert press.FEED_HEALTH[0]["status"] == "hijacked"


# --- The aggregator rule, one level down at the path -----------------------

def test_a_funding_roundup_feed_is_refused_even_from_an_outlet_we_read(tmp_path):
    """The rule is about who did the reporting. A roundup compiles other
    people's, and one item names many employers, so whatever comes back as
    `company` is at best one of them attached to another one's figure."""
    path = tmp_path / "cat.csv"
    fields = ["name", "url", "rss", "country", "city", "coverage", "category",
              "language", "source_type"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerow({"name": "Metro Daily funding roundup",
                         "rss": "https://metro.example/funding-roundup/feed",
                         "country": "United States", "category": "City Tech Press"})
        writer.writerow({"name": "Metro Daily deal tracker",
                         "rss": "https://metro.example/deal-tracker/rss",
                         "country": "United States", "category": "City Tech Press"})
        writer.writerow({"name": "Metro Daily",
                         "rss": "https://metro.example/feed",
                         "country": "United States", "category": "City Tech Press"})
    assert [f.name for f in press.load_feeds(path)] == ["Metro Daily"]


@pytest.mark.parametrize("url,refused", [
    ("https://metro.example/funding-roundup/feed", True),
    ("https://metro.example/tech/weekly-funding.xml", True),
    ("https://metro.example/deals-roundup/rss", True),
    ("https://metro.example/feed", False),
    ("https://metro.example/news/rss", False),
])
def test_the_roundup_test_is_about_the_path_not_the_word_funding(url, refused):
    """`/funding/` alone is an ordinary section and must stay readable: the
    refusal is for the roundup PRODUCT, not for the topic."""
    assert press.is_roundup_feed(url) is refused


def test_the_shipped_catalogue_wires_no_roundup_feed():
    for row in _catalogue_rows():
        rss = (row.get("rss") or "").strip()
        if rss.startswith("http"):
            assert not press.is_roundup_feed(rss), row["name"]


# --- The set is switchable, and cannot starve the registers ----------------

def test_the_whole_set_switches_off_before_a_single_request(monkeypatch):
    monkeypatch.setenv("TIT_CITY_PRESS", "off")
    assert not press.city_press_enabled()
    feeds = press.load_feeds()
    assert not [f for f in feeds if f.is_city_press]
    # And nothing else went with it.
    assert len(feeds) > 500


def test_the_set_is_on_by_default_because_it_priced_inside_its_ceiling(monkeypatch):
    monkeypatch.delenv("TIT_CITY_PRESS", raising=False)
    assert press.city_press_enabled()
    assert [f for f in press.load_feeds() if f.is_city_press]


def test_adding_feeds_buys_no_extra_reads_so_the_registers_cannot_starve():
    """The structural guarantee, not a promise in a comment.

    Reads are the expensive stage. `classify.BINDING_READ_BUDGET` is a fixed
    total split between collectors by measured conversion, so national_press
    buys exactly the cap it bought before this set was wired, and the
    structured registers are separate collectors this cannot reach at all.
    """
    caps = classify.COLLECTOR_READ_CAPS
    assert sum(caps.values()) == classify.BINDING_READ_BUDGET
    assert set(caps) == {"national_press", "google_news"}, (
        "a new collector in the split would move money away from the others")
    for register in ("companies_house", "spain_borme", "czechia_ares",
                     "estonia_ariregister", "bse_india", "edinet_japan",
                     "opendart_korea"):
        assert register not in caps, (
            f"{register} must not draw on the news read budget")


def test_one_metro_cannot_crowd_out_the_others():
    """The per-feed cap is what bounds the worst case this set was priced at."""
    padded = METRO.replace(
        b"</channel>",
        b"".join(b"<item><title>Filler %d</title>"
                 b"<link>https://www.bizjournals.com/newyork/news/x%d.html</link>"
                 b"</item>" % (n, n) for n in range(60)) + b"</channel>")
    assert len(press.parse(padded, NYBJ)) == press.MAX_ITEMS_PER_FEED


# --- The catalogue is the configuration ------------------------------------

def test_every_wired_city_is_reached_by_a_named_publisher():
    """A count stays true while the city it was wired for loses its feed."""
    live = {f.city: f.name for f in press.load_feeds() if f.is_city_press}
    for city, publisher in WIRED_CITIES.items():
        assert live.get(city) == publisher, (
            f"{city} is no longer reached by {publisher}")


def test_each_wired_city_feed_records_what_it_returned():
    """'A feed that returns nothing is degraded, not coverage', so the count
    and the age of the newest item are written down at the moment of wiring
    rather than inferred later from a ledger that holds only the last run."""
    for row in _catalogue_rows():
        if (row.get("category") or "").strip() != press.CITY_PRESS_CATEGORY:
            continue
        checked = (row.get("feed_checked") or "").strip()
        assert "items" in checked and "newest" in checked, (
            f"{row['name']} was wired without recording what it returned: {checked!r}")
        assert (row.get("feed_role") or "").strip() == "direct"
        assert (row.get("feed_kind") or "").strip() == "rss"


def test_the_publishers_we_probed_and_refused_are_written_down_with_the_reason():
    """A refusal with evidence is finished work; silence makes the next session
    probe the same fifteen paths. Built In publishes no feed on any path, and
    BostInno's own feed is gone."""
    rows = {r["name"]: r for r in _catalogue_rows()}
    for name in ("Built In", "BostInno", "Silicon Allee"):
        row = rows[name]
        assert not (row.get("rss") or "").strip(), f"{name} is wired again"
        checked = (row.get("feed_checked") or "").lower()
        assert "404" in checked or "dead" in checked or "no feed" in checked, (
            f"{name} carries no probe verdict: {checked!r}")


def test_no_competitor_database_can_reach_this_set():
    """Standalone-brand rule. The named databases must not appear in the tree
    and must be refused if a catalogue edit ever names one."""
    for row in _catalogue_rows():
        rss = (row.get("rss") or "").strip()
        if not rss.startswith("http"):
            continue
        host = (urlparse(rss).hostname or "").lower()
        assert host not in press._AGGREGATOR_HOSTS, row["name"]
        assert press.registrable_domain(host) not in press._AGGREGATOR_DOMAINS, row["name"]
