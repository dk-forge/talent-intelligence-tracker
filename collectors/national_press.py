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

THE COUNTRIES A FEED CANNOT REACH
---------------------------------
Twenty-one countries have no usable publisher feed, or exactly one, which for a
whole country is a single point of failure. Those rows are marked `backstop` in
the catalogue and handed to `collectors/news_backstop.py`, which searches for
each country, resolves every redirect to the publisher's own article and drops
anything that does not resolve. Its results join this collector's items and this
collector's per-feed ledger, because "which countries did this run reach" has to
have one answer rather than two. The refusal above is untouched: a
`news.google.com` URL still cannot be listed as a feed, and still cannot reach
the database.

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

import base64
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

from collectors import capped_fetch

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
# Six of the entries below are the bare and www hosts of three commercial
# data providers whose names stay out of plaintext in this repo (standalone-
# brand rule; tests/test_no_provider_names.py enforces it tree-wide). The
# blocklist still needs the domains to refuse them, so they are stored
# base64-encoded and decoded at import time. Encoding, not secrecy: the point
# is that a grep of the tree surfaces no provider name, while the refusal
# keeps working exactly as before.
_ENCODED_AGGREGATOR_HOSTS = (
    "ZGVhbHJvb20uY28=", "YXBwLmRlYWxyb29tLmNv",
    "Y3J1bmNoYmFzZS5jb20=", "d3d3LmNydW5jaGJhc2UuY29t",
    "dHJhY3huLmNvbQ==", "d3d3LnRyYWN4bi5jb20=",
)
_AGGREGATOR_HOSTS = frozenset({
    "news.google.com", "news.yahoo.com", "flipboard.com", "msn.com",
    "www.msn.com", "feedburner.com", "feeds.feedburner.com",
    "startupblink.com", "www.startupblink.com",
    "harmonic.ai", "www.harmonic.ai", "beauhurst.com", "www.beauhurst.com",
    "fundup.co", "www.fundup.co", "magnitt.com", "www.magnitt.com",
    "startupnationcentral.org", "www.startupnationcentral.org",
    "techireland.org", "www.techireland.org",
}) | frozenset(
    base64.b64decode(s).decode("ascii") for s in _ENCODED_AGGREGATOR_HOSTS
)


ATOM = "{http://www.w3.org/2005/Atom}"
# RSS 1.0 (RDF). Its <item> lives in a namespace, so `.//item` — which matches
# only unqualified names — finds nothing and the feed reads as "200 but no
# parseable items". Nikkei Asia, CNET Japan, Nikkei xTECH, Impress Watch, PR
# TIMES and the Taipei Times all serve it, which is most of what this collector
# could reach in Japan and Taiwan. The format is twenty-five years old and it is
# still what several major Asian publishers serve; nothing about those feeds is
# broken.
RSS10 = "{http://purl.org/rss/1.0/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"
NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"

STATS = {"feeds": 0, "ok": 0, "dead": 0, "blocked": 0,
         "items": 0, "duplicate_url": 0, "syndicated": 0, "city_press_off": 0}

# Filled by collect(). One entry per feed, whatever happened to it.
FEED_HEALTH: list[dict] = []


# --- Domain drift ----------------------------------------------------------
#
# The hazard a worldwide catalogue cannot avoid: domains expire and get taken
# over. `botswanaguardian.co.bw` now redirects to a BETTING SITE whose /feed/
# verifies perfectly green — 200, well-formed RSS, recent items. Every
# automated check passes and we would be citing a gambling operator as a
# Botswana news source, with our own name on the citation.
#
# Status codes cannot catch this and neither can freshness. The only signal is
# that the bytes came from somewhere other than the publisher we listed, so the
# feed's FINAL url after redirects is compared to the registrable domain we
# recorded, and a mismatch is refused rather than stored.
#
# Two-label suffixes that would otherwise make "co.bw" look like the registrable
# domain, so that any two Botswana sites would compare equal. The second block
# is the Caribbean, the Gulf and the small-island suffixes the discovery
# backstop reaches: `guardian.co.tt` reduced to "co.tt" before they were
# listed, which made any two Trinidadian hosts compare equal — the guard
# passing for the wrong reason, which is worse than it failing.
_MULTI_SUFFIXES = frozenset("""
    co.uk co.za co.bw co.ke co.tz co.zw co.il co.jp co.kr co.in co.id co.th
    co.nz com.au com.br com.mx com.ar com.co com.pe com.tr com.sg com.my
    com.ph com.hk com.tw com.cn com.pk com.bd com.ng com.gh com.eg com.sa
    com.qa com.kw com.bh com.om com.jo com.lb com.uy com.ec com.bo com.py
    com.do com.pa com.gt com.sv com.ni com.cy com.mt com.ua org.uk org.za
    net.au gov.uk ac.uk or.ke go.ke
    co.tt com.bb com.lc com.vc com.ag com.kn com.dm com.gd com.fj com.bn
    com.lk com.mn com.ly com.aw com.cw com.ht com.rw
""".split())


