"""Discovery backstop for countries with no direct publisher feed.

WHY THIS EXISTS
---------------
`collectors/national_press.py` reads publishers' own feeds and covers 139
countries that way. Twenty-one countries cannot be covered that way today:

  * Kuwait has no usable non-aggregator feed AT ALL. Seven national dailies
    publish HTML only and the state news agency times out.
  * Aruba, Curacao and Saint Kitts and Nevis have no working feed either.
  * Qatar, Oman, Rwanda, Libya, Fiji, Brunei, Laos, Mongolia, Sri Lanka,
    Nicaragua, Barbados, Haiti, Antigua and Barbuda, Saint Lucia, Dominica,
    Saint Vincent and the Grenadines and Grenada have exactly one, which is a
    single point of failure for a whole country.

The choice is between one Google News query per country and a blank. A blank
is not neutral: a country with no collector produces no rows, which is
indistinguishable on the page from a country where nothing happened.

WHAT IT IS NOT
--------------
It is not a source. Google News is a POINTER and never a citation:

  1. Every item's redirect is resolved to the publisher's own article URL
     before it is returned, and an item whose redirect did not resolve is
     DROPPED rather than stored. A `news.google.com` URL cannot leave this
     module, and `storable()` is the single place that decides.
  2. What gets stored is the publisher's article, exactly as with a direct
     feed. The catalogue row that configures the backstop names a COUNTRY, not
     a publisher, because we have no relationship with the outlets Google
     happens to surface and the sources page must not imply one.
  3. Nothing here writes a row. `collect()` returns raw dicts and they go
     through the same prefilter, the same two-stage gate, the same validate
     and the same store as everything else.

THE HAZARD THIS INHERITS
------------------------
Resolving a redirect is precisely where the domain-drift class of failure
lives — `botswanaguardian.co.bw` now redirects to a betting site whose feed
verifies green, and no status code or freshness check catches it. Here the
same guard has a better handle than it does on a direct feed: the RSS item
already carries the publisher Google BELIEVES it came from, so the resolved
URL can be compared against it. Landing anywhere else means something
redirected between the two, and the item is refused rather than cited.

COST
----
Fetching is free. Resolution is not free in time: two HTTP round trips per
item. So the free prefilter runs HERE, before resolving, as a cost guard —
run_collect still runs the real one afterwards, and it must, because this one
is looking at an unresolved pointer. `RESOLVE_BUDGET` is the hard bound on a
run, taken one country at a time so a chatty country cannot starve a quiet
one.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from collectors import capped_fetch

import source_registry as registry
from collectors import google_news
from collectors.national_press import (CATALOGUE_CSV, _AGGREGATOR_HOSTS,
                                       registrable_domain)

COLLECTOR = "news_backstop"

# The catalogue column that tells the two kinds of row apart. Rows without it
# are direct publisher feeds, which is what the column defaulting to "direct"
# means and why an older catalogue needs no migration.
BACKSTOP_ROLE = "backstop"

# Pointers kept per country per run, counted AFTER the free prefilter. These
# countries do not produce eight business stories a day; a bigger number would
# buy re-reads of the same fortnight, not coverage.
MAX_POINTERS_PER_COUNTRY = 8

# The hard bound on resolution round trips in one run, spent round-robin
# across countries so the twenty-first country in the list is not the one that
# always misses out.
RESOLVE_BUDGET = 60

WINDOW_DAYS = 21

# Politeness between queries. One query per country, so this is the whole
# pacing story.
PAUSE = 1.0
TIMEOUT = 25


@dataclass(frozen=True)
class Backstop:
    """One country covered by discovery rather than by a named publisher."""

    name: str
    country: str
    iso2: str
    lang: str

    @property
    def query(self) -> str:
        return registry.backstop_query(self.country, window_days=WINDOW_DAYS)

    @property
    def query_url(self) -> str:
        return google_news.build_query_url(
            self.query, lang=self.lang, country=self.iso2)


def load_backstops(path: Path | None = None) -> list[Backstop]:
    """Catalogue rows marked as the discovery backstop.

    The catalogue IS the configuration here too. A country stops needing the
    backstop the day someone verifies a publisher feed for it: change the row's
    `feed_role` to `direct`, fill in `rss`, and national_press picks it up with
    no Python change on either side.
    """
    from pipeline import vocab

    target = Path(path or CATALOGUE_CSV)
    if not target.exists():
        return []

    out: list[Backstop] = []
    with target.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("feed_role") or "").strip().lower() != BACKSTOP_ROLE:
                continue
            country = (row.get("country") or "").strip()
            iso2 = vocab.normalize_country(country)
            if not (country and iso2):
                # A country the vocabulary cannot produce is a country whose
                # rows validate.py will reject, so searching for them would be
                # work with a guaranteed empty result.
                print(f"  [{COLLECTOR}] SKIPPED {country or row.get('name','?')}: "
                      f"no entry in the country vocabulary, so nothing found "
                      f"here could ever be stored")
                continue
            out.append(Backstop(
                name=(row.get("name") or f"{country} (discovery backstop)").strip(),
                country=country,
                iso2=iso2,
                lang=(row.get("language") or "").strip()[:2].lower() or "en",
            ))
    return out


def dateline(spot: Backstop) -> str:
    """The country, as CONTEXT and nothing more.

    Same rule as the direct feeds: never written to raw["country"], because
    validate.py reads that field as sourced. A backstop dateline is weaker
    still than a publisher's, since the only thing placing the story is the
    query we asked, so it says so.
    """
    return (f"(Discovery: searched for {spot.country} because no publisher feed "
            f"is catalogued there. The search term is not a stated fact.)")


def fetch(spot: Backstop, *, timeout: int = TIMEOUT, session=None) -> list[dict]:
    """One country's pointers. Unresolved: nothing here is storable yet."""
    resp, body = capped_fetch.capped_get(
        spot.query_url, session=session,
        headers={"User-Agent": google_news.USER_AGENT}, timeout=timeout,
        max_bytes=capped_fetch.FEED_BYTES)
    resp.raise_for_status()

    line = dateline(spot)
    items = []
    for item in google_news.parse(body, spot.query):
        item.update({
            "raw_text": f"{item['raw_text']}\n\n{line}".strip(),
            "collector": COLLECTOR,
            "query": spot.name,
            "source_country": spot.country,
            "language": spot.lang,
            "discovery": "google_news_backstop",
        })
        items.append(item)
    return items


