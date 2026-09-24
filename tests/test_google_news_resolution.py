"""Recovering the publisher URL from Google's encoded redirect.

This was written off once as impossible after a base64 decode came back empty.
The URL is not in the token — it is behind Google's own resolution endpoint,
and giving up on the first attempt cost this project its highest-recall source
for a day.

Live resolution is not unit-tested (it is a network call); what is pinned here
is the parsing, which is where the second failure was: the URL comes back inside
an escaped JSON string, so the obvious regex silently matches nothing.
"""

from collectors import google_news


REAL_RESPONSE = (
    ")]}'\n\n"
    '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"'
    'https://www.hotel-online.com/news/generator-appoints-chief-executive'
    '\\",1]",null,null,null,""],["di",13],["af.httprm",12,"194128",17]]'
)


def test_parses_the_url_out_of_the_escaped_json():
    hit = google_news._RESOLVED.search(REAL_RESPONSE)
    assert hit, "the escaped-JSON shape is what broke the first attempt"
    assert hit.group(1) == (
        "https://www.hotel-online.com/news/generator-appoints-chief-executive"
    )


def test_the_regex_that_actually_failed_finds_nothing():
    """The first attempt excluded backslashes from the character class. The URL
    is wrapped in escaped quotes, so the class terminates immediately and the
    match never happens — which read as "the URL is not in the response"."""
    import re
    first_attempt = re.compile(r'"(https?://(?!news\.google)[^"\\]+)"')
    assert not first_attempt.search(REAL_RESPONSE)


def test_article_id_is_the_last_path_segment():
    assert google_news.article_id(
        "https://news.google.com/rss/articles/CBMiabc123?oc=5"
    ) == "CBMiabc123"


def test_non_google_urls_are_left_alone():
    item = {"discovery_url": "https://www.ft.com/content/abc", "source_url": "x"}
    assert google_news.resolve_source_url(dict(item)) == item


def test_resolution_uses_a_browser_agent():
    """Google's endpoint does not answer a bot UA. The project's descriptive
    agent is for the WordPress host and does not apply here."""
    assert "Mozilla/5.0" in google_news.BROWSER_UA
    assert "TalentIntel" not in google_news.BROWSER_UA


class _Resp:
    def __init__(self, text):
        self.text = text


class _RecordingSession:
    """Answers like Google does from a US address, and records what was sent."""

    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        return _Resp('<c-wiz data-n-a-sg="SIG" data-n-a-ts="1726000000">')

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        return _Resp(REAL_RESPONSE)


def test_resolution_presents_the_consent_cookie():
    """2026-09-17 regression. collect.yml moved to the Contabo VPS (an EU
    address) on 2026-09-16, and from then on google_news stored ZERO rows:
    every candidate kept the RSS <source> homepage and was rejected as a bare
    domain. From EU addresses Google answers the article page with its consent
    interstitial, which carries no data-n-a-sg signature, so resolution
    silently gave up. The consent cookie must ride on both requests."""
    session = _RecordingSession()
    item = {"discovery_url": "https://news.google.com/rss/articles/CBMiabc?oc=5",
            "source_url": "https://www.hotel-online.com"}
    out = google_news.resolve_source_url(item, session=session)
    assert out["source_url"].endswith("/generator-appoints-chief-executive")
    for _verb, _url, kw in session.calls:
        cookies = kw.get("cookies") or {}
        assert cookies.get("SOCS") and cookies.get("CONSENT"), _verb


def test_consent_wall_page_is_recognised():
    wall = '<form action="https://consent.google.com/save">Before you continue</form>'
    assert google_news.is_consent_wall(wall)
    assert not google_news.is_consent_wall('<c-wiz data-n-a-sg="x">')


def test_unresolved_items_are_counted():
    items = [
        {"discovery_url": "https://news.google.com/rss/articles/A", "source_url": "https://www.ft.com"},
        {"discovery_url": "https://news.google.com/rss/articles/B", "source_url": "https://www.ft.com/content/abc"},
    ]
    assert google_news.unresolved_count(items) == 1
