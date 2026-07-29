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

# Two-stage classification. The gate model answers ONE question (is this a
# talent signal at a named employer?) in one word, at roughly 1/40th the cost
# of a full read-through; only survivors reach MODEL for the full schema. The
# A/B on file (docs) tested exactly this split: the cheap model matched or
# corrected the incumbent on the KEEP/DROP decision (every disagreement was the
# incumbent wrongly rejecting a real funding signal) while the read-through
# stayed on the incumbent, whose prose quality is the product. Set
# TIT_GATE_MODEL=off to run single-stage.
GATE_MODEL = os.environ.get("TIT_GATE_MODEL", "google/gemini-2.5-flash-lite")

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# Per-run visibility for the spend ledger: how many one-word gate calls, how
# many were rejected there (cost avoided), how many full read-throughs ran.
STATS = {"gate_calls": 0, "gate_rejects": 0, "full_calls": 0}

# Hard ceiling on FULL read-throughs per run. The gate makes it cheap to LOOK
# at many candidates; this makes it impossible for a busy news day to turn
# looking into a budget-sized bill. Overflow raises Throttled, so run_collect
# defers the candidate un-seen and the next run picks it up: a capped day
# spreads over runs instead of being silently dropped.
READTHROUGH_CAP = int(os.environ.get("TIT_READTHROUGH_CAP", "60") or "60")

# Spec 4 rule 1: a narrow classification does not need a 1,400-token prompt.
MINI_SYSTEM = (
    "Classify a talent-market news item. Use only facts present in the text. "
    "Never state a number that is not in the text. Reply with JSON only."
)

