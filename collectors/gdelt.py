"""GDELT collector: raw 15-minute GKG files first, DOC 2.0 for the archive.

---------------------------------------------------------------------------
2026-10-14 (#257): discovery moved off the DOC API onto the raw files
---------------------------------------------------------------------------
The DOC 2.0 API answered 429 ("limit requests to one every 5 seconds") on
almost every request from the self-hosted box, a GitHub-hosted runner and an
unrelated third network, so every daily run stored 0. That throttling is
GDELT's side and no pacing fixed it.

GDELT also publishes everything it indexes as static files every 15 minutes
(http://data.gdeltproject.org/gdeltv2/lastupdate.txt names the newest). They
are plain downloads with no request-rate limit and no key. `collect()` now
reads the newest few English GKG 2.0 files (sequentially, `DEFAULT_FILES`,
hard-capped at `MAX_FILES`) and selects an article when EITHER

  * its title or URL slug contains one of the quoted phrases of the production
    `GDELT_QUERIES` (the old query vocabulary, unchanged), or
  * GDELT tagged it with a signal theme (`SIGNAL_THEME`: RECRUITMENT,
    APPOINTMENT, RESIGNATION, job creation, CEO/CxO, wages, funding ...) AND its
    title passes the same free employment prefilter the pipeline runs anyway.

Measured on three live files (2026-10-14 09:45-10:15 UTC, 3,390 articles): the
phrases alone matched 0 titles (DOC matched article bodies; GKG gives only the
title), theme + prefilter selected 115, among them "Tesco announce plans to
hire 28,000 extra Christmas staff", "Aegean Airlines appoints ... as new CEO"
and "HydRegen Raises $8M Series A". Items keep the exact shape parse() builds,
so the downstream pipeline is untouched. GKG carries no outlet country, so
`source_country` is empty on this path.

`collect_doc()` below is the old DOC 2.0 path, kept because the 2026 backfill
needs DOC's date-ranged archive. `collect()` delegates to it when given
explicit dates.

---------------------------------------------------------------------------
The DOC 2.0 notes, kept for the backfill path
---------------------------------------------------------------------------

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

import html
import io
import os
import re
import time
import urllib.parse
import zipfile
from datetime import date, datetime, timedelta, timezone

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
         "articles": 0, "duplicate_url": 0, "syndicated": 0, "truncated": 0,
         "files_read": 0, "files_failed": 0, "rows_scanned": 0}


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


def collect_doc(queries: list[str], *, timespan: str = "3d", pause: float = MIN_PAUSE,
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


# ---------------------------------------------------------------------------
# Raw 15-minute GKG files (#257). See the module docstring.
# ---------------------------------------------------------------------------

RAW_BASE = "https://data.gdeltproject.org/gdeltv2/"
LASTUPDATE_URL = RAW_BASE + "lastupdate.txt"

#: Files read per run: one hour of world news. Each English GKG file is ~4-6 MB
#: zipped and ~1,100-1,300 articles; measured theme+prefilter yield is ~40 per
#: file before title dedup. `GDELT_RAW_FILES` overrides, never above MAX_FILES.
DEFAULT_FILES = 4
MAX_FILES = 12

#: Zipped GKG files run ~4-6 MB; 40 MB is room for a busy quarter hour, not
#: for a surprise. The unzipped member is capped separately.
GKG_ZIP_BYTES = 40_000_000
GKG_CSV_BYTES = 150_000_000

#: Courtesy gap between sequential downloads. No limit is documented for the
#: raw files; this keeps the run a polite single reader anyway.
FILE_PAUSE = 2.0

#: GKG V2Themes that mark a talent signal. A theme alone is not enough (an
#: article on Iraqi health policy carries WB_2670_JOBS), so match() also asks
#: the title to pass the free employment prefilter.
SIGNAL_THEME = re.compile(
    r"^(?:RECRUITMENT|APPOINTMENT|RESIGNATION|TAKE_OFFICE"
    r"|WB_1170_JOB_CREATION\w*|WB_2670_JOBS|UNGP_JOB_OPPORTUNITIES\w*"
    r"|WB_2748_EMPLOYMENT|WB_2704_EMPLOYER"
    r"|TAX_FNCACT_(?:CEO|CHIEF_\w*OFFICER|EXECUTIVE_OFFICER|EMPLOYEES?|INVESTOR)"
    r"|WB_856_WAGES|WB_2737_EMPLOYEE_BENEFITS|WB_380_FUNDING_INNOVATION"
    r"|ECON_FOREIGNINVEST)$")

_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_WORDS = re.compile(r"[^\w]+", re.UNICODE)


class RawFilesUnavailable(RuntimeError):
    """lastupdate.txt could not be read. Raised, not swallowed: an index we
    cannot read must not look like a quiet news day."""


def _get(url: str) -> tuple[int, bytes]:
    resp, body = capped_fetch.capped_get(
        url, headers={"User-Agent": USER_AGENT}, timeout=60,
        max_bytes=GKG_ZIP_BYTES)
    return resp.status_code, body


def _norm(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").lower()).split())


def query_phrases(queries) -> list[str]:
    """The quoted phrases of the DOC-syntax production queries, normalised."""
    out = []
    for query in queries:
        for phrase in re.findall(r'"([^"]+)"', query):
            norm = _norm(phrase)
            if norm and norm not in out:
                out.append(norm)
    return out


def update_urls(latest: str, n: int) -> list[str]:
    """English GKG file URLs, newest first, walking back 15 minutes a file."""
    n = max(1, min(int(n), MAX_FILES))
    t = datetime.strptime(latest, "%Y%m%d%H%M%S")
    return [f"{RAW_BASE}{(t - timedelta(minutes=15 * k)):%Y%m%d%H%M%S}.gkg.csv.zip"
            for k in range(n)]


def latest_stamp() -> str:
    status, body = _get(LASTUPDATE_URL)
    if status != 200:
        raise RawFilesUnavailable(f"lastupdate.txt answered HTTP {status}")
    found = re.search(rb"/(\d{14})\.gkg\.csv\.zip", body)
    if not found:
        raise RawFilesUnavailable("lastupdate.txt names no GKG file")
    return found.group(1).decode()


def parse_gkg(payload: bytes) -> list[dict]:
    """Rows of one zipped GKG 2.0 file: url, title, domain, date, themes.
    Rows with no title are skipped: the classifier has nothing to read."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            member = z.infolist()[0]
            if member.file_size > GKG_CSV_BYTES:
                raise ValueError(f"GKG member is {member.file_size} bytes")
            text = z.read(member).decode("utf8", "replace")
    except (zipfile.BadZipFile, IndexError) as exc:
        raise ValueError(f"not a GKG zip: {exc}") from exc

    rows = []
    for line in text.split("\n"):
        cols = line.split("\t")
        if len(cols) < 27 or not cols[4].startswith("http"):
            continue
        found = _TITLE.search(cols[26])
        title = html.unescape(found.group(1)).strip() if found else ""
        if not title:
            continue
        themes = {t.split(",")[0] for t in cols[8].split(";") if t}
        if not themes:
            themes = {t for t in cols[7].split(";") if t}
        rows.append({
            "url": cols[4].strip(),
            "title": title,
            "domain": cols[3].strip(),
            "date": _parse_seendate(cols[1]),
            "themes": themes,
        })
    return rows


