"""The catalogue-driven press collector.

Every test here runs offline against a recorded fixture or an in-memory feed
list. Nothing in this file touches the network or the model.
"""

import base64
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

from collectors import national_press as press
from pipeline import validate

FIXTURES = Path(__file__).parent / "fixtures"


# The fixtures are recorded documents, so the dates the publishers served are
# fixed in the past. Several checks below assert a feed reads LIVE, and "live"
# is a distance from TODAY rather than a date — so those dates are re-stamped
# relative to now when this module loads.
#
# They were pinned to 14/15 Jul 2026 until 2026-08-30, when the newer of the two
# crossed STALE_AFTER_DAYS (45) and three tests went red on main for no reason
# but the calendar. A pinned date in a freshness assertion is not a test, it is
# a countdown: it says nothing about the collector on any day but the one the
# fixture was recorded. Age each date from `now` and it holds for ever.
def _rfc822(days_ago: float, *, now=None) -> bytes:
    """The date format RSS serves, `days_ago` before `now` (default: this moment)."""
    stamp = (now or datetime.now(timezone.utc)) - timedelta(days=days_ago)
    return stamp.strftime("%a, %d %b %Y %H:%M:%S GMT").encode()


def _iso(days_ago: float, *, now=None) -> bytes:
    """The date format Atom serves, `days_ago` before `now` (default: this moment)."""
    stamp = (now or datetime.now(timezone.utc)) - timedelta(days=days_ago)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S+00:00").encode()


# What the recorded files say, and what this module reads them as.
_RECORDED_OLDER = b"Mon, 14 Jul 2026 08:30:00 GMT"
_RECORDED_NEWEST = b"Tue, 15 Jul 2026 09:00:00 GMT"
_RECORDED_ATOM = b"2026-07-20T09:30:00+01:00"

OLDER_PUBDATE = _rfc822(3)     # the first item
NEWEST_PUBDATE = _rfc822(2)    # the second, and the one staleness is judged on

RSS = ((FIXTURES / "national_press_rss.xml").read_bytes()
       .replace(_RECORDED_OLDER, OLDER_PUBDATE)
       .replace(_RECORDED_NEWEST, NEWEST_PUBDATE))
# Read by exactly two tests: one parses the recorded document and asserts the
# date it carries, so it keeps the file byte for byte; the other needs a date
# measured from a clock and re-stamps it itself.
ATOM = (FIXTURES / "national_press_atom.xml").read_bytes()


# Re-stamping keeps the fixture live, which is necessary and is not the whole
# job: it makes the ARGUMENT hold on any day, while every health check still
# asks the wall clock what day it is. Since 2026-08-30 `collect()` takes `now`,
# the seam every other window-based collector here already had
# (`singapore_acra.collect(today=)`, `companies_house`, `bse_india`,
# `ats_boards.trajectory(today=)`), so a staleness verdict can be stated as
# arithmetic between two fixed instants instead. That is the stronger claim:
# not "this reads live today" but "this reads live, full stop".
#
# The instant is arbitrary — the day the countdown ran out, for the record. What
# matters is that the fixture's dates are stamped from the SAME instant the
# collector is handed.
PINNED_NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def _rss_aged(older_days: float, newest_days: float, *, now=None) -> bytes:
    """The recorded RSS with its two item dates aged that many days before `now`."""
    return (RSS.replace(OLDER_PUBDATE, _rfc822(older_days, now=now))
               .replace(NEWEST_PUBDATE, _rfc822(newest_days, now=now)))


# The fixture as a live feed against the pinned clock: the two item dates three
# and two days old, the spacing the recording carried, fixed for ever.
LIVE_RSS = _rss_aged(3, 2, now=PINNED_NOW)

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
        b"<pubDate>" + OLDER_PUBDATE + b"</pubDate>",
        b"<dc:publishDate>2026-07-14</dc:publishDate>")
    assert press.parse(dc, GLOBES)[0]["published_date"] == "2026-07-14"


