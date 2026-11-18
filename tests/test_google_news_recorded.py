"""Google News resolution against REAL recorded traffic (2026-11-18).

2026-11-16, Railway: "UNRESOLVED: 1500/1500 Google News links kept a homepage",
every candidate rejected as a bare domain, the run exited 1 and Railway showed
the service as Crashed. Actions runs the same day resolved 99%+, so the
encoding had not changed; the host had. These tests pin (1) that the current
two-step decode (article page signature -> batchexecute Fbv4je) turns three
real, recorded Google News links into their publisher articles, (2) that every
way it can fail is counted under a name, and (3) that a host Google will not
answer stops after a bounded streak instead of burning an hour.
"""

import collections
import json
import pathlib

import pytest
import requests

import run_collect
from collectors import google_news

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "google_news_resolution_2026-11-18.json"
RECORDED = json.loads(FIXTURE.read_text())


class _Resp:
    def __init__(self, text, status_code=200):
        self.text, self.status_code = text, status_code


class _Replay:
    """Serves the recorded page and batchexecute answer for one article."""

    def __init__(self, rec):
        self.rec, self.posted = rec, []

    def get(self, url, **kw):
        assert google_news.article_id(self.rec["discovery_url"]) in url
        return _Resp(f"<html><c-wiz>{self.rec['page_snippet']}</c-wiz></html>")

    def post(self, url, **kw):
        self.posted.append(kw["data"]["f.req"])
        return _Resp(self.rec["batch_response"])


@pytest.fixture(autouse=True)
def _fresh_stats(monkeypatch):
    monkeypatch.setattr(google_news, "STATS", {k: 0 for k in google_news.STATS})


@pytest.mark.parametrize("rec", RECORDED, ids=lambda r: r["expected"][:40])
def test_recorded_links_resolve_to_the_publisher_article(rec):
    item = {"discovery_url": rec["discovery_url"], "source_url": rec["rss_source"]}
    assert google_news.is_unresolved(item), "the RSS gives only the homepage"
    session = _Replay(rec)
    out = google_news.resolve_source_url(item, session=session)
    assert out["source_url"] == rec["expected"]
    assert not google_news.is_unresolved(out)
    # The signature and timestamp read off the page are what was sent.
    sent = session.posted[0]
    sig = google_news._SIG.search(rec["page_snippet"]).group(1)
    ts = google_news._TS.search(rec["page_snippet"]).group(1)
    assert sig in sent and ts in sent
    assert google_news.STATS["resolved"] == 1


def _fail_with(get=None, post=None, raise_exc=None):
    class S:
        def get(self, url, **kw):
            if raise_exc:
                raise raise_exc
            return get

        def post(self, url, **kw):
            return post
    return S()


GN = {"discovery_url": RECORDED[0]["discovery_url"], "source_url": "https://www.example.com"}
GOOD_PAGE = _Resp(RECORDED[0]["page_snippet"])


@pytest.mark.parametrize("session,key", [
    (_fail_with(get=_Resp("blocked", 429)), "http_blocked"),
    (_fail_with(get=_Resp("<form>Before you continue</form>")), "consent_wall"),
    (_fail_with(get=_Resp("<html>something new</html>")), "no_signature"),
    (_fail_with(get=GOOD_PAGE, post=_Resp(')]}\'\n[["er",null,null,null,null,400]]')),
     "batch_no_url"),
    (_fail_with(raise_exc=requests.ConnectionError("dns")), "network_error"),
])
def test_every_failure_has_a_name(session, key):
    out = google_news.resolve_source_url(dict(GN), session=session)
    assert google_news.is_unresolved(out)
    assert google_news.STATS[key] == 1
    assert sum(v for k, v in google_news.STATS.items() if k != key) == 0
    assert google_news.failure_breakdown().startswith(" (1 ")


def test_breaker_stops_a_host_google_will_not_answer():
    calls = []

    def dead(item):
        calls.append(item)
        return item
    items = [dict(GN) for _ in range(1500)]
    out = google_news.resolve_all(items, resolve=dead, give_up_after=40)
    assert len(out) == 1500 and len(calls) == 40
    assert google_news.STATS["skipped_breaker"] == 1460
    assert "1460 not tried, resolver gave up" in google_news.failure_breakdown()


def test_breaker_never_trips_once_anything_resolved():
    rec = RECORDED[0]
    n = [0]

    def flaky(item):
        n[0] += 1
        if n[0] == 1:
            item["source_url"] = rec["expected"]
        return item
    google_news.resolve_all([dict(GN) for _ in range(200)], resolve=flaky, give_up_after=5)
    assert n[0] == 200 and google_news.STATS["skipped_breaker"] == 0


def test_rejects_are_sampled_and_summarised(capsys):
    """1500 two-line REJECTs overran Railway's 500 lines/s and hid the exit."""
    tally = collections.Counter()
    for i in range(1500):
        run_collect.reject_log(
            tally, {"headline": f"h{i}"},
            f"source_url is a bare domain, not an article: https://o{i}.com")
    out = capsys.readouterr().out
    assert out.count("REJECT") == run_collect.REJECT_LOG_SAMPLE
    summary = run_collect.reject_summary(tally)
    assert summary[0].startswith(f"  ... {1500 - run_collect.REJECT_LOG_SAMPLE} more rejected")
    assert "1500  source_url is a bare domain, not an article" in summary[1]
    assert len(summary) == 2, "per-item URLs must not split the tally"
