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
from urllib.parse import urlparse

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


def _tidy(raw: bytes) -> bytes:
    body = raw.lstrip(b"\xef\xbb\xbf").lstrip()
    match = None
    for match in _ROOT_CLOSE.finditer(body):
        pass
    return body[:match.end()] if match else body


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


def _plain(html: str, limit: int = 700) -> str:
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
        where = f"a {feed.coverage.lower() or 'regional'} publication" if feed.coverage else "a regional publication"
        return (f"(Feed record: reported by {feed.name}, {where}. "
                f"The publisher's location does not establish the story's location.)")
    seat = f"{feed.city}, {feed.country}" if feed.city else feed.country
    return (f"(Feed record: reported by {feed.name}, a national publication based in "
            f"{seat}. This is where the OUTLET is, which is a hint about the story "
            f"and not a statement of fact about it.)")


def parse(payload: bytes, feed: Feed) -> list[dict]:
    """Parse one feed body into raw candidate dicts.

    Separate from fetch() so every test runs offline against a recorded fixture.
    """
    try:
        root = ET.fromstring(_tidy(payload))
    except ET.ParseError:
        return []

    nodes = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    line = dateline(feed)
    items: list[dict] = []

    for node in nodes:
        # Capped on ITEMS KEPT, not on nodes read: a feed padded with entries
        # that carry no link would otherwise yield less than its share purely
        # because its junk was counted against it.
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
        title = _text(node, "title", f"{ATOM}title")
        link = _text(node, "link", f"{ATOM}link")
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
            "published_date": _text(node, "pubDate", f"{ATOM}published",
                                    f"{ATOM}updated", "date"),
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

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []
    last_hit: dict[str, float] = {}

    for feed in feed_list:
        # Politeness, per host rather than per request: two feeds on one
        # publisher wait, a hundred feeds on a hundred hosts do not.
        host = feed.host
        elapsed = time.monotonic() - last_hit.get(host, 0.0)
        if host in last_hit and elapsed < pause:
            time.sleep(pause - elapsed)

        record = {"name": feed.name, "country": feed.country, "url": feed.rss,
                  "status": "ok", "items": 0, "new": 0, "detail": ""}
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

        STATS["items"] += record["items"]
        STATS[record["status"] if record["status"] == "ok" else "dead"] += 1
        FEED_HEALTH.append(record)

    _report(dry_run)
    return out


def _report(dry_run: bool) -> None:
    """Per-feed health, loudly. A dead feed among a hundred live ones is exactly
    the failure that hides inside an aggregate: the collector still returns
    hundreds of items and still reports `ok`, so nothing ever says that Israel
    went dark three weeks ago."""
    dead = [r for r in FEED_HEALTH if r["status"] != "ok"]
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
        "feeds": STATS["feeds"], "live": STATS["ok"], "dead": len(dead),
        "items": STATS["items"], "blocked_aggregators": STATS["blocked"],
        "by_feed": sorted(FEED_HEALTH, key=lambda r: r["name"].lower()),
    }
    if dry_run:
        print(f"[{COLLECTOR}] ledger NOT written (dry run) — {HEALTH_PATH}")
        return
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"[{COLLECTOR}] per-feed ledger written to {HEALTH_PATH}")
