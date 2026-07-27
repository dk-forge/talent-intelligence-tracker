"""GDELT DOC 2.0 collector.

Free, keyless, worldwide, machine-translated from 65 languages before indexing.

The reason it now leads rather than supplements: **GDELT returns the real
article URL.** Google News RSS gives only the outlet homepage, its redirect no
longer resolves, and the URL is not recoverable from its encoded token, so
every record sourced that way linked to a front page instead of the article
that made the claim. A homepage is not a receipt.
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timezone

import requests

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
COLLECTOR = "gdelt"

MAX_RECORDS = 75

# GDELT allows roughly one request every 5 seconds and answers 429 with plain
# text. Anything shorter than this loses whole runs.
MIN_PAUSE = 6.0


def build_query_url(query: str, *, timespan: str = "3d", records: int = MAX_RECORDS) -> str:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(records),
        "timespan": timespan,
        "sort": "DateDesc",
    }
    return f"{ENDPOINT}?{urllib.parse.urlencode(params)}"


class RateLimited(RuntimeError):
    """GDELT answers 429 as plain text, so a naive JSON parse turns a rate
    limit into a silent zero. Raise instead."""


def fetch(query: str, *, timespan: str = "3d", timeout: int = 45) -> list[dict]:
    resp = requests.get(
        build_query_url(query, timespan=timespan),
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if resp.status_code == 429 or b"limit requests" in resp.content[:400]:
        raise RateLimited("GDELT rate limit: one request per 5 seconds")
    resp.raise_for_status()
    return parse(resp.content, query)


def parse(payload: bytes, query: str = "") -> list[dict]:
    """Parse a GDELT ArtList response. Separate from fetch() so tests run
    offline against captured fixtures."""
    import json

    try:
        data = json.loads(payload or b"{}")
    except ValueError:
        # GDELT answers with an HTML error page under load rather than JSON.
        return []

    items = []
    for article in data.get("articles") or []:
        url = (article.get("url") or "").strip()
        title = (article.get("title") or "").strip()
        if not (url and title):
            continue

        items.append({
            # The classifier reads only this.
            "raw_text": title,
            "headline": title,
            "source_url": url,
            "source_name": (article.get("domain") or "").strip(),
            "discovery_url": url,
            "published_date": _parse_seendate(article.get("seendate")),
            "language": article.get("language") or "",
            "query": query,
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    return items


def collect(queries: list[str], *, timespan: str = "3d", pause: float = MIN_PAUSE) -> list[dict]:
    """Fetch every query, de-duplicating by URL within the run.

    GDELT rate-limits aggressively, so the pause between queries is longer than
    it looks like it needs to be. A 429 here costs the whole run.
    """
    seen: set[str] = set()
    out: list[dict] = []

    for query in queries:
        try:
            batch = fetch(query, timespan=timespan)
        except RateLimited:
            # Back off hard and retry once; a lost query is a coverage hole.
            time.sleep(pause * 3)
            try:
                batch = fetch(query, timespan=timespan)
            except (RateLimited, requests.RequestException):
                continue
        except requests.RequestException:
            # One bad query must not lose the queries that already succeeded.
            continue

        for item in batch:
            if item["source_url"] in seen:
                continue
            seen.add(item["source_url"])
            out.append(item)
        time.sleep(pause)

    return out


def _parse_seendate(value) -> str | None:
    """GDELT stamps 20260726T141500Z. Return YYYY-MM-DD."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
