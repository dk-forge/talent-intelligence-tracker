"""US executive-appointment wire discovery.

Every test runs offline against a fixture or an in-memory list. Nothing touches
the network, Google or the model.

The properties under test:
  * Google News is a POINTER — what is stored is the wire's own release, or
    nothing (the discovery-backstop rule, applied to the US private-company
    leadership gap).
  * No request is ever built against a blocked wire's OWN feed path. We reach
    the releases through the index only.
  * A non-appointment headline is dropped by the free prefilter before any
    resolution round trip (and, in production, before the paid gate).
  * The source is DORMANT by default and spends nothing itself.
"""

from urllib.parse import urlparse

import pytest
import requests

from collectors import google_news
from collectors import us_exec_wire as wire
from collectors.national_press import registrable_domain

# A Google News RSS payload: <link> is an encoded redirect and <source url=...>
# is the publisher Google believes it came from. One appointment (a private-
# company wire release) and one non-appointment (must not survive the gate).
RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Acme Robotics Names Jane Roe as Chief Executive Officer</title>
    <link>https://news.google.com/rss/articles/CBMiAPPOINT?oc=5</link>
    <description>The privately held robotics firm appointed a new CEO.</description>
    <pubDate>Tue, 26 Aug 2026 09:00:00 GMT</pubDate>
    <source url="https://www.prnewswire.com">PR Newswire</source>
  </item>
  <item>
    <title>Acme Robotics unveils new warehouse product line</title>
    <link>https://news.google.com/rss/articles/CBMiPRODUCT?oc=5</link>
    <description>A product launch, no leadership change of any kind.</description>
    <pubDate>Tue, 26 Aug 2026 10:00:00 GMT</pubDate>
    <source url="https://www.businesswire.com">Business Wire</source>
  </item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, body=b"", status=200):
        # No `raw` attribute on purpose: capped_fetch.read_capped falls back to
        # .content, which is the stubbed-response path.
        self.content, self.status_code = body, status
        self.text = body.decode("utf8", "replace")
        self.url = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def close(self):
        pass


