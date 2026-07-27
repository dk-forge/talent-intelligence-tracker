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
