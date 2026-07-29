"""The outside view: one search-backed question per dimension, parsed strictly.

WHY AN OUTSIDE VIEW AT ALL
--------------------------
On 2026-07-28 the owner asked a general search-backed model what Israeli
companies had raised that month and it named four rounds — Glow, Plantopia,
Harmony, Enigma — that this pipeline did not hold. It beat our own collectors
not because it is better at extraction but because it does not share their
blind spots: our feeds decide what we can see, and no amount of care inside the
pipeline can find a story no feed carries.

WHAT COMES BACK IS A CLAIM, NOT A FACT
--------------------------------------
Every field the model returns is stored under a `claimed_` name, and nothing
named `claimed_*` is ever written to the signals table. The model will state a
round size, a date and an outlet with complete confidence and be wrong about
any of them, or invent the whole item. So the lead's only job is to name an
EMPLOYER worth looking at; the chase collector then goes and finds the
publisher's own article, and that article — not this reply — is what the normal
classify -> validate -> store path reads. A hallucinated company simply yields
no articles and dies at the diff or the chase, having cost one query.

The URL the model offers is kept for the human reading the work list and is
never stored either: an invented URL looks exactly like a real one.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import requests

from pipeline.classify import (AuthFailed, ClassifyError, CreditsExhausted,
                               Throttled, TRANSIENT_STATUS, _strip_fences)
from pipeline.vocab import normalize_country, normalize_industry

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# A search-backed model, because the whole point is current events the model's
# training data cannot contain. Overridable, because which model is cheapest at
# search changes faster than this file does.
MODEL = os.environ.get("TIT_TRIPWIRE_MODEL", "perplexity/sonar")

# Models that search natively need no plugin; anything else gets OpenRouter's
# web plugin, whose per-result fee is what USD_PER_QUERY_ESTIMATE is sized on.
WEB_PLUGIN_RESULTS = 5

SYSTEM = (
    "You report recent, publicly announced company news. You answer only with "
    "items a named publisher has actually published, and you name that "
    "publisher. If you are not sure an item was published, leave it out: a "
    "short accurate list is worth far more than a long one. Reply with JSON "
    "only."
)

SCHEMA = """Return JSON of exactly this shape:
{"items": [
  {"company": "the employer's name as the publisher writes it",
   "country": "the country the employer is based in",
   "city": "the city, or empty",
   "signal_type": "funding or leadership",
   "event_date": "YYYY-MM-DD the announcement was published, as best you know",
   "amount": "the round size exactly as reported (e.g. '$34M'), or empty",
   "stage": "seed, series_a, series_b, ... or empty",
   "outlet": "the publisher that reported it",
   "url": "the publisher's own article URL, or empty if you do not have it"}
]}