class FakeSession:
    """Records every URL it is asked for, so a test can prove which hosts were
    (and were not) reached."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        answer = self.answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else FakeResponse(b"", 404)

    def post(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(b"", 200)


def _query_urls(window_days=wire.WINDOW_DAYS):
    return [google_news.build_query_url(q, lang=wire.LANG, country=wire.COUNTRY)
            for q in wire.registry.us_exec_wire_queries(window_days=window_days)]


# --- The rule the whole module exists for ----------------------------------

def test_a_resolved_wire_release_url_is_storable():
    """The whole point: a full-path wire release URL is a legitimate citation
    (the database already cites prnewswire.com). It is stored, never fetched."""
    ok, why = wire.storable({
        "source_url": "https://www.prnewswire.com/news-releases/acme-names-ceo.html",
        "stated_publisher": "https://www.prnewswire.com",
    })
    assert ok, why


@pytest.mark.parametrize("url", [
    "https://news.google.com/rss/articles/CBMiAPPOINT",
    "https://www.prnewswire.com",   # bare homepage, resolution failed
    "https://www.prnewswire.com/",
])
def test_an_unresolved_pointer_is_never_storable(url):
    ok, why = wire.storable({
        "source_url": url,
        "stated_publisher": "https://www.prnewswire.com",
    })
    assert not ok
    assert why


def test_domain_drift_is_refused():
    ok, why = wire.storable({
        "source_url": "https://luckybet-example.com/acme-ceo",
        "stated_publisher": "https://www.prnewswire.com",
    })
    assert not ok
    assert "redirected" in why


# --- Fetching from the index -----------------------------------------------

def test_fetch_returns_pointers_that_are_not_yet_storable():
    answers = {u: FakeResponse(RSS) for u in _query_urls()}
    session = FakeSession(answers)
    items = wire.fetch(session=session, pause=0)
    assert items, "the index payload should yield candidates"
    for item in items:
        assert "news.google.com" in item["discovery_url"]
        assert not wire.storable(item)[0]


def test_every_item_carries_the_text_the_classifier_reads():
    """A collector that forgets raw_text posts zero records silently."""
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    for item in wire.fetch(session=session, pause=0):
        assert item["raw_text"].strip()
        assert item["headline"] in item["raw_text"]


def test_the_country_is_a_dateline_and_never_a_stored_field():
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    items = wire.fetch(session=session, pause=0)
    assert all("country" not in i for i in items)
    assert all(i["source_country"] == "United States" for i in items)


# --- Robots / ToS: never a request against a wire's own feed ----------------

def test_no_request_is_ever_built_against_a_blocked_wire_feed():
    """The compliance core. Business Wire's terms are unreadable (403 on its own
    robots.txt), GlobeNewswire disallows its RSS, PR Newswire publishes no feed.
    This collector must reach their releases ONLY through the Google News index
    and Google's resolution endpoint — never their own hosts. A resolution that
    fails leaves the homepage and the item is dropped; either way no wire host
    is ever contacted."""
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    wire.collect(dry_run=True, session=session, pause=0)
    assert session.calls, "the run should have contacted the index"
    for url in session.calls:
        host = (urlparse(url).hostname or "").lower()
        assert not wire.targets_blocked_wire_feed(url), (
            f"built a request against a blocked wire host: {url}")
        # Every request is Google's own — the index or the resolver.
        assert host.endswith("google.com"), f"unexpected host contacted: {url}"


def test_targets_blocked_wire_feed_flags_each_wire():
    for host in ("https://www.businesswire.com/feed",
                 "https://feeds.prnewswire.com/x",
                 "https://www.globenewswire.com/rss/news"):
        assert wire.targets_blocked_wire_feed(host)
    # A wire ARTICLE url is still a wire host, so this helper flags it too. That
    # is correct and not a contradiction: the helper answers "is this a wire
    # host" (used only to prove no REQUEST targets one), while the storable rule
    # is what allows a release URL as a stored CITATION. We cite but never fetch.
    assert wire.targets_blocked_wire_feed(
        "https://www.prnewswire.com/news-releases/acme-ceo.html")
    assert not wire.targets_blocked_wire_feed("https://www.reuters.com/x")


# --- Cost: the free prefilter drops noise before resolution -----------------

def test_a_non_appointment_headline_is_dropped_before_resolution(monkeypatch):
    """The product-launch item must not reach the resolver (or, in production,
    the paid gate). Resolution is monkeypatched to a wire release so any item
    that DID reach it would resolve and store — proving the drop is the gate's,
    not an accident of a failed resolution."""
    def fake_resolve(item, **kwargs):
        item["source_url"] = ("https://www.prnewswire.com/news-releases/"
                              "acme-names-ceo.html")
        return item

    monkeypatch.setattr(google_news, "resolve_source_url", fake_resolve)
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    stored = wire.collect(dry_run=True, session=session, pause=0)
    headlines = " ".join(i["headline"] for i in stored)
    assert "Names Jane Roe" in headlines
    assert "warehouse product" not in headlines


def test_dedupe_drops_a_repeat_url(monkeypatch):
    """The same appointment surfaces in both queries (same discovery_url). It
    must be classified once, so fetch de-duplicates within the run."""
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    items = wire.fetch(session=session, pause=0)
    seen = [i["discovery_url"] for i in items]
    assert len(seen) == len(set(seen))


def test_the_resolution_budget_bounds_a_run():
    items = [{"source_url": "https://news.google.com/rss/articles/A",
              "discovery_url": "https://news.google.com/rss/articles/A",
              "headline": "x"} for _ in range(10)]
    _, counts = wire.resolve(items, budget=3, session=FakeSession())
    assert counts["over_budget"] == 7


# --- Dormancy: armable, off by default --------------------------------------

def test_dormant_by_default_makes_no_request(monkeypatch):
    monkeypatch.delenv("TIT_US_EXEC_WIRE", raising=False)
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    out = wire.collect(dry_run=False, session=session, pause=0)
    assert out == []
    assert session.calls == [], "a disarmed live run must not touch the network"


def test_arming_the_flag_enables_a_live_run(monkeypatch):
    monkeypatch.setenv("TIT_US_EXEC_WIRE", "on")
    assert wire.is_armed()
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    wire.collect(dry_run=False, session=session, pause=0)
    assert session.calls, "an armed run should contact the index"


def test_a_dry_run_rehearses_even_while_dormant(monkeypatch):
    """The coverage preview must work before the owner arms it."""
    monkeypatch.delenv("TIT_US_EXEC_WIRE", raising=False)
    assert not wire.is_armed()
    session = FakeSession({u: FakeResponse(RSS) for u in _query_urls()})
    wire.collect(dry_run=True, session=session, pause=0)
    assert session.calls, "a dry run should still read the index"


# --- Metering: the collector spends nothing itself --------------------------

def test_the_collector_calls_no_model_directly():
    """Paid classification is the shared classify path's job, which is the
    metered wrapper (single OpenRouter POST through spend accounting). This
    collector must never build its own client or POST to a model endpoint, or a
    charge would land outside the meter. Asserted over the source text so a
    later edit that adds one fails here."""
    from pathlib import Path

    src = Path(wire.__file__).read_text().lower()
    assert "openai(" not in src
    assert "chat/completions" not in src
    assert "openrouter" not in src
    assert "api_key" not in src


# --- Query shape ------------------------------------------------------------

def test_the_queries_are_us_appointment_shaped():
    queries = wire.registry.us_exec_wire_queries(window_days=7)
    assert queries
    joined = " ".join(queries).lower()
    assert "appoint" in joined or "names" in joined
    assert "chief executive" in joined or "ceo" in joined
    assert all("when:7d" in q for q in queries)


def test_it_is_registered_but_scheduled_by_nothing():
    """Registration makes it runnable by hand; dormancy keeps it off. The
    workflow half of dormancy is that no cron names it — asserted separately in
    the workflow tests; here we pin the registration and the default-off flag."""
    import run_collect

    assert run_collect.SOURCES.get("us_exec_wire") is wire