SCHEMA_HINT = """Return JSON with exactly these keys:
{"is_talent_signal": true|false,
 "company": "the employer NAMED in the text, exactly as the text names it, or empty. A description is not a name: '$7B firm', 'a major bank', 'the company', 'an undisclosed buyer' all mean the employer was not named, so return empty. Numbers and symbols in a real name are fine (3M, 7-Eleven, 23andMe).",
 "pillar": "exactly one of these four, and they are defined so you do not have to guess: company_development = money and corporate events (funding raised, investment, acquisitions, mergers, results, entering a market); leadership_change = an appointment, departure, promotion or board change; rewards_comp = pay, equity, bonuses, benefits, pay transparency; how_we_work = WHERE and HOW the work happens — a company opening, closing, expanding or relocating an office, hub, plant, campus or site, AND remote, hybrid, return-to-office, four-day-week or flexible-working policy. A site opening is how_we_work even when the story is mostly about the money spent on it; the funding of a company is company_development, the building it opens is how_we_work.",
 "signal_direction": "hiring|displacement|neutral|comp_shift",
 "city": "city named IN THE TEXT where the roles are, or empty. Do not guess.",
 "country": "country IN THE TEXT. The dateline, the publisher's own country, and a nationality in the story all count ('Egyptian startup' means Egypt; a story in the Post and Courier is the United States). Empty only if nothing in the text carries a country.",
 "headquarters_city": "the employer's headquarters city, from your own knowledge. Empty if you do not know the company.",
 "headquarters_country": "the employer's headquarters country, from your own knowledge. This is recorded separately from the sourced country and shown to readers as the employer's HQ, never as the story's location, so answer whenever you know the company. Empty only if you do not.",
 "confidence": "verified|reported|rumored",
 "functions": ["closed list, pick every one the text supports: engineering, data_ai, it_infrastructure, product, design, finance, hr_people, sales, marketing, customer_support, operations, supply_chain, manufacturing, legal_compliance, research, clinical_healthcare, executive. Empty list if the text names none."],
 "industry": "the employer's industry: technology, financial_services, healthcare, pharma_biotech, retail_ecommerce, manufacturing, energy_utilities, telecom, media_entertainment, transport_logistics, professional_services, public_sector, hospitality_travel, education, food_beverage, automotive, aerospace_defence, real_estate_construction. Empty if unclear.",
 "state": "US state name, ONLY if the text names one. Empty otherwise.",
 "headcount": "number of roles, ONLY if the text states one. Use 0 if not stated. Never estimate.",
 "headcount_scope": "what that number COUNTS, ONLY if headcount is stated: new_roles (roles being added), total_workforce (the whole company), single_site (one office, plant or facility), affected (roles being cut or impacted). Empty otherwise.",
 "funding_amount": "funding or investment figure exactly as written in the text (e.g. '$10.5M'), or empty. Never estimate. Copy the characters the text uses, including the currency symbol. Do NOT convert it to a number: we do that ourselves.",
 "funding_stage": "the round's name IF the text names it: pre_seed, seed, series_a, series_b, series_c, series_d_plus, growth, debt, grant, ipo, other. Empty when the text does not say which round it was.",
 "effective_date": "YYYY-MM-DD when the change TAKES EFFECT, ONLY if the text dates it. 'Steps down in September' in a July article is September, not July. Empty unless the text names the month. Never guess, and never repeat the publication date here.",
 "ticker": "the stock ticker ONLY if the text prints it (e.g. 'NASDAQ: AAPL' means AAPL). Empty otherwise. Never recall it from memory.",
 "work_mode": "where the work happens, ONLY if the text says: remote, hybrid, onsite, rto_mandate (staff ordered back to the office), flexible. Empty otherwise.",
 "deal_type": "the corporate event, ONLY if the text describes one, and ALWAYS from the point of view of the company you named above: acquisition (that company is BUYING another), acquired (that company is BEING bought), merger (a merger of equals, or the text calls it a merger), divestiture (it is selling a unit, spinning one off, or carving one out), joint_venture, ipo (it is going public). Empty when the text describes no deal. The DIRECTION is the whole point, because the buyer and the bought company mean opposite things to a recruiter. Worked examples: 'Acme acquires Beta Systems' -> company Acme, deal_type acquisition; 'Beta Systems to be acquired by Acme' -> company Beta Systems, deal_type acquired. A funding round is NOT a deal_type. A deal on its own says NOTHING about headcount, so it never changes signal_direction.",
 "site_event": "what the employer did with a PLACE OF WORK, ONLY if the text says so: opened (a site is now open), closed (a site is closing or has closed), expanded (an existing site is getting bigger), relocated (a site is moving), announced (a site is planned, proposed or under construction but not open yet). Empty when the text describes no site event. 'To open in 2028' is announced, not opened. This is an event type and NOT a headcount claim: it never changes signal_direction, and a site opening with no stated roles is still 'neutral'.",
 "employer_type": "what kind of organisation the employer is, from your own knowledge of the company: public, private, startup, government, nonprofit, education. This is recorded as background about the employer, never as something the article claimed, so answer whenever you know the company. Empty only if you do not.",
 "headline": "the factual headline, unembellished",
 "summary": "1-2 sentences restating ONLY what the source says",
 "talent_readthrough": "1 sentence a recruiter can act on: WHO is affected, WHERE, and WHAT CHANGES for them. Name the function or level if the text supports it. No hedging words (potential, possibly, may, could, indicates, suggests). If the text does not support a concrete read, say what is not yet known instead of padding. NEVER write a code from the functions or industry lists into this sentence: those are storage values, not English. Write 'pharma and biotech', not 'pharma_biotech'; 'data and AI', not 'data_ai'; 'customer support', not 'customer_support'. A sentence containing an underscore is wrong.",
 "predicted_outcome": "a checkable consequence, or empty",
 "check_after_date": "YYYY-MM-DD when that could be checked, or empty"}

Set is_talent_signal true for any of these at a NAMED EMPLOYER:
 - hiring, headcount or workforce change
 - a leadership or board appointment or departure
 - a pay, equity or benefits action
 - a location decision: opening, closing, expanding or relocating an office,
   hub, capability centre, plant, campus or site. These are how_we_work, and
   they are a signal in their own right: a site decision is public months
   before the job adverts are, which is why it counts even when the source
   states no headcount at all.
 - a work-policy change: return to office, remote, hybrid, four-day week,
   flexible working. Also how_we_work.
 - FUNDING: a round raised, investment received, or capital raised. Funding is
   company_development.

signal_direction is what the SOURCE STATES about headcount, never what the
event usually implies. This rule exists because the previous instruction told
you funding was "hiring", which put "Hiring up" on the page beside a
read-through that said the announcement discloses no hiring plans. Readers are
recruiters and job seekers; a badge that contradicts the sentence under it is
worse than no badge.
 - "hiring" ONLY when the source says the employer is adding, recruiting or
   opening roles. A funding round is NOT hiring. A new office with no stated
   roles is NOT hiring.
 - "displacement" ONLY when the source says roles are being cut. One executive
   leaving, retiring or being replaced is NOT displacement: it is one person in
   a planned succession, so it is "neutral".
   A site CLOSING is not displacement either unless the source states that
   roles go with it. Plenty of closures are consolidations into another site.
 - "comp_shift" for a pay, equity or benefits action.
 - "neutral" for everything else, including funding with no stated hiring, and
   leadership appointments and departures. Most rows are legitimately neutral;
   that is the honest shape of this data, not a failure to decide.
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

Set is_talent_signal false for a WORKFORCE REDUCTION story — layoffs,
redundancies, job cuts, dismissals, despidos, licenciements, Stellenabbau,
licenziamenti, demissoes em massa. A sibling product collects those and this
one promises it does not. A story whose subject is hiring, funding, an
appointment, pay or a location stays true even when it mentions past cuts.

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


class BudgetDeferred(Throttled):
    """The per-run read-through cap was reached. Same retry-next-run handling
    as Throttled (it IS a Throttled), but distinguishable, because a run that
    deferred work on purpose must not trip the mostly-throttled breakage alarm
    the way a busy provider should."""


class CreditsExhausted(RuntimeError):
    """Raised on a 402 so the caller stops cleanly (spec 4 rule 4)."""


class AuthFailed(RuntimeError):
    """Raised on a 401. A bad key is permanent for the run, so retrying it 25
    times just prints the same error 25 times — the first live run did exactly
    that."""


class ClassifyError(RuntimeError):
    pass


GATE_SYSTEM = (
    "You decide whether a news item is a talent-market signal about ONE NAMED "
    "employer: hiring or headcount change, a leadership or board appointment "
    "or departure, a pay, equity or benefits action, a decision about a place "
    "of work (opening, closing, expanding or relocating an office, hub, plant, "
    "campus or site), a remote, hybrid, return-to-office or four-day-week "
    "policy, or a funding round raised. A site decision counts even when the "
    "item states no headcount. Answer NO for items "
    "with no named employer, market roundups, opinion pieces, single job "
    "adverts, and government programmes. Answer NO when the STORY IS ABOUT "
    "layoffs, redundancies, job cuts or dismissals in any language: a "
    "sibling product collects those and this one must not. A story about "
    "hiring, funding, an appointment or pay stays YES even if it mentions "
    "past cuts. Reply with exactly one word: YES or NO."
)


def _api_key() -> str:
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
    return api_key


def gate(text: str, *, timeout: int = 30) -> bool:
    """One-word KEEP/DROP from the cheap model. Fails OPEN: if the gate itself
    errors or is throttled, the candidate goes through to the full model, so a
    flaky gate can cost money but can never cost coverage. 401/402 still
    propagate — those end the run whichever stage sees them."""
    STATS["gate_calls"] += 1
    try:
        content = _call(
            GATE_MODEL, GATE_SYSTEM, text[:1500],
            timeout=timeout, max_tokens=4, json_mode=False,
        )
    except (AuthFailed, CreditsExhausted):
        raise
    except (Throttled, ClassifyError):
        return True
    keep = "YES" in content.upper()
    if not keep:
        STATS["gate_rejects"] += 1
    return keep


def classify(raw: dict, *, timeout: int = 45) -> dict | None:
    """Classify one candidate. Returns None if it is not a talent signal."""
    text = (raw.get("raw_text") or "").strip()

    # The publisher is the single best geography hint we were throwing away.
    # "USTA SC names new CEO" places nowhere on its own; the same story from
    # the Post and Courier is South Carolina, and the Denver Gazette is
    # Colorado. Five dry runs stored nine of eleven records with no location
    # while this line sat in the item, unused.
    #
    # Passed as context, not as fact: it feeds the model's own knowledge the
    # same way the company name feeds headquarters_country, and whatever it
    # concludes still has to normalise through the country vocabulary.
    outlet = (raw.get("source_name") or "").strip()
    if outlet:
        text = f"Published by: {outlet}\n\n{text}"
    if not text:
        raise ClassifyError("raw_text is empty")

    # Stage 1: the one-word gate. A rejection here costs ~1/40th of a full
    # read-through and is the whole reason the candidate cap can be generous.
    if GATE_MODEL and GATE_MODEL.lower() not in ("off", "0", "none"):
        if not gate(text, timeout=min(timeout, 30)):
            return None

    # Stage 2 is the expensive call, so it carries the per-run ceiling.
    # Raised as Throttled because that is already the "not now, retry next
    # run, do not mark seen" path in run_collect.
    if STATS["full_calls"] >= READTHROUGH_CAP:
        raise BudgetDeferred(
            f"read-through cap ({READTHROUGH_CAP}/run) reached — deferring to the next run"
        )
    STATS["full_calls"] += 1

    content = _call(
        MODEL, MINI_SYSTEM, f"{SCHEMA_HINT}\n\n---\n{text[:4000]}",
        timeout=timeout, json_mode=True,
    )

    try:
        parsed = json.loads(_strip_fences(content))
    except ValueError as exc:
        raise ClassifyError(f"unparseable model response: {exc} — got {content[:200]!r}") from exc

    if not parsed.get("is_talent_signal"):
        return None
    return parsed


def _call(model: str, system: str, user: str, *, timeout: int,
          max_tokens: int | None = None, json_mode: bool = True) -> str:
    """One OpenRouter chat call with the retry/status discipline both stages
    share. Returns the content string; raises the same typed errors as before."""
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if json_mode:
        body["response_format"] = {"type": "json_object"}
        # OpenRouter routes a model across several providers, and not all of
        # them honour response_format. One that ignores it returns empty
        # content, which looks like a parse bug rather than a routing one —
        # 8 of the first 10 live classifications failed this way. This pins
        # routing to providers that actually support the parameters we send.
        body["provider"] = {"require_parameters": True}

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
                "Authorization": f"Bearer {_api_key()}",
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
    return content


def _strip_fences(content: str) -> str:
    """Models wrap JSON in ```json fences often enough to be worth handling."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text
