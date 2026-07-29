"""National and regional press, read straight from publishers' own feeds.

WHY THIS EXISTS
---------------
A live recall test on 2026-07-28 asked for four Israeli rounds announced that
July — Glow ($180M), Enigma ($71M), Harmony ($34M) and Plantopia ($9M). We held
one of the four, and that one had no country on it. Israel had 23 rows in the
whole table.

The cause was not discovery and not the model. `data/sources_catalogue.csv`
ALREADY listed CTech, the outlet that broke all four. Its `rss` column was
empty, and no collector read the column anyway. Across 381 catalogue rows only
65 carried a feed URL, and not one of those 65 was ever fetched: the catalogue
rendered onto the public sources page and did nothing else. Every one of those
feeds was a working connector nobody had plumbed in.

So this is one collector for all of them, driven by the catalogue rather than by
code. Adding a country means adding a verified row to the CSV; no Python
changes, no new workflow.

WHAT IT IS NOT
--------------
It is not an aggregator reader. Every item's `source_url` is the publisher's own
article URL as the publisher's own feed states it, and `load_feeds()` refuses
outright to load a feed hosted on an aggregator (see `_AGGREGATOR_HOSTS`), so
the rule cannot be broken by a careless CSV edit later.

GEOGRAPHY, AND WHY IT IS A DATELINE
-----------------------------------
"Enigma Raises $71M" places nowhere. Enigma is Israeli, we stored country NULL,
and it is the outlet that knew: an Israeli business daily reporting a funding
round is reporting an Israeli one far more often than not.

The catalogue records each publisher's own country per row, so that fact is
already ours. It is passed exactly the way `collectors/gdelt.py` passes
`sourcecountry`: folded into `raw_text` as a dateline, as CONTEXT for the model,
and deliberately NOT written to `raw["country"]` — validate.py treats that field
as a sourced value, so writing it there would file a Reuters-style story about a
US employer under Israel purely because an Israeli outlet carried it. The model
still decides, and whatever it decides still normalises through the country
vocabulary or is dropped.

A regional feed says so in its own dateline ("covering South-East Europe")
rather than claiming its home country, because The Recursive is in Sofia and
writes about Romania as often as Bulgaria.

COST
----
Fetching is free. The free prefilter and then the one-word gate throw away the
overwhelming majority before a full read-through is paid for, and
`classify.READTHROUGH_CAP` bounds the money per run regardless of how many feeds
are listed. That is what makes breadth affordable here: adding a country costs
bandwidth, not budget.

PAYWALLED PUBLISHERS
--------------------
Some of these (Tech in Asia, The Business Times, Handelsblatt) run subscriptions
and their public feed is headline-and-teaser. That is fine and is all we want:
we store the headline, a short excerpt and a link to THEIR article, which is
ordinary citation. Nothing here follows a link, renders a page or bypasses a
paywall — the only bytes read are the ones the publisher chose to put in a feed
they serve openly. The visible consequence is a lower pass rate from those
feeds, because the gate sees less text. That is expected, and the per-feed
ledger records `items` separately from `kept` so a thin teaser feed is never
mistaken for a broken one.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

COLLECTOR = "national_press"

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE_CSV = Path(os.environ.get("TIT_CATALOGUE_CSV")
                     or REPO_ROOT / "data" / "sources_catalogue.csv")
HEALTH_PATH = Path(os.environ.get("TIT_PRESS_HEALTH")
                   or REPO_ROOT / "data" / "national_press_health.json")

# A publisher serving a feed to the open web expects a browser. Several of these
# hosts 403 a bare `python-requests` outright — the same ModSecurity lesson the
# sibling tracker learned against its own host.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "TalentIntel/1.0 (+https://asktherecruiter.com)"
)

# The run keeps state, so it must know when it is only rehearsing: a dry run
# that overwrote the health ledger would report a rehearsal as the live picture.
ACCEPTS_DRY_RUN = True

# One feed can carry 2,000 entries (TechNode does). Taking all of them would let
# a single archive-heavy publisher fill the whole candidate cap and starve the
# other hundred-odd feeds, which is the exact trap `fair_share` exists to catch
# one stage later. A feed read twice a day cannot produce 25 new stories per run
# for long, so this loses nothing real.
MAX_ITEMS_PER_FEED = 25

# Politeness. Feeds are cheap for a publisher to serve, but a host carrying
# several of our feeds (bizjournals, di.se, techinasia) must not be hit back to
# back. Enforced PER HOST, so a hundred distinct hosts cost a hundred fetches
# and no sleeping at all.
PER_HOST_PAUSE = 2.0
TIMEOUT = 25

# A feed can answer 200, parse cleanly, hand over 25 items, and still be dead:
# NoCamels does exactly that, and its newest entry is from October 2024. Every
# status check that only asks "did it respond" calls that healthy, so Israel
# would carry a source that has published nothing in 21 months and nothing would
# ever say so. Staleness is the quieter half of a dead feed.
#
# Two thresholds, because the sibling's health digest learned this the hard way:
# a QUARTERLY source is not a stale one. A national daily silent for six weeks
# is broken; a government agency announcing a programme twice a year is not.
STALE_AFTER_DAYS = 45
STALE_AFTER_DAYS_AGENCY = 150
_AGENCY_TYPES = frozenset({
    "Government Agency", "Government Programme", "Government Open Data",
    "Government Portal", "Government Program", "Innovation Hub",
    "Government-Supported Organization", "Regulator", "Central Bank",
})

# Rule: aggregators are discovery pointers, never stored sources. validate.py
# blocks these at the point of storage; blocking them at the point of LOADING
# means a well-meaning CSV edit cannot even queue one up. Anything added to the
# catalogue whose feed lives on one of these hosts is refused with a loud line
# in the run log rather than silently skipped.
_AGGREGATOR_HOSTS = frozenset({
    "news.google.com", "news.yahoo.com", "flipboard.com", "msn.com",
    "www.msn.com", "feedburner.com", "feeds.feedburner.com",
    "dealroom.co", "app.dealroom.co", "crunchbase.com", "www.crunchbase.com",
    "tracxn.com", "www.tracxn.com", "startupblink.com", "www.startupblink.com",
    "harmonic.ai", "www.harmonic.ai", "beauhurst.com", "www.beauhurst.com",
    "fundup.co", "www.fundup.co", "magnitt.com", "www.magnitt.com",
    "startupnationcentral.org", "www.startupnationcentral.org",
    "techireland.org", "www.techireland.org",
})

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"
NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"

STATS = {"feeds": 0, "ok": 0, "dead": 0, "blocked": 0,
         "items": 0, "duplicate_url": 0, "syndicated": 0}

# Filled by collect(). One entry per feed, whatever happened to it.
FEED_HEALTH: list[dict] = []


@dataclass(frozen=True)
class Feed:
    name: str
    rss: str
    country: str
    city: str
    coverage: str
    language: str
    source_type: str

    @property
    def host(self) -> str:
        return (urlparse(self.rss).hostname or "").lower()

    @property
    def is_regional(self) -> bool:
        return self.coverage.strip().lower() in ("regional", "global", "international")


# --- Earned cadence --------------------------------------------------------
#
# With 481 feeds wired, most of a run is spent on publishers that produce
# nothing we keep. Dead weight should cost nothing — not budget, and not much
# free compute either — so a feed earns its polling rate.
#
# A feed that has produced no NEW item for this many consecutive runs drops to
# a periodic probe. It is never dropped: the probe still fetches and still
# reports health, so a feed that went quiet because it BROKE is still detected,
# which is the whole failure this collector exists to make visible. It simply
# stops being fetched twice a day to prove the same silence.
#
# 14 runs is a week at the 2/day schedule. A weekly trade publication or a
# quarterly agency therefore reaches probe cadence and stays perfectly healthy.
QUIET_RUNS_BEFORE_PROBE = 14
PROBE_EVERY = 14


def _previous_ledger() -> dict:
    try:
        return json.loads(HEALTH_PATH.read_text())
    except (OSError, ValueError):
        return {}


def due_this_run(feed_name: str, previous: dict, run_number: int) -> bool:
    """Whether a feed is polled this run, or is resting between probes."""
    record = previous.get(feed_name)
    if not record:
        return True
    quiet = int(record.get("quiet_runs") or 0)
    if quiet < QUIET_RUNS_BEFORE_PROBE:
        return True
    # Staggered by name so the quiet feeds do not all probe on the same run.
    return (run_number + (hash(feed_name) % PROBE_EVERY)) % PROBE_EVERY == 0


def load_feeds(path: Path | None = None) -> list[Feed]:
    """Every catalogue row carrying an http(s) feed URL, aggregators refused.

    The catalogue IS the configuration. There is no second list to keep in sync,
    which is the whole point: the CTech miss happened because the fact lived in
    one place and the behaviour in another.
    """
    target = Path(path or CATALOGUE_CSV)
    if not target.exists():
        return []

    feeds: list[Feed] = []
    with target.open(newline="") as fh:
        for row in csv.DictReader(fh):
            rss = (row.get("rss") or "").strip()
            if not rss.startswith("http"):
                continue
            host = (urlparse(rss).hostname or "").lower()
            if host in _AGGREGATOR_HOSTS:
                STATS["blocked"] += 1
                print(f"  [{COLLECTOR}] REFUSED aggregator feed: "
                      f"{row.get('name','?')} ({host}) — we store publishers, not compilers")
                continue
            feeds.append(Feed(
                name=(row.get("name") or host).strip(),
                rss=rss,
                country=(row.get("country") or "").strip(),
                city=(row.get("city") or "").strip(),
                coverage=(row.get("coverage") or "").strip(),
                language=(row.get("language") or "").strip(),
                source_type=(row.get("source_type") or "").strip(),
            ))
    return feeds


# --- Parsing ---------------------------------------------------------------

# Real feeds in the wild are not well-formed. Maddyness appends a WordPress
# debug notice AFTER </rss>, which makes a strict parse raise "junk after
# document element" and read as a dead feed — a whole country lost to a trailing
# newline. Cutting at the closing root tag recovered it, and costs nothing when
# the document is already clean.
_ROOT_CLOSE = re.compile(rb"</(?:rss|feed|rdf:RDF)\s*>")
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


# The other everyday malformation: a bare `&` inside an attribute, which is
# what a CMS produces when it pastes a tag URL containing an ampersand without
# escaping it. Diario Libre's front-page feed carries 191 of them and dies at
# the first; its economy feed is clean, so the outlet looks half-broken for a
# reason that has nothing to do with the outlet. This matches an `&` that does
# NOT begin a valid entity, so a well-formed document contains none of them and
# is never touched.
_BARE_AMP = re.compile(
    rb"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,10}|#[0-9]{1,6}|#x[0-9a-fA-F]{1,6});)")


# The same failure at the LEADING edge. IO+ / Innovation Origins serves two XML
# declarations back to back — `<?xml ...?><?xml version="1.0"?>` — and a strict
# parse dies at byte 38, so a healthy Dutch publisher with 20 current items
# including funding rounds reads as a dead feed. Trimming only the tail (which
# is what Maddyness needed) does not touch it.
_LEADING_DECL = re.compile(rb"^\s*(?:<\?xml[^>]*\?>\s*)+")


def _tidy(raw: bytes) -> bytes:
    """Trim a BOM, any leading junk or duplicated XML declaration, and anything
    after the closing root tag."""
    body = raw.lstrip(b"\xef\xbb\xbf").lstrip()

    # Keep at most ONE declaration, and only if the document opens with them.
    match = _LEADING_DECL.match(body)
    if match and body[match.start():match.end()].count(b"<?xml") > 1:
        body = body[match.end():].lstrip()

    # Anything before the first tag is not XML and never was.
    first = body.find(b"<")
    if first > 0:
        body = body[first:]

    match = None
    for match in _ROOT_CLOSE.finditer(body):
        pass
    return body[:match.end()] if match else body


def _repair(body: bytes) -> bytes:
    """Escape bare ampersands. Only ever reached AFTER a strict parse failed,
    so a valid feed is parsed exactly as served and never rewritten."""
    return _BARE_AMP.sub(b"&amp;", body)


def _text(node, *tags: str) -> str:
    for tag in tags:
        el = node.find(tag)
        if el is not None:
            if el.text and el.text.strip():
                return el.text.strip()
            # Atom puts the article link in an attribute, not in the body.
            href = el.get("href")
            if href:
                return href.strip()
    return ""


# What the paid gate actually reads. The gate is charged per token and runs on
# EVERY candidate, so this number is the main lever on the bill once 481 feeds
# are wired: at ~4 chars per token, 400 characters of teaser plus a headline and
# a one-line dateline is roughly 120 tokens, against ~250 at the original 700.
#
# Deliberately not smaller. The teaser is where a funding figure usually sits
# when the headline omits it ("Enigma closes seed round" / "...$71m led by
# Greenfield"), and the whole reason the funding pillar exists is that those
# figures are readable for free. Trimming to a bare headline would save a
# fraction of a cent per run and cost the numbers.
TEASER_CHARS = 400


def _plain(html: str, limit: int = TEASER_CHARS) -> str:
    """Feed summaries are HTML. The classifier reads prose, and leaving the
    markup in wastes tokens on `<img srcset=...>` in every single candidate."""
    text = _TAGS.sub(" ", html or "")
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&#8217;", "’").replace("&quot;", '"')
                .replace("&lt;", "<").replace("&gt;", ">"))
    return _WS.sub(" ", text).strip()[:limit]


def dateline(feed: Feed) -> str:
    """The publisher's own country, as CONTEXT.

    Never written to raw["country"]: validate.py would take that as sourced, and
    a Brazilian outlet reporting a US round would file the US job under Brazil.
    A regional publisher says it is regional rather than naming its home
    country, because claiming Bulgaria for a Romanian story is the same mistake
    one step removed.
    """
    if feed.is_regional or not feed.country:
        return f"(Outlet: {feed.name}, regional — its base does not place the story.)"
    seat = f"{feed.city}, {feed.country}" if feed.city else feed.country
    return f"(Outlet: {feed.name}, based in {seat} — a hint, not a stated fact.)"


def parse(payload: bytes, feed: Feed) -> list[dict]:
    """Parse one feed body into raw candidate dicts.

    Separate from fetch() so every test runs offline against a recorded fixture.
    """
    body = _tidy(payload)
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        # Repair and retry ONCE. A publisher's feed being invalid is not a
        # reason to lose the publisher, and both repairs here are no-ops on a
        # document that was already well-formed.
        try:
            root = ET.fromstring(_repair(body))
        except ET.ParseError:
            return []

    nodes = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    line = dateline(feed)
    items: list[dict] = []

    # Some publishers put a relative slug in <link> ("/business/acme-raises").
    # Dropping those loses the outlet; storing them verbatim is worse, because
    # every figure here is meant to link to the document that makes the claim
    # and a relative URL links to nothing. Resolve against the channel's own
    # declared home page, falling back to the feed's host.
    # `find` returns an element whose truth value is its child count, so the
    # obvious `root.find("channel") or root` treats a childless <channel> as
    # missing. Compare against None explicitly.
    channel = root.find("channel")
    base = _text(channel if channel is not None else root, "link", f"{ATOM}link")
    if not base.startswith("http"):
        base = feed.rss

    for node in nodes:
        # Capped on ITEMS KEPT, not on nodes read: a feed padded with entries
        # that carry no link would otherwise yield less than its share purely
        # because its junk was counted against it.
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
        title = _text(node, "title", f"{ATOM}title")
        link = _text(node, "link", f"{ATOM}link", "guid")
        if link and not link.startswith("http"):
            link = urljoin(base, link)
        if not (title and link.startswith("http")):
            continue

        body = _plain(_text(node, "description", f"{CONTENT}encoded",
                            f"{ATOM}summary", f"{ATOM}content"))

        items.append({
            # The classifier reads ONLY this. A collector that forgets it posts
            # zero records and says nothing about it (spec 6 rule 2).
            "raw_text": f"{title}\n\n{body}\n\n{line}".strip(),
            "headline": title,
            # The publisher's own article URL, exactly as the publisher stated
            # it. This is the receipt.
            "source_url": link,
            "discovery_url": link,
            "source_name": feed.name,
            # Publishers disagree about which element carries the date: KED
            # Global uses dc:publishDate, Digital Business KZ uses
            # news:publication_date, Atom uses published/updated. A
            # pubDate-only reader calls all of those dateless.
            #
            # When NOTHING dates an item (Nikkei Asia, Sixth Tone, Kathmandu
            # Post, Maldives Financial Review), this stays empty and the record
            # is stored with published_date NULL. It is deliberately NOT
            # stamped with the collection time: that would file last month's
            # article as today's news and quietly corrupt every period column.
            "published_date": _text(node, "pubDate", f"{ATOM}published",
                                    f"{ATOM}updated", f"{DC}date",
                                    f"{DC}publishDate", f"{NEWS}publication_date",
                                    "date", "published"),
            "language": feed.language,
            # Reporting only ("how many countries did this run reach?"). NOT
            # `country`: validate.py would read that as sourced.
            "source_country": feed.country,
            "query": feed.name,
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    return items


# --- Fetching --------------------------------------------------------------

def fetch(feed: Feed, *, timeout: int = TIMEOUT, session=None) -> list[dict]:
    """Fetch one feed. Raises requests.RequestException upward so collect() can
    record WHY a feed produced nothing."""
    http = session or requests
    resp = http.get(feed.rss, headers={
        "User-Agent": USER_AGENT,
        "Accept": ("application/rss+xml, application/atom+xml, "
                   "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"),
    }, timeout=timeout)
    resp.raise_for_status()
    return parse(resp.content, feed)


def newest_item_age_days(items: list[dict], now=None) -> int | None:
    """Age in days of the most recent DATED item, or None if none is dated.

    Reuses validate._normalize_date so a feed's date is read exactly the way the
    stored record's would be — a second date parser would eventually disagree
    with the first, and the disagreement would show up as phantom staleness.
    """
    from pipeline.validate import _normalize_date

    today = (now or datetime.now(timezone.utc)).date()
    dates = []
    for item in items:
        parsed = _normalize_date(item.get("published_date"))
        if parsed:
            dates.append(datetime.strptime(parsed, "%Y-%m-%d").date())
    if not dates:
        return None
    return (today - max(dates)).days


# --- robots.txt ------------------------------------------------------------
#
# A publisher's robots.txt is that publisher telling us their terms, and routing
# around it is exactly how a product whose only asset is credibility loses it.
# So this is checked in code rather than trusted to a one-time audit: a feed
# added to the catalogue next month is covered without anybody remembering to
# re-run anything.
#
# The first audit over 112 feeds found EIGHT disallowed, three of which had been
# sitting in the catalogue since before this collector existed (Finextra,
# Tech.eu, UKTN) and two of which are the paywalled publishers whose licensing
# was under discussion anyway (Tech in Asia and its Indonesian edition, both
# `Disallow: */feed$`).
#
# Fails OPEN on a network error or a missing robots.txt, which is the standard
# reading: no robots.txt means no restriction. It fails CLOSED only on an
# explicit Disallow, because that is the publisher having actually spoken.
_ROBOTS_CACHE: dict[str, object] = {}


def robots_allows(url: str, *, session=None) -> bool:
    """Whether the publisher's robots.txt permits fetching this feed."""
    from urllib.robotparser import RobotFileParser

    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _ROBOTS_CACHE:
        parser = None
        try:
            resp = (session or requests).get(
                f"{origin}/robots.txt",
                headers={"User-Agent": USER_AGENT}, timeout=15)
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
        except requests.RequestException:
            parser = None
        _ROBOTS_CACHE[origin] = parser

    parser = _ROBOTS_CACHE[origin]
    if parser is None:
        return True
    return parser.can_fetch("*", url) and parser.can_fetch("TalentIntel", url)


