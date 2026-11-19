"""Old-format Google News ids decode with no request; a blocked host stops fast.

2026-11-19 (Railway, dk-forge/todo#1753): 1280/1280 links kept a homepage
because the host got HTTP 403/429/503 from Google, and every item was rejected
as a bare domain. Ids of the older "CBMi" format carry the URL in the token,
so those need no request at all, and a host Google blocks must not keep asking.
"""

import base64

from collectors import google_news

URL = "https://www.handelszeitung.ch/unternehmen/cfo-verlaesst-flsmidth-829311"


def _old_id(url: str) -> str:
    body = url.encode()
    raw = b"\x08\x13\x22" + bytes([len(body)]) + body + b"\xd2\x01\x00"
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class _NoNetwork:
    def get(self, *a, **k):
        raise AssertionError("an old-format id must not need a request")

    post = get


def test_real_looking_old_format_id_decodes_offline():
    aid = _old_id(URL)
    assert aid.startswith("CBMi")
    item = {"discovery_url": f"https://news.google.com/rss/articles/{aid}?oc=5",
            "source_url": "https://www.handelszeitung.ch"}
    out = google_news.resolve_source_url(item, session=_NoNetwork())
    assert out["source_url"] == URL
    assert not google_news.is_unresolved(out)


def test_new_format_token_is_not_decoded_offline():
    raw = b"\x08\x13\x22\x1aAU_yqLPkz9Xq3d0FakeOpaqueTok\xd2\x01\x00"
    aid = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert google_news.decode_offline(aid) is None


def test_garbage_id_is_not_decoded():
    assert google_news.decode_offline("not-base64-!!") is None
    assert google_news.decode_offline("CBMi") is None


def test_blocked_host_stops_after_a_few_requests():
    calls = []

    def blocked(item):
        calls.append(item)
        google_news.STATS["http_blocked"] += 1
        return item

    items = [{"discovery_url": f"https://news.google.com/rss/articles/AU{i}",
              "source_url": "https://www.example.ch"} for i in range(1280)]
    out = google_news.resolve_all(items, resolve=blocked)
    assert len(out) == 1280
    assert len(calls) == google_news.RESOLVE_GIVE_UP_AFTER_BLOCKED


def test_old_format_ids_still_decode_after_the_breaker_trips():
    def blocked(item):
        google_news.STATS["http_blocked"] += 1
        return item

    dead = [{"discovery_url": f"https://news.google.com/rss/articles/AU{i}",
             "source_url": "https://www.example.ch"} for i in range(10)]
    old = {"discovery_url": f"https://news.google.com/rss/articles/{_old_id(URL)}",
           "source_url": "https://www.handelszeitung.ch"}
    out = google_news.resolve_all(dead + [old], resolve=blocked)
    assert out[-1]["source_url"] == URL