def match(row: dict, phrases: list[str]) -> str | None:
    """Why `row` is a candidate, or None. Phrases first, then theme+title."""
    slug = urllib.parse.urlparse(row["url"]).path
    hay = f" {_norm(row['title'])} {_norm(slug)} "
    for phrase in phrases:
        if f" {phrase} " in hay:
            return f'"{phrase}"'
    themes = [t for t in row["themes"] if SIGNAL_THEME.match(t)]
    if themes:
        from pipeline import prefilter
        ok, _ = prefilter.passes(row["title"])
        if ok:
            return f"theme:{sorted(themes)[0]}"
    return None


def _configured_files() -> int:
    try:
        return int(os.environ.get("GDELT_RAW_FILES", DEFAULT_FILES))
    except ValueError:
        return DEFAULT_FILES


def collect(queries: list[str], *, files: int | None = None,
            pause: float = FILE_PAUSE, startdatetime=None, enddatetime=None,
            **doc_kwargs) -> list[dict]:
    """Discovery for the daily run, from the raw GKG files.

    Explicit dates mean an archive window, which only DOC 2.0 serves, so that
    call goes to collect_doc() unchanged.
    """
    if startdatetime or enddatetime:
        return collect_doc(queries, startdatetime=startdatetime,
                           enddatetime=enddatetime, **doc_kwargs)
    reset_stats()
    phrases = query_phrases(queries)
    urls = update_urls(latest_stamp(), files or _configured_files())
    seen: set[str] = set()
    titles: set[str] = set()
    out: list[dict] = []
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for i, url in enumerate(urls):
        if i:
            time.sleep(pause)
        try:
            status, body = _get(url)
        except requests.RequestException as exc:
            print(f"  [gdelt] file unreadable ({exc.__class__.__name__}): {url}")
            STATS["files_failed"] += 1
            continue
        if status == 429:
            STATS["throttled_out"] += 1
            continue
        if status != 200:
            print(f"  [gdelt] file HTTP {status}: {url}")
            STATS["files_failed"] += 1
            continue
        try:
            rows = parse_gkg(body)
        except ValueError as exc:
            print(f"  [gdelt] file unparseable ({exc}): {url}")
            STATS["files_failed"] += 1
            continue
        STATS["files_read"] += 1
        STATS["rows_scanned"] += len(rows)

        for row in rows:
            why = match(row, phrases)
            if not why:
                continue
            if row["url"] in seen:
                STATS["duplicate_url"] += 1
                continue
            seen.add(row["url"])
            key = title_key(row["title"])
            if key and key in titles:
                STATS["syndicated"] += 1
                continue
            titles.add(key)
            out.append({
                "raw_text": _with_dateline(row["title"], row["domain"], "", "English"),
                "headline": row["title"],
                "source_url": row["url"],
                "source_name": row["domain"],
                "discovery_url": row["url"],
                "published_date": row["date"],
                "language": "English",
                "source_country": "",
                "query": why,
                "collector": COLLECTOR,
                "fetched_at": fetched_at,
            })

    STATS["articles"] += len(out)
    print(f"  [gdelt] raw GKG: {STATS['files_read']}/{len(urls)} files, "
          f"{STATS['rows_scanned']} articles scanned, {len(out)} selected")
    return out
