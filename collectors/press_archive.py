"""Publishers' own XML sitemaps, read as an ARCHIVE rather than as a feed.

WHY THIS EXISTS
---------------
`analysis/recall/rejection_audit.py` measured the 81 gold-set misses of
2026-07-28 and found the answer nobody expected: **zero were fetched and
rejected**. There is no filter defect. What there is, is 51 misses classed
`outside_our_history` — the news collectors first ran on 2026-07-27 and
`national_press` on 2026-07-29, and every one of those 51 was published between
2026-07-01 and 2026-07-17. We did not miss them. We did not exist yet.

`national_press` reads 653 publisher feeds and cannot help, because **an RSS
feed is a window and not an archive**: it serves the last few dozen items, and
`MAX_ITEMS_PER_FEED` caps us at 25 of them anyway. A busy daily's feed covers
two or three days. Nothing in the RSS route reaches 2026-07-01, at any price,
ever.

A publisher's XML sitemap is a different document with a different promise. It
is written for crawlers that want the whole site, so where one exists it can
list years. This collector reads exactly that, for the SAME catalogue and the
SAME publishers `national_press` already reads, and returns the same shape of
raw dict.

WHAT WAS MEASURED, WITH THIS CODE (2026-07-30, 82 catalogue publishers, 2026-03)
--------------------------------------------------------------------------------
    serve a discoverable sitemap                72 / 82   (88%)
    reach 2026-03 with a dated article URL      34 / 82   (41%)
    ... with 50 URLs or more                    25 / 82
    ... with 100 or more                        23 / 82
    URLs per reaching publisher, one month      median 163, mean 233
    wall clock                                  980s for all 82, median 5.3s

March 2026 is the honest test month and the reason the number is half what a
first pass suggested. A first probe counted `<lastmod>` months and reported 54
of 72 publishers "reaching 2026-07" — which is nonsense twice over, because **a
section page's `<lastmod>` moves to today every time a story is added to it**
and because a 48-hour news sitemap read on 30 July trivially "reaches July".
Half of those 54 were tag pages, topic hubs and site furniture: WirtschaftsWoche's
`sitemapExternal` index is a list of TOPICS ("cisco", "chiphersteller"), and
Baguete's whole sitemap is seven URLs of site structure. PR TIMES scored 942
"July" URLs on the same mistake and actually reaches four. A month that predates
both us and the news-sitemap window cannot be faked either way, so that is what
is measured and that is what this collector is sized against.

THE PART THAT DECIDES THE SHAPE: A SITEMAP HAS NO HEADLINE
----------------------------------------------------------
`<urlset>` gives a URL and a date. It does not give a title, so the free
prefilter — which is the whole reason breadth is affordable here — has nothing
to read. Three sources of text were measured and only the last two are used:

  * **The URL slug.** Free, and it works in English: SmartCompany's July slugs
    put 15 of 203 past `prefilter.passes`. But it is 3.08% across the whole
    sample against a real-candidate rate near 5.6%, so it silently drops about
    half — and on PR TIMES and CTech it drops EVERYTHING, because their slugs
    are `/tv/detail/3164` and `0,7340,L-3723664,00.html`. A prefilter that
    returns zero for Japan, Korea, Israel and China while looking healthy in
    English is the exact failure `tests/test_locale_rotation.py` exists to
    prevent one layer up. It is used here only to ORDER a publisher's month,
    never to reject.
  * **`<news:title>`, where the sitemap carries it.** 17 of 72 publishers do.
    Free and exact, and it is used when present.
  * **The article page's own `og:title` / `og:description`.** What the publisher
    publishes FOR syndication — the same two fields a feed teaser is built from,
    served from the page head with no subscription, no cookie and no paywall
    prompt. Measured 0.17s per URL on a keep-alive session, and 11 of 60
    SmartCompany URLs past the prefilter against the slug's 6.

So the order is: enumerate (free) -> rank by slug and country need (free) ->
read the HEAD of a bounded number (free, slow) -> prefilter on the real title
and description (free) -> gate (money). The bounded number is the wall-clock
budget; the ration in the walker is the money budget. They are different
budgets and conflating them is how a walker either stalls or overspends.

WHAT THIS IS NOT
----------------
It does not render a page, run script, follow a paywall prompt or read an
article body. `head_text()` reads the first `HEAD_BYTES` of the response and
takes only the sharing metadata out of it. If a publisher does not publish that
metadata, the item is dropped rather than fetched harder.

It is not an aggregator reader. Every `source_url` is the publisher's own
article URL exactly as the publisher's own sitemap states it, on the publisher's
own registrable domain — checked, because a sitemap can list anything.

robots.txt is checked through `national_press.robots_allows`, imported rather
than reimplemented, for the sitemap AND for every article head fetch. Same for
`registrable_domain`: the dangerous case is never a 404, it is a cited URL that
answers 200 from somebody else's site, and `botswanaguardian.co.bw` becoming a
betting site is why that guard exists rather than a status-code check.

ROUTE B, AND WHY IT IS A FALLBACK RATHER THAN THE ROUTE
-------------------------------------------------------
The Wayback CDX API enumerates archived URLs for a domain in a date range,
keyless and free, and it reaches publishers who have since paywalled,
reorganised or died — which is precisely the set a sitemap cannot reach (Wamda
serves no sitemap at all; BetaKit and Globes serve only a 48-hour news sitemap).
Measured 2026-07-30, `url=<domain>/*` with a 20-day range answered **HTTP 504
after exactly 60 seconds on all four domains tried**, which is the shape of a
query the gateway will not finish rather than a throttle. Re-asked as
`matchType=prefix` the same window answered 200 on six of eight domains, in 7 to
29 seconds each, and 504 on the other two.

**A 429, a 504 and a timeout all mean UNKNOWN and never "nothing there."** That
confusion was a live bug in this repo two days ago and it cost real coverage, so
it is not a convention here: `wayback_urls` raises `ArchiveUnknown` and the
caller records `archive_unknown`, which is a status a human reads, not a zero
that averages away. (Measured honestly: across 14 domain queries and a
six-query burst with no pause at all, archive.org returned **no 429 at any
point**. Every failure was a 504. The 429 rule stays because the property being
guarded is "did not answer", not a particular number.)

**And the date range is a CAPTURE window, not a publication window.** This is
the finding that keeps Route B out of the walk rather than merely behind it.
Asking for captures between 2026-07-01 and 2026-07-20 returned FINSMES articles
from 2013 and 2014 and Wamda articles from 2012, because a crawler visiting a
site in July 2026 re-captures a decade of its pages. So CDX cannot be filtered
to a historical month by the API at all: everything it returns has to be dated
from the URL or from the page, and most of what it returns is old. Combined with
7 to 60 seconds per domain and a quarter of the domains tried answering 504,
that is not a route a 50-minute slice can walk 653 publishers of.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from collectors import capped_fetch

from collectors.national_press import (
    USER_AGENT, DomainDrift, Feed, dateline, load_feeds, registrable_domain,
    robots_allows, title_key, _plain,
)

COLLECTOR = "press_archive"

REPO_ROOT = Path(__file__).resolve().parent.parent
HEALTH_PATH = Path(os.environ.get("TIT_PRESS_ARCHIVE_HEALTH")
                   or REPO_ROOT / "data" / "press_archive_health.json")

# The run keeps a ledger, so it must know when it is only rehearsing.
ACCEPTS_DRY_RUN = True

TIMEOUT = 30

#: Children of a sitemap index read per publisher. An index can list hundreds
#: (SmartCompany lists 105, THE BRIDGE 26) and a historical window normally
#: intersects a handful of them. Bounded because the cost of this collector is
#: wall clock, not money, and wall clock is what the slice budget spends.
MAX_CHILDREN = 8

#: URLs kept per publisher per run, after date filtering. A single publisher can
#: return thousands for one month (Annahar returned 3,000 at the probe's own
#: cap) and taking all of them would let one outlet fill the whole run — the
#: same trap `MAX_ITEMS_PER_FEED` catches in `national_press`, one document
#: further out.
MAX_URLS_PER_PUBLISHER = 400

#: Article heads fetched per publisher per run. This is the WALL CLOCK budget
#: and it is deliberately not the money budget: at a measured 0.17s per head on
#: a keep-alive session, 60 is about ten seconds a publisher.
MAX_HEADS_PER_PUBLISHER = 60

#: Politeness, per host. A publisher serving us a sitemap and then sixty article
#: heads is doing us a favour; the heads go through one keep-alive session and
#: this bounds the burst.
PER_HOST_PAUSE = 0.5
HEAD_PAUSE = 0.15

#: How much of an article response is read before it is thrown away. The
#: sharing metadata is in `<head>`; nothing here wants the body, and saying so
#: in bytes rather than in a comment is what makes it true.
HEAD_BYTES = 200_000

STATS = {
    "publishers": 0, "sitemaps": 0, "no_sitemap": 0, "robots": 0, "dead": 0,
    "hijacked": 0, "archive_unknown": 0, "urls": 0, "in_window": 0,
    "heads_fetched": 0, "heads_failed": 0, "no_metadata": 0,
    "items": 0, "duplicate_url": 0, "syndicated": 0, "off_domain": 0,
}

#: One entry per publisher, whatever happened to it. Same contract as
#: `national_press.FEED_HEALTH`: a dead source among two hundred live ones is
#: exactly the failure an aggregate hides.
PUBLISHER_HEALTH: list[dict] = []


# --------------------------------------------------------------------------
# sitemap discovery and parsing
# --------------------------------------------------------------------------

#: Tried in order AFTER whatever robots.txt declares. Every one of these was
#: observed serving a real sitemap in the 82-publisher probe.
CONVENTIONAL_PATHS = (
    "/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml",
    "/sitemap-index.xml", "/sitemap/sitemap-index.xml", "/sitemap/",
)

_ENTRY = re.compile(rb"<(?:url|sitemap)\b[^>]*>(.*?)</(?:url|sitemap)>", re.S | re.I)
_LOC = re.compile(rb"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", re.S | re.I)
_LASTMOD = re.compile(rb"<lastmod>\s*(.*?)\s*</lastmod>", re.S | re.I)
_NEWS_DATE = re.compile(
    rb"<[a-z]*:?publication_date>\s*(.*?)\s*</[a-z]*:?publication_date>", re.S | re.I)
_NEWS_TITLE = re.compile(
    rb"<[a-z]*:?title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</[a-z]*:?title>", re.S | re.I)
_IS_INDEX = re.compile(rb"<sitemapindex", re.I)
_IS_URLSET = re.compile(rb"<urlset", re.I)

#: A date inside the path, which is how most CMSs publish and the only date
#: available when a sitemap carries neither `lastmod` nor `publication_date`.
_PATH_DATE = re.compile(r"/(20\d\d)[/-](\d{1,2})(?:[/-](\d{1,2}))?(?:[/-]|$)")

#: A year or year-month naming a child of a sitemap index, which is how a
#: historical child is recognised without fetching it.
_CHILD_PERIOD = re.compile(r"(20\d\d)[-_/]?(0[1-9]|1[0-2])?")


@dataclass(frozen=True)
class Entry:
    """One row of a sitemap: a URL, a date if the publisher stated one, and a
    title if the sitemap carries the news namespace."""
    url: str
    day: str = ""        # ISO date, or "" when the publisher stated none
    title: str = ""

    @property
    def month(self) -> str:
        return self.day[:7]


class ArchiveUnknown(Exception):
    """The archive did not answer. NOT "the archive holds nothing".

    A 429, a 504 and a timeout are all this. Reading any of them as an empty
    result is how a throttle becomes a coverage claim, which is the bug this
    repo shipped on 2026-07-28 and paid for in missing rows.
    """


def _http(session):
    return session or requests


def _headers(accept: str) -> dict:
    return {"User-Agent": USER_AGENT, "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9"}


_ACCEPT_XML = ("application/xml,text/xml,application/rss+xml;q=0.9,"
               "text/html;q=0.5,*/*;q=0.3")
_ACCEPT_HTML = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8")


def declared_sitemaps(origin: str, *, session=None) -> list[str]:
    """The `Sitemap:` lines of a publisher's robots.txt.

    robots.txt is where a publisher SAYS where its sitemap is, so it is asked
    first and the conventional paths are the fallback. Reading it here is also
    free: `robots_allows` has already cached the fetch for this origin.
    """
    out: list[str] = []
    try:
        resp = _http(session).get(f"{origin}/robots.txt",
                                  headers={"User-Agent": USER_AGENT}, timeout=15)
    except requests.RequestException:
        return out
    if resp.status_code != 200:
        return out
    for line in resp.text.splitlines():
        if line.strip().lower().startswith("sitemap:"):
            value = line.split(":", 1)[1].strip()
            if value.startswith("http"):
                out.append(value)
    return out[:12]


def looks_like_sitemap(body: bytes) -> bool:
    head = body[:4000]
    return bool(_IS_INDEX.search(head) or _IS_URLSET.search(head))


def parse_sitemap(body: bytes) -> tuple[bool, list[Entry]]:
    """(is_index, entries). Never raises: a malformed sitemap is an empty one.

    Deliberately regex rather than ElementTree, and for the reason
    `national_press._scrape_items` exists: real documents in the wild are not
    well-formed, and a strict parser turns one stray ampersand into a lost
    publisher. There is no structure here worth a parser — a sitemap is a flat
    list of three fields.
    """
    is_index = bool(_IS_INDEX.search(body[:4000]))
    entries: list[Entry] = []
    for match in _ENTRY.finditer(body):
        blob = match.group(1)
        loc = _LOC.search(blob)
        if not loc:
            continue
        url = html.unescape(loc.group(1).decode("utf8", "replace").strip())
        if not url.startswith("http"):
            continue
        day = ""
        for pattern in (_NEWS_DATE, _LASTMOD):
            hit = pattern.search(blob)
            if hit:
                day = _iso_day(hit.group(1).decode("utf8", "replace").strip())
                if day:
                    break
        if not day:
            day = _url_day(url)
        title = ""
        hit = _NEWS_TITLE.search(blob)
        if hit:
            title = html.unescape(hit.group(1).decode("utf8", "replace")).strip()
        entries.append(Entry(url=url, day=day, title=title))
    return is_index, entries


def _iso_day(value: str) -> str:
    """The date part of a W3C datetime, or "" — never a guess."""
    text = (value or "").strip()
    if len(text) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    if re.match(r"^\d{4}-\d{2}$", text):
        return f"{text}-01"
    return ""


def _url_day(url: str) -> str:
    """A date in the path. Only ever a fallback, and only when it parses as a
    real calendar date — `/2026/07/` is a month and `/12345/67890/` is not."""
    hit = _PATH_DATE.search(urlparse(url).path)
    if not hit:
        return ""
    year, month, day = hit.group(1), int(hit.group(2)), int(hit.group(3) or 1)
    try:
        return date(int(year), month, day).isoformat()
    except ValueError:
        return ""


def child_period(loc: str) -> str:
    """The period a child NAMES (`post-sitemap-2026-03.xml` -> "2026-03"), or "".

    The only exact signal an index gives for free. Read from the basename, so a
    year in the host or in a path prefix cannot be mistaken for one.
    """
    hit = _CHILD_PERIOD.search(loc.rsplit("/", 1)[-1])
    if not hit:
        return ""
    year, month = hit.group(1), hit.group(2)
    return f"{year}-{month}" if month else year


def named_period_holds(loc: str, lo: str, hi: str) -> bool:
    stamp = child_period(loc)
    if not stamp:
        return False
    return lo[:len(stamp)] <= stamp <= hi[:len(stamp)]


# --- Why the index's own <lastmod> cannot be trusted to locate a month -----
#
# The obvious selection is "fetch every child whose lastmod is not older than
# the window". Measured against a real index on 2026-07-30, that is wrong twice
# over, and both errors are silent.
#
# SmartCompany's `sitemap_index.xml` lists 105 children:
#
#     post-sitemap.xml           lastmod 2026-07-29   contents 2006-12..2007-08
#     post-sitemap45.xml         lastmod 2013-08-29   contents 2013-07..2013-08
#     post-sitemap89.xml         lastmod 2026-07-29   contents 2026-04..2026-07
#     site-post-tag-sitemap5..9  lastmod 2026-07-30   contents TAG PAGES
#     news-sitemap.xml           lastmod 2026-07-29   contents the last 48 hours
#
# So page ONE of a chronologically paginated set carries the whole site's newest
# modification date while holding posts from 2006 — a lastmod filter fetches a
# quarter of a megabyte and finds nothing — and the tag sitemaps carry today's
# date while holding no articles at all. A first probe that counted lastmod
# months reported 54 of 72 publishers "reaching 2026-07" on exactly this
# mistake.
#
# What IS reliable is the ordering: a paginated family is chronological, so the
# window can be found by bisection instead of by scanning. Page 89 holds
# 2026-04-16..07-29, which means a July window is ONE fetch rather than 105.
#
#: How many children may be fetched purely to locate the window. log2(105) is
#: about 7, so this bounds a bisection over any index anyone actually serves,
#: and it bounds the damage when a family turns out not to be chronological.
MAX_PROBES = 9

_FAMILY_DIGITS = re.compile(r"\d+")


def family_key(loc: str) -> str:
    """Children of one paginated set, with the page number removed.

    `post-sitemap.xml`, `post-sitemap2.xml` ... `post-sitemap89.xml` are one
    family; `site-post-tag-sitemap5.xml` is another. Grouping matters because
    bisection is only valid WITHIN a family — interleaving a tag set with an
    article set produces a sequence that is not ordered by anything.
    """
    return _FAMILY_DIGITS.sub("#", loc.rsplit("/", 1)[-1])


def _page_number(loc: str) -> int:
    digits = _FAMILY_DIGITS.findall(loc.rsplit("/", 1)[-1])
    return int(digits[-1]) if digits else 0


def span_of(entries: list[Entry]) -> tuple[str, str]:
    """(oldest, newest) dated entry, or ("", "") when the child dates nothing."""
    days = sorted(e.day for e in entries if e.day)
    return (days[0], days[-1]) if days else ("", "")


def locate_children(children: list[Entry], lo: str, hi: str, read,
                    *, max_probes: int = MAX_PROBES,
                    max_children: int = MAX_CHILDREN) -> list[str]:
    """Which children of an index to read for [lo, hi]. `read(loc) -> entries`.

    Three strategies, cheapest first, and the caller pays only for what the
    strategy costs:

      1. **Named periods.** Free and exact. Used alone when any child names one.
      2. **Bisection within the largest paginated family.** Costs at most
         `max_probes` fetches and is what makes a 105-child index affordable.
         A family whose probes come back out of order is abandoned rather than
         trusted — an unordered bisection is a wrong answer delivered quietly.
      3. **Everything else, newest lastmod first.** For an index with a handful
         of children, where scanning is cheaper than reasoning.

    Every probe's body is handed back to the caller through `read`, so a child
    fetched to locate the window is never fetched again to read it.
    """
    named = [c.url for c in children if named_period_holds(c.url, lo, hi)]
    if named:
        return named[:max_children]

    families: dict[str, list[Entry]] = {}
    for child in children:
        families.setdefault(family_key(child.url), []).append(child)

    picked: list[str] = []
    probes = 0
    biggest = max(families.values(), key=len) if families else []
    if len(biggest) >= 3:
        pages = sorted(biggest, key=lambda c: _page_number(c.url))
        low, high = 0, len(pages) - 1
        spans: dict[int, tuple[str, str]] = {}

        def probe(index: int) -> tuple[str, str]:
            nonlocal probes
            if index not in spans:
                probes += 1
                spans[index] = span_of(read(pages[index].url) or [])
            return spans[index]

        # Anchor on the last page: in a chronological family it holds the most
        # recent posts, and if it does not, the family is not chronological and
        # the whole strategy is abandoned.
        newest = probe(high)
        oldest = probe(low) if len(pages) > 1 else newest
        ordered = bool(newest[1] and oldest[1] and newest[1] >= oldest[1])
        if ordered:
            while low < high and probes < max_probes:
                middle = (low + high) // 2
                span = probe(middle)
                if not span[1]:
                    break
                if span[1] < lo:
                    low = middle + 1
                else:
                    high = middle
            # Take the located page and its neighbours: a window straddles a
            # page boundary far more often than not.
            for index in range(max(low - 1, 0), min(low + 2, len(pages))):
                picked.append(pages[index].url)
            # Any page already probed that turned out to intersect the window is
            # free to include — it has been read.
            for index, span in spans.items():
                if span[0] and span[0] <= hi and span[1] >= lo:
                    picked.append(pages[index].url)

    rest = [c for c in children if c.url not in picked]
    rest.sort(key=lambda c: (c.day, c.url), reverse=True)
    for child in rest:
        if len(picked) >= max_children:
            break
        picked.append(child.url)

    out: list[str] = []
    for url in picked:
        if url not in out:
            out.append(url)
    return out[:max_children]


def find_sitemap(feed: Feed, *, session=None) -> tuple[str, bytes] | None:
    """(url, body) of the first thing that is actually a sitemap, or None.

    Every candidate is robots-checked and drift-checked. A sitemap served from
    a registrable domain we did not catalogue is refused for the same reason a
    feed is: it is somebody else's site answering in the publisher's place.
    """
    parts = urlparse(feed.site or feed.rss)
    origin = f"{parts.scheme or 'https'}://{parts.netloc}"
    if not parts.netloc:
        return None

    candidates = declared_sitemaps(origin, session=session)
    candidates += [origin + path for path in CONVENTIONAL_PATHS]

    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if not robots_allows(url, session=session):
            continue
        try:
            resp = _http(session).get(url, headers=_headers(_ACCEPT_XML),
                                      timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        landed = registrable_domain(getattr(resp, "url", "") or url)
        expected = feed.expected_domains
        if landed and expected and landed not in expected:
            raise DomainDrift(
                f"sitemap answers from {landed}, not "
                f"{' or '.join(sorted(expected))} — the domain may have been "
                f"taken over")
        if looks_like_sitemap(resp.content):
            return url, resp.content
    return None


def entries_in_window(feed: Feed, lo: str, hi: str, *, session=None,
                      max_children: int = MAX_CHILDREN,
                      pause: float = PER_HOST_PAUSE) -> list[Entry]:
    """Every dated article URL this publisher's sitemap places in [lo, hi].

    Undated entries are DROPPED, not kept with today's date. A row stamped with
    the collection time files last March's article as this month's news and
    quietly corrupts every period column on the dashboard — the same reasoning
    that leaves `published_date` empty in `national_press.parse`.
    """
    found = find_sitemap(feed, session=session)
    if found is None:
        return []
    _, body = found

    is_index, entries = parse_sitemap(body)
    if not is_index:
        return _in_window(entries, lo, hi)

    # One cache for the whole publisher, shared between locating the window and
    # reading it. A child fetched to work out WHERE the window is has already
    # been read, and fetching it twice would double the only cost this collector
    # actually has.
    cache: dict[str, list[Entry]] = {}

    def read(loc: str) -> list[Entry]:
        if loc in cache:
            return cache[loc]
        cache[loc] = []
        if pause:
            time.sleep(pause)
        if not robots_allows(loc, session=session):
            return cache[loc]
        try:
            resp = _http(session).get(loc, headers=_headers(_ACCEPT_XML),
                                      timeout=TIMEOUT)
        except requests.RequestException:
            return cache[loc]
        if resp.status_code != 200 or not looks_like_sitemap(resp.content):
            return cache[loc]
        nested, rows = parse_sitemap(resp.content)
        # One level only. An index of indexes exists, and walking it without a
        # bound is how a collector spends a whole slice budget on one publisher.
        cache[loc] = [] if nested else rows
        return cache[loc]

    out: list[Entry] = []
    for loc in locate_children(entries, lo, hi, read, max_children=max_children):
        out.extend(_in_window(read(loc), lo, hi))
        if len(out) >= MAX_URLS_PER_PUBLISHER:
            break
    return out[:MAX_URLS_PER_PUBLISHER]


def _in_window(entries: list[Entry], lo: str, hi: str) -> list[Entry]:
    return [e for e in entries if e.day and lo <= e.day <= hi]


# --------------------------------------------------------------------------
# the free relevance signal, and the honest one
# --------------------------------------------------------------------------

_SLUG_SPLIT = re.compile(r"[-_+]+")

#: A path segment carrying no readable word. Two forms in the wild and both
#: matter: PR TIMES' bare `3164`, and CTech's `0,7340,L-3723664,00.html`, which
#: is not numeric by any simple test and still says nothing.
_SLUG_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def slug_words(url: str) -> str:
    """The last path segment as words, or "" when it carries none.

    ORDERING ONLY. It is not a filter and must never become one: PR TIMES
    publishes `.../000000016.000166016`, so a slug prefilter returns exactly
    zero for Japan while looking perfectly healthy in English. Measured 1.9%
    survival across the sample against a real-candidate rate near 3.5%, which
    is the other half of the same objection.
    """
    path = unquote(urlparse(url).path)
    path = re.sub(r"\.(html?|php|aspx?|shtml)$", "", path, flags=re.I)
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""

    def words(segment: str) -> str:
        # Two or more real words, or nothing. One word is a section name
        # ("articles", "detail", "news") far more often than a headline, and a
        # one-word "slug" is what makes a slug prefilter look alive on a
        # publisher it cannot read at all.
        text = _SLUG_SPLIT.sub(" ", segment).strip()
        return text if len(_SLUG_WORD.findall(text)) >= 2 else ""

    for segment in reversed(parts[-2:]):
        found = words(segment)
        if found:
            return found
    return ""


_META = re.compile(
    rb"""<meta[^>]+?(?:property|name)\s*=\s*["'](og:title|og:description|"""
    rb"""twitter:title|twitter:description|description)["'][^>]*>""", re.I)
