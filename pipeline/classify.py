"""LLM classification via OpenRouter.

Cost discipline (spec 4): the system prompt is deliberately tiny, candidates
are keyword-gated before they ever reach here, and a 402 stops the run rather
than burning a batch of failures.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

import time

import requests

from . import cheap_extract, prompts, validate, vocab

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("TIT_MODEL", "deepseek/deepseek-chat")

# Two-stage classification. The gate model answers ONE question (is this a
# talent signal at a named employer?) in one word, at roughly 1/40th the cost
# of a full read-through; only survivors reach MODEL for the full schema.
#
# Why gemini-2.5-flash-lite is the default: the repo's own A/B
# (docs/HANDOVER.md, "Model switch — measured 2026-07-28", reproducible via
# ab_models.py) priced it at about HALF the incumbent's gate cost per item,
# and every KEEP/DROP disagreement went the same way — the incumbent said
# reject, the challenger said SIGNAL, and the headlines were real funding
# rounds ("Enigma Raises $71M in Seed Funding"). The challenger was not
# disagreeing with the incumbent, it was CORRECTING it, which is why the
# sibling's "reject below 90% agreement" rule would have picked the wrong
# model here. Set TIT_GATE_MODEL=off to run single-stage.
GATE_MODEL = os.environ.get("TIT_GATE_MODEL", "google/gemini-2.5-flash-lite")

# Stage 3: the READ-THROUGH, on its own model and its own small prompt.
#
# MODEL still does extraction with SCHEMA_HINT untouched, because extraction is
# pattern-matching and deepseek does it well at $0.00128 a call. Interpretation
# is judgement, and the quality A/B this comment used to say had not been run
# (ab_models.py --readthrough) has now been run: deepseek RESTATED the headline
# where the Claude models produced a read-through a recruiter could act on.
#
# The reason this is a SECOND CALL rather than a better model on the first one:
# the fused prompt is ~3,100 input tokens and ~2,476 of them are SCHEMA_HINT,
# which interpretation does not need. Upgrading the fused call pays a frontier
# rate on the extraction schema — ~$0.0102/record. The split pays it only on
# the ~450 tokens the judgement actually reads. See pipeline/prompts.py for
# what the small prompt carries and, more importantly, what it refuses to.
#
# TIT_READ_MODEL=off falls back to the fused behaviour: extraction's own
# talent_readthrough ships, exactly as it did before this split. That is the
# one-line revert if the interpretation model is unavailable for a whole run.
READ_MODEL = os.environ.get("TIT_READ_MODEL", "anthropic/claude-sonnet-5")

# ~60 tokens of sentence, with headroom for a second sentence and for a model
# that opens with a brace and a key. Low enough that a runaway generation is
# bounded; high enough that a truncated sentence is not the failure mode.
READ_MAX_TOKENS = int(os.environ.get("TIT_READ_MAX_TOKENS", "200") or "200")

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# Per-run visibility for the spend ledger: how many one-word gate calls, how
# many were rejected there (cost avoided), how many full read-throughs ran.
# The token counters come from OpenRouter's usage accounting on every call
# (both stages), so a run can report what it actually sent and what the cache
# actually served rather than estimating either.
STATS = {
    "gate_calls": 0, "gate_rejects": 0, "full_calls": 0,
    "full_chars_raw": 0,   # candidate text length before truncation
    "full_chars_sent": 0,  # what actually went to the model
    "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0,
    "usd": 0.0,            # OpenRouter's own cost figure, summed
    # Rows that a full read-through actually bought. run_collect increments it
    # at store time (would-store on a dry run), and only for records that came
    # out of classify() — never a deterministic close or a derived row. Beside
    # full_calls it is the waste ratio: the last real run bought 60 reads and
    # stored 34 rows, and until this counter existed that gap was invisible.
    "read_stored": 0,
    # --- stage 3, the read-through call -----------------------------------
    # written: interpretations that came back and passed the grounding check.
    # unavailable: the provider was busy or the response was unparseable.
    # ungrounded: it came back and named a figure or a place that is in
    #   neither the source text nor the extracted fields — a refusal, counted
    #   apart from a transport failure because retrying it is pointless.
    # hedged: it came back grounded but hedged. NOT a rejection: hedging is a
    #   quality flaw, not an invented claim, and a run that quietly stopped
    #   storing rows over an adverb would be worse than a hedge on the page.
    #   Counted so the A/B's verdict stays measurable in production.
    # queued/served: the batch path only, both zero on the default sync path.
    "read_calls": 0, "read_written": 0, "read_unavailable": 0,
    "read_ungrounded": 0, "read_hedged": 0,
    "read_queued": 0, "read_served": 0,
}

# --- Read sizes, named because they are the cost levers ---------------------
#
# GATE_CHARS: the gate answers ONE yes/no question, and headline + teaser is
# the whole candidate for every news source (national_press caps teasers at
# 400 chars; google_news items are shorter still), so 1,500 chars covers them
# with room and only ever truncates SEC filing bodies.
#
# FULL_READ_CHARS: every field we store lives in the opening paragraphs — a
# funding amount, a named officer, a site city are all first-paragraph facts,
# and 4,000 chars is ~1,000 tokens. News candidates never reach this limit
# (they ARE their opening paragraphs); the only texts it truncates are 8-K
# filing bodies, where what follows page one is exhibits and boilerplate.
# Raising it buys tokens, not fields.
GATE_CHARS = 1500
FULL_READ_CHARS = 4000

# Hard ceiling on FULL read-throughs per run. The gate makes it cheap to LOOK
# at many candidates; this makes it impossible for a busy news day to turn
# looking into a budget-sized bill. Overflow raises Throttled, so run_collect
# defers the candidate un-seen and the next run picks it up: a capped day
# spreads over runs instead of being silently dropped.
#
# RAISED 60 -> 200, authorized by the owner on 2026-07-30, so that every
# candidate the gate keeps can actually be read instead of queueing behind a
# ceiling sized for the single-stage era: the last real run bought all 60 of
# its reads and still budget-deferred 95 gate survivors to the next run. At
# the measured ~$0.00128 per read this bounds ONE RUN at ~$0.26; it was never
# the monthly guarantee and still is not. The enforced ceiling remains
# spend.py, which runs first on every collect job and hard-stops at 90% of
# the monthly allowance, backstopped by the hard cap on the OpenRouter key
# itself. The deterministic closers, story clustering and pre-read known-round
# matching are what keep real demand under this number.
READTHROUGH_CAP = int(os.environ.get("TIT_READTHROUGH_CAP", "200") or "200")

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
 "city": "the city the TEXT NAMES: where the roles are, or where it says the employer is based or headquartered. City only, no country. Empty unless the text names one. Never infer it from the publisher, the country, the language or your own knowledge; that is headquarters_city.",
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


class ReadThroughUnavailable(Throttled):
    """Extraction succeeded and the interpretation did not, so the WHOLE RECORD
    is deferred to a later run.

    THE DECISION, written down because the alternative is the worst outcome in
    this pipeline. Storing the record with an empty read-through would mean
    weakening `validate.build_signal`, which requires a non-empty
    talent_readthrough, and the thing that guard exists to prevent is exactly a
    blank differentiator reaching the page. Writing a placeholder would be
    inventing the interpretation. So the record is deferred:

      * it is NOT lost — this is a Throttled, so run_collect prints DEFER and
        deliberately does not mark the URL seen, and the next run (12h later,
        inside a recency window measured in days) picks the candidate up again;
      * it is NOT silent — the DEFER line names the reason, STATS counts it,
        and because deferrals feed run_collect's `mostly_throttled` check, a
        run where interpretation is broken for every candidate reports the
        collector `degraded` and ops_status exits 2 for a human;
      * it is NOT free — the extraction call was already paid for (~$0.0013),
        and that is the honest cost of the decision. `read_unavailable` beside
        `full_calls` is where that waste shows up.

    Deliberately a subclass of Throttled and NOT of BudgetDeferred: a run that
    defers work on purpose must not trip the breakage alarm, and this IS
    breakage. TIT_READ_MODEL=off is the revert if it stays broken.
    """


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


def gate_enabled() -> bool:
    """Two-stage or single-stage. Read in two places, so it is read once."""
    return bool(GATE_MODEL) and GATE_MODEL.lower() not in ("off", "0", "none")


def read_enabled() -> bool:
    """Split read-through, or the fused behaviour extraction still produces."""
    return bool(READ_MODEL) and READ_MODEL.lower() not in ("off", "0", "none")


# --- The rules still bind on the new call -----------------------------------
#
# The read-through may not introduce a figure, a place, an employer or a claim
# that is not already in the source text or in the extracted fields. Three
# things enforce that, in order of how much they can actually be relied on:
#
# 1. STRUCTURE. The interpretation call is given the headline, 500 characters
#    of source text and the extracted facts — and nothing else. It never sees
#    headquarters_city/country (the model's own knowledge of where a company
#    is), and never sees the publisher line. It has no material to invent a
#    place FROM except its own memory, which is what 2 checks.
# 2. DETERMINISM. Figures and gazetteer places in the returned sentence are
#    checked against the source text and the extracted fields, below. A miss
#    is a refusal, not a repair.
# 3. THE PROMPT. Claim-level grounding ("this raise means hiring") cannot be
#    checked by a regex, so it is prompt-enforced and honestly labelled as
#    such. What backs it up is that the sentence is one or two clauses long
#    and every figure and place in it is machine-checked.
#
# Confidence needs no new guard at all: the interpretation call returns exactly
# one key, `talent_readthrough`. It cannot promote a record's tier because it is
# never asked for one, and infer_confidence still caps on the source host.

# Same rule as validate.assert_figures_are_sourced, with one addition: the word
# multiplier is folded to a letter, so "$71M" in the source and "71 million" in
# the sentence are the same figure. Without that fold, a model restating the
# amount in words would be rejected as an invention — a false refusal, which
# for a deferred record means a story quietly lost.
_MULTIPLIER = {"billion": "b", "bn": "b", "b": "b", "million": "m", "mn": "m",
               "m": "m", "thousand": "k", "k": "k"}
_MULTIPLIER_TAIL = re.compile(r"(billion|thousand|million|bn|mn|[bmk])$", re.I)
_YEAR = re.compile(r"(19|20)\d\d")
_HEDGE = re.compile(
    r"\b(suggests?|may|might|could|possibly|potentially|indicates?|likely)\b", re.I)


def _figures(text: str) -> set[str]:
    """Every numeric token in `text`, canonicalised so the two sides compare."""
    found = set()
    for match in validate._NUMBER.finditer(text or ""):
        token = re.sub(r"[,\s.]", "", match.group(0).lower())
        found.add(_MULTIPLIER_TAIL.sub(
            lambda m: _MULTIPLIER[m.group(0).lower()], token))
    return found


# Where a read-through puts a place: "hiring in Dublin", "adds roles to the
# Dublin market", "engineers across Bengaluru". The frame scanner below reads
# the six phrasings that state a company's SEAT; this reads the ones that state
# where the WORK is, which is what a read-through sentence is actually about.
#
# Frames, not a bare name sweep, in both cases. A sweep over 422 gazetteer
# aliases flags "Reading the announcement" and "Florence" the person, and a
# false positive here defers a real record. The two lookaheads are the cheap
# fixes for the two real collisions: a following capital is a surname ("reports
# to Charlotte Jones"), and a word character means it was never the alias.
#
# The surname lookahead is scoped `(?-i:...)` deliberately: under re.I a bare
# `[A-Z]` matches lowercase too, so the check silently inverted and every
# "in the Dublin market" walked through it. Case is the whole signal here.
_PREPOSITION_PLACE = re.compile(
    r"\b(?:in|to|at|across|near|for|around|throughout)\s+(?:the\s+)?"
    r"(" + cheap_extract._ALIAS_AT.pattern + r")(?!\s+(?-i:[A-Z]))",
    re.I)


def _places(text: str) -> set[str]:
    """Cities the text NAMES, by the frames English uses to name one.

    Two scanners, unioned: the deterministic extractor's own seat frames
    ("X-based", "based in X", "its X office") and the preposition frames above.
    Both resolve through the curated gazetteer, so the comparison in
    `ungrounded_reason` is city-to-city and not string-to-string.
    """
    found, _declined = cheap_extract._scan_for_cities(text or "")
    places = {city for city, _region, _iso2 in found}
    for match in _PREPOSITION_PLACE.finditer(text or ""):
        hit = cheap_extract._resolve_alias(match.group(1))
        if hit:
            places.add(hit[0])
    return places


def ungrounded_reason(sentence: str, classified: dict, raw_text: str) -> str:
    """"" if the sentence is grounded, else why it is not."""
    sourced = f"{raw_text}\n{classified.get('headline') or ''}"
    for key in ("funding_amount", "headcount", "effective_date"):
        value = classified.get(key)
        if value:
            sourced += f"\n{value}"

    invented = _figures(sentence) - _figures(sourced)
    invented = {n for n in invented if not _YEAR.fullmatch(n)}
    if invented:
        return f"figure(s) in neither the source text nor the extracted fields: {sorted(invented)}"

    allowed = _places(sourced)
    stated = vocab_city(classified.get("city"))
    if stated:
        allowed.add(stated)
    elsewhere = _places(sentence) - allowed
    if elsewhere:
        return f"place(s) the source never stated: {sorted(elsewhere)}"

    # A storage code in English prose. prompts._readable strips underscores from
    # every value the writer is shown, so an underscore in the answer is the
    # model's own invention, not a leak from the prompt.
    if "_" in sentence:
        return "contains a storage code (underscore) rather than English"
    return ""


def vocab_city(value) -> str:
    """The curated city name for a stated city, or "". Thin on purpose: the
    gazetteer is the authority and this only unwraps its tuple."""
    hit = vocab.normalize_city((value or "").strip())
    return hit[0] if hit else ""


def interpret(classified: dict, raw: dict, *, timeout: int = 30) -> str:
    """The read-through, from READ_MODEL, on ~450 input tokens.

    Raises ReadThroughUnavailable rather than returning anything doubtful. 401
    and 402 still propagate: a bad key or an exhausted balance ends the run
    whichever stage meets it.
    """
    prompt = prompts.build(classified, raw)
    STATS["read_calls"] += 1
    try:
        content = _call(
            READ_MODEL, prompts.READ_SYSTEM, prompt,
            timeout=timeout, max_tokens=READ_MAX_TOKENS,
            # Anthropic endpoints on OpenRouter do not advertise
            # response_format, and require_parameters then filters every
            # provider out and the request 404s with "No endpoints found".
            # Claude follows a JSON-only instruction; _strip_fences handles the
            # rest. ab_models.py learned this the same way.
            json_mode=not READ_MODEL.startswith("anthropic/"),
        )
    except (AuthFailed, CreditsExhausted):
        raise
    except (Throttled, ClassifyError) as exc:
        STATS["read_unavailable"] += 1
        raise ReadThroughUnavailable(
            f"read-through model {READ_MODEL} did not answer ({exc}) — "
            "deferring the whole record, nothing stored") from exc

    return _accept(content, classified, raw)


def _accept(content: str, classified: dict, raw: dict) -> str:
    """Parse one interpretation and hold it to the rules, or refuse it."""
    try:
        sentence = (json.loads(_strip_fences(content)).get("talent_readthrough")
                    or "").strip()
    except (ValueError, AttributeError):
        STATS["read_unavailable"] += 1
        raise ReadThroughUnavailable(
            f"read-through was unreadable ({content[:120]!r}) — deferring the "
            "whole record rather than storing a blank differentiator")
    if not sentence:
        STATS["read_unavailable"] += 1
        raise ReadThroughUnavailable(
            "read-through came back empty — deferring the whole record rather "
            "than storing a blank differentiator")

    problem = ungrounded_reason(sentence, classified, raw.get("raw_text") or "")
    if problem:
        STATS["read_ungrounded"] += 1
        raise ReadThroughUnavailable(
            f"read-through refused, {problem} — deferring the whole record "
            "rather than storing an invented claim")

    if _HEDGE.search(sentence):
        STATS["read_hedged"] += 1
    STATS["read_written"] += 1
    return sentence


def usage_snapshot() -> dict | None:
    """What this run charged, in the shape store.report_health persists.

    Returns None when no model was called at all — a structured source, an
    offline dry run, a sweep that closed everything deterministically. That is
    reported as NULL rather than as zeros, because a genuinely free run and a
    run whose accounting went missing must not look the same in the ledger.

    `cost_usd` is the PROVIDER's own figure summed across both stages, never
    arithmetic from a published price list. Every cost claim in this repo used
    to be the latter, and a rate card is a forecast.
    """
    if not (STATS["gate_calls"] or STATS["full_calls"]):
        return None
    return {
        "model": MODEL,
        "gate_model": GATE_MODEL if gate_enabled() else "",
        "prompt_tokens": STATS["prompt_tokens"],
        "cached_tokens": STATS["cached_tokens"],
        "completion_tokens": STATS["completion_tokens"],
        # Rounded at the sixth decimal: a single gate call costs ~$0.000004, so
        # anything coarser records a real charge as free.
        "cost_usd": round(float(STATS["usd"]), 6),
        "reads_bought": STATS["full_calls"],
        "rows_from_reads": STATS["read_stored"],
    }


def gate(text: str, *, timeout: int = 30) -> bool:
    """One-word KEEP/DROP from the cheap model. Fails OPEN: if the gate itself
    errors or is throttled, the candidate goes through to the full model, so a
    flaky gate can cost money but can never cost coverage. 401/402 still
    propagate — those end the run whichever stage sees them."""
    STATS["gate_calls"] += 1
    try:
        content = _call(
            GATE_MODEL, GATE_SYSTEM, text[:GATE_CHARS],
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
    if gate_enabled():
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
    STATS["full_chars_raw"] += len(text)
    STATS["full_chars_sent"] += min(len(text), FULL_READ_CHARS)

    # PROMPT-CACHE SHAPE — do not reorder this call. The prefix
    # (MINI_SYSTEM, then SCHEMA_HINT at the head of the user message) is
    # byte-identical on every read-through, and the per-item text comes LAST.
    # DeepSeek caches prompt prefixes automatically and OpenRouter passes that
    # through with no configuration, billing cache reads at 0.1x the input
    # price (openrouter.ai/docs/features/prompt-caching) — SCHEMA_HINT is
    # ~2,600 of the ~3,600 input tokens, so a cache hit removes most of the
    # input bill. Anything inserted BEFORE SCHEMA_HINT (a date, the outlet, a
    # counter) breaks the shared prefix and silently forfeits that. Whether a
    # given call actually hit depends on which provider OpenRouter routed to;
    # STATS["cached_tokens"] records what really happened, per run.
    content = _call(
        MODEL, MINI_SYSTEM, f"{SCHEMA_HINT}\n\n---\n{text[:FULL_READ_CHARS]}",
        timeout=timeout, json_mode=True,
    )

    try:
        parsed = json.loads(_strip_fences(content))
    except ValueError as exc:
        raise ClassifyError(f"unparseable model response: {exc} — got {content[:200]!r}") from exc

    if not parsed.get("is_talent_signal"):
        return None

    # Stage 3. Extraction's own talent_readthrough is overwritten, never merged
    # — a sentence half-written by each model is neither model's judgement. The
    # ORIGINAL raw dict is passed, not the `text` built above: that one carries
    # the "Published by:" geography hint, and a writer handed the outlet writes
    # the outlet's home town into the sentence.
    #
    # A failure here raises ReadThroughUnavailable (a Throttled), so the record
    # is deferred whole rather than stored with a blank differentiator. Read the
    # class docstring before changing that.
    if read_enabled():
        parsed["talent_readthrough"] = interpret(parsed, raw, timeout=timeout)
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
    # OpenRouter usage accounting: the response then carries token counts,
    # its own cost figure, and prompt_tokens_details.cached_tokens — the only
    # ground truth on whether prefix caching actually fired. Costs nothing.
    body["usage"] = {"include": True}
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
        payload = resp.json()
        choice = payload["choices"][0]
    except (KeyError, IndexError, ValueError) as exc:
        raise ClassifyError(f"unexpected response shape: {exc}") from exc

    # Record what this call really cost before any content check can raise:
    # an unparseable response was still paid for.
    usage = payload.get("usage") or {}
    STATS["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    STATS["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    STATS["cached_tokens"] += int(details.get("cached_tokens") or 0)
    try:
        STATS["usd"] += float(usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        pass

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