# --- The one place a pointer becomes a citation ----------------------------

def storable(item: dict) -> tuple[bool, str]:
    """Whether this item may leave the module. (ok, why_not).

    Four refusals, and the ordering is deliberate — the first three are about
    what the URL IS, the last about where it went:

      1. Not a URL we can cite at all.
      2. Still an aggregator. Resolution failed, or Google handed back its own
         domain. Storing this would cite the compiler rather than the
         publisher, which is the rule this whole module is written around.
      3. A bare domain. When resolution fails, `google_news.parse` leaves the
         outlet HOMEPAGE on the item — a URL that is neither an aggregator nor
         wrong, and proves nothing, because a homepage is not a receipt.
         validate.py rejects these later; refusing here is what makes the
         claim "an unresolved pointer is dropped" actually true rather than
         nearly true.
      4. Domain drift. Google names the publisher it believes the item came
         from; the resolved URL is where following it actually landed. A
         mismatch means something redirected in between, which is how an
         expired national daily becomes a betting site with a green feed.
    """
    url = (item.get("source_url") or "").strip()
    if not url.startswith("http"):
        return False, "no publisher URL"

    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if not host:
        return False, "no publisher URL"
    if host in _AGGREGATOR_HOSTS or host.endswith("news.google.com"):
        return False, "still points at the aggregator — the redirect did not resolve"

    if parts.path.strip("/") == "" and not parts.query:
        return False, ("the outlet's home page, not the article — the redirect "
                       "did not resolve and a home page is not a receipt")

    stated = registrable_domain(item.get("stated_publisher") or "")
    landed = registrable_domain(url)
    if stated and landed and stated != landed:
        return False, (f"resolves to {landed}, but the feed named {stated} — "
                       f"something redirected between the two")
    return True, ""