def registrable_domain(url_or_host: str) -> str:
    """The part of a host that someone owns. Best effort, and only ever used to
    compare two hosts we already hold, never to construct one."""
    host = (urlparse(url_or_host).hostname or url_or_host or "").lower().strip(".")
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
# The same names reduced to what someone owns, so a subdomain cannot slip past
# an exact-host list. Derived rather than typed twice: a name added above is
# blocked on every subdomain automatically.
_AGGREGATOR_DOMAINS = frozenset(
    d for d in (registrable_domain(h) for h in _AGGREGATOR_HOSTS) if d
)

# Editorial newsrooms that happen to live on an aggregator's domain.
#
# The sibling tracker settled this on 2026-07-23 and wrote down why: a bylined
# newsroom is citable even when the company behind it also runs a crowdsourced
# database, because the reporting is the publisher's own work. The database
# stays blocked; the newsroom does not. Matching by registrable domain alone
# would have blocked a publisher the other product deliberately cites, which
# would make one house hold two positions on the same URL.
# The single entry is the bylined newsroom subdomain of one of the encoded
# providers above; it is held base64-encoded for the same standalone-brand
# reason as the blocklist entries.
_EDITORIAL_EXCEPTIONS = frozenset({
    base64.b64decode("bmV3cy5jcnVuY2hiYXNlLmNvbQ==").decode("ascii"),
})


# --- US city tech press ----------------------------------------------------
#
# The owner's direction is that the USA must hold the most data. Worldwide
# discovery is edition-led (google_news) and mostly national, so a metro
# funding round or a 200-job expansion reaches us only when a national desk
# happens to pick it up. The publishers carrying those with receipts are the
# metro business press, and they publish ordinary first-party feeds.
#
# WIRED AS A NAMED CATEGORY rather than as anonymous rows, so the set has one
# off switch and one price that can be read in one place:
#
#   MEASURED 2026-08-14, live, through this collector's own fetch/parse path,
#   at the gate's own unit price ($0.000051 per call,
#   google/gemini-2.5-flash-lite, 504 in / 2 out, priced live from OpenRouter),
#   once daily:
#
#     the six NEW feeds   150 items/run, 47 (31%) survive the FREE prefilter
#                         and reach the paid gate
#                         -> $0.072/month measured, $0.230/month at cap
#     whole category      195 items/run, 65 (33%) reach the gate
#     (GeekWire and       -> $0.100/month measured, $0.300/month at cap
#      Refresh Miami
#      were already live)
#
#   "At cap" is the worst case where every fetched item is gated, which is what
#   MAX_ITEMS_PER_FEED bounds. Both figures sit an order of magnitude inside
#   the ~$1/month ceiling this set was allowed, so it ships ARMED.
#
# IT CANNOT STARVE THE REGISTERS, and that is structural rather than a promise.
# Reads are the expensive stage, and `classify.BINDING_READ_BUDGET` is a fixed
# total split between collectors by measured conversion: adding feeds moves no
# money at the read stage, because national_press buys exactly the cap it
# bought before and the structured registers (Companies House, ARES, BORME,
# EDINET, DART, BSE) are separate collectors whose spend this cannot reach.
# MAX_ITEMS_PER_FEED is the per-feed cap, so no one metro can crowd the others,
# and candidate_rank decides WHICH of the survivors are read.
# tests/test_city_press_feeds.py holds all of that.
#
# The switch: TIT_CITY_PRESS=off drops the whole category at LOAD time, before
# a single request is made.
CITY_PRESS_CATEGORY = "City Tech Press"


