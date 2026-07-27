"""LLM classification via OpenRouter.

Cost discipline (spec 4): the system prompt is deliberately tiny, candidates
are keyword-gated before they ever reach here, and a 402 stops the run rather
than burning a batch of failures.
"""

from __future__ import annotations

import json
import os

import time

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("TIT_MODEL", "deepseek/deepseek-chat")
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# Spec 4 rule 1: a narrow classification does not need a 1,400-token prompt.
MINI_SYSTEM = (
    "Classify a talent-market news item. Use only facts present in the text. "
    "Never state a number that is not in the text. Reply with JSON only."
)

SCHEMA_HINT = """Return JSON with exactly these keys:
{"is_talent_signal": true|false,
 "company": "the employer named, or empty",
 "pillar": "company_development|leadership_change|rewards_comp|how_we_work",
 "signal_direction": "hiring|displacement|neutral|comp_shift",
 "city": "city named IN THE TEXT where the roles are, or empty. Do not guess.",
 "country": "country IN THE TEXT. The dateline, the outlet's own country, and a nationality in the story all count as in the text ('Egyptian startup' means Egypt). Empty only if the text really carries no country.",
 "headquarters_city": "the employer's headquarters city, from your own knowledge. Empty if you do not know the company.",
 "headquarters_country": "the employer's headquarters country, from your own knowledge. This is recorded separately from the sourced country and shown to readers as the employer's HQ, never as the story's location, so answer whenever you know the company. Empty only if you do not.",
 "confidence": "verified|reported|rumored",
 "functions": ["closed list, pick every one the text supports: engineering, data_ai, it_infrastructure, product, design, finance, hr_people, sales, marketing, customer_support, operations, supply_chain, manufacturing, legal_compliance, research, clinical_healthcare, executive. Empty list if the text names none."],
 "industry": "the employer's industry: technology, financial_services, healthcare, pharma_biotech, retail_ecommerce, manufacturing, energy_utilities, telecom, media_entertainment, transport_logistics, professional_services, public_sector, hospitality_travel, education, food_beverage, automotive, aerospace_defence, real_estate_construction. Empty if unclear.",
 "state": "US state name, ONLY if the text names one. Empty otherwise.",
 "headcount": "number of roles, ONLY if the text states one. Use 0 if not stated. Never estimate.",
 "funding_amount": "funding or investment figure exactly as written in the text (e.g. '$10.5M'), or empty. Never estimate.",
 "headline": "the factual headline, unembellished",
 "summary": "1-2 sentences restating ONLY what the source says",
 "talent_readthrough": "1 sentence a recruiter can act on: WHO is affected, WHERE, and WHAT CHANGES for them. Name the function or level if the text supports it. No hedging words (potential, possibly, may, could, indicates, suggests). If the text does not support a concrete read, say what is not yet known instead of padding.",
 "predicted_outcome": "a checkable consequence, or empty",
 "check_after_date": "YYYY-MM-DD when that could be checked, or empty"}

Set is_talent_signal true for any of these at a NAMED EMPLOYER:
 - hiring, headcount or workforce change
 - a leadership or board appointment or departure
 - a pay, equity or benefits action
 - a location decision: new office, hub, capability centre, RTO policy
 - FUNDING: a round raised, investment received, or capital raised. Funding is
   a leading indicator of hiring, so it counts as company_development with
   signal_direction "hiring".
   For a funding read-through, state what IS known and say plainly what is not.
   Money is reported, hiring plans usually are not, so do not hedge about what
   might happen — name the fact and the gap.
   Call the source what it is: "the filing" only for an SEC filing, otherwise
   "the announcement" or "the report".
   Say what the company does and where, so the line is worth reading. A row of
   read-throughs that differ only by the number is not useful to anyone.
     BAD:  "Holobiome's $10M funding suggests upcoming hiring in biotech roles."
     GOOD: "Holobiome has $10M of new capital in Boston. The filing does not
            disclose hiring plans; watch its careers page for biotech roles."
     BAD:  "Enigma has $71M of new capital. The filing does not disclose hiring
            plans; watch its careers page."
     GOOD: "Enigma raised $71M in seed for physical-AI robotics, a stage where
            headcount usually goes into research and engineering. The
            announcement names no roles."

Set is_talent_signal false for anything else, and for anything with no named
employer (government programmes, economic-development announcements, single job
adverts, civil-service exam notices).

A weak read-through is worse than none. Compare:
  BAD:  "This indicates a potential increase in hiring, possibly in tech roles."
  GOOD: "Adds roughly 300 engineering roles to the Dublin market over 2026,
         the largest single tech intake there this year."
  BAD:  "This suggests a shift towards automation."
  GOOD: "Slows NHS clinical-admin recruitment in England; candidates in those
         roles should expect fewer openings from the largest UK employer."
"""


