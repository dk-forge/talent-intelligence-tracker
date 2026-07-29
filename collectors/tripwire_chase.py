"""Chase the tripwire's leads to the publisher's own article.

THIS IS THE HALF THAT MAKES THE TRIPWIRE SAFE.

`run_tripwire.py` produces a work list of employers a search-backed model says
had news we do not hold. Every field in it is a CLAIM: the model states amounts
and dates with total confidence and gets them wrong, and it invents items
outright. None of that may reach the database.

So this collector throws all of it away except one thing — the employer's name —
and uses that as a search term against Google News, exactly as the ordinary news
collector does. What comes back is the publisher's own article, and that article
goes through the same classify -> validate -> store path as every other
candidate, with the same guards: no source URL, no record; a bare domain is not
a receipt; the model may never state a number the text does not contain.

Three consequences worth being explicit about:

  * A hallucinated company yields no articles and stores nothing. It cost one
    search and no money.
  * A real company whose round the model mis-sized still stores the RIGHT size,
    because the size comes from the article and never from the lead.
  * The lead is never cited. The stored source is the publisher, as always. An
    aggregator is a discovery pointer, and so is a model.

DORMANT. Nothing schedules this. Run it by hand after a tripwire run:

    python run_collect.py --source tripwire_chase --dry-run

With no work list on disk it fetches nothing and says so, which run_collect
correctly reports as degraded — a collector that found zero is never 'ok' here,
and a chase with nothing to chase should look exactly as quiet as it is.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import requests

from analysis.recall.match import first_token
from collectors import google_news

COLLECTOR = "tripwire_chase"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKLIST_PATH = os.path.join(ROOT, "data", "tripwire_worklist.json")

# Leads chased per run. The money is bounded downstream by
# classify.READTHROUGH_CAP, so this is about politeness to Google News and about
# keeping one run's output readable, not about spend.
MAX_LEADS = 25

# Articles kept per lead. Resolving Google's redirect costs an HTTP round trip
# each, and the second and third write-ups of the same round add nothing the
# first does not — the dedupe would drop them anyway.
MAX_ITEMS_PER_LEAD = 3

# How far back to search for the lead's article. Wider than the tripwire's own
# window because the model's date is the field it is least reliable about.
LOOKBACK_DAYS = 75

# One Google News edition per country, reusing the registry's list so a market
# is queried in its own edition rather than through the US one.
def _edition(iso2: str) -> tuple[str, str]:
    import source_registry as registry

    for lang, country in registry.GOOGLE_NEWS_LOCALES:
        if country == iso2:
            return lang, country
    return registry.GOOGLE_NEWS_ANCHOR


def load_worklist(path: str = WORKLIST_PATH) -> list[dict]:
    """The MISSING leads from the most recent tripwire run, or nothing."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, ValueError):
        return []
    return [lead for lead in (data.get("leads") or [])
            if lead.get("claimed_company")]


def query_for(lead: dict) -> str:
    """A company-targeted query. The lead contributes the NAME and nothing else.

    The intent words are the collector's own vocabulary, not the lead's claimed
    signal type: chasing 'funding' because the model said funding would miss the
    appointment it got wrong, and both are worth having.
    """
    company = lead["claimed_company"].replace('"', "").strip()
    return (f'"{company}" (raises OR raised OR funding OR "Series" OR appoints '
            f'OR "chief executive" OR "steps down") when:{LOOKBACK_DAYS}d')


def _mentions(company: str, item: dict) -> bool:
    """Does the article actually name this employer?

    Without this a company-targeted search that finds nothing returns whatever
    Google thought was close, and the classifier is paid to read a story about
    somebody else. The token test is the same first-distinctive-word rule the
    recall matcher uses.
    """
    token = first_token(company)
    if not token:
        return False
    haystack = f"{item.get('headline', '')} {item.get('raw_text', '')}".lower()
    return token in haystack


def collect(queries=None, *, leads: list[dict] | None = None,
            worklist_path: str = WORKLIST_PATH, limit: int = MAX_LEADS,
            session=None, pause: float = 1.0, fetch=None, resolve=None) -> list[dict]:
    """One targeted search per lead. `queries` is accepted and ignored: the work
    list IS this collector's population, exactly as the catalogue is
    national_press's."""
    work = leads if leads is not None else load_worklist(worklist_path)
    if not work:
        print(f"[{COLLECTOR}] no work list at "
              f"{os.path.relpath(worklist_path, ROOT)} — run run_tripwire.py first")
        return []

    work = work[:limit]
    print(f"[{COLLECTOR}] chasing {len(work)} lead(s) to their publishers")

    # Injectable so the tests exercise the real selection and provenance logic
    # without a network call. Defaults are the live functions.
    fetch = fetch or google_news.fetch
    resolve = resolve or google_news.resolve_source_url

    seen: set[str] = set()
    out: list[dict] = []

    for lead in work:
        company = lead["claimed_company"]
        lang, country = _edition(lead.get("claimed_country") or "")
        try:
            items = fetch(query_for(lead), lang=lang, country=country)
        except requests.RequestException as exc:
            print(f"  MISS    {company}: fetch failed ({type(exc).__name__})")
            continue

        kept = 0
        for item in items:
            if kept >= MAX_ITEMS_PER_LEAD:
                break
            if item["discovery_url"] in seen or not _mentions(company, item):
                continue
            seen.add(item["discovery_url"])

            # Resolve here rather than in run_collect: only the google_news
            # source is resolved there, and an unresolved item is an outlet
            # homepage, which validate.py rejects as "not a receipt".
            item = resolve(item, session=session)
            item["collector"] = COLLECTOR
            item["locale"] = f"{country}:{lang}"
            # Bucket by employer so run_collect's fair_share spreads the cap
            # across leads instead of spending it all on the first one.
            item["query"] = company
            # Provenance, for the run log only. Deliberately NOT the claimed
            # amount, date or URL: nothing downstream may read a model's
            # assertion, so nothing downstream is given one.
            item["chased_from"] = {
                "dimension": lead.get("dimension"),
                "dimension_key": lead.get("dimension_key"),
                "company": company,
            }
            item["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            out.append(item)
            kept += 1

        print(f"  {'FOUND' if kept else 'NONE '}   {company:<34} "
              f"{kept} article(s) via {country}:{lang}")
        time.sleep(pause)

    print(f"[{COLLECTOR}] {len(out)} candidate article(s) from {len(work)} lead(s)")
    return out