_PUNCT = re.compile(r"[^\w]+", re.UNICODE)


def title_key(title: str) -> str:
    """Syndication key, borrowed wholesale from the GDELT collector.

    One wire item reaching us through eight national outlets is eight URLs and
    one story. De-duplicating on the title here is free; discovering it after
    the classifier has read all eight is not."""
    return _PUNCT.sub("", (title or "").lower())[:90]


def collect(queries=None, *, dry_run: bool = False, feeds: list[Feed] | None = None,
            session=None, pause: float = PER_HOST_PAUSE) -> list[dict]:
    """Read every catalogue feed once, de-duplicating by URL and by title.

    `queries` is accepted and ignored: this source has no search vocabulary, the
    catalogue IS the population. run_collect passes it positionally to every
    collector.
    """
    for key in STATS:
        STATS[key] = 0
    FEED_HEALTH.clear()

    feed_list = feeds if feeds is not None else load_feeds()
    STATS["feeds"] = len(feed_list)
    print(f"[{COLLECTOR}] {len(feed_list)} feeds from {CATALOGUE_CSV.name}")

    previous_run = _previous_ledger()
    previous = {r["name"]: r for r in previous_run.get("by_feed", [])}
    run_number = int(previous_run.get("run_number") or 0) + 1

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []
    last_hit: dict[str, float] = {}

    for feed in feed_list:
        if not due_this_run(feed.name, previous, run_number):
            # Resting, not forgotten: carry the previous verdict forward so the
            # ledger stays complete and a quiet feed keeps its history.
            record = dict(previous[feed.name])
            record.update(status="resting", items=0, new=0,
                          detail=f"quiet for {record.get('quiet_runs')} runs, "
                                 f"probing every {PROBE_EVERY}")
            FEED_HEALTH.append(record)
            continue

        # Politeness, per host rather than per request: two feeds on one
        # publisher wait, a hundred feeds on a hundred hosts do not.
        host = feed.host
        elapsed = time.monotonic() - last_hit.get(host, 0.0)
        if host in last_hit and elapsed < pause:
            time.sleep(pause - elapsed)

        record = {"name": feed.name, "country": feed.country, "url": feed.rss,
                  "status": "ok", "items": 0, "new": 0, "detail": ""}

        if not robots_allows(feed.rss, session=session):
            record.update(status="robots",
                          detail="robots.txt disallows this path — the publisher's "
                                 "own terms, so we do not fetch it")
            STATS["dead"] += 1
            FEED_HEALTH.append(record)
            last_hit[host] = time.monotonic()
            continue

        try:
            items = fetch(feed, session=session)
        except requests.HTTPError as exc:
            code = getattr(exc.response, "status_code", "?")
            record.update(status="dead", detail=f"HTTP {code}")
            items = []
        except requests.RequestException as exc:
            record.update(status="dead", detail=type(exc).__name__)
            items = []
        finally:
            last_hit[host] = time.monotonic()

        record["items"] = len(items)
        if record["status"] == "ok" and not items:
            # A feed that answers 200 with nothing we can parse is broken in the
            # way that hides best: the request succeeded. Say so explicitly.
            record.update(status="empty", detail="200 but no parseable items")
        elif record["status"] == "ok":
            age = newest_item_age_days(items)
            limit = (STALE_AFTER_DAYS_AGENCY if feed.source_type in _AGENCY_TYPES
                     else STALE_AFTER_DAYS)
            record["newest_days"] = age
            if age is not None and age > limit:
                record.update(
                    status="stale",
                    detail=f"newest item is {age}d old (limit {limit}d) — "
                           f"the feed answers but the publisher has stopped")

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
            record["new"] += 1

        # Yield history, which is what earns the polling rate.
        was_quiet = int((previous.get(feed.name) or {}).get("quiet_runs") or 0)
        record["quiet_runs"] = 0 if record["new"] else was_quiet + 1

        STATS["items"] += record["items"]
        STATS[record["status"] if record["status"] == "ok" else "dead"] += 1
        FEED_HEALTH.append(record)

    _report(dry_run, run_number)
    return out