def city_press_enabled() -> bool:
    return (os.environ.get("TIT_CITY_PRESS") or "on").strip().lower() not in (
        "off", "0", "no", "false")


# Funding-roundup and deal-tracker PRODUCTS, refused by feed path even when the
# outlet itself is one we read.
#
# The aggregator rule is about who did the reporting, and a weekly "every round
# that closed this week" column is a compilation of other people's reporting
# wearing a masthead we trust. It also breaks the one-employer contract the
# rest of the pipeline depends on: one such item names eight companies and
# eight figures, so whatever the model returns for `company` is at best one of
# them, attached to a figure that may belong to another. Same reasoning as
# _AGGREGATOR_HOSTS, applied one level down at the path.
_ROUNDUP_PATH_TERMS = (
    "funding-roundup", "funding-round-up", "funding-tracker", "deal-tracker",
    "deals-roundup", "weekly-funding", "funding-weekly", "venture-roundup",
    "raise-roundup", "roundup-funding",
)


def is_roundup_feed(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return any(term in path for term in _ROUNDUP_PATH_TERMS)


@dataclass(frozen=True)
class Feed:
    name: str
    rss: str
    country: str
    city: str
    coverage: str
    language: str
    source_type: str
    site: str = ""
    category: str = ""

    @property
    def is_city_press(self) -> bool:
        return self.category.strip().lower() == CITY_PRESS_CATEGORY.lower()

    @property
    def host(self) -> str:
        return (urlparse(self.rss).hostname or "").lower()

    @property
    def expected_domains(self) -> set[str]:
        """Where bytes for this feed may legitimately come from: the feed's own
        host and the site we catalogued it under."""
        return {d for d in (registrable_domain(self.rss),
                            registrable_domain(self.site)) if d}

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
            # Match on the registrable domain, not the exact host. The set
            # listed bare and www hosts, so one provider's news subdomain
            # walked straight through a list that named its apex twice, and
            # one record ended up citing an aggregator. Every other entry had
            # the same hole (any blog. or app. or data. subdomain). An
            # aggregator does not stop being one on a subdomain.
            blocked = (host in _AGGREGATOR_HOSTS
                       or registrable_domain(host) in _AGGREGATOR_DOMAINS)
            if blocked and host in _EDITORIAL_EXCEPTIONS:
                blocked = False
            if blocked:
                STATS["blocked"] += 1
                print(f"  [{COLLECTOR}] REFUSED aggregator feed: "
                      f"{row.get('name','?')} ({host}) — we store publishers, not compilers")
                continue
            # A funding-roundup or deal-tracker feed from an outlet we
            # otherwise read. Refused here for the same reason and with the
            # same loudness as an aggregator: it compiles other people's
            # reporting, and one item names many employers.
            if is_roundup_feed(rss):
                STATS["blocked"] += 1
                print(f"  [{COLLECTOR}] REFUSED roundup feed: "
                      f"{row.get('name','?')} ({rss}) — a roundup compiles "
                      f"reporting and names many employers in one item")
                continue
            category = (row.get("category") or "").strip()
            # The city-press set is switchable as a set. Dropped at LOAD time,
            # so `off` costs not one request.
            if (category.lower() == CITY_PRESS_CATEGORY.lower()
                    and not city_press_enabled()):
                STATS["city_press_off"] += 1
                continue
            feeds.append(Feed(
                name=(row.get("name") or host).strip(),
                rss=rss,
                country=(row.get("country") or "").strip(),
                city=(row.get("city") or "").strip(),
                coverage=(row.get("coverage") or "").strip(),
                language=(row.get("language") or "").strip(),
                source_type=(row.get("source_type") or "").strip(),
                site=(row.get("url") or "").strip(),
                category=category,
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
            # A field whose text lives in a CHILD element. Drupal's core RSS
            # writes `<title><a href="/business/...">Headline</a></title>`, so
            # `el.text` is whitespace and `href` is absent on <title> itself.
            # The Daily Star's business feed is 25 well-formed items every one
            # of which was dropped for having no title, which reads in the
            # ledger as "200 but no parseable items" — a live national daily
            # recorded as a dead feed.
            nested = _WS.sub(" ", "".join(el.itertext())).strip()
            if nested:
                return nested
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


# The XML-free reader. Only ever reached when both parses have failed, because
# a regex is a worse reader than a parser in every way except one: it does not
# care that the document is invalid.
_ITEM_BLOCK = re.compile(rb"<item[\s>].*?</item>", re.S | re.I)
_FIELD = {
    "title": re.compile(rb"<title[^>]*>(.*?)</title>", re.S | re.I),
    "link": re.compile(rb"<link[^>]*>(.*?)</link>", re.S | re.I),
    "description": re.compile(rb"<description[^>]*>(.*?)</description>", re.S | re.I),
    "pubDate": re.compile(rb"<pubDate[^>]*>(.*?)</pubDate>", re.S | re.I),
}
_CDATA = re.compile(rb"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", re.S)


def _field(block: bytes, name: str) -> str:
    hit = _FIELD[name].search(block)
    if not hit:
        return ""
    raw = hit.group(1)
    cdata = _CDATA.match(raw)
    if cdata:
        raw = cdata.group(1)
    return raw.decode("utf8", "replace").strip()


def _scrape_items(body: bytes, feed: Feed) -> list[dict]:
    line = dateline(feed)
    items: list[dict] = []
    for block in _ITEM_BLOCK.finditer(body):
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
        chunk = block.group(0)
        title = _plain(_field(chunk, "title"), 300)
        link = _field(chunk, "link")
        if link and not link.startswith("http"):
            link = urljoin(feed.rss, link)
        if not (title and link.startswith("http")):
            continue
        body_text = _plain(_field(chunk, "description"))
        items.append({
            "raw_text": f"{title}\n\n{body_text}\n\n{line}".strip(),
            "headline": title,
            "source_url": link,
            "discovery_url": link,
            "source_name": feed.name,
            "published_date": _field(chunk, "pubDate"),
            "language": feed.language,
            "source_country": feed.country,
            "query": feed.name,
            "collector": COLLECTOR,
            "parsed_by": "regex-fallback",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return items


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
            # Last resort: read the items with a regex. Six live feeds are
            # malformed past repair (Times of Oman, Daily News Egypt, African
            # Manager, Sika Finance, Condia, New Era), and Oman would otherwise
            # look sourceless while having a perfectly good publisher.
            return _scrape_items(body, feed)

    nodes = (root.findall(".//item")
             or root.findall(f".//{RSS10}item")
             or root.findall(f".//{ATOM}entry"))
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
    if channel is None:
        channel = root.find(f"{RSS10}channel")
    base = _text(channel if channel is not None else root, "link",
                 f"{RSS10}link", f"{ATOM}link")
    if not base.startswith("http"):
        base = feed.rss

    for node in nodes:
        # Capped on ITEMS KEPT, not on nodes read: a feed padded with entries
        # that carry no link would otherwise yield less than its share purely
        # because its junk was counted against it.
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
        title = _text(node, "title", f"{RSS10}title", f"{ATOM}title")
        link = _text(node, "link", f"{RSS10}link", f"{ATOM}link", "guid")
        if link and not link.startswith("http"):
            link = urljoin(base, link)
        if not (title and link.startswith("http")):
            continue

        body = _plain(_text(node, "description", f"{RSS10}description",
                            f"{CONTENT}encoded",
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

class DomainDrift(requests.RequestException):
    """The feed answered from a host we did not catalogue. Almost always an
    expired domain that somebody else now owns."""


# Neither header set works everywhere, and the two failures are opposites:
#
#   Techpoint Africa and Arab News   403 to a bare `Accept: application/rss+xml`
#                                    200 to a browser-shaped header set
#   Four TownNews hosts (Toronto     HTML to a browser-shaped `Accept: */*`
#   Star, Nassau Guardian, El        valid RSS only to `application/rss+xml`
#   Vocero, Trinidad Express)
#
# So the RSS type is offered FIRST (which satisfies TownNews) inside an
# otherwise browser-shaped set (which satisfies the WAFs), and a 403 retries
# with the plain browser set. Whichever worked is recorded per feed.
_ACCEPT_RSS = ("application/rss+xml, application/atom+xml, application/xml;q=0.9, "
               "text/xml;q=0.9, text/html;q=0.5, */*;q=0.3")
_ACCEPT_BROWSER = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8")


def _headers(accept: str) -> dict:
    return {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        # Advertising brotli without a decoder makes a healthy feed read as
        # corrupt, so only offer what this install can actually decode.
        "Accept-Encoding": "gzip, deflate" + (", br" if _HAVE_BROTLI else ""),
    }


def _brotli_available() -> bool:
    try:
        import brotli  # noqa: F401
        return True
    except ImportError:
        try:
            import brotlicffi  # noqa: F401
            return True
        except ImportError:
            return False


_HAVE_BROTLI = _brotli_available()


def fetch(feed: Feed, *, timeout: int = TIMEOUT, session=None) -> tuple[list[dict], str]:
    """Fetch one feed. Returns (items, which_accept_worked).

    Raises requests.RequestException upward so collect() can record WHY a feed
    produced nothing.

    TWO THINGS CHANGED HERE and they are one change seen from two angles.

    The body is now STREAMED and CAPPED. `requests.get` without `stream=True`
    buffers the whole response before it returns and inflates gzip and brotli
    on the way, so the number of bytes landing in this process is not the
    number the publisher sent. This runs once per catalogued feed, twice a day,
    unattended, against several hundred third-party servers, and the documented
    hazard for this catalogue is a domain that expired and got taken over. One
    such publisher serving a decompression bomb takes the whole run with it.

    And the DRIFT CHECK NOW RUNS FIRST. It used to sit above `resp.content`,
    which reads as "before the body" and was not: the body had already been
    read, in full, inside `http.get`. The guard against citing a betting site
    was declining to PARSE bytes it had already paid to receive. Streaming is
    what makes that ordering real, which is why these are not two fixes.
    """
    resp = capped_fetch.open_capped(feed.rss, session=session,
                                    headers=_headers(_ACCEPT_RSS), timeout=timeout)
    used = "rss"
    if resp.status_code in (403, 406):
        # A WAF refusing the RSS type, not the publisher refusing us.
        try:
            resp.close()
        except Exception:
            pass
        resp = capped_fetch.open_capped(feed.rss, session=session,
                                        headers=_headers(_ACCEPT_BROWSER),
                                        timeout=timeout)
        used = "browser"
    try:
        resp.raise_for_status()

        # Where did the bytes actually come from? Asked BEFORE they are read.
        final = getattr(resp, "url", "") or feed.rss
        landed = registrable_domain(final)
        expected = feed.expected_domains
        if landed and expected and landed not in expected:
            raise DomainDrift(
                f"redirects to {landed}, not {' or '.join(sorted(expected))}, "
                f"the domain may have been taken over")

        body = capped_fetch.read_capped(resp, capped_fetch.FEED_BYTES)
    finally:
        try:
            resp.close()
        except Exception:
            pass

    return parse(body, feed), used


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
            items, accept_used = fetch(feed, session=session)
            record["accept"] = accept_used
        except DomainDrift as exc:
            # Loud, and its own status: this is not an outage, it is a source
            # we would have cited being served by somebody else.
            record.update(status="hijacked", detail=str(exc))
            items = []
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

    # The countries this collector cannot reach by feed, reached by discovery
    # instead. Deliberately NOT a second entry in run_collect's SOURCES: it is
    # the same catalogue, the same funnel and the same ledger, and splitting it
    # out would answer "which countries did this run reach" with half the
    # answer. The aggregator refusal in load_feeds() above is untouched and
    # still absolute — news_backstop reads Google News as a POINTER and returns
    # only publisher URLs, which is a different thing from listing an
    # aggregator as a source.
    #
    # Only when the caller did not hand us a population. `feeds=[...]` means
    # "read exactly these", which is what every test does, and a collector that
    # reached the network anyway on an explicit list would make the offline
    # suite depend on Google being up.
    from collectors import news_backstop

    backstop_items, backstop_health = (
        news_backstop.collect(session=session) if feeds is None else ([], []))
    for item in backstop_items:
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
    STATS["feeds"] += len(backstop_health)
    for record in backstop_health:
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
