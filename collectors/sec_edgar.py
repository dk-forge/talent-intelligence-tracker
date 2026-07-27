"""SEC EDGAR 8-K collector — the leadership pillar's primary source.

Item 5.02 is "Departure of Directors or Certain Officers; Election of
Directors; Appointment of Certain Officers". Every US-listed company must file
one, dated, within four business days. That makes it the opposite of news
discovery in every way that matters here:

- the URL is always a real sec.gov document, never a homepage or an aggregator
- it is a primary source, so records earn `verified` confidence
- no throttling games: SEC allows 10 requests/second, it just requires a
  descriptive User-Agent

Google News cannot produce article URLs at all, and GDELT's yield collapsed to
zero on a live run. This is the source that ends that problem.
"""

from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
COLLECTOR = "sec_edgar"

# SEC rejects anonymous requests outright; the policy asks for a contact
# address. Overridable so the operator can put their own address on it.
USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "TalentIntel/1.0 (info@asktherecruiter.com)"
)

REQUEST_DELAY = 0.15          # comfortably under SEC's 10 req/s
PAGE_SIZE = 10

# Exact-match phrases: EFTS does not stem. "item 5.02" is the item code itself,
# which is the highest-precision term available — it appears in the filing
# whenever the event is an officer or director change.
PHRASES = (
    "item 5.02",
    "appointed as chief executive officer",
    "appointed chief financial officer",
    "named chief executive officer",
    "will serve as chief executive officer",
)


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def search(phrase: str, *, days_back: int = 7, page: int = 0) -> list[dict]:
    """One EFTS page for one phrase. Returns raw hits."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    params = {
        "q": f'"{phrase}"',
        "dateRange": "custom",
        "startdt": start.strftime("%Y-%m-%d"),
        "enddt": end.strftime("%Y-%m-%d"),
        "forms": "8-K",
        "from": page * PAGE_SIZE,
    }
    time.sleep(REQUEST_DELAY)
    resp = requests.get(EFTS_URL, params=params, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return (resp.json().get("hits") or {}).get("hits") or []


def _company_and_cik(hit: dict) -> tuple[str, str | None]:
    """EFTS display_names look like 'Apple Inc.  (AAPL)  (CIK 0000320193)'."""
    names = (hit.get("_source") or {}).get("display_names") or []
    if not names:
        return "", None
    name = names[0]
    cik = None
    m = re.search(r"CIK\s*(\d{4,10})", name)
    if m:
        cik = m.group(1).lstrip("0")
    company = re.sub(r"\s*\((?:[A-Z0-9.\-]{1,10})\)\s*", " ", name)
    company = re.sub(r"\s*\(CIK[^)]*\)\s*$", "", company).strip()
    return company, cik


def document_url(hit: dict) -> str | None:
    """Build the real filing URL. EFTS `_id` is 'accession:filename'."""
    raw_id = hit.get("_id") or ""
    if ":" not in raw_id:
        return None
    accession, filename = raw_id.split(":", 1)
    _company, cik = _company_and_cik(hit)
    if not cik:
        return None
    return f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/{filename}"


def fetch_text(url: str, *, limit: int = 3000) -> str:
    """Full-text search returns no document body, so the filing is fetched.

    Bounded and tag-stripped: an 8-K exhibit can be megabytes and the
    classifier only ever reads the first few thousand characters.
    """
    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    text = resp.text[:500_000]
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def collect(queries=None, *, days_back: int = 7, max_per_phrase: int = 4) -> list[dict]:
    """Return raw candidate dicts. `queries` is accepted and ignored so this
    collector is interchangeable with the others in run_collect."""
    seen: set[str] = set()
    out: list[dict] = []

    for phrase in PHRASES:
        try:
            hits = search(phrase, days_back=days_back)
        except requests.RequestException:
            # One bad phrase must not lose the ones that already succeeded.
            continue

        for hit in hits[:max_per_phrase]:
            url = document_url(hit)
            if not url or url in seen:
                continue
            seen.add(url)

            company, _cik = _company_and_cik(hit)
            src = hit.get("_source") or {}
            try:
                body = fetch_text(url)
            except requests.RequestException:
                continue
            if not body:
                continue

            # The filing itself is dense legal prose; leading with the company
            # and the item gives the classifier the same framing a headline
            # would, without inventing one.
            headline = f"{company} 8-K filing (Item 5.02): officer or director change"
            out.append({
                "raw_text": f"{headline}\n\n{body}",
                "headline": headline,
                "source_url": url,
                "source_name": "SEC EDGAR",
                "discovery_url": url,
                "published_date": src.get("file_date"),
                "country": "United States",
                "query": phrase,
                "collector": COLLECTOR,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    return out
