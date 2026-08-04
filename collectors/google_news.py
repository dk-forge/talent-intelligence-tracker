"""Google News RSS collector.

Free, keyless, unthrottled, and the single best discovery source: headlines
carry the figures even when the article is paywalled. The sibling replaced a
$449/month news API with this and coverage went up (spec 5).

**Resolving the real article URL.** Google wraps every link in an encoded
`news.google.com/rss/articles/CBMi...` redirect, and following it does not
resolve. It is tempting to conclude the publisher URL is unrecoverable — that
conclusion was reached here once and it was wrong. Google exposes its own
resolution endpoint: the article page carries a signature and timestamp, and
posting those to `batchexecute` returns the publisher URL.

That matters because a homepage is not a receipt. With resolution working, what
we store is the article that makes the claim (spec 2 rule 5); without it,
nothing from this collector is storable at all.

**The edition places the story, and we were throwing that away.** Measured on
2026-08-01 over every current row:

    collector        pillar                rows   no country
    google_news      company_development    382   81.4%
    national_press   company_development    118   35.6%
    gdelt            company_development    130    5.4%
    google_news      ALL                    999   70.6%

The other two collectors are not better at geography, they are better INFORMED
about it: `national_press` folds the publisher's own country into `raw_text` as
a dateline and `gdelt` does the same with `sourcecountry`. This one passed no
geography at all, and it is the collector that had it for free the whole time —
we do not read "Google News", we read the `gl=BR` edition of it, and the
country is chosen by us at fetch time.

One of the 311 unplaced funding rows is literally "Enigma Raises $71M", the
headline `collectors/national_press.py`'s docstring was written about.

Passed the same way and with the same discipline: folded into `raw_text` as
CONTEXT, never written to `raw["country"]`. validate.py treats that field as a
sourced value, so writing it there would file a story under whichever edition
happened to surface it.

**And only for the editions where `gl=` actually selects a place.** Measured on
the same day, an ENGLISH edition returns almost nothing published in its own
country, while every non-English edition is majority-local. See
_LANGUAGE_ONLY_EDITIONS below for the numbers and the hand-read behind them. A
dateline on an English edition would put a wrong country on the rows this
change exists to fix, and a wrong country is worse than an empty one because
nothing downstream can tell it from a right one.

**The rotation followed the same finding on 2026-08-01.** All seventeen English
non-US editions were withdrawn from source_registry.GOOGLE_NEWS_LOCALES; the
en-US anchor is the only English edition left. The measurement, per edition, is
recorded beside WITHDRAWN_ENGLISH_EDITIONS in that file, and
`analysis/editions/measure.py` re-runs it for free.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from collectors import capped_fetch

RSS_ENDPOINT = "https://news.google.com/rss/search"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
COLLECTOR = "google_news"


#: Editions whose `gl=` is a LANGUAGE selector and not a geography one.
#:
#: MEASURED 2026-08-01, live, on the leadership query alone. Every English
#: edition returned the SAME result set as en-US:
#:
#:     en-GB   47 items   100.0% also in the en-US result set
#:     en-IE   47 items   100.0%
#:     en-SG   47 items   100.0%
#:     en-IN   47 items    97.9%
#:     en-ZA   47 items    97.9%
#:     de-DE  100 items     0.0%
#:     pt-BR   47 items     0.0%
#:     es-MX   64 items     0.0%
#:     fr-FR   50 items     0.0%
#:     ja-JP   47 items     0.0%
#:
#: RE-MEASURED THE SAME DAY ON THE FULL FIVE-QUERY PRODUCTION PACK, and the
#: 100% does NOT hold: at ~375 items per edition the English editions overlap
#: en-US by 62-70%, not 100 (en-BD and en-HK are the two exceptions, still at
#: 99.7%, which is the run's churn floor). The one-query figures above are left
#: standing because they are what was measured, and because correcting the
#: number does not move the conclusion an inch: the ~35% that DIFFERS is the
#: same global English wire re-ranked. On the publisher test — how many of an
#: edition's items come from a publisher in that edition's own country — the
#: English editions score 0.0-11.5% and the non-English ones 49.0-67.7%. The
#: full table is in source_registry.WITHDRAWN_ENGLISH_EDITIONS.
#:
#: `gl=GB` with an English query is not a British edition in any useful sense.
#: A hand-read of the 14 items it returned put ONE British employer among them
#: (Restore plc); the other thirteen were Cracker Barrel, Hormel, Conagra, The
#: Toro Company, Iowa 80, Chemquest, Apple, BBVA Mexico and Banijay. The
#: en-IN edition scored 2 of 14. The pt-BR edition scored 12 of 14.
#:
#: So a dateline on an English edition would tag one identical Cracker Barrel
#: story as British, Indian, Irish, Singaporean and South African across five
#: runs — and it would do it hardest on exactly the rows this change exists to
#: fix, the ones carrying no other geography. That is worse than the empty
#: country field it replaces: an absent value is honestly absent, and a wrong
#: one is indistinguishable from a right one on the page.
#:
#: en-US is in here too. It is the same global English wire seen from its
#: largest market, and its 47 items included BBVA Mexico, Banijay and an
#: Australian data-centre operator.
#:
#: This is not a claim about English-speaking markets being uninteresting. It
#: is a measurement of what `gl=` does, and it says the seventeen English
#: non-US editions in source_registry.GOOGLE_NEWS_LOCALES were fetching the
#: anchor edition again under another name. That separate fix landed the same
#: day: they are withdrawn, their markets are read through their own
#: publishers' feeds instead, and the rule below is unchanged either way — the
#: en-US anchor is still an edition whose `gl=` selects a language.
_LANGUAGE_ONLY_EDITIONS = frozenset({"en"})


def edition_dateline(country_code: str, lang: str = "") -> str:
    """The edition we queried, as CONTEXT for the classifier.

    Empty for an edition whose `gl=` selects a language rather than a place
    (see _LANGUAGE_ONLY_EDITIONS above), and empty for a country the vocabulary
    does not hold — a hint the model cannot act on is a hint the token budget
    pays for and nothing acts on.

    Deliberately worded as the EDITION and not as the outlet's home. Those are
    different claims and only one of them is true: `gl=BR` is where we aimed
    the question, and Brazil's edition still carries stories about elsewhere
    (2 of the 14 measured). Saying "based in Brazil" would be the collector
    asserting something it does not know, which is the failure the
    never-write-raw["country"] rule exists to prevent, one layer up.
    """
    from pipeline import vocab

    if (lang or "").strip().lower() in _LANGUAGE_ONLY_EDITIONS:
        return ""
    code = (country_code or "").strip().upper()
    name = vocab.COUNTRY_NAMES.get(code)
    if not name:
        return ""
    return (f"(Google News {name} edition — the edition queried, "
            f"a hint, not a stated fact.)")


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
    resp, body = capped_fetch.capped_get(
        build_query_url(query, lang=lang, country=country),
        headers={"User-Agent": USER_AGENT}, timeout=timeout,
        max_bytes=capped_fetch.FEED_BYTES)
    resp.raise_for_status()
    return parse(body, query, country=country, lang=lang)


def parse(xml_bytes: bytes, query: str = "", *, country: str = "",
          lang: str = "") -> list[dict]:
    """Parse an RSS payload into raw dicts.

    Kept separate from fetch() so tests run offline against captured fixtures.

    `country` is the edition's ISO code and `lang` its language. Both default
    to empty rather than to "US"/"en" on purpose: a caller that forgets to pass
    them should add no dateline, not silently label every item American.
    """
    root = ET.fromstring(xml_bytes)
    items = []
    line = edition_dateline(country, lang)

    for node in root.findall(".//item"):
        title = _text(node, "title")
        link = _text(node, "link")
        if not (title and link):
            continue

        source_el = node.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        source_url = (source_el.get("url") or "").strip() if source_el is not None else ""

        items.append({
            # The edition's own code, kept out of raw["country"] on purpose
            # (see the module docstring). Carried so a later stage can tell
            # which edition surfaced a story without re-deriving it.
            "edition_country": (country or "").strip().upper(),
            # raw_text is what the classifier reads. A collector that forgets
            # this posts zero records forever (spec 6 rule 2). The dateline is
            # appended rather than prepended so the headline still leads: the
            # gate reads the first tokens hardest, and the country is context.
            "raw_text": "\n\n".join(
                part for part in (title, _text(node, "description"), line)
                if part
            ).strip(),
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


# Google's resolution endpoint expects a browser. Its own UA rule (a descriptive
# agent for the WP host) does not apply here.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

_SIG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')
# The URL comes back inside an escaped JSON string, so the naive
# `"(https?://[^"]+)"` match stops at the backslash and finds nothing.
_RESOLVED = re.compile(r'garturlres\\",\\"(https?://[^\\"]+)')


def article_id(discovery_url: str) -> str:
    return discovery_url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def resolve_source_url(item: dict, *, timeout: int = 20, session=None) -> dict:
    """Recover the publisher's own URL from Google's encoded redirect.

    Two steps: read the signature and timestamp off the article page, then ask
    Google's batchexecute endpoint to resolve the id. Best effort — on failure
    the item keeps the outlet homepage from the RSS <source> element, and
    validate.py rejects it as a bare domain rather than crediting Google.
    """
    url = item.get("discovery_url") or ""
    if "news.google.com" not in url:
        return item

    http = session or requests
    try:
        aid = article_id(url)
        page = http.get(f"https://news.google.com/rss/articles/{aid}",
                        headers={"User-Agent": BROWSER_UA}, timeout=timeout)
        sig, ts = _SIG.search(page.text), _TS.search(page.text)
        if not (sig and ts):
            return item

        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
              None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            aid, int(ts.group(1)), sig.group(1),
        ])
        resp = http.post(
            BATCH_URL,
            headers={"User-Agent": BROWSER_UA,
                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data={"f.req": json.dumps([[["Fbv4je", inner]]])},
            timeout=timeout,
        )
        hit = _RESOLVED.search(resp.text)
        if hit:
            item["source_url"] = hit.group(1)
    except (requests.RequestException, ValueError, IndexError):
        pass
    return item


def collect(queries: list[str], *, pause: float = 1.0,
            locales: list[tuple[str, str]] | None = None,
            queries_for=None) -> list[dict]:
    """Fetch every query in every locale, de-duplicating by URL within the run.

    `locales` is a list of (lang, country) pairs selecting the Google News
    edition. Asking the US English edition about hiring in Germany returns
    whatever US outlets happened to cover it, which is almost nothing, so a
    global product has to ask each edition itself. Every request is still
    keyless and free, which is why worldwide coverage costs nothing.

    Dedup happens here, before anything paid runs: the same story surfaces in
    several editions and the same URL must not be classified twice.

    Deliberately does NOT resolve redirects. Resolution costs a full HTTP round
    trip per item, so it belongs after the free filters have thrown most
    candidates away — see the ordering in run_collect.py.
    """
    seen: set[str] = set()
    out: list[dict] = []

    for lang, country in (locales or [("en", "US")]):
        # Each edition asks in its own language. Reusing the English phrases
        # everywhere returned 2 items from the German edition against 20 for
        # the German phrasing, and 0 from Brazil.
        for query in (queries_for(lang) if queries_for else queries):
            try:
                items = fetch(query, lang=lang, country=country)
            except requests.RequestException:
                # One edition being unreachable must not lose the other forty.
                continue
            for item in items:
                key = item["discovery_url"]
                if key in seen:
                    continue
                seen.add(key)
                item["locale"] = f"{country}:{lang}"
                out.append(item)
            time.sleep(pause)

    return out


def _text(node, tag: str) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""
