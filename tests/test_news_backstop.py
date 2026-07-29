"""The discovery backstop for countries with no direct publisher feed.

Every test here runs offline against a fixture or an in-memory list. Nothing
touches the network, Google or the model.

The property under test throughout is the same one: Google News is a POINTER.
What ends up in the database is the publisher's own article, or nothing.
"""

import csv

import pytest
import requests

from collectors import national_press as press
from collectors import news_backstop as backstop

KUWAIT = backstop.Backstop(name="Kuwait (discovery backstop)",
                           country="Kuwait", iso2="KW", lang="en")

# Shaped like a real Google News RSS payload: the <link> is an encoded
# redirect, and <source url=...> is the publisher Google believes it came from.
RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Acme Kuwait appoints chief executive</title>
    <link>https://news.google.com/rss/articles/CBMiEXPLE?oc=5</link>
    <description>The Kuwaiti logistics group named a new CEO.</description>
    <pubDate>Tue, 28 Jul 2026 09:00:00 GMT</pubDate>
    <source url="https://www.kuwaittimes.com">Kuwait Times</source>
  </item>
  <item>
    <title>Kuwait fund raises $40m for regional startups</title>
    <link>https://news.google.com/rss/articles/CBMiOTHER?oc=5</link>
    <description>A seed funding round closed this week.</description>
    <pubDate>Tue, 28 Jul 2026 10:00:00 GMT</pubDate>
    <source url="https://www.arabtimesonline.com">Arab Times</source>
  </item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, body=b"", status=200):
        self.content, self.status_code = body, status
        # google_news.resolve_source_url reads .text off the article page.
        self.text = body.decode("utf8", "replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeSession:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        answer = self.answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else FakeResponse(b"", 404)


# --- The rule the whole module exists for ----------------------------------

@pytest.mark.parametrize("url", [
    # Resolution failed and Google's own URL is what is left on the item.
    "https://news.google.com/rss/articles/CBMiEXPLE",
    # The quieter half, and the one that would actually have shipped: when
    # resolution fails, what is left is the outlet HOME PAGE, which is neither
    # an aggregator nor obviously wrong and still proves nothing.
    "https://www.kuwaittimes.com",
    "https://www.kuwaittimes.com/",
])
def test_an_unresolved_pointer_is_never_storable(url):
    """Resolution is best effort, so "we resolve the redirect" is only true if
    the failure case is a drop. Storing either of these would leave a citation
    that does not contain the claim."""
    ok, why = backstop.storable({
        "source_url": url,
        "stated_publisher": "https://www.kuwaittimes.com",
    })
    assert not ok
    assert why


@pytest.mark.parametrize("url", [
    "https://news.google.com/rss/articles/CBMiEXPLE",
    "https://news.yahoo.com/some-story",
    "https://www.msn.com/en-us/news/story",
    "https://flipboard.com/topic/kuwait",
])
def test_no_aggregator_survives_the_gate(url):
    """One list of aggregators, shared with the direct-feed collector. A URL
    that is refused as a FEED cannot be acceptable as a SOURCE."""
    ok, _ = backstop.storable({"source_url": url})
    assert not ok


def test_a_resolved_publisher_url_is_storable():
    ok, why = backstop.storable({
        "source_url": "https://www.kuwaittimes.com/business/acme-names-ceo",
        "stated_publisher": "https://www.kuwaittimes.com",
    })
    assert ok, why


def test_domain_drift_is_refused_even_though_the_url_looks_fine():
    """The hazard the direct collector found the hard way, in the one place it
    is most likely: following a redirect. The feed said one publisher, the
    redirect landed on another, and nothing about the second URL looks wrong.
    An expired national daily now serving a betting site is exactly this."""
    ok, why = backstop.storable({
        "source_url": "https://luckybet-example.com/kuwait-ceo-news",
        "stated_publisher": "https://www.kuwaittimes.com",
    })
    assert not ok
    assert "redirect" in why


def test_a_subdomain_is_not_drift():
    """Publishers serve articles off subdomains and AMP hosts constantly. The
    guard compares the registrable domain, not the hostname, or it would refuse
    most of the real world."""
    ok, why = backstop.storable({
        "source_url": "https://business.kuwaittimes.com/acme-names-ceo",
        "stated_publisher": "https://www.kuwaittimes.com",
    })
    assert ok, why


def test_the_multi_label_suffix_table_covers_the_countries_the_backstop_reaches():
    """`guardian.co.tt` reduced to "co.tt" before Trinidad was in the suffix
    table, which made ANY two Trinidadian hosts compare equal — the drift guard
    passing for the wrong reason, which is worse than failing. Asserted over
    the property rather than the table: two different publishers on the same
    country suffix must not look like one domain."""
    assert (press.registrable_domain("https://www.guardian.co.tt/business/x")
            != press.registrable_domain("https://www.someoneelse.co.tt/y"))
    assert (press.registrable_domain("https://a.com.bb/x")
            != press.registrable_domain("https://b.com.bb/y"))


# --- Fetching --------------------------------------------------------------

def test_fetch_returns_pointers_that_are_not_yet_storable():
    """Deliberate: the collector hands back the raw pointer and resolution is a
    separate, budgeted step. A fetch that silently resolved would spend two
    HTTP round trips on every candidate before the free filter had a say."""
    session = FakeSession({KUWAIT.query_url: FakeResponse(RSS)})
    items = backstop.fetch(KUWAIT, session=session)
    assert len(items) == 2
    for item in items:
        assert "news.google.com" in item["discovery_url"]
        assert not backstop.storable(item)[0]


def test_every_item_carries_the_text_the_classifier_reads():
    """A collector that forgets raw_text posts zero records and says nothing
    about it. That bug cost the sibling weeks and it cannot be caught by
    looking at the run log."""
    session = FakeSession({KUWAIT.query_url: FakeResponse(RSS)})
    for item in backstop.fetch(KUWAIT, session=session):
        assert item["raw_text"].strip()
        assert item["headline"] in item["raw_text"]


def test_the_country_is_a_dateline_and_never_a_stored_field():
    """Same rule as the direct feeds. validate.py treats `country` as a sourced
    value, so writing the search term there would file a story about a US
    employer under Kuwait because that is what we happened to search for."""
    session = FakeSession({KUWAIT.query_url: FakeResponse(RSS)})
    items = backstop.fetch(KUWAIT, session=session)
    assert all("country" not in i for i in items)
    assert all(i["source_country"] == "Kuwait" for i in items)
    assert "Kuwait" in items[0]["raw_text"]


def test_the_query_leads_with_the_country():
    """Measured, not assumed: a phrase-led query against a thin edition falls
    back to the global index and answers a Barbados question with US stories.
    Asserting the property (the country is in the query and the window is
    bounded) rather than the exact phrasing, which is tuning."""
    query = KUWAIT.query
    assert query.index("Kuwait") < 5
    assert "when:" in query


def test_a_dead_query_is_reported_rather_than_read_as_a_quiet_fortnight():
    """A country with no rows and no complaint is the failure this collector
    exists to prevent, so an empty answer has to be its own status."""
    session = FakeSession({KUWAIT.query_url: requests.ConnectionError("boom")})
    items, health = backstop.collect([KUWAIT], session=session, pause=0)
    assert items == []
    assert health[0]["status"] == "dead"
    assert health[0]["detail"]


def test_an_empty_answer_is_not_ok():
    empty = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    session = FakeSession({KUWAIT.query_url: FakeResponse(empty)})
    _, health = backstop.collect([KUWAIT], session=session, pause=0)
    assert health[0]["status"] == "empty"


def test_health_records_say_which_countries_are_backstopped():
    """The sources page has to be able to tell a country with a publisher feed
    from a country with a search, and the ledger is where the run says so."""
    session = FakeSession({KUWAIT.query_url: FakeResponse(RSS)})
    _, health = backstop.collect([KUWAIT], session=session, pause=0, budget=0)
    assert health[0]["role"] == backstop.BACKSTOP_ROLE
    assert health[0]["country"] == "Kuwait"


def test_the_resolution_budget_bounds_a_run():
    """Resolution is two HTTP round trips per item and twenty-one countries can
    produce more pointers than a run should spend. Nothing is lost by
    deferring: the recency window overlaps and already-seen URLs are free."""
    items = [{"source_url": "https://news.google.com/rss/articles/A",
              "discovery_url": "https://news.google.com/rss/articles/A",
              "headline": "x"} for _ in range(10)]
    _, counts = backstop.resolve(items, budget=3,
                                 session=FakeSession({}))
    assert counts["over_budget"] == 7


# --- Catalogue wiring ------------------------------------------------------

def test_backstop_rows_are_loaded_from_the_catalogue(tmp_path):
    """The catalogue IS the configuration on this side too, so retiring a
    backstop is a CSV edit: fill in a verified feed, flip the role to direct,
    and the direct collector picks the country up with no Python change."""
    path = tmp_path / "catalogue.csv"
    fields = ["name", "url", "rss", "api", "country", "language", "feed_role"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"name": "Kuwait (discovery backstop)", "url": "",
                         "rss": "", "api": "", "country": "Kuwait",
                         "language": "English", "feed_role": "backstop"})
        writer.writerow({"name": "Globes", "url": "https://globes.co.il",
                         "rss": "https://globes.co.il/feed", "api": "",
                         "country": "Israel", "language": "Hebrew",
                         "feed_role": "direct"})

    loaded = backstop.load_backstops(path)
    assert [b.country for b in loaded] == ["Kuwait"]
    assert loaded[0].iso2 == "KW"