def test_an_item_with_no_date_anywhere_is_never_stamped_with_the_fetch_time():
    """Nikkei Asia, Sixth Tone, the Kathmandu Post and the Maldives Financial
    Review carry no item-level date at all. Stamping those with the collection
    time would file a month-old article as today's news and quietly corrupt
    every period column — a wrong date is worse than no date."""
    undated = RSS.replace(b"<pubDate>" + OLDER_PUBDATE + b"</pubDate>", b"")
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
        # The aggregator's domain is a banned plaintext string (standalone-
        # brand rule), so the fixture decodes it at runtime, exactly like the
        # blocklist itself does.
        {"name": "An aggregator", "country": "Netherlands",
         "rss": "https://" + base64.b64decode("ZGVhbHJvb20uY28=").decode("ascii") + "/feed"},
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


def test_the_feed_this_suite_calls_live_is_live_today_not_on_its_recording_day():
    """The defect this stands on, in one line: on 2026-08-30 three tests below
    went red because the recorded fixture's newest item turned 46 days old
    against a 45-day limit. Nothing about the collector had changed, and the
    healer that woke up to it would have been reasoning about the wrong thing.

    A calendar date inside a freshness assertion is a countdown, not a test. If
    this ever fails, do NOT raise STALE_AFTER_DAYS and do NOT re-record the
    fixture with a later date — both buy the same silence again. Age the
    fixture's dates from a clock, which is what _rfc822 at the top of this file
    is for.

    Stated twice, because the health checks below come in two kinds. The
    stronger one first: LIVE_RSS is stamped from PINNED_NOW and read against
    PINNED_NOW, two fixed instants, so this arithmetic is the same on every day
    there will ever be."""
    age = press.newest_item_age_days(press.parse(LIVE_RSS, GLOBES), now=PINNED_NOW)
    assert age is not None, "a fixture with no date cannot stand in for a live feed"
    assert age < press.STALE_AFTER_DAYS, (
        f"the pinned RSS fixture reads {age}d old against a "
        f"{press.STALE_AFTER_DAYS}d limit, so every check below that expects "
        "this feed to report 'ok' is wrong about its own arithmetic")
    assert age < press.STALE_AFTER_DAYS_AGENCY

    # And the module-level RSS, which the tests that do NOT pin a clock still
    # read, is live against the wall clock. That is the re-stamping helper's
    # claim, and it is the one that went red on 2026-08-30 when the dates were
    # calendar literals instead.
    today = press.newest_item_age_days(press.parse(RSS, GLOBES))
    assert today is not None and today < press.STALE_AFTER_DAYS, (
        f"the re-stamped RSS fixture reads {today}d old against a "
        f"{press.STALE_AFTER_DAYS}d limit — it is a countdown again")


