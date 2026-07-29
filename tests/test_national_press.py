"""The catalogue-driven press collector.

Every test here runs offline against a recorded fixture or an in-memory feed
list. Nothing in this file touches the network or the model.
"""

import csv
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

from collectors import national_press as press
from pipeline import validate

FIXTURES = Path(__file__).parent / "fixtures"
RSS = (FIXTURES / "national_press_rss.xml").read_bytes()
ATOM = (FIXTURES / "national_press_atom.xml").read_bytes()

GLOBES = press.Feed(name="Globes", rss="https://en.globes.co.il/feed",
                    country="Israel", city="Tel Aviv", coverage="National",
                    language="English", source_type="News Organization")
RECURSIVE = press.Feed(name="The Recursive", rss="https://www.therecursive.com/rss/",
                       country="Bulgaria", city="Sofia", coverage="Regional",
                       language="English", source_type="Technology News")


class FakeResponse:
    def __init__(self, body=b"", status=200):
        self.content, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeSession:
    """Answers by feed URL. Never reaches a network."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        answer = self.answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else FakeResponse(b"", 404)


# --- Parsing ---------------------------------------------------------------

def test_parse_pulls_the_publishers_own_article_url():
    """A homepage is not a receipt. The feed's <link> is the article itself,
    which is the whole reason a publisher feed beats an aggregator."""
    items = press.parse(RSS, GLOBES)
    assert [i["headline"] for i in items] == [
        "Enigma raises $71m in seed funding round", "Glow raises $180m Series B"]
    first = items[0]
    assert first["source_url"].startswith("https://en.globes.co.il/en/article-enigma")
    assert urlparse(first["source_url"]).path.strip("/"), "a bare domain is not an article"
    assert first["source_name"] == "Globes"
    assert first["collector"] == "national_press"


def test_raw_text_carries_the_story_because_an_empty_one_stores_nothing():
    """The classifier reads ONLY raw_text. A collector that forgets it posts
    zero records and reports no error — the bug that cost the sibling weeks."""
    item = press.parse(RSS, GLOBES)[0]
    assert item["raw_text"]
    assert "Enigma" in item["raw_text"]
    assert "$71m" in item["raw_text"]
    # The HTML the feed wrapped it in is gone; the words are not.
    assert "<b>" not in item["raw_text"] and "&lt;" not in item["raw_text"]
    assert "hire" in item["raw_text"]


def test_an_item_without_a_link_is_dropped_not_stored_bare():
    assert len(press.parse(RSS, GLOBES)) == 2  # the third fixture item has no link


def test_atom_feeds_read_the_link_out_of_the_href_attribute():
    """Atom puts the URL in an attribute rather than element text, so the RSS
    reader returns an empty link and every gov.uk item would be discarded."""
    items = press.parse(ATOM, press.Feed(
        name="Innovate UK", rss="https://www.gov.uk/x.atom", country="United Kingdom",
        city="Swindon", coverage="National", language="English",
        source_type="Government Agency"))
    assert len(items) == 1
    assert items[0]["source_url"] == "https://www.gov.uk/government/news/multi-million-pound-backing"
    assert items[0]["published_date"].startswith("2026-07-20")


def test_a_feed_with_junk_after_the_closing_tag_still_parses():
    """Maddyness appends a WordPress debug notice AFTER </rss>. A strict parse
    raises 'junk after document element', which reads as a dead feed — France
    lost to a trailing newline."""
    assert press.parse(RSS + b"\n<!-- Notice: undefined index -->\n", GLOBES)


def test_a_feed_with_unescaped_ampersands_is_repaired_rather_than_lost():
    """Diario Libre's front page carries 191 bare `&` inside tag URLs and dies
    on the first; its economy feed is clean. Without this the outlet looks
    half-broken for a reason that has nothing to do with the outlet.

    The repair runs only AFTER a strict parse has failed, so a valid feed is
    read exactly as served.
    """
    broken = RSS.replace(
        b"<link>https://en.globes.co.il/en/article-enigma-raises-71m-1001500001</link>",
        b"<link>https://en.globes.co.il/en/article?a=1&b=2&tag=m&a-del-caribe</link>")
    with pytest.raises(Exception):
        __import__("xml.etree.ElementTree", fromlist=["x"]).fromstring(broken)
    items = press.parse(broken, GLOBES)
    assert len(items) == 2
    assert "&b=2" in items[0]["source_url"]


def test_a_relative_item_link_is_resolved_against_the_publisher():
    """B2B Cambodia emits bare slugs in <link>. Storing one verbatim gives a
    source_url that links to nothing, and every figure here is supposed to link
    to the document that makes the claim."""
    relative = RSS.replace(
        b"https://en.globes.co.il/en/article-enigma-raises-71m-1001500001",
        b"/en/article-enigma-raises-71m-1001500001")
    item = press.parse(relative, GLOBES)[0]
    assert item["source_url"] == "https://en.globes.co.il/en/article-enigma-raises-71m-1001500001"


def test_the_dates_publishers_actually_use_are_all_read():
    """KED Global uses dc:publishDate and Digital Business KZ uses
    news:publication_date. A pubDate-only reader calls both dateless, and a
    misdated row lands in the wrong period column rather than nowhere."""
    dc = RSS.replace(
        b"<rss version=\"2.0\"",
        b"<rss version=\"2.0\" xmlns:dc=\"http://purl.org/dc/elements/1.1/\"").replace(
        b"<pubDate>Mon, 14 Jul 2026 08:30:00 GMT</pubDate>",
        b"<dc:publishDate>2026-07-14</dc:publishDate>")
    assert press.parse(dc, GLOBES)[0]["published_date"] == "2026-07-14"


def test_an_item_with_no_date_anywhere_is_never_stamped_with_the_fetch_time():
    """Nikkei Asia, Sixth Tone, the Kathmandu Post and the Maldives Financial
    Review carry no item-level date at all. Stamping those with the collection
    time would file a month-old article as today's news and quietly corrupt
    every period column — a wrong date is worse than no date."""
    undated = RSS.replace(b"<pubDate>Mon, 14 Jul 2026 08:30:00 GMT</pubDate>", b"")
    item = press.parse(undated, GLOBES)[0]
    assert item["published_date"] == ""

    classified = {
        "company": "Enigma", "pillar": "company_development",
        "signal_direction": "hiring", "headline": "Enigma raises $71m",
        "summary": "Enigma raised $71m.", "talent_readthrough": "Hiring capacity.",
    }
    signal = validate.build_signal(classified, item, "national_press")
    assert signal.published_date is None
    assert signal.captured_at, "captured_at is when WE saw it, and stays separate"


def test_no_more_than_the_per_feed_cap_is_taken():
    """TechNode's feed carries 2,000 entries. Without a cap one archive-heavy
    publisher fills the whole candidate budget and starves the other hundred."""
    body = RSS.replace(
        b"</channel>",
        b"".join(b"<item><title>Filler %d</title><link>https://x.example/%d</link></item>"
                 % (n, n) for n in range(60)) + b"</channel>")
    assert len(press.parse(body, GLOBES)) == press.MAX_ITEMS_PER_FEED


# --- Geography is context, never a sourced fact ----------------------------

def test_the_publisher_country_is_a_dateline_and_never_a_sourced_country():
    """The Enigma miss in one test.

    The outlet's country is the best hint we have for a headline that places
    nowhere, and it is passed the way gdelt.py passes sourcecountry: inside
    raw_text, for the model to weigh. Writing it to raw['country'] instead would
    make validate.py treat it as sourced, and an Israeli paper reporting a US
    round would file the US job under Israel.
    """
    item = press.parse(RSS, GLOBES)[0]
    assert "Israel" in item["raw_text"]
    assert "Tel Aviv" in item["raw_text"]
    assert "country" not in item, "the publisher's country must not be a sourced field"
    assert item["source_country"] == "Israel"  # carried for reporting only


def test_validate_does_not_take_the_dateline_as_a_location():
    """Belt and braces: even holding the raw dict, the pipeline must not place
    a signal from the dateline alone."""
    classified = {
        "company": "Enigma", "pillar": "company_development",
        "signal_direction": "hiring", "headline": "Enigma raises $71m",
        "summary": "Enigma raised $71m.", "talent_readthrough": "Hiring capacity.",
        "confidence": "reported",
    }
    signal = validate.build_signal(classified, press.parse(RSS, GLOBES)[0], "national_press")
    assert signal.country is None, "a dateline must never become a stored country"


def test_a_regional_publisher_does_not_claim_its_home_country():
    """The Recursive is in Sofia and writes about Romania as often as Bulgaria.
    Naming Bulgaria in its dateline is the same error one step removed."""
    line = press.dateline(RECURSIVE)
    assert "Bulgaria" not in line and "Sofia" not in line
    assert "regional" in line.lower()


# --- Feed loading ----------------------------------------------------------

def test_load_feeds_reads_the_catalogue_and_skips_rows_without_one(tmp_path):
    path = tmp_path / "cat.csv"
    _write_catalogue(path, [
        {"name": "Globes", "rss": "https://en.globes.co.il/feed", "country": "Israel"},
        {"name": "CTech", "rss": "", "country": "Israel"},
    ])
    feeds = press.load_feeds(path)
    assert [f.name for f in feeds] == ["Globes"]


def test_an_aggregator_feed_is_refused_at_load_time(tmp_path):
    """Aggregators are discovery pointers, never stored sources. validate.py
    blocks them at storage; refusing them here means a careless CSV edit cannot
    even queue one up."""
    path = tmp_path / "cat.csv"
    _write_catalogue(path, [
        {"name": "Dealroom", "rss": "https://dealroom.co/feed", "country": "Netherlands"},
        {"name": "Google News", "rss": "https://news.google.com/rss", "country": "United States"},
        {"name": "Globes", "rss": "https://en.globes.co.il/feed", "country": "Israel"},
    ])
    assert [f.name for f in press.load_feeds(path)] == ["Globes"]


def test_the_real_catalogue_holds_no_aggregator_feed():
    """The product rule, asserted against the file that actually ships."""
    with press.CATALOGUE_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            rss = (row.get("rss") or "").strip()
            if not rss.startswith("http"):
                continue
            assert rss.startswith("https://"), f"{row['name']} feed is not https"
            host = (urlparse(rss).hostname or "").lower()
            assert host not in press._AGGREGATOR_HOSTS, f"{row['name']} is an aggregator"


# --- Per-feed health -------------------------------------------------------

def test_a_dead_feed_is_named_rather_than_hidden_in_the_aggregate(tmp_path, capsys):
    """The failure this collector is most exposed to: one feed among a hundred
    goes dark, the run still returns hundreds of items and still reports ok, and
    nothing ever says that Israel stopped three weeks ago."""
    press.HEALTH_PATH = tmp_path / "health.json"
    dead = press.Feed(name="Techpoint Africa", rss="https://techpoint.africa/feed/",
                      country="Nigeria", city="Lagos", coverage="National",
                      language="English", source_type="Technology News")
    session = FakeSession({
        GLOBES.rss: FakeResponse(RSS),
        dead.rss: FakeResponse(b"", 403),
    })
    items = press.collect(feeds=[GLOBES, dead], session=session, pause=0, dry_run=True)

    assert len(items) == 2
    by_name = {r["name"]: r for r in press.FEED_HEALTH}
    assert by_name["Globes"]["status"] == "ok" and by_name["Globes"]["items"] == 2
    assert by_name["Techpoint Africa"]["status"] == "dead"
    assert "403" in by_name["Techpoint Africa"]["detail"]

    out = capsys.readouterr().out
    assert "Techpoint Africa" in out and "needing attention" in out


def test_a_feed_answering_200_with_nothing_is_empty_not_ok():
    """A 200 carrying an HTML error page is the breakage that hides best,
    because the request succeeded."""
    feed = press.Feed(name="WRAL", rss="https://wraltechwire.com/feed/",
                      country="United States", city="Raleigh", coverage="Regional",
                      language="English", source_type="Technology News")
    session = FakeSession({feed.rss: FakeResponse(b"<!doctype html><html></html>")})
    press.collect(feeds=[feed], session=session, pause=0, dry_run=True)
    assert press.FEED_HEALTH[0]["status"] == "empty"


def test_a_feed_that_answers_but_stopped_publishing_is_stale_not_ok():
    """The quieter half of a dead feed, and a real one: NoCamels answers 200,
    parses cleanly, hands over 25 items — and its newest entry is from October
    2024. Any check that only asks "did it respond" calls that healthy, and
    Israel keeps a source that has published nothing in 21 months."""
    old = RSS.replace(b"Mon, 14 Jul 2026 08:30:00 GMT", b"Mon, 14 Oct 2024 08:30:00 GMT") \
             .replace(b"Tue, 15 Jul 2026 09:00:00 GMT", b"Tue, 15 Oct 2024 09:00:00 GMT")
    session = FakeSession({GLOBES.rss: FakeResponse(old)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)
    record = press.FEED_HEALTH[0]
    assert record["status"] == "stale"
    assert "stopped" in record["detail"]


def test_a_quarterly_agency_feed_is_not_called_stale():
    """The sibling's health digest learned this one: a source that publishes
    twice a year is not a broken source. A national daily silent for six weeks
    is; an innovation agency announcing a programme is not."""
    agency = press.Feed(name="Innovate UK", rss="https://www.gov.uk/x.atom",
                        country="United Kingdom", city="Swindon", coverage="National",
                        language="English", source_type="Government Agency")
    aged = ATOM.replace(b"2026-07-20T09:30:00+01:00", b"2026-04-20T09:30:00+01:00")
    session = FakeSession({agency.rss: FakeResponse(aged)})
    press.collect(feeds=[agency], session=session, pause=0, dry_run=True)
    assert press.FEED_HEALTH[0]["status"] == "ok"

    # The same gap on a daily newspaper is not fine.
    press.collect(feeds=[GLOBES], pause=0, dry_run=True, session=FakeSession({
        GLOBES.rss: FakeResponse(RSS.replace(b"Mon, 14 Jul 2026", b"Mon, 14 Jan 2026")
                                    .replace(b"Tue, 15 Jul 2026", b"Tue, 15 Jan 2026"))}))
    assert press.FEED_HEALTH[0]["status"] == "stale"


def test_an_undated_feed_is_not_guessed_at():
    """No date is not the same as an old date, and treating it as one would
    retire a working feed on no evidence."""
    undated = RSS.replace(b"<pubDate>Mon, 14 Jul 2026 08:30:00 GMT</pubDate>", b"") \
                 .replace(b"<pubDate>Tue, 15 Jul 2026 09:00:00 GMT</pubDate>", b"")
    session = FakeSession({GLOBES.rss: FakeResponse(undated)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)
    assert press.FEED_HEALTH[0]["status"] == "ok"
    assert press.newest_item_age_days(press.parse(undated, GLOBES)) is None


def test_a_thin_teaser_feed_is_not_recorded_as_a_broken_one():
    """Paywalled publishers serve headline-and-teaser feeds, so fewer of their
    items survive the gate. The ledger counts items separately from new ones so
    a low yield never reads as a dead feed."""
    session = FakeSession({GLOBES.rss: FakeResponse(RSS)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)
    record = press.FEED_HEALTH[0]
    assert record["status"] == "ok"
    assert record["items"] == 2 and record["new"] == 2


def test_a_dry_run_does_not_overwrite_the_ledger(tmp_path):
    """A rehearsal that rewrote the ledger would report the rehearsal as the
    live picture."""
    press.HEALTH_PATH = tmp_path / "health.json"
    session = FakeSession({GLOBES.rss: FakeResponse(RSS)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)
    assert not press.HEALTH_PATH.exists()

    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=False)
    written = json.loads(press.HEALTH_PATH.read_text())
    assert written["live"] == 1 and written["by_feed"][0]["name"] == "Globes"


def test_syndicated_copies_are_dropped_before_anything_is_paid_for():
    """One wire item through eight national outlets is eight URLs and one
    story. Finding that out after the classifier has read all eight is the
    expensive way (gdelt learned it at 34 copies of one release)."""
    other = press.Feed(name="Mirror Daily", rss="https://mirror.example/feed",
                       country="Ireland", city="Dublin", coverage="National",
                       language="English", source_type="News Organization")
    copy = RSS.replace(b"https://en.globes.co.il/en/article-enigma-raises-71m-1001500001",
                       b"https://mirror.example/enigma-raises-71m")
    session = FakeSession({GLOBES.rss: FakeResponse(RSS), other.rss: FakeResponse(copy)})
    items = press.collect(feeds=[GLOBES, other], session=session, pause=0, dry_run=True)
    # The re-hosted copy is caught by its title, the untouched one by its URL.
    # Both are free; both would otherwise have been paid for.
    assert press.STATS["syndicated"] == 1
    assert press.STATS["duplicate_url"] == 1
    assert len(items) == 2


# --- Registration ----------------------------------------------------------

def test_the_collector_is_registered_and_told_about_dry_runs():
    import run_collect

    assert run_collect.SOURCES["national_press"] is press
    # It keeps state between runs, so it must be told when it is rehearsing.
    assert press.ACCEPTS_DRY_RUN is True


def test_the_catalogue_reaches_the_countries_the_recall_test_named():
    """Israel is the proven miss. The others are the priority markets that had
    no reachable publication at all before this."""
    feeds = press.load_feeds()
    countries = {f.country for f in feeds}
    for country in ("Israel", "India", "Nigeria", "South Africa", "Brazil",
                    "France", "Germany", "Singapore", "Indonesia", "Mexico",
                    "Poland", "Sweden", "Canada", "Australia"):
        assert country in countries, f"no feed reaches {country}"


def _write_catalogue(path: Path, rows: list[dict]) -> None:
    fields = ["name", "url", "rss", "api", "country", "state", "city", "coverage",
              "category", "industry", "source_type", "signals", "language", "free",
              "notes"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})


def test_the_portable_feed_export_is_in_sync_with_the_catalogue():
    """data/feeds.csv is a generated artifact for OTHER products to consume —
    the sibling layoff tracker most of all, since the outlets that report a
    funding round also report a redundancy programme. Run
    build_feeds_export.py if this fails; never hand-edit the CSV."""
    import build_feeds_export

    with (Path(__file__).parent.parent / "data" / "feeds.csv").open(newline="") as fh:
        assert list(csv.DictReader(fh)) == build_feeds_export.rows()


def test_the_export_carries_no_aggregator_and_no_bare_domain():
    """It leaves this repo, so the product rule has to hold in the artifact and
    not merely in the collector that produced it."""
    import build_feeds_export

    for row in build_feeds_export.rows():
        host = (urlparse(row["feed_url"]).hostname or "").lower()
        assert host not in press._AGGREGATOR_HOSTS, row["publisher"]
        assert row["feed_url"].startswith("https://"), row["publisher"]
        assert row["publisher"] and row["country"], row


# --- robots.txt ------------------------------------------------------------

def _robots(body: str):
    """A session that serves one robots.txt and nothing else."""
    return FakeSession({"https://en.globes.co.il/robots.txt":
                        FakeResponseText(body), GLOBES.rss: FakeResponse(RSS)})


class FakeResponseText(FakeResponse):
    def __init__(self, text, status=200):
        super().__init__(text.encode(), status)
        self.text = text


def test_a_feed_its_publisher_disallows_is_not_fetched():
    """robots.txt is the publisher stating their terms. Routing around it is
    how a product whose only asset is credibility loses it.

    The first audit over 112 feeds found eight disallowed, three of which had
    been in the catalogue since before this collector existed.
    """
    press._ROBOTS_CACHE.clear()
    session = _robots("User-agent: *\nDisallow: /feed\n")
    items = press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)

    assert items == []
    assert press.FEED_HEALTH[0]["status"] == "robots"
    assert GLOBES.rss not in session.calls, "the feed must not be fetched at all"


def test_an_allowed_feed_is_fetched_normally():
    press._ROBOTS_CACHE.clear()
    session = _robots("User-agent: *\nDisallow: /wp-admin/\n")
    assert len(press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)) == 2


def test_a_missing_robots_txt_means_no_restriction_not_a_block():
    """No robots.txt is the standard 'no restriction'. Failing closed here
    would silently retire every publisher that does not publish one."""
    press._ROBOTS_CACHE.clear()
    session = FakeSession({GLOBES.rss: FakeResponse(RSS)})  # robots 404s
    assert len(press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)) == 2


def test_robots_is_fetched_once_per_host_not_once_per_feed():
    """A hundred feeds on a hundred hosts is a hundred robots fetches; several
    feeds on one host must still be one."""
    press._ROBOTS_CACHE.clear()
    second = press.Feed(name="Globes Tech", rss="https://en.globes.co.il/tech-feed",
                        country="Israel", city="Tel Aviv", coverage="National",
                        language="English", source_type="News Organization")
    session = FakeSession({
        "https://en.globes.co.il/robots.txt": FakeResponseText("User-agent: *\nAllow: /\n"),
        GLOBES.rss: FakeResponse(RSS), second.rss: FakeResponse(RSS)})
    press.collect(feeds=[GLOBES, second], session=session, pause=0, dry_run=True)
    assert session.calls.count("https://en.globes.co.il/robots.txt") == 1


def test_the_shipped_catalogue_carries_no_feed_its_publisher_disallows():
    """Asserted against the file that ships, so a re-added feed fails here
    rather than in production."""
    withdrawn = {"Tech in Asia", "Tech in Asia Indonesia", "Tech.eu", "UKTN",
                 "Finextra", "Webrazzi", "The Times of Israel",
                 "MaRS Discovery District"}
    with press.CATALOGUE_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row["name"] in withdrawn:
                assert not row["rss"].strip(), (
                    f"{row['name']} was withdrawn for robots.txt and is back")
