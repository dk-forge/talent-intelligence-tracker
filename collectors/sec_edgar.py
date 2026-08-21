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
# `or`, not a get() default: a workflow that maps a MISSING repo secret into
# env sets the variable to empty string, which get()'s default ignores - and
# SEC 403s every request carrying an empty User-Agent. That exact combination
# turned the first backfill dispatch into five silent 403 windows (2026-07-28).
USER_AGENT = (os.environ.get("EDGAR_USER_AGENT") or "").strip() \
    or "TalentIntel/1.0 (info@asktherecruiter.com)"

REQUEST_DELAY = 0.15          # comfortably under SEC's 10 req/s

# How many hits EFTS puts in one response. This is MEASURED, not chosen: the
# endpoint takes `from` as a record offset and ignores any size we ask for, so
# the only correct stride is the one it actually serves. It serves 100
# (tests/test_efts_page_offset.py pins it, and the committed fixture records
# the measurement).
#
# It said 10 until 2026-08-20, which meant `from` advanced a tenth of a page at
# a time and consecutive "pages" overlapped by 90%: pages 0/1/2 returned 300
# hits containing 120 distinct filings. Three requests bought 1.2 pages of
# reach. Every EFTS caller here derives its offset from this one constant, so
# there is one place to be wrong and one place to fix.
PAGE_SIZE = 100

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


def search(phrase: str, *, days_back: int = 7, page: int = 0,
           startdt: str | None = None, enddt: str | None = None) -> list[dict]:
    """One EFTS page for one phrase. Returns raw hits.

    Explicit startdt/enddt (YYYY-MM-DD) override days_back; the backfill
    walks historical windows this way while the daily run keeps its rolling
    week.
    """
    if not (startdt and enddt):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        startdt = start.strftime("%Y-%m-%d")
        enddt = end.strftime("%Y-%m-%d")
    params = {
        "q": f'"{phrase}"',
        "dateRange": "custom",
        "startdt": startdt,
        "enddt": enddt,
        "forms": "8-K",
        "from": page * PAGE_SIZE,
    }
    time.sleep(REQUEST_DELAY)
    resp = requests.get(EFTS_URL, params=params, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return (resp.json().get("hits") or {}).get("hits") or []


# EFTS renders one rigid shape, appending its own two groups to the filer's
# conformed name:
#
#     {conformed name}  ({ticker}[, {ticker}...])  (CIK {digits})
#
# joined by exactly TWO spaces every time — 771 of 771 separators in
# tests/fixtures/sec_edgar_display_names.json — and always ending in the CIK
# group. So this reads the string's END rather than hunting for a ticker-shaped
# token anywhere inside it, and the two-space delimiter is what does the work.
# A company's own parenthetical is joined by ONE space, which is the only thing
# separating "ACUITY INC. (DE)  (AYI)", "Super Group (SGHC) Ltd  (SGHC)" and
# "Western Asset Diversified Income Fund (WDI)  (CIK ...)" from a ticker block:
# in all three the two are spelled identically, so no test of the token itself
# could ever tell them apart.
#
# The old rule read neither end. It substituted a single parenthesised
# ticker-shaped token wherever it appeared, and was wrong in both directions:
#
#  - it never matched a ticker LIST, because "(BBBY, BBBY-WT)" has a comma in
#    it, so 126 published headlines read "BED BATH & BEYOND, INC.  (BBBY,
#    BBBY-WT) 8-K filing..." with the block and a doubled space left in;
#  - it matched inside the name, so "Jerash Holdings (US), Inc." became
#    "Jerash Holdings , Inc." and part of a real company's name was deleted.
_CIK_GROUP = re.compile(r"\s{2,}\(CIK\s*(\d{4,10})\)\s*$")
# A ticker as EDGAR spells one: uppercase alphanumerics with an optional class
# suffix (BF-A, USB-PQ, EVAC-WT). The longest in the fixture is 7 characters;
# the bound is 10 for headroom, which costs nothing because the two-space
# delimiter above is what decides, not the token's length.
_TICKER_GROUP = re.compile(
    r"\s{2,}\([A-Z0-9][A-Z0-9.\-]{0,9}(?:,\s*[A-Z0-9][A-Z0-9.\-]{0,9})*\)\s*$")


def _company_and_cik(hit: dict) -> tuple[str, str | None]:
    """EFTS display_names look like 'Apple Inc.  (AAPL)  (CIK 0000320193)'.

    Returns the filer's own conformed name and its CIK. See the note above for
    why this reads the tail of the string and not its tokens.
    """
    names = (hit.get("_source") or {}).get("display_names") or []
    if not names:
        return "", None
    name = names[0]

    m = _CIK_GROUP.search(name)
    if not m:
        # A display_name that does not end in the CIK group is a shape we have
        # never seen. Recover the CIK if it is in there at all, because
        # document_url() drops the filing without one — but do not strip a
        # trailing group off a string whose structure we cannot vouch for.
        loose = re.search(r"\(CIK\s*(\d{4,10})\)", name)
        if not loose:
            return name.strip(), None
        return (name[:loose.start()] + name[loose.end():]).strip(), \
            loose.group(1).lstrip("0")

    cik = m.group(1).lstrip("0")
    return _TICKER_GROUP.sub("", name[:m.start()]).strip(), cik


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

            company, cik = _company_and_cik(hit)
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
                # The filer's CIK, straight from the search hit. It is the
                # join key to the sibling layoff tracker, and it was being
                # extracted and thrown away on every filing.
                "cik": cik,
                "query": phrase,
                "collector": COLLECTOR,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    return out