def test_a_dead_feed_is_named_rather_than_hidden_in_the_aggregate(tmp_path, capsys):
    """The failure this collector is most exposed to: one feed among a hundred
    goes dark, the run still returns hundreds of items and still reports ok, and
    nothing ever says that Israel stopped three weeks ago."""
    press.HEALTH_PATH = tmp_path / "health.json"
    dead = press.Feed(name="Techpoint Africa", rss="https://techpoint.africa/feed/",
                      country="Nigeria", city="Lagos", coverage="National",
                      language="English", source_type="Technology News")
    session = FakeSession({
        GLOBES.rss: FakeResponse(LIVE_RSS),
        dead.rss: FakeResponse(b"", 403),
    })
    items = press.collect(feeds=[GLOBES, dead], session=session, pause=0,
                          dry_run=True, now=PINNED_NOW)

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
    old = _rss_aged(651, 650, now=PINNED_NOW)
    session = FakeSession({GLOBES.rss: FakeResponse(old)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True,
                  now=PINNED_NOW)
    record = press.FEED_HEALTH[0]
    assert record["status"] == "stale"
    assert "stopped" in record["detail"]


def test_the_staleness_verdict_is_read_from_the_clock_the_caller_hands_in():
    """The seam itself, stated as the only thing a seam is for: the same bytes,
    two clocks, two verdicts.

    This could not be written before 2026-08-30. `newest_item_age_days` took an
    injectable clock and `collect` called it without one, so the only way to
    move a staleness verdict was to move the fixture — and a fixture aged from
    today is a countdown, which is precisely how three checks here went red on
    main with nothing wrong but the calendar. Neither assertion below knows what
    day it is."""
    press.collect(feeds=[GLOBES], pause=0, dry_run=True, now=PINNED_NOW,
                  session=FakeSession({GLOBES.rss: FakeResponse(LIVE_RSS)}))
    record = press.FEED_HEALTH[0]
    assert record["status"] == "ok" and record["newest_days"] == 2

    later = PINNED_NOW + timedelta(days=press.STALE_AFTER_DAYS + 1)
    press.collect(feeds=[GLOBES], pause=0, dry_run=True, now=later,
                  session=FakeSession({GLOBES.rss: FakeResponse(LIVE_RSS)}))
    record = press.FEED_HEALTH[0]
    assert record["status"] == "stale"
    assert record["newest_days"] == press.STALE_AFTER_DAYS + 3


def test_the_limit_is_a_boundary_with_a_different_verdict_on_each_side():
    """A day either side of each limit, in literal days.

    The literals are the point, and the first draft of this test did not have
    them: it derived its ages from STALE_AFTER_DAYS, so widening the limit moved
    the test along with it and the suite stayed green. That is the same silence
    the fixture fix refused to buy, bought a different way. These numbers are the
    promise — a national daily silent for 45 days is quiet, at 46 the publisher
    has stopped, and an agency gets 150 because an agency announcing a programme
    twice a year is not a broken source. Change either limit and this fails,
    which is the intention."""
    daily = _rss_aged(1, 0, now=PINNED_NOW)   # newest item dated PINNED_NOW itself
    agency = press.Feed(name="Innovate UK", rss="https://www.gov.uk/x.atom",
                        country="United Kingdom", city="Swindon", coverage="National",
                        language="English", source_type="Government Agency")
    agency_body = ATOM.replace(_RECORDED_ATOM, _iso(0, now=PINNED_NOW))

    for feed, body, age, expected in (
            (GLOBES, daily, 44, "ok"),
            (GLOBES, daily, 45, "ok"),
            (GLOBES, daily, 46, "stale"),
            (agency, agency_body, 46, "ok"),
            (agency, agency_body, 150, "ok"),
            (agency, agency_body, 151, "stale")):
        press.collect(feeds=[feed], pause=0, dry_run=True,
                      now=PINNED_NOW + timedelta(days=age),
                      session=FakeSession({feed.rss: FakeResponse(body)}))
        record = press.FEED_HEALTH[0]
        assert record["newest_days"] == age
        assert record["status"] == expected, (
            f"{feed.name} at {age}d read {record['status']}, wanted {expected}")


def test_a_quarterly_agency_feed_is_not_called_stale():
    """The sibling's health digest learned this one: a source that publishes
    twice a year is not a broken source. A national daily silent for six weeks
    is; an innovation agency announcing a programme is not."""
    agency = press.Feed(name="Innovate UK", rss="https://www.gov.uk/x.atom",
                        country="United Kingdom", city="Swindon", coverage="National",
                        language="English", source_type="Government Agency")
    # Older than a daily's limit (45d) and well inside an agency's (150d), and
    # it must stay between the two however long from now this runs.
    aged = ATOM.replace(_RECORDED_ATOM, _iso(100, now=PINNED_NOW))
    session = FakeSession({agency.rss: FakeResponse(aged)})
    press.collect(feeds=[agency], session=session, pause=0, dry_run=True,
                  now=PINNED_NOW)
    assert press.FEED_HEALTH[0]["status"] == "ok"

    # The same gap on a daily newspaper is not fine.
    press.collect(feeds=[GLOBES], pause=0, dry_run=True, now=PINNED_NOW,
                  session=FakeSession({
                      GLOBES.rss: FakeResponse(_rss_aged(101, 100, now=PINNED_NOW))}))
    assert press.FEED_HEALTH[0]["status"] == "stale"


def test_an_undated_feed_is_not_guessed_at():
    """No date is not the same as an old date, and treating it as one would
    retire a working feed on no evidence."""
    undated = RSS.replace(b"<pubDate>" + OLDER_PUBDATE + b"</pubDate>", b"") \
                 .replace(b"<pubDate>" + NEWEST_PUBDATE + b"</pubDate>", b"")
    session = FakeSession({GLOBES.rss: FakeResponse(undated)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True,
                  now=PINNED_NOW)
    assert press.FEED_HEALTH[0]["status"] == "ok"
    assert press.newest_item_age_days(
        press.parse(undated, GLOBES), now=PINNED_NOW) is None


def test_a_thin_teaser_feed_is_not_recorded_as_a_broken_one():
    """Paywalled publishers serve headline-and-teaser feeds, so fewer of their
    items survive the gate. The ledger counts items separately from new ones so
    a low yield never reads as a dead feed."""
    session = FakeSession({GLOBES.rss: FakeResponse(LIVE_RSS)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True,
                  now=PINNED_NOW)
    record = press.FEED_HEALTH[0]
    assert record["status"] == "ok"
    assert record["items"] == 2 and record["new"] == 2


def test_a_dry_run_does_not_overwrite_the_ledger(tmp_path):
    """A rehearsal that rewrote the ledger would report the rehearsal as the
    live picture."""
    press.HEALTH_PATH = tmp_path / "health.json"
    session = FakeSession({GLOBES.rss: FakeResponse(LIVE_RSS)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True,
                  now=PINNED_NOW)
    assert not press.HEALTH_PATH.exists()

    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=False,
                  now=PINNED_NOW)
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


def test_a_feed_with_two_xml_declarations_still_parses():
    """IO+ / Innovation Origins serves `<?xml ...?><?xml version="1.0"?>` back
    to back and a strict parse dies at byte 38, so a healthy Dutch publisher
    with 20 current items reads as dead. Same class as the Maddyness trailing
    junk, at the LEADING edge, which the tail trim does not touch."""
    doubled = b'<?xml version="1.0" encoding="UTF-8"?>' + RSS
    assert len(press.parse(doubled, GLOBES)) == 2


def test_leading_junk_before_the_first_tag_is_trimmed():
    """A stray blank line or PHP notice before the document is not XML and
    never was."""
    assert len(press.parse(b"\n\n  Notice: something\n" + RSS, GLOBES)) == 2


def test_a_single_declaration_is_left_alone():
    """The trim must not eat the ordinary case it is standing next to."""
    assert press._tidy(RSS).startswith(b"<?xml")


# --- Earned cadence --------------------------------------------------------

def test_a_feed_that_has_produced_nothing_for_weeks_drops_to_a_probe(tmp_path):
    """With 481 feeds wired, most of a run is spent on publishers producing
    nothing we keep. Dead weight should cost nothing — not budget, and not much
    free compute either."""
    press.HEALTH_PATH = tmp_path / "health.json"
    quiet = {"name": "Globes", "quiet_runs": press.QUIET_RUNS_BEFORE_PROBE + 3,
             "country": "Israel", "url": GLOBES.rss, "status": "ok",
             "items": 0, "new": 0, "detail": ""}
    press.HEALTH_PATH.write_text(json.dumps({"run_number": 1, "by_feed": [quiet]}))

    session = FakeSession({GLOBES.rss: FakeResponse(RSS)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)

    rested = press.FEED_HEALTH[0]["status"] == "resting"
    if rested:
        assert GLOBES.rss not in session.calls, "a resting feed must not be fetched"
    else:
        # It was its turn to probe. A probe is a real fetch AND a real health
        # report, which is the point: a feed that went quiet because it BROKE
        # still has to be detected.
        assert GLOBES.rss in session.calls


def test_a_probe_still_happens_so_a_broken_quiet_feed_is_still_found():
    """Resting must never mean forgotten. Across a full probe cycle every quiet
    feed is fetched at least once."""
    previous = {"Globes": {"name": "Globes", "quiet_runs": 99}}
    assert any(press.due_this_run("Globes", previous, n)
               for n in range(press.PROBE_EVERY))


def test_a_feed_that_yields_is_polled_every_run():
    previous = {"Globes": {"name": "Globes", "quiet_runs": 0}}
    assert all(press.due_this_run("Globes", previous, n) for n in range(20))


def test_a_feed_we_have_never_seen_is_always_polled():
    assert press.due_this_run("Brand New Outlet", {}, 7)


def test_yield_history_resets_the_moment_a_feed_produces_something(tmp_path):
    press.HEALTH_PATH = tmp_path / "health.json"
    press.HEALTH_PATH.write_text(json.dumps(
        {"run_number": 1,
         "by_feed": [{"name": "Globes", "quiet_runs": 5, "country": "Israel",
                      "url": GLOBES.rss, "status": "ok", "items": 0, "new": 0,
                      "detail": ""}]}))
    session = FakeSession({GLOBES.rss: FakeResponse(RSS)})
    press.collect(feeds=[GLOBES], session=session, pause=0, dry_run=True)
    assert press.FEED_HEALTH[0]["quiet_runs"] == 0


def test_the_gate_payload_stays_small_because_it_is_charged_per_token():
    """The gate runs on EVERY candidate and is billed by the token, so this is
    the main lever on the bill once hundreds of feeds are wired. It is
    deliberately not smaller than this: the teaser is where a funding figure
    sits when the headline omits it."""
    item = press.parse(RSS, GLOBES)[0]
    assert len(item["raw_text"]) < 700, "the gate payload has grown"
    # And the figure still survives the trim, which is the whole point.
    assert "$71m" in item["raw_text"]


# --- Domain drift ----------------------------------------------------------

class FakeRedirected(FakeResponse):
    def __init__(self, body, final_url, status=200):
        super().__init__(body, status)
        self.url = final_url


def test_a_feed_that_now_answers_from_another_domain_is_refused():
    """botswanaguardian.co.bw redirects to a BETTING SITE whose /feed/ verifies
    perfectly green — 200, well-formed RSS, recent items. Every automated check
    passes and we would be citing a gambling operator as a Botswana news
    source, under our own name.

    Status codes cannot catch this and neither can freshness. The only signal
    is that the bytes came from somewhere other than the publisher we listed.
    """
    press._ROBOTS_CACHE.clear()
    feed = press.Feed(name="Botswana Guardian",
                      rss="https://www.botswanaguardian.co.bw/feed/",
                      country="Botswana", city="Gaborone", coverage="National",
                      language="English", source_type="News Organization",
                      site="https://www.botswanaguardian.co.bw")
    session = FakeSession({feed.rss: FakeRedirected(RSS, "https://bettingbotswana.com/feed/")})
    items = press.collect(feeds=[feed], session=session, pause=0, dry_run=True)

    assert items == [], "not one row may be stored from a hijacked domain"
    assert press.FEED_HEALTH[0]["status"] == "hijacked"
    assert "bettingbotswana.com" in press.FEED_HEALTH[0]["detail"]


def test_an_ordinary_redirect_inside_the_same_domain_is_fine():
    """http->https and www->bare are redirects too, and refusing those would
    retire most of the catalogue."""
    press._ROBOTS_CACHE.clear()
    feed = press.Feed(name="Globes", rss="https://en.globes.co.il/feed",
                      country="Israel", city="Tel Aviv", coverage="National",
                      language="English", source_type="News Organization",
                      site="https://en.globes.co.il")
    session = FakeSession({feed.rss: FakeRedirected(RSS, "https://www.globes.co.il/feed/")})
    assert len(press.collect(feeds=[feed], session=session, pause=0, dry_run=True)) == 2


def test_the_registrable_domain_is_not_fooled_by_a_two_part_suffix():
    """Without this, every .co.bw site compares equal to every other and the
    guard silently protects nothing at all."""
    assert press.registrable_domain("https://www.botswanaguardian.co.bw/feed/") == "botswanaguardian.co.bw"
    assert press.registrable_domain("https://bettingbotswana.com/x") == "bettingbotswana.com"
    assert press.registrable_domain("https://en.globes.co.il") == "globes.co.il"
    assert press.registrable_domain("https://sub.example.com") == "example.com"


def test_the_shipped_catalogue_no_longer_wires_the_hijacked_domain():
    with press.CATALOGUE_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            assert "botswanaguardian" not in (row.get("rss") or ""), (
                "the hijacked Botswana domain is wired again")


# --- Last-resort parsing ---------------------------------------------------

def test_a_feed_too_malformed_to_parse_is_read_with_a_regex():
    """Six live feeds are malformed past repair (Times of Oman, Daily News
    Egypt, African Manager, Sika Finance, Condia, New Era). Strict parsing
    reports every one as dead, so Oman would look sourceless while having a
    perfectly good publisher."""
    hopeless = (b"<rss><channel><item><title>Acme raises $5m</title>"
                b"<link>https://ex.example/acme</link>"
                b"<description>Acme raised $5m and will hire.</description>"
                b"</item><item><title>Unclosed" + b"\x00\x01" + b"</channel></rss>")
    items = press.parse(hopeless, GLOBES)
    assert len(items) == 1
    assert items[0]["source_url"] == "https://ex.example/acme"
    assert items[0]["parsed_by"] == "regex-fallback"
    # It still goes through the same funnel, so raw_text and the dateline hold.
    assert "$5m" in items[0]["raw_text"] and "Israel" in items[0]["raw_text"]


def test_the_regex_reader_is_never_used_on_a_feed_that_parses():
    """A regex is a worse reader than a parser in every way except tolerating
    invalid input, so it must stay the last resort."""
    assert "parsed_by" not in press.parse(RSS, GLOBES)[0]


def test_only_an_encoding_we_can_decode_is_advertised():
    """Advertising brotli without a decoder makes a healthy feed read as
    corrupt."""
    accept_encoding = press._headers(press._ACCEPT_RSS)["Accept-Encoding"]
    if "br" in accept_encoding.split(", "):
        assert press._HAVE_BROTLI


def test_an_aggregator_is_blocked_on_every_subdomain():
    """One record ended up citing an aggregator's news subdomain because the
    blocklist named exact hosts: it listed the bare and www hosts, and a third
    subdomain walked straight through a set that mentions the company twice.
    Every other entry had the same hole. Matching what someone OWNS closes it
    for names already listed and for any name added later.

    Three of the probed hosts belong to providers whose names are banned in
    plaintext (standalone-brand rule), so their apexes decode at runtime from
    the same base64 form the blocklist uses."""
    from collectors.national_press import _AGGREGATOR_DOMAINS, registrable_domain

    encoded_apexes = ("Y3J1bmNoYmFzZS5jb20=", "ZGVhbHJvb20uY28=", "dHJhY3huLmNvbQ==")
    news, blog, data = (base64.b64decode(s).decode("ascii") for s in encoded_apexes)
    for host in (f"news.{news}", f"blog.{blog}", f"data.{data}",
                 "app.magnitt.com", "www.startupnationcentral.org"):
        assert registrable_domain(host) in _AGGREGATOR_DOMAINS, host

    # Publishers we legitimately read must stay readable.
    for host in ("www.geektime.co.il", "globes.co.il", "techcrunch.com",
                 "www.geekwire.com"):
        assert registrable_domain(host) not in _AGGREGATOR_DOMAINS, host


# --- The two formats a "200 but no parseable items" verdict was hiding -------
#
# Both were found by re-probing all 662 wired feeds on 2026-08-02. Both looked
# identical in the ledger to a publisher that had gone away, and neither
# publisher had gone anywhere.

RSS10 = b"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/"
         xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
 <channel rdf:about="https://asia.example/">
  <title>Asia Business Daily</title>
  <link>https://asia.example/</link>
 </channel>
 <item rdf:about="https://asia.example/enigma">
  <title>Enigma raises $71m in seed funding round</title>
  <link>https://asia.example/enigma</link>
  <description>The round will fund 40 hires in Tel Aviv.</description>
  <dc:date>2026-08-02T12:00:00+09:00</dc:date>
 </item>
</rdf:RDF>
"""

# Drupal core's RSS. The title is an ANCHOR, not text.
DRUPAL_RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xml:base="https://star.example/">
 <channel>
  <title>The Daily Star</title>
  <link>https://star.example/</link>
  <item>
   <title><a href="/business/enigma" hreflang="en">Enigma raises $71m in seed funding round</a></title>
   <link>https://star.example/business/enigma</link>
   <description>The round will fund 40 hires.</description>
   <pubDate>Sun, 02 Aug 26 00:00:00 +0600</pubDate>
  </item>
 </channel>
</rss>
"""


def test_an_rss_1_0_feed_is_read_rather_than_recorded_as_empty():
    """Nikkei Asia, CNET Japan, Nikkei xTECH, Impress Watch, PR TIMES and the
    Taipei Times all serve RDF, whose <item> is namespaced. `.//item` matches
    unqualified names only, so all six answered 200, parsed cleanly and
    yielded nothing, which the ledger records as "200 but no parseable items"
    -- the wording for a publisher that has stopped."""
    items = press.parse(RSS10, GLOBES)
    assert len(items) == 1
    assert items[0]["headline"] == "Enigma raises $71m in seed funding round"
    assert items[0]["source_url"] == "https://asia.example/enigma"
    assert "40 hires" in items[0]["raw_text"]


def test_a_title_wrapped_in_a_link_is_still_a_title():
    """Drupal writes `<title><a href=...>Headline</a></title>`. Reading only an
    element's own text made every item titleless, and an item with no title is
    dropped, so The Daily Star's business desk published 25 stories a day into
    a row recorded as dead."""
    items = press.parse(DRUPAL_RSS, GLOBES)
    assert len(items) == 1
    assert items[0]["headline"] == "Enigma raises $71m in seed funding round"
    assert items[0]["source_url"] == "https://star.example/business/enigma"


def test_the_dates_those_two_formats_carry_are_read_as_dates():
    """Staleness is how a feed that dies later gets noticed, and an item whose
    date will not parse is an item that can never make its feed look stale. So
    recovering the items without recovering their dates would have swapped one
    silent failure for a quieter one."""
    assert validate._normalize_date(
        press.parse(RSS10, GLOBES)[0]["published_date"]) == "2026-08-02"
    assert validate._normalize_date(
        press.parse(DRUPAL_RSS, GLOBES)[0]["published_date"]) == "2026-08-02"


def test_the_anchored_date_match_still_refuses_what_it_was_written_for():
    """The anchor stops a date being pulled out of arbitrary text. Loosening
    `\\b` to a lookahead so an ISO instant parses must not loosen that."""
    assert validate._normalize_date("2026-08-021") is None
    assert validate._normalize_date("2026-08-02-03") is None
    assert validate._normalize_date("see https://x.example/2021/07/a") is None


def test_the_ordinary_rss_2_0_path_is_unchanged_by_either_repair():
    """Both repairs sit in the reader every feed goes through, so the format
    that 600-odd of them actually serve has to come out identical."""
    items = press.parse(RSS, GLOBES)
    assert len(items) == 2
    assert items[0]["headline"] == "Enigma raises $71m in seed funding round"
    assert "parsed_by" not in items[0]


def test_the_feeds_withdrawn_for_robots_stay_withdrawn():
    """The Cayman Compass row already SAID robots.txt disallowed its feed, in
    the notes, while the rss column stayed populated -- so the collector went
    on fetching it twice a day against the publisher's terms. A note is not a
    withdrawal; an empty rss column is."""
    withdrawn = {"Cayman Compass", "Monitor"}
    with press.CATALOGUE_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row["name"] in withdrawn:
                assert not row["rss"].strip(), (
                    f"{row['name']} is wired and its publisher disallows it")
