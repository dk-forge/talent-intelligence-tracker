"""Google News RSS collector.

Free, keyless, and the single best discovery source: headlines carry the
figures even when the article is paywalled. The sibling replaced a $449/month
news API with this and coverage went up (spec 5).

Google News is a **discovery pointer only**. What we store is the primary
source it points at (spec 2 rule 5), which is why every item is resolved
through its redirect before it is offered to the pipeline.
"""

from __future__ import annotations

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


def resolve_source_url(item: dict, *, timeout: int = 15, session=None) -> dict:
    """Follow the Google redirect to the publisher's own URL.

    Best effort. If resolution fails the item keeps whatever `source_url` the
    RSS `<source>` element gave, and validate.py rejects it if that is still an
    aggregator host — a record is dropped rather than attributed to Google.
    """
    url = item.get("discovery_url") or ""
    if "news.google.com" not in url:
        return item

    http = session or requests
    try:
        resp = http.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        final = resp.url
        if final and "news.google.com" not in final:
            item["source_url"] = final
    except requests.RequestException:
        pass
    return item


def collect(queries: list[str], *, resolve: bool = True, pause: float = 1.0) -> list[dict]:
    """Fetch every query, de-duplicating by URL within the run."""
    seen: set[str] = set()
    out: list[dict] = []

    for query in queries:
        for item in fetch(query):
            key = item["discovery_url"]
            if key in seen:
                continue
            seen.add(key)
            out.append(resolve_source_url(item) if resolve else item)
        time.sleep(pause)

    return out


def _text(node, tag: str) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""