_CONTENT = re.compile(rb"""content\s*=\s*["'](.*?)["']""", re.S | re.I)
_HTML_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.S | re.I)


def head_text(url: str, *, session=None, timeout: int = TIMEOUT
              ) -> tuple[str, str]:
    """(title, description) from the article's own sharing metadata.

    These are the two fields a publisher writes so that other people may quote
    the piece — the same two an RSS teaser is built from. Only the first
    `HEAD_BYTES` are read and no body text is taken, so nothing here is a
    paywall bypass and nothing here is a scrape of the article.

    Returns ("", "") on any failure, which the caller counts rather than
    guesses about.
    """
    if not robots_allows(url, session=session):
        return "", ""
    try:
        # This capped read is where `capped_fetch` came from. It lived here
        # only, written for a local reason (nothing wants the article body),
        # and every other collector buffered whole third-party responses while
        # the right pattern sat one file away. It is now the shared helper and
        # this call site is one of its users rather than its only copy.
        resp = capped_fetch.open_capped(url, session=_http(session),
                                        headers=_headers(_ACCEPT_HTML),
                                        timeout=timeout)
        if resp.status_code != 200:
            resp.close()
            return "", ""
        body = capped_fetch.read_capped(resp, capped_fetch.HEAD_BYTES)
    except (requests.RequestException, AttributeError, ValueError):
        return "", ""
    return metadata(body or b"")