# Statuses that say "not now", never "no".
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRIES = 4
BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 30.0


class Throttled(RuntimeError):
    """The provider was busy. The candidate is untouched and must be retried on
    a later run, not recorded as rejected and not marked seen."""


class CreditsExhausted(RuntimeError):
    """Raised on a 402 so the caller stops cleanly (spec 4 rule 4)."""


class AuthFailed(RuntimeError):
    """Raised on a 401. A bad key is permanent for the run, so retrying it 25
    times just prints the same error 25 times — the first live run did exactly
    that."""


class ClassifyError(RuntimeError):
    pass


def classify(raw: dict, *, timeout: int = 45) -> dict | None:
    """Classify one candidate. Returns None if it is not a talent signal."""
    # Strip: a key pasted into a secrets box often carries a trailing newline,
    # which makes the Authorization header malformed and the failure look like
    # a missing key rather than a whitespace problem.
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise AuthFailed("OPENROUTER_API_KEY is not set")
    if "..." in api_key or len(api_key) < 40:
        raise AuthFailed(
            "OPENROUTER_API_KEY looks truncated — it may be the abbreviated "
            "value shown in the dashboard rather than the full key"
        )

    text = (raw.get("raw_text") or "").strip()
    if not text:
        raise ClassifyError("raw_text is empty")

    body = {
        "model": MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        # OpenRouter routes a model across several providers, and not all of
        # them honour response_format. One that ignores it returns empty
        # content, which looks like a parse bug rather than a routing one —
        # 8 of the first 10 live classifications failed this way. This pins
        # routing to providers that actually support the parameters we send.
        "provider": {"require_parameters": True},
        "messages": [
            {"role": "system", "content": MINI_SYSTEM},
            {"role": "user", "content": f"{SCHEMA_HINT}\n\n---\n{text[:4000]}"},
        ],
    }

    # A 429 here is the upstream provider being busy, not a verdict on the
    # candidate. Treating it as one threw five real stories away in a single
    # dry run — OpenAI tripling its Dublin headcount among them — and reported
    # them as REJECT, which reads exactly like the model declining them.
    # The sibling paid for this same lesson with transient 5xx from its host.
    resp = None
    for attempt in range(RETRIES):
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json=body,
            timeout=timeout,
        )
        if resp.status_code not in TRANSIENT_STATUS or attempt == RETRIES - 1:
            break
        # Honour Retry-After when the provider sends one, else back off.
        wait = float(resp.headers.get("Retry-After") or 0) or BACKOFF_SECONDS * (2 ** attempt)
        time.sleep(min(wait, MAX_BACKOFF_SECONDS))

    if resp.status_code in TRANSIENT_STATUS:
        raise Throttled(
            f"OpenRouter {resp.status_code} after {RETRIES} attempts: {resp.text[:200]}"
        )
    if resp.status_code == 402:
        raise CreditsExhausted("OpenRouter returned 402 — stopping the run")
    if resp.status_code == 401:
        raise AuthFailed(f"OpenRouter rejected the API key (401): {resp.text[:200]}")
    if resp.status_code >= 400:
        raise ClassifyError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")

    try:
        choice = resp.json()["choices"][0]
    except (KeyError, IndexError, ValueError) as exc:
        raise ClassifyError(f"unexpected response shape: {exc}") from exc

    content = (choice.get("message", {}).get("content") or "").strip()
    if not content:
        # Say WHY it was empty. "Expecting value: line 1 column 1" tells you
        # nothing; finish_reason usually tells you everything.
        raise ClassifyError(
            f"model returned empty content (finish_reason="
            f"{choice.get('finish_reason')!r}, provider={resp.json().get('provider')!r})"
        )

    try:
        parsed = json.loads(_strip_fences(content))
    except ValueError as exc:
        raise ClassifyError(f"unparseable model response: {exc} — got {content[:200]!r}") from exc

    if not parsed.get("is_talent_signal"):
        return None
    return parsed


def _strip_fences(content: str) -> str:
    """Models wrap JSON in ```json fences often enough to be worth handling."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text
