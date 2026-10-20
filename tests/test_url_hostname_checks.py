"""URL checks compare HOSTNAMES, never substrings (CodeQL
py/incomplete-url-substring-sanitization). Offline, no network."""
from pathlib import Path

from collectors import google_news, news_backstop, us_exec_wire

ROOT = Path(__file__).resolve().parent.parent


def test_google_news_is_recognised_by_host_not_by_mention():
    assert google_news._is_google_news("https://news.google.com/rss/articles/abc")
    assert not google_news._is_google_news(
        "https://www.publisher.example/story?via=news.google.com")
    assert not google_news._is_google_news("https://news.google.com.evil.example/x")
    assert not google_news._is_google_news(None)


def test_a_publisher_url_mentioning_google_news_still_counts_as_resolved():
    items = [{"discovery_url": "https://news.google.com/rss/articles/abc",
              "source_url": "https://www.publisher.example/a/story?ref=news.google.com"},
             {"discovery_url": "https://news.google.com/rss/articles/def",
              "source_url": "https://news.google.com/rss/articles/def"}]
    assert google_news.unresolved_count(items) == 1


def test_backstops_refuse_google_news_but_not_a_lookalike_host():
    for mod in (news_backstop, us_exec_wire):
        ok, why = mod.storable({"source_url": "https://news.google.com/articles/x"})
        assert not ok and "aggregator" in why
        ok, why = mod.storable({"source_url": "https://notnews.google.com.example/a/b"})
        assert "aggregator" not in why


def test_export_links_rewrite_only_the_query():
    js = (ROOT / "wordpress-plugin/talent-intelligence-tracker/assets/dashboard.js").read_text()
    body = js[js.index("function updateExportLinks"):js.index("function refresh()")]
    assert "a.search =" in body
    assert "a.href =" not in body, "DOM text must never be assigned back as a URL"