def metadata(body: bytes) -> tuple[str, str]:
    """(title, description) out of a chunk of HTML. Pure, so it is tested
    against recorded bytes rather than against a live publisher."""
    found: dict[str, str] = {}
    for match in _META.finditer(body):
        key = match.group(1).decode("ascii", "replace").lower()
        content = _CONTENT.search(match.group(0))
        if content:
            found.setdefault(
                key, html.unescape(content.group(1).decode("utf8", "replace")).strip())
    title = found.get("og:title") or found.get("twitter:title") or ""
    if not title:
        hit = _HTML_TITLE.search(body)
        if hit:
            title = html.unescape(hit.group(1).decode("utf8", "replace")).strip()
    description = (found.get("og:description") or found.get("description")
                   or found.get("twitter:description") or "")
    return _plain(title, 300), _plain(description)


def to_raw(feed: Feed, entry: Entry, title: str, description: str) -> dict:
    """One candidate, in exactly the shape `national_press.parse` returns.

    `raw_text` is set here and the classifier reads ONLY this. A collector that
    forgets it stores zero rows and says nothing about it.
    """
    line = dateline(feed)
    return {
        "raw_text": f"{title}\n\n{description}\n\n{line}".strip(),
        "headline": title,
        # The publisher's own article URL, as the publisher's own sitemap
        # states it. This is the receipt.
        "source_url": entry.url,
        "discovery_url": entry.url,
        "source_name": feed.name,
        # From the sitemap, never from the clock. An undated entry never
        # reaches here — see entries_in_window.
        "published_date": entry.day,
        "language": feed.language,
        # Reporting only. NOT `country`: validate.py reads that as sourced, and
        # an Israeli outlet carrying a US round would file the US job under
        # Israel.
        "source_country": feed.country,
        "query": feed.name,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------
# ROUTE B: the Wayback CDX API
# --------------------------------------------------------------------------

CDX_URL = "http://web.archive.org/cdx/search/cdx"

#: Measured 2026-07-30, and this corrects the query shape everybody reaches for
#: first. `url=<domain>/*` with a date range answered **HTTP 504 after exactly
#: 60 seconds** on all four domains tried — nginx's gateway timeout, not a
#: throttle and not an empty result. The same window asked as
#: `url=<domain>/&matchType=prefix` answered 200. So the wildcard form is not
#: used here; `matchType` is.
#:
#:     url=finsmes.com                              200   7.3s
#:     url=finsmes.com&matchType=prefix (no dates)   200   2.3s
#:     url=finsmes.com&matchType=prefix + 10d        200  45.9s
#:     url=finsmes.com/* (no dates)                  200  55.5s
#:     url=finsmes.com/* + 3d                        504  60.1s
#:
#: 45.9 seconds for ONE publisher and ten days is the number that decides
#: whether Route B can be a route at all — see the walker's --plan-cost.
CDX_TIMEOUT = 120

#: Statuses that mean the archive did not answer. Every one of them raises
#: `ArchiveUnknown`. A 429 is the loud one, a 504 is the one actually observed,
#: and a 503 is archive.org's ordinary Saturday.
CDX_UNKNOWN_STATUSES = frozenset({429, 500, 502, 503, 504, 509})


def wayback_urls(domain: str, lo: str, hi: str, *, session=None,
                 limit: int = 2000, timeout: int = CDX_TIMEOUT) -> list[Entry]:
    """Archived URLs for one domain in [lo, hi], or `ArchiveUnknown`.

    THE RULE, and it is the whole reason this function is not three lines: a
    non-answer is never an empty answer. archive.org throttles hard and times
    out on wildcard queries, and reading either as "this publisher has nothing"
    turns an outage into a coverage claim that nobody will ever re-check. That
    exact confusion was live in this repo on 2026-07-28.
    """
    params = {
        # NOT `f"{domain}/*"`. See CDX_TIMEOUT: the wildcard form 504s.
        "url": f"{domain}/",
        "matchType": "prefix",
        "from": lo.replace("-", ""),
        "to": hi.replace("-", ""),
        "output": "json",
        "collapse": "urlkey",
        "limit": str(limit),
        "filter": "statuscode:200",
        "fl": "timestamp,original,mimetype",
    }
    try:
        resp = _http(session).get(CDX_URL, params=params,
                                  headers={"User-Agent": USER_AGENT},
                                  timeout=timeout)
    except requests.RequestException as exc:
        raise ArchiveUnknown(f"{domain}: {type(exc).__name__}") from exc
    if resp.status_code in CDX_UNKNOWN_STATUSES:
        retry = resp.headers.get("Retry-After", "")
        raise ArchiveUnknown(
            f"{domain}: HTTP {resp.status_code}"
            + (f", Retry-After {retry}" if retry else "")
            + " — the archive did not answer. This is NOT 'nothing archived'.")
    if resp.status_code != 200:
        raise ArchiveUnknown(f"{domain}: HTTP {resp.status_code}")
    try:
        rows = resp.json()
    except ValueError as exc:
        raise ArchiveUnknown(f"{domain}: unparseable CDX response") from exc
    if not rows:
        return []
    out: list[Entry] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        stamp, original = row[0], row[1]
        day = ""
        if len(stamp) >= 8 and stamp[:8].isdigit():
            day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
        out.append(Entry(url=original, day=day))
    return out


# --------------------------------------------------------------------------
# one publisher, end to end
# --------------------------------------------------------------------------

def read_publisher(feed: Feed, lo: str, hi: str, *, session=None,
                   max_heads: int = MAX_HEADS_PER_PUBLISHER,
                   order=None, head_pause: float = HEAD_PAUSE,
                   pause: float = PER_HOST_PAUSE) -> tuple[list[dict], dict]:
    """(candidates, health record) for one publisher over one window.

    `order` is an optional key function over `Entry` — the walker passes the
    ranking that decides which `max_heads` of a publisher's month get read.
    Ordering is all it does; nothing here rejects on it.
    """
    record = {"name": feed.name, "country": feed.country,
              "site": feed.site or feed.rss, "status": "ok",
              "urls": 0, "heads": 0, "items": 0, "detail": ""}
    try:
        entries = entries_in_window(feed, lo, hi, session=session, pause=pause)
    except DomainDrift as exc:
        record.update(status="hijacked", detail=str(exc))
        STATS["hijacked"] += 1
        return [], record
    except requests.RequestException as exc:
        record.update(status="dead", detail=type(exc).__name__)
        STATS["dead"] += 1
        return [], record

    if not entries:
        record.update(status="no_window",
                      detail=f"no dated article URL in {lo}..{hi} — either no "
                             f"sitemap, or one that does not reach back")
        STATS["no_sitemap"] += 1
        return [], record

    STATS["sitemaps"] += 1
    record["urls"] = len(entries)
    STATS["in_window"] += len(entries)

    # A sitemap can list anything, including a syndication partner's domain.
    expected = feed.expected_domains
    if expected:
        kept = []
        for entry in entries:
            if registrable_domain(entry.url) in expected:
                kept.append(entry)
            else:
                STATS["off_domain"] += 1
        entries = kept

    if order is not None:
        entries = sorted(entries, key=order)

    out: list[dict] = []
    for entry in entries[:max_heads]:
        title, description = entry.title, ""
        if not title:
            if head_pause:
                time.sleep(head_pause)
            title, description = head_text(entry.url, session=session)
            STATS["heads_fetched"] += 1
            record["heads"] += 1
            if not title:
                STATS["heads_failed"] += 1
                continue
        if not title:
            STATS["no_metadata"] += 1
            continue
        out.append(to_raw(feed, entry, title, description))

    record["items"] = len(out)
    STATS["items"] += len(out)
    if not out:
        record.update(status="empty",
                      detail=f"{record['urls']} URLs in window, none yielded a "
                             f"headline")
    return out, record


def collect(queries=None, *, start: str, end: str, feeds: list[Feed] | None = None,
            session=None, dry_run: bool = False,
            max_heads: int = MAX_HEADS_PER_PUBLISHER,
            order_for=None, pause: float = PER_HOST_PAUSE,
            head_pause: float = HEAD_PAUSE) -> list[dict]:
    """Every publisher's sitemap once, for one historical window.

    `queries` is accepted and ignored: this source has no search vocabulary, the
    catalogue IS the population — the same contract `national_press.collect`
    keeps, so `run_collect` could call it positionally if it were ever wired to
    the daily schedule. It is NOT, and that is deliberate: this reads history,
    and history does not need reading twice a day.
    """
    for key in STATS:
        STATS[key] = 0
    PUBLISHER_HEALTH.clear()

    population = feeds if feeds is not None else load_feeds()
    STATS["publishers"] = len(population)
    print(f"[{COLLECTOR}] {len(population)} publishers, window {start}..{end}")

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []

    for feed in population:
        order = order_for(feed) if order_for else None
        # A third party's failure is DATA, not an exception: whatever
        # transport error escapes the reader is this one publisher's `dead`
        # record, never the end of the walk over everybody else.
        try:
            items, record = read_publisher(
                feed, start, end, session=session, max_heads=max_heads,
                order=order, pause=pause, head_pause=head_pause)
        except (requests.RequestException, OSError) as exc:
            items = []
            record = {"name": feed.name, "country": feed.country,
                      "site": feed.site or feed.rss, "status": "dead",
                      "urls": 0, "heads": 0, "items": 0,
                      "detail": f"host failure escaped the reader: "
                                f"{type(exc).__name__}"}
            STATS["dead"] += 1
        for item in items:
            url = item["source_url"]
            if url in seen_urls:
                STATS["duplicate_url"] += 1
                continue
            seen_urls.add(url)
            key = title_key(item["headline"])
            if key and key in seen_titles:
                STATS["syndicated"] += 1
                continue
            seen_titles.add(key)
            out.append(item)
        PUBLISHER_HEALTH.append(record)

    _report(dry_run, start, end)
    return out


def _report(dry_run: bool, start: str, end: str) -> None:
    reached = [r for r in PUBLISHER_HEALTH if r["status"] == "ok"]
    silent = [r for r in PUBLISHER_HEALTH if r["status"] != "ok"]
    print(f"[{COLLECTOR}] {len(reached)} publishers reached back into "
          f"{start}..{end}, {len(silent)} did not; "
          f"{STATS['in_window']} URLs in window, {STATS['heads_fetched']} heads "
          f"read ({STATS['heads_failed']} failed), {STATS['items']} candidates, "
          f"{STATS['syndicated']} syndicated copies dropped")
    by_status: dict[str, int] = {}
    for record in silent:
        by_status[record["status"]] = by_status.get(record["status"], 0) + 1
    if by_status:
        print(f"[{COLLECTOR}] not reached: "
              + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))

    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": f"{start}..{end}",
        "publishers": STATS["publishers"],
        "reached": len(reached),
        "stats": dict(STATS),
        "by_publisher": sorted(PUBLISHER_HEALTH, key=lambda r: r["name"].lower()),
    }
    if dry_run:
        print(f"[{COLLECTOR}] ledger NOT written (dry run) — {HEALTH_PATH}")
        return
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"[{COLLECTOR}] per-publisher ledger written to {HEALTH_PATH}")