Rules:
- Only real, published announcements. Never fill the list to reach a count.
- No workforce reductions, layoffs or redundancies of any kind.
- A named employer only. Government programmes and market roundups do not count.
- Do not include an item you cannot attribute to a publisher.
"""


def country_query(iso2: str, name: str, *, lookback_days: int,
                  leads: int, today: date | None = None) -> str:
    since = (today or date.today()) - timedelta(days=lookback_days)
    return (
        f"{SCHEMA}\n---\n"
        f"List up to {leads} companies headquartered in {name} ({iso2}) that "
        f"announced a funding round, or appointed or lost a chief executive or "
        f"other C-level officer, between {since.isoformat()} and today. "
        f"Favour the ones a specialist local outlet covered over the ones "
        f"every international wire carried."
    )


def industry_query(industry: str, *, lookback_days: int, leads: int,
                   today: date | None = None) -> str:
    since = (today or date.today()) - timedelta(days=lookback_days)
    label = industry.replace("_", " ")
    return (
        f"{SCHEMA}\n---\n"
        f"List up to {leads} companies in the {label} sector, anywhere in the "
        f"world, that announced a funding round, or appointed or lost a chief "
        f"executive or other C-level officer, between {since.isoformat()} and "
        f"today. Spread them across countries rather than listing several from "
        f"one market."
    )


def build_queries(plan: dict, today: date | None = None) -> list[dict]:
    """One question per dimension value, with its provenance attached."""
    out = []
    for country in plan.get("countries", []):
        out.append({
            "dimension": "country",
            "key": country["iso2"],
            "label": country["name"],
            "tier": country["tier"],
            "measured": country.get("measured"),
            "prompt": country_query(country["iso2"], country["name"],
                                    lookback_days=plan["lookback_days"],
                                    leads=plan.get("leads_per_query", 8),
                                    today=today),
        })
    for industry in plan.get("industries", []):
        out.append({
            "dimension": "industry",
            "key": industry,
            "label": industry.replace("_", " "),
            "tier": "sweep",
            "measured": None,
            "prompt": industry_query(industry,
                                     lookback_days=plan["lookback_days"],
                                     leads=plan.get("leads_per_query", 8),
                                     today=today),
        })
    return out


def parse_leads(content: str, query: dict) -> list[dict]:
    """Turn one reply into leads. Anything unusable is dropped here, quietly.

    Every model-asserted value keeps a `claimed_` prefix all the way to the work
    list. That is not decoration: it is what stops a later reader, or a later
    session, from treating the model's number as ours.
    """
    try:
        parsed = json.loads(_strip_fences(content))
    except ValueError:
        return []

    rows = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return []

    leads = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        company = str(row.get("company") or "").strip()
        if not company or len(company) < 2:
            continue
        signal = str(row.get("signal_type") or "").strip().lower()
        signal = signal if signal in ("funding", "leadership") else ""
        iso2 = normalize_country(str(row.get("country") or "")) or ""
        # A country query knows the country; the model naming a different one is
        # not a reason to throw the lead away, but the query's own value is the
        # one the counts are grouped by.
        if not iso2 and query["dimension"] == "country":
            iso2 = query["key"]

        leads.append({
            "dimension": query["dimension"],
            "dimension_key": query["key"],
            "dimension_tier": query["tier"],
            "claimed_company": company,
            "claimed_country": iso2,
            "claimed_city": str(row.get("city") or "").strip(),
            "claimed_signal_type": signal,
            "claimed_event_date": str(row.get("event_date") or "").strip()[:10],
            "claimed_amount": str(row.get("amount") or "").strip(),
            "claimed_stage": str(row.get("stage") or "").strip(),
            "claimed_outlet": str(row.get("outlet") or "").strip(),
            "claimed_url": str(row.get("url") or "").strip(),
            "claimed_industry": (normalize_industry(query["key"]) or "")
                                if query["dimension"] == "industry" else "",
        })
    return leads


def ask(query: dict, *, timeout: int = 90, model: str = None) -> tuple[list[dict], float, dict]:
    """One search-backed question. Returns (leads, usd_cost, diagnostics).

    Raises the same typed errors the classifier does, so the caller has one
    vocabulary for "stop the run" (auth, credits) versus "try again next run"
    (throttled).
    """
    content, cost, diag = _call(query["prompt"], timeout=timeout,
                                model=model or MODEL)
    return parse_leads(content, query), cost, diag


def _api_key() -> str:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise AuthFailed("OPENROUTER_API_KEY is not set")
    return key


def _call(prompt: str, *, timeout: int, model: str) -> tuple[str, float, dict]:
    """One OpenRouter call that reports its own cost.

    `usage.include` is what makes cost-per-lead a measurement rather than an
    estimate: OpenRouter returns what the request actually cost, search fee
    included, and the run records it. There is no retry ladder here on purpose —
    a search-backed request is the expensive kind, and retrying one that already
    billed is how a $1 budget becomes a $4 one. Transient failures raise
    Throttled and the next run asks again.
    """
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "usage": {"include": True},
    }
    if not ("sonar" in model or model.endswith(":online")):
        # A model with no native search would answer from training data, which
        # is worse than useless here: it would confidently name last year's
        # rounds as this month's.
        body["plugins"] = [{"id": "web", "max_results": WEB_PLUGIN_RESULTS}]

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        json=body,
        timeout=timeout,
    )

    if resp.status_code in TRANSIENT_STATUS:
        raise Throttled(f"OpenRouter {resp.status_code}: {resp.text[:200]}")
    if resp.status_code == 402:
        raise CreditsExhausted("OpenRouter returned 402 — stopping the run")
    if resp.status_code == 401:
        raise AuthFailed(f"OpenRouter rejected the API key (401): {resp.text[:200]}")
    if resp.status_code >= 400:
        raise ClassifyError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")

    try:
        payload = resp.json()
        choice = payload["choices"][0]
    except (KeyError, IndexError, ValueError) as exc:
        raise ClassifyError(f"unexpected response shape: {exc}") from exc

    usage = payload.get("usage") or {}
    cost = float(usage.get("cost") or 0.0)
    diag = {
        "model": payload.get("model") or model,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "finish_reason": choice.get("finish_reason"),
    }

    content = (choice.get("message", {}).get("content") or "").strip()
    if not content:
        raise ClassifyError(
            f"model returned empty content (finish_reason={choice.get('finish_reason')!r})")
    return content, cost, diag
