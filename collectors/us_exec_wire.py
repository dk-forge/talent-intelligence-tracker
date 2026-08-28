"""US private-company executive appointments, discovered through Google News.

WHY THIS EXISTS
---------------
The leadership pillar's US spine is `collectors/sec_edgar.py`, which reads 8-K
Item 5.02 officer and director changes. An 8-K is a PUBLIC company's obligation,
so that spine is blind to private employers. The UK has no such hole: Companies
House files every officer appointment at every company (collectors/
companies_house.py). A US private company announcing a new chief executive does
not file with anyone — it puts the release on a press-release wire, and those
wires are the signal this collector adds.

WHAT IT IS NOT
--------------
It is not a wire reader, and it never touches a wire's own feed. Business Wire,
PR Newswire and GlobeNewswire are all recorded NOT WIRED in
data/sources_catalogue.csv: Business Wire answers 403 even on its robots.txt so
its terms are unreadable, GlobeNewswire's robots.txt disallows its RSS paths by
name, and PR Newswire publishes no parseable feed. The PR Newswire row names the
sanctioned route instead — "the route to this publisher is google_news resolving
to the release URL" — and that is exactly what this does:

  1. It reads the Google News INDEX (its RSS gives the headline plus a <source>
     element) for US executive-appointment phrasing, biased to the en-US edition.
  2. The appointment is almost always in the headline the index already carries
     ("Acme Names Jane Roe as Chief Executive Officer"), so the pipeline acts on
     the index entry. The wire's release page is NEVER fetched — no robots.txt,
     no paywall and no bot wall of any wire is ever engaged.
  3. Every item's Google redirect is resolved to the publisher's own release URL
     before it is returned (the same resolver collectors/google_news.py uses),
     and an item whose redirect did not resolve is DROPPED rather than stored.
     `storable()` is the single place that decides, and it refuses aggregators,
     bare homepages and domain drift. The stored citation is the wire release
     URL; the database already cites prnewswire.com and businesswire.com.

Nothing here writes a row. `collect()` returns raw dicts and they go through the
same prefilter, the same two-stage gate, the same validate and the same store as
everything else. The appointment classifies into the leadership pillar the same
way an 8-K does — this collector adds no pillar of its own, it feeds the one the
classifier already assigns.

DORMANCY
--------
A new recurring paid cost ships DORMANT and armable, like every other key-gated
source here. Two independent locks keep it off:

  * No workflow schedules it. Registration in run_collect.SOURCES makes it
    runnable by hand (`--source us_exec_wire`), nothing more.
  * `TIT_US_EXEC_WIRE` defaults OFF. A disarmed `collect()` returns nothing and
    makes no request, so even a workflow that named it would stay dormant until
    the owner sets the flag.

The dry-run diagnostic is exempt from the arming lock: rehearsing coverage must
work before the owner decides to arm. Run `us_exec_wire_probe.py` to see which
US appointments this WOULD capture (headline, resolved outlet, would-store) with
zero storage and zero model spend.

COST
----
Fetching the index is free and keyless. Resolution costs two HTTP round trips
per item, so the free prefilter runs HERE first as a cost guard, and
`RESOLVE_BUDGET` hard-bounds a run. No model is called in this module: paid gate
and read-through calls happen only inside the ordinary classify path when armed,
metered by spend.py, and bounded by classify.READTHROUGH_CAP and the per-run
candidate cap. Spend lands under the `us_exec_wire` health tag, apart from the
scheduled collectors' pot.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import requests
from collectors import capped_fetch

import source_registry as registry
from collectors import google_news
from collectors.national_press import _AGGREGATOR_HOSTS, registrable_domain

COLLECTOR = "us_exec_wire"

# A collector that keeps a rehearsal honest: run_collect passes dry_run when the
# whole pipeline is rehearsing, so a dry run never spends and never stores.
ACCEPTS_DRY_RUN = True

# The edition. en-US is what biases the answer to US employers — the same lever
# every locale query in google_news pulls. See source_registry.us_exec_wire_*.
LANG = "en"
COUNTRY = "US"

# Pointers kept per run, counted AFTER the free prefilter. A short window of US
# appointment releases is not a large population; a bigger cap buys re-reads of
# the same fortnight, not more coverage.
MAX_POINTERS = 24

# The hard bound on resolution round trips in one run. Resolution is two HTTP
# round trips per item, so this is the real cost lever on a run's time.
RESOLVE_BUDGET = 40

# Short: an appointment is announced once and reads best fresh, and already-seen
# URLs are skipped before any spend, so re-asking a week costs nothing.
WINDOW_DAYS = 7

# Politeness between the (few) queries.
PAUSE = 1.0
TIMEOUT = 25

# The wires whose OWN feeds must never be fetched, per their documented status
# in data/sources_catalogue.csv. This collector reaches them ONLY through the
# Google News index and Google's resolution endpoint; it constructs no request
# against these hosts. Kept as data so the guarantee is testable rather than
# merely asserted in prose. Registrable domains, matched on any subdomain.
_BLOCKED_WIRE_DOMAINS = frozenset({
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
})


def is_armed() -> bool:
    """Whether the owner has switched this recurring paid source on.

    Defaults OFF. A disarmed live run makes no request and stores nothing; the
    dry-run diagnostic ignores this because rehearsing coverage must work before
    the owner decides.
    """
    return (os.environ.get("TIT_US_EXEC_WIRE") or "off").strip().lower() in (
        "1", "on", "true", "yes", "arm", "armed")


def targets_blocked_wire_feed(url: str) -> bool:
    """True if a URL points at a blocked wire's own host.

    Used only to PROVE, in a test, that no request this module builds targets a
    wire feed. It is never used to decide whether to STORE a resolved release
    URL: a wire release URL is a legitimate citation (the database already cites
    prnewswire.com), and the rule is that we never FETCH the wire, not that we
    never cite it.
    """
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and registrable_domain(host) in _BLOCKED_WIRE_DOMAINS


def dateline() -> str:
    """The US intent, as CONTEXT and nothing more.

    Never written to raw["country"]: validate.py reads that field as sourced,
    and the only thing placing the story is the edition we queried. The model
    still decides the country off the headline, and it normalises or is dropped.
    """
    return ("(Discovery: searched US executive-appointment announcements on the "
            "press-release wires via Google News. The search intent is not a "
            "stated fact.)")


def fetch(*, window_days: int = WINDOW_DAYS, session=None,
          timeout: int = TIMEOUT, pause: float = PAUSE) -> list[dict]:
    """The run's pointers, de-duplicated by URL. Unresolved: nothing storable yet."""
    line = dateline()
    seen: set[str] = set()
    items: list[dict] = []

    queries = registry.us_exec_wire_queries(window_days=window_days)
    for index, query in enumerate(queries):
        url = google_news.build_query_url(query, lang=LANG, country=COUNTRY)
        resp, body = capped_fetch.capped_get(
            url, session=session,
            headers={"User-Agent": google_news.USER_AGENT}, timeout=timeout,
            max_bytes=capped_fetch.FEED_BYTES)
        resp.raise_for_status()

        for item in google_news.parse(body, query, country=COUNTRY, lang=LANG):
            key = item["discovery_url"]
            if key in seen:
                continue
            seen.add(key)
            item.update({
                "raw_text": f"{item['raw_text']}\n\n{line}".strip(),
                "collector": COLLECTOR,
                "query": query,
                "source_country": "United States",
                "language": LANG,
                "discovery": "google_news_us_exec_wire",
            })
            items.append(item)

        if index < len(queries) - 1 and pause:
            time.sleep(pause)

    return items