def _report(dry_run: bool, run_number: int = 0) -> None:
    """Per-feed health, loudly. A dead feed among a hundred live ones is exactly
    the failure that hides inside an aggregate: the collector still returns
    hundreds of items and still reports `ok`, so nothing ever says that Israel
    went dark three weeks ago."""
    resting = [r for r in FEED_HEALTH if r["status"] == "resting"]
    dead = [r for r in FEED_HEALTH if r["status"] not in ("ok", "resting")]
    if resting:
        print(f"[{COLLECTOR}] {len(resting)} feeds resting (quiet for "
              f"{QUIET_RUNS_BEFORE_PROBE}+ runs, probed every {PROBE_EVERY})")
    print(f"[{COLLECTOR}] {STATS['ok']} feeds live, {len(dead)} not answering, "
          f"{STATS['items']} items, {STATS['duplicate_url']} duplicate URLs, "
          f"{STATS['syndicated']} syndicated copies dropped")

    if dead:
        print(f"[{COLLECTOR}] feeds needing attention:")
        for r in sorted(dead, key=lambda r: (r["country"], r["name"])):
            print(f"    {r['status'].upper():6s} {r['country'][:16]:18s} "
                  f"{r['name'][:32]:34s} {r['detail']}")

    reached = Counter(r["country"] for r in FEED_HEALTH
                      if r["status"] == "ok" and r["country"])
    print(f"[{COLLECTOR}] countries reached: {len(reached)} "
          f"({', '.join(f'{c}:{n}' for c, n in reached.most_common(8))} ...)")

    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_number": run_number,
        "feeds": STATS["feeds"], "live": STATS["ok"], "dead": len(dead),
        "resting": len(resting),
        "items": STATS["items"], "blocked_aggregators": STATS["blocked"],
        "by_feed": sorted(FEED_HEALTH, key=lambda r: r["name"].lower()),
    }
    if dry_run:
        print(f"[{COLLECTOR}] ledger NOT written (dry run) — {HEALTH_PATH}")
        return
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"[{COLLECTOR}] per-feed ledger written to {HEALTH_PATH}")
