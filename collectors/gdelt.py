"""GDELT DOC 2.0 collector.

Free, keyless, worldwide, machine-translated from 65 languages before indexing.

The reason it leads rather than supplements: **GDELT returns the real article
URL.** Google News RSS gives only the outlet homepage, its redirect no longer
resolves, and the URL is not recoverable from its encoded token, so every
record sourced that way linked to a front page instead of the article that made
the claim. A homepage is not a receipt.

It is also the only news route with an ARCHIVE. Google News RSS serves a recent
window and nothing else, so the first half of 2026 is unrecoverable through it;
DOC 2.0 takes explicit `startdatetime`/`enddatetime` and holds years. That is
what `backfill_gdelt_2026.py` uses.

---------------------------------------------------------------------------
Why it had stored 3 records in its whole life, measured 2026-07-28
---------------------------------------------------------------------------
Eight production queries, `timespan=3d`, run through this module unchanged:

    8 of 8 queries returned HTTP 200, and 335 articles between them.

So the two theories on file were both wrong. It is not the query syntax — the
queries work — and it is not the throttling: `fetch()` already retries through
429s, and every query landed (two only after a retry, which is why the run took
seven minutes). The yield was lost in three places downstream, and all three
are addressed here:

1. **GDELT matches the ARTICLE BODY; we shipped only the TITLE.** A query for
   "hiring spree" returns an essay on software engineering whose fourth
   paragraph says "hiring spree", and the title carries none of it. 241 of the
   335 were dropped by the free prefilter for having no employment term — a
   correct decision on the text it was given, and an invisible one. Nothing was
   broken, so nothing reported a problem. The title is still all DOC 2.0 gives
   us, so the fix is not more text but honest accounting: the run report now
   states the body-only-match rate as a first-class number instead of letting
   it hide inside "filtered".

2. **Syndication ate the money.** Those 335 URLs were 212 distinct stories. One
   wire item ("Skills That Create Careers…") appeared on 34 domains of the same
   content-farm network, each a different URL, so URL-dedup passed all 34 to
   the paid classifier before the content hash rejected 33 at the very end.
   `collect()` now de-duplicates on a normalised title too — free, and it
   removed 123 of 335 candidates on the measured run.

3. **The geography hint was thrown away.** DOC 2.0 returns `sourcecountry` on
   every article and `parse()` dropped it. A bare headline often places
   nowhere, validate.py rejects a signal with no geography, and that is exactly
   the shape of the losses — and exactly why the live product reads
   "1 country". The outlet's country is now carried and folded into `raw_text`
   as a dateline, as CONTEXT rather than as fact: the model still decides, and
   whatever it concludes still normalises through the country vocabulary. It is
   deliberately NOT written to `raw["country"]`, which validate.py would take
   as a sourced value — a Thai business site reporting a US company would then
   file a US appointment under Thailand.

Also measured, and deliberately not used: the sibling **Context 2.0 API**
returns the matched SENTENCE, which would fix (1) properly. It rejects
`startdatetime` outright ("Invalid query start date") and rejects
`sourcelang:english` ("Invalid/Unsupported Language"), so it cannot serve a
backfill at all. Recorded here so the next person does not re-discover it.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from datetime import date, datetime, timezone

import requests
from collectors import capped_fetch

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
COLLECTOR = "gdelt"

# DOC 2.0's ceiling is 250 and there is no pagination: no offset, no cursor.
# A query that hits the ceiling is SILENTLY TRUNCATED to the most recent 250
# and the rest of the window is simply gone. That is why the backfill windows
# by day rather than by month, and why truncation is counted and reported.
MAX_RECORDS = 250

# GDELT documents one request per 5 seconds. The real behaviour is erratic and
# load-dependent: identical requests succeed and 429 minutes apart, and a burst
# earns a penalty box that outlasts the nominal window. Pace generously and
# retry rather than treating a 429 as fatal — measured 2026-07-28, every one of
# eight queries eventually landed at 12s spacing with this retry ladder.
MIN_PAUSE = 12.0
MAX_ATTEMPTS = 4

# Run-level counters, read by the backfill for its report. reset_stats() first.
STATS = {"queries": 0, "throttled_out": 0, "rejected_queries": 0,
         "articles": 0, "duplicate_url": 0, "syndicated": 0, "truncated": 0}


def reset_stats() -> None:
    for key in STATS:
        STATS[key] = 0


def as_stamp(value) -> str:
    """GDELT's date format is YYYYMMDDHHMMSS. Accept a date, a datetime or a
    string in that format or in ISO, so callers can pass what they have."""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d%H%M%S")
    if isinstance(value, date):
        return value.strftime("%Y%m%d") + "000000"
    digits = re.sub(r"\D", "", str(value).strip())
    if len(digits) == 14:
        return digits
    if len(digits) == 8:
        return digits + "000000"
    raise ValueError(f"not a GDELT datetime: {value!r}")


def build_query_url(query: str, *, timespan: str = "3d", records: int = MAX_RECORDS,
                    startdatetime=None, enddatetime=None) -> str:
    """One DOC 2.0 URL.

    Same shape as sec_edgar.search(): explicit dates when given, a rolling
    window otherwise. GDELT treats `timespan` and an explicit range as
    alternatives, so sending both would be ambiguous — the range wins.
    """
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(records),
        "sort": "DateDesc",
    }
    if startdatetime or enddatetime:
        if startdatetime:
            params["startdatetime"] = as_stamp(startdatetime)
        if enddatetime:
            params["enddatetime"] = as_stamp(enddatetime)
    else:
        params["timespan"] = timespan
    return f"{ENDPOINT}?{urllib.parse.urlencode(params)}"


class RateLimited(RuntimeError):
    """GDELT answers 429 as plain text, so a naive JSON parse turns a rate
    limit into a silent zero. Raise instead."""


class QueryRejected(RuntimeError):
    """GDELT answers a bad query with HTTP 200 and a one-line plain-text
    complaint ("Invalid/Unsupported Language.", "Invalid query start date.").
    Without this, a rejected query is indistinguishable from a quiet day —
    which is the failure mode this whole module exists to stop repeating."""


# The plain-text refusals GDELT returns with a 200. Matched loosely on purpose:
# the wording changes, and a new one must not read as an empty result set.
_REFUSAL = re.compile(rb"^\s*(invalid|unsupported|your query|specify|no articles)", re.I)


def fetch(query: str, *, timespan: str = "3d", timeout: int = 45,
          attempts: int = MAX_ATTEMPTS, records: int = MAX_RECORDS,
          startdatetime=None, enddatetime=None) -> list[dict]:
    """Fetch one query, retrying through GDELT's erratic throttling."""
    STATS["queries"] += 1
    url = build_query_url(query, timespan=timespan, records=records,
                          startdatetime=startdatetime, enddatetime=enddatetime)
    for attempt in range(attempts):
        # Capped: GDELT is not a hostile party, but "the endpoint we query is
        # trustworthy" is an assumption a collector should not be built on,
        # and one shared read means one place to be wrong.
        resp, body = capped_fetch.capped_get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout,
            max_bytes=capped_fetch.FEED_BYTES)
        throttled = resp.status_code == 429 or b"limit requests" in body[:400]
        if not throttled:
            resp.raise_for_status()
            head = body[:200].lstrip()
            if head and not head.startswith(b"{") and _REFUSAL.match(head):
                STATS["rejected_queries"] += 1
                raise QueryRejected(
                    head.decode("utf8", "replace").strip()[:120]
                )
            items = parse(body, query)
            if len(items) >= records:
                # There is no pagination, so this is a coverage hole rather
                # than a success. The caller narrows the window.
                STATS["truncated"] += 1
            return items
        if attempt < attempts - 1:
            time.sleep(MIN_PAUSE * (attempt + 1))
    STATS["throttled_out"] += 1
    raise RateLimited(f"GDELT throttled after {attempts} attempts")


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

        domain = (article.get("domain") or "").strip()
        country = (article.get("sourcecountry") or "").strip()
        language = (article.get("language") or "").strip()

        items.append({
            # The classifier reads ONLY this. The dateline is the outlet's own
            # country, passed as context the way classify.py passes the
            # publisher — a hint that may place a headline placing nowhere,
            # never a fact. See the module docstring.
            "raw_text": _with_dateline(title, domain, country, language),
            "headline": title,
            "source_url": url,
            "source_name": domain,
            "discovery_url": url,
            "published_date": _parse_seendate(article.get("seendate")),
            "language": language,
            # Carried for reporting (how many countries did this run reach?)
            # and NOT written to `country`: validate.py treats that as sourced.
            "source_country": country,
            "query": query,
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    STATS["articles"] += len(items)
    return items


def _with_dateline(title: str, domain: str, country: str, language: str) -> str:
    if not (domain or country):
        return title
    where = f"{domain} ({country})" if domain and country else (domain or country)
    lang = f", published in {language}" if language else ""
    return f"{title}\n\n(Wire record: reported by {where}{lang}.)"


_PUNCT = re.compile(r"[^\w]+", re.UNICODE)


def title_key(title: str) -> str:
    """A syndication key.

    GDELT tokenises titles inconsistently across mirrors of one wire item
    ("Coca - Cola" here, "Coca-Cola" there), so compare on letters and digits
    only. Truncated because content farms append their own outlet name.
    """
    return _PUNCT.sub("", (title or "").lower())[:90]


def collect(queries: list[str], *, timespan: str = "3d", pause: float = MIN_PAUSE,
            startdatetime=None, enddatetime=None, seen_urls: set | None = None,
            seen_titles: set | None = None) -> list[dict]:
    """Fetch every query, de-duplicating by URL AND by title within the run.

    The title half is not tidiness, it is the money. One press release reached
    us on 34 different URLs on the measured run, and URL-dedup passed all 34 to
    the paid classifier before the content hash rejected 33 of them. Free
    filtering beats paid filtering.

    Pass `seen_urls`/`seen_titles` to carry the de-duplication across calls,
    which is how the backfill avoids paying for one wire item once per day.
    """
    seen: set[str] = seen_urls if seen_urls is not None else set()
    titles: set[str] = seen_titles if seen_titles is not None else set()
    out: list[dict] = []

    for query in queries:
        try:
            batch = fetch(query, timespan=timespan,
                          startdatetime=startdatetime, enddatetime=enddatetime)
        except RateLimited:
            # fetch() already retried with backoff. A lost query is a coverage
            # hole, not a crash — the health ledger records the shortfall.
            continue
        except QueryRejected as exc:
            # A rejected query is a BUG in the query, not a quiet day, and it
            # has to be loud enough to see in a run log.
            print(f"  [gdelt] QUERY REJECTED ({exc}): {query}")
            continue
        except requests.RequestException:
            # One bad query must not lose the queries that already succeeded.
            continue

        for item in batch:
            if item["source_url"] in seen:
                STATS["duplicate_url"] += 1
                continue
            seen.add(item["source_url"])
            key = title_key(item["headline"])
            if key and key in titles:
                STATS["syndicated"] += 1
                continue
            titles.add(key)
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
