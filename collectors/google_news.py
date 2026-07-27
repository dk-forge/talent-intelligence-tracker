"""Google News RSS collector.

Free, keyless, unthrottled, and the single best discovery source: headlines
carry the figures even when the article is paywalled. The sibling replaced a
$449/month news API with this and coverage went up (spec 5).

**Resolving the real article URL.** Google wraps every link in an encoded
`news.google.com/rss/articles/CBMi...` redirect, and following it does not
resolve. It is tempting to conclude the publisher URL is unrecoverable — that
conclusion was reached here once and it was wrong. Google exposes its own
resolution endpoint: the article page carries a signature and timestamp, and
posting those to `batchexecute` returns the publisher URL.

That matters because a homepage is not a receipt. With resolution working, what
we store is the article that makes the claim (spec 2 rule 5); without it,
nothing from this collector is storable at all.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

RSS_ENDPOINT = "https://news.google.com/rss/search"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
COLLECTOR = "google_news"


def build_query_url(query: str, *, lang: str = "en", country: str = "US") -> str:
    params = {
        "q": query,
        "hl": lang,
        "gl": country,
        "ceid": f"{country}:{lang}",
    }
    return f"{RSS_ENDPOINT}?{urllib.parse.urlencode(params)}"


def fetch(query: str, *, lang: str = "en", country: str = "US", timeout: int = 30) -> list[dict]:
    """Fetch one query and return raw candidate dicts."""
    resp = requests.get(
        build_query_url(query, lang=lang, country=country),
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse(resp.content, query)


def parse(xml_bytes: bytes, query: str = "") -> list[dict]:
    """Parse an RSS payload into raw dicts.

    Kept separate from fetch() so tests run offline against captured fixtures.
    """
    root = ET.fromstring(xml_bytes)
    items = []

    for node in root.findall(".//item"):
        title = _text(node, "title")
        link = _text(node, "link")
        if not (title and link):
            continue

        source_el = node.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        source_url = (source_el.get("url") or "").strip() if source_el is not None else ""

        items.append({
            # raw_text is what the classifier reads. A collector that forgets
            # this posts zero records forever (spec 6 rule 2).
            "raw_text": f"{title}\n\n{_text(node, 'description')}".strip(),
            "headline": title,
            "discovery_url": link,
            "source_url": source_url or link,
            "source_name": source_name,
            "published_date": _text(node, "pubDate"),
            "query": query,
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    return items


# Google's resolution endpoint expects a browser. Its own UA rule (a descriptive
# agent for the WP host) does not apply here.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

_SIG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')
# The URL comes back inside an escaped JSON string, so the naive
# `"(https?://[^"]+)"` match stops at the backslash and finds nothing.
_RESOLVED = re.compile(r'garturlres\\",\\"(https?://[^\\"]+)')


def article_id(discovery_url: str) -> str:
    return discovery_url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def resolve_source_url(item: dict, *, timeout: int = 20, session=None) -> dict:
    """Recover the publisher's own URL from Google's encoded redirect.

    Two steps: read the signature and timestamp off the article page, then ask
    Google's batchexecute endpoint to resolve the id. Best effort — on failure
    the item keeps the outlet homepage from the RSS <source> element, and
    validate.py rejects it as a bare domain rather than crediting Google.
    """
    url = item.get("discovery_url") or ""
    if "news.google.com" not in url:
        return item

    http = session or requests
    try:
        aid = article_id(url)
        page = http.get(f"https://news.google.com/rss/articles/{aid}",
                        headers={"User-Agent": BROWSER_UA}, timeout=timeout)
        sig, ts = _SIG.search(page.text), _TS.search(page.text)
        if not (sig and ts):
            return item

        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
              None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            aid, int(ts.group(1)), sig.group(1),
        ])
        resp = http.post(
            BATCH_URL,
            headers={"User-Agent": BROWSER_UA,
                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data={"f.req": json.dumps([[["Fbv4je", inner]]])},
            timeout=timeout,
        )
        hit = _RESOLVED.search(resp.text)
        if hit:
            item["source_url"] = hit.group(1)
    except (requests.RequestException, ValueError, IndexError):
        pass
    return item


def collect(queries: list[str], *, pause: float = 1.0) -> list[dict]:
    """Fetch every query, de-duplicating by URL within the run.

    Deliberately does NOT resolve redirects. Resolution costs a full HTTP round
    trip per item, so it belongs after the free filters have thrown most
    candidates away — see the ordering in run_collect.py.
    """
    seen: set[str] = set()
    out: list[dict] = []

    for query in queries:
        for item in fetch(query):
            key = item["discovery_url"]
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        time.sleep(pause)

    return out


def _text(node, tag: str) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""