# --- The one place a pointer becomes a citation ----------------------------

def storable(item: dict) -> tuple[bool, str]:
    """Whether this item may leave the module. (ok, why_not).

    The same four refusals as the discovery backstop, in the same order — the
    first three are about what the URL IS, the last about where it went:

      1. Not a URL we can cite at all.
      2. Still an aggregator. Resolution failed, or Google handed back its own
         domain. Storing this would cite the compiler, not the publisher.
      3. A bare domain. A failed resolution leaves the outlet HOMEPAGE on the
         item, which is neither an aggregator nor wrong and still proves
         nothing — a homepage is not a receipt.
      4. Domain drift. Google names the publisher it believes the item came
         from; the resolved URL is where following it actually landed. A
         mismatch means something redirected in between.

    A wire release URL (prnewswire.com/news-releases/..., businesswire.com/
    news/home/...) passes: it is a full-path publisher article, exactly the
    citation this collector exists to capture. It is stored, never fetched.
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
        return False, ("the outlet's home page, not the release — the redirect "
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
    lost to an unresolved redirect is not the same event as a quiet fortnight,
    and a silent drop would make them look identical.
    """
    counts = {"resolved": 0, "unresolved": 0, "drift": 0, "over_budget": 0}
    out: list[dict] = []

    for item in items:
        if counts["resolved"] + counts["unresolved"] + counts["drift"] >= budget:
            counts["over_budget"] += 1
            continue

        # Remember who Google said published it BEFORE resolution overwrites the
        # field — that claim is the only thing the drift check compares against.
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


def collect(queries=None, *, dry_run: bool = False, session=None,
            budget: int = RESOLVE_BUDGET, window_days: int = WINDOW_DAYS,
            pause: float = PAUSE) -> list[dict]:
    """Read the US executive-appointment index once and return storable items.

    `queries` is accepted and ignored: run_collect passes it positionally to
    every collector, and this source's vocabulary lives in source_registry.

    DORMANT unless armed. A disarmed LIVE run returns nothing and makes no
    request. A dry run rehearses regardless, so the owner can see the coverage
    before arming — it fetches and resolves (both free of model spend) but the
    caller stores nothing.
    """
    from pipeline import prefilter

    if not dry_run and not is_armed():
        print(f"[{COLLECTOR}] DORMANT (TIT_US_EXEC_WIRE is off) — "
              f"no request made, nothing collected")
        return []

    try:
        found = fetch(window_days=window_days, session=session, pause=pause)
    except requests.RequestException as exc:
        print(f"[{COLLECTOR}] FETCH FAILED: {type(exc).__name__}: {exc}")
        raise

    # The free filter, run here only to decide which pointers are worth a
    # resolution round trip. run_collect runs the real one afterwards on the
    # resolved item, which is the one that counts.
    worth_resolving = [i for i in found
                       if prefilter.passes(i.get("raw_text", ""))[0]]
    worth_resolving = worth_resolving[:MAX_POINTERS]

    resolved, counts = resolve(worth_resolving, budget=budget, session=session)

    print(f"[{COLLECTOR}] {len(found)} pointers, "
          f"{len(worth_resolving)} past the free filter, "
          f"{counts['resolved']} publisher URLs recovered, "
          f"{counts['unresolved']} unresolved and dropped, "
          f"{counts['drift']} refused for domain drift, "
          f"{counts['over_budget']} left for the next run (budget {budget})")
    return resolved