def test_a_country_the_vocabulary_cannot_produce_is_skipped(tmp_path):
    """Not a silent skip and not a crash: searching for a country whose rows
    validate.py will reject on normalisation is work with a guaranteed empty
    result, and it should say so rather than look like a quiet country."""
    path = tmp_path / "catalogue.csv"
    fields = ["name", "url", "rss", "api", "country", "language", "feed_role"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"name": "Nowhere (discovery backstop)", "url": "",
                         "rss": "", "api": "", "country": "Freedonia",
                         "language": "English", "feed_role": "backstop"})
    assert backstop.load_backstops(path) == []


def test_the_real_catalogue_backstops_all_normalise():
    """Every backstop country in the shipped catalogue has to be a country the
    vocabulary can produce, or its rows are collected and then thrown away at
    validation — cost with no coverage, and nothing says so."""
    from pipeline import vocab

    loaded = backstop.load_backstops()
    assert loaded, "the catalogue should carry backstop rows"
    for spot in loaded:
        assert vocab.normalize_country(spot.country) == spot.iso2


def test_backstop_rows_never_reach_the_direct_feed_collector():
    """They carry no `rss`, so the aggregator refusal in load_feeds() is not
    the thing keeping them out — nothing is asking it to. Worth pinning: a
    later edit that put a news.google.com URL in the rss column would be
    refused loudly by that guard, and this asserts the quieter half."""
    names = {f.name for f in press.load_feeds()}
    assert not any(n.endswith("(discovery backstop)") for n in names)


def test_the_direct_collector_does_not_reach_the_network_for_an_explicit_list():
    """`feeds=[...]` means "read exactly these". A backstop firing anyway would
    make the whole offline suite depend on Google being reachable."""
    session = FakeSession({})
    press.collect(feeds=[], session=session, pause=0, dry_run=True)
    assert session.calls == []