def resolve(items: list[dict], *, budget: int = RESOLVE_BUDGET,
            session=None) -> tuple[list[dict], dict]:
    """Turn pointers into citations, and drop the ones that stay pointers.

    Returns (storable items, counts). The counts are the honest part: an item
    lost to an unresolved redirect is not the same event as a country having a
    quiet fortnight, and a silent drop would make them look identical.
    """
    counts = {"resolved": 0, "unresolved": 0, "drift": 0, "over_budget": 0}
    out: list[dict] = []

    for item in items:
        if counts["resolved"] + counts["unresolved"] + counts["drift"] >= budget:
            counts["over_budget"] += 1
            continue

        # Remember who Google said published it BEFORE resolution overwrites
        # the field — that claim is the only thing the drift check has to
        # compare against.
        item["stated_publisher"] = item.get("source_url") or ""
        google_news.resolve_source_url(item, session=session)

        ok, why = storable(item)
        if ok:
            counts["resolved"] += 1
            out.append(item)
            continue
        counts["drift" if "redirected" in why else "unresolved"] += 1
        print(f"  [{COLLECTOR}] DROPPED {item.get('headline','')[:56]}  ({why})")

    return out, counts


def collect(spots: list[Backstop] | None = None, *, session=None,
            pause: float = PAUSE, budget: int = RESOLVE_BUDGET,
            path: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Every backstop country once. Returns (items, health records).

    The health records use national_press's per-feed shape on purpose: a
    country covered by discovery and a country covered by a publisher belong
    in the same ledger, or the run report answers "which countries did we
    reach" with only half the answer.
    """
    from pipeline import prefilter

    spot_list = spots if spots is not None else load_backstops(path)
    items: list[dict] = []
    health: list[dict] = []
    if not spot_list:
        return items, health

    print(f"[{COLLECTOR}] {len(spot_list)} countries with no direct publisher feed")

    # Round-robin, so the budget is shared rather than eaten by whoever is
    # first in the catalogue.
    per_country: list[tuple[Backstop, list[dict], dict]] = []
    for index, spot in enumerate(spot_list):
        record = {"name": spot.name, "country": spot.country,
                  "url": spot.query_url, "status": "ok", "items": 0, "new": 0,
                  "detail": "", "role": BACKSTOP_ROLE}
        try:
            found = fetch(spot, session=session)
        except requests.HTTPError as exc:
            code = getattr(exc.response, "status_code", "?")
            record.update(status="dead", detail=f"HTTP {code}")
            found = []
        except requests.RequestException as exc:
            record.update(status="dead", detail=type(exc).__name__)
            found = []

        record["items"] = len(found)
        # The free filter, run here only to decide which pointers are worth a
        # resolution round trip. run_collect runs the real one afterwards on
        # the resolved item, which is the one that counts.
        worth_resolving = [i for i in found
                           if prefilter.passes(i.get("raw_text", ""))[0]]
        per_country.append((spot, worth_resolving[:MAX_POINTERS_PER_COUNTRY], record))

        if index < len(spot_list) - 1 and pause:
            time.sleep(pause)

    queue: list[tuple[dict, dict]] = []
    depth = 0
    while any(len(sel) > depth for _, sel, _ in per_country):
        for _, selected, record in per_country:
            if depth < len(selected):
                queue.append((selected[depth], record))
        depth += 1

    resolved, counts = resolve([i for i, _ in queue], budget=budget,
                               session=session)
    kept = {id(i) for i in resolved}
    for item, record in queue:
        if id(item) in kept:
            record["new"] += 1

    for _, _, record in per_country:
        if record["status"] == "ok" and not record["items"]:
            record.update(status="empty",
                          detail="the discovery query returned nothing — "
                                 "either a quiet fortnight or a dead query")
        health.append(record)

    items.extend(resolved)
    print(f"[{COLLECTOR}] {counts['resolved']} publisher URLs recovered, "
          f"{counts['unresolved']} unresolved and dropped, "
          f"{counts['drift']} refused for domain drift, "
          f"{counts['over_budget']} left for the next run "
          f"(budget {budget})")
    return items, health
