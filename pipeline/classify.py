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

from . import cheap_extract, gate_classifier, gate_ledger, prompts, validate, vocab

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

# --- Provider routing, pinned ------------------------------------------------
#
# OpenRouter serves one model slug from several providers and picks per request.
# Extraction's prompt is ~3,100 input tokens of which ~2,476 are a byte-stable
# prefix (MINI_SYSTEM + SCHEMA_HINT), and a prefix cache is per provider: scatter
# the requests and the cache never warms on any of them. `provider.order` is
# OpenRouter's documented way to state a preference (verified against
# openrouter.ai/docs/features/provider-routing on 2026-07-29 rather than guessed:
# the fields are `order`, `allow_fallbacks`, `only`, `ignore`,
# `require_parameters`, `sort`, `data_collection`, `max_price`).
#
# WHAT THIS IS WORTH TODAY: NOTHING, AND THE COMMENT SAYS SO. OpenRouter's own
# endpoints API for `deepseek/deepseek-chat` (checked 2026-07-29) returns exactly
# three endpoints — streamlake, deepinfra/fp4, novita/fp8 — and NOT ONE of them
# publishes an `input_cache_read` price. There is no cache to hit on this slug,
# so the -$2.84/month in TECHLOG 2026-07-30 is not available by pinning and is
# not claimed here. What IS true: `deepseek/deepseek-chat-v3.1` has four
# endpoints that do price cache reads, at ~0.5x and not the 0.1x DeepSeek's own
# API charges, and DeepSeek's first-party endpoint serves neither slug through
# OpenRouter right now. So the honest saving is a model switch away, at half the
# advertised rate, and that is a decision about extraction quality rather than a
# routing tweak.
#
# It ships anyway, because the ordering itself buys three things that cost
# nothing: the prefix stops scattering the day a caching endpoint appears (the
# order already prefers it), `cached_tokens` becomes interpretable instead of a
# mixture, and extraction stops being a quantisation lottery — deepinfra serves
# this model at fp4 and novita at fp8, and today which one reads a filing is
# decided per request.
#
# THE AVAILABILITY TRADEOFF, made explicitly: `allow_fallbacks` is TRUE and there
# is no code path that sets it false. A pinned provider having an outage must
# cost money, never a run: with fallbacks on, OpenRouter drops to the next
# endpoint and the only loss is the cache. `only`/`ignore` would turn one
# provider's bad afternoon into a red collect job, and a collect job that fails
# is a day of signals nobody publishes.
#
# Keyed by model AUTHOR, because a provider slug means nothing for a model that
# provider does not serve: "streamlake" in front of an anthropic/ model is noise.
# TIT_PROVIDER_ORDER overrides the list for the extraction author; set it to
# "off" to send no `order` at all.
PROVIDER_ORDER = {
    # deepseek first: the model author's own endpoint is the only one that would
    # bill a cache read at 0.1x. It is absent from the slug today, and an absent
    # slug in `order` is skipped, so this line is a no-op that becomes a saving
    # by itself if DeepSeek starts serving here again.
    "deepseek": ("deepseek", "streamlake", "novita", "deepinfra"),
}


def provider_order(model: str) -> tuple[str, ...]:
    """The preferred provider slugs for `model`, or () for no preference."""
    author = (model or "").split("/", 1)[0].lower()
    override = (os.environ.get("TIT_PROVIDER_ORDER") or "").strip()
    if override and author in PROVIDER_ORDER:
        if override.lower() in ("off", "0", "none"):
            return ()
        return tuple(s.strip() for s in override.split(",") if s.strip())
    return PROVIDER_ORDER.get(author, ())

# Per-run visibility for the spend ledger: how many one-word gate calls, how
# many were rejected there (cost avoided), how many full read-throughs ran.
# The token counters come from OpenRouter's usage accounting on every call
# (both stages), so a run can report what it actually sent and what the cache
# actually served rather than estimating either.
STATS = {
    "gate_calls": 0, "gate_rejects": 0, "full_calls": 0,
    # The classifier gate's confident bands (plan step 2). clf_relevant skipped
    # a paid LLM gate call; clf_irrelevant dropped without one. The uncertain
    # band needs no counter of its own — it IS gate_calls once the flag is
    # armed, and all of gate_calls before that.
    "clf_relevant": 0, "clf_irrelevant": 0,
    "full_chars_raw": 0,   # candidate text length before truncation
    "full_chars_sent": 0,  # what actually went to the model
    "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0,
    # Which endpoints actually served this run, comma-separated, from
    # OpenRouter's own `provider` field. The only way to know whether
    # PROVIDER_ORDER took effect, and the only way to read `cached_tokens`
    # honestly: 60% cached across three providers means something different from
    # 60% on one. A plain string on purpose — STATS is restored by shallow copy
    # in the tests, so a nested dict here would leak between them.
    "providers": "",
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
    # The conditional second pass. `read_skipped_strong` is extraction's own
    # sentence standing on its own merits and a frontier call not bought;
    # `read_bought_weak` is one the free triage judged not good enough. Both
    # counted because the ratio IS the saving, and a triage that silently
    # stopped flagging anything would otherwise look like a cheaper month.
    "read_skipped_strong": 0, "read_bought_weak": 0,
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
# 60 -> 200 on 2026-07-30 (the cap was starving coverage), then 200 -> 75 the
# same day, and the second move is the one that needs explaining, because it
# looks like a retreat and is not.
#
# WHAT CHANGED IS WHICH CEILING BINDS. At 60 the per-run cap was the constraint:
# a run bought all 60 of its reads and deferred 95 gate survivors. At 200 the
# per-run cap stopped binding almost everywhere and the MONTH became the
# constraint instead. Measured, at cap 200 and full cadence:
#
#   $2.52/day charged across collect.yml and collect-press.yml
#     = $75.60/month against a $25 allowance          (run cost_projection.py)
#
# A cap of 200 does not spend $75; a cap of 200 lets DEMAND spend $75, and
# demand is 862 reads a day. So a run at 200 would spend the month's whole
# allowance in the first ten days, `spend.py --degrade` would switch paid reads
# off, and the last twenty days of every month would collect nothing but the
# free sources. Ten good days and twenty thin ones is worse coverage than
# thirty even ones, and much worse for a tracker whose promise is that it is
# current.
#
# So the cap is sized to the MONTH. 75 is national_press's share of what $25
# buys after the gate's own $4.15: the arithmetic is in cost_projection.py
# section [5] and it is re-derived from the ledger rather than typed here.
# collect.yml sets its own per-source values for the collectors whose demand
# differs (google_news 45, gdelt 8, the SEC pair 40, where 40 is headroom on a
# demand of one or two).
#
# THIS IS RATIONING, AND THE RATION IS SPENT DELIBERATELY. What makes a cap of
# 75 acceptable is `pipeline/candidate_rank.py`: the reads go to the countries
# holding least, and every country's best story is placed before any country's
# second. A capped run is not a random 75 of 249, it is the 75 that buy the
# most coverage. A candidate past the cap defers UNMARKED and returns on the
# next run, so the cap decides when a story is read, never whether.
#
# 75 -> 88 on 2026-07-30, and the raise is EARNED rather than authorised: the
# second model pass became conditional the same day, so a read now costs
# $0.00139 instead of $0.00278 and the same $25 buys twice as many. That is the
# rule for this number in one line — RAISE IT WHEN THE MONEY PER READ FALLS,
# AND NOT BEFORE. Re-derive rather than trusting this comment:
#
#     python3 cost_projection.py        # section [5]
#
# THIS IS STILL RATIONING. Full coverage is 1,282 reads a day and $25 buys 461,
# so 36% of what the gate keeps gets read. What makes that acceptable is
# `pipeline/candidate_rank.py`: the reads go to the countries holding least and
# every country's best story is placed before any country's second, so a capped
# run is not a random 88 of 249, it is the 88 that buy the most coverage. A
# candidate past the cap defers UNMARKED and returns next run, so the cap
# decides when a story is read, never whether.
READTHROUGH_CAP = int(os.environ.get("TIT_READTHROUGH_CAP", "88") or "88")

# --- Whose reads are they? ------------------------------------------------
#
# THE DEFECT (measured 2026-08-01, source_health, the seven days to that date)
#
#     collector        runs  items found  candidates  reads  rows  conversion
#     google_news         6        6,870       3,892    761   354       46.5%
#     national_press      2       21,158       1,160    288   160       55.6%
#     gdelt               4          967         106     62    26       41.9%
#     sec_edgar           3           30          11     12     5       41.7%
#     sec_form_d          3           23           8      6     4       66.7%
#
# The per-run ceilings behind those numbers were google_news 129 and
# national_press 88 (the module default; nothing set one for it). So the
# collector that converts a read into a stored row LESS often, and reads a
# third as many items to find its candidates, held the larger ration. That is
# backwards, and nobody chose it: the two numbers were set in different files
# months apart, one in a bash `case` in collect.yml and one by defaulting.
#
# THE RULE, and it is a rule rather than a pair of numbers
# -------------------------------------------------------
#     A collector's share of the read budget is its share of MEASURED
#     CONVERSION among the collectors whose demand actually reaches the cap.
#
# The arithmetic, in full, so it can be checked rather than believed:
#
#     binding collectors     google_news, national_press
#     their current caps     129 + 88 = 217 reads/run   <- HELD CONSTANT
#     measured conversion    0.465 and 0.556, sum 1.021
#
#     google_news     217 x 0.465 / 1.021 =  98.8  ->   99
#     national_press  217 x 0.556 / 1.021 = 118.2  ->  118
#                                                       217  (unchanged)
#
# THIS REALLOCATES SPEND AND DOES NOT RAISE IT. The sum is pinned to what the
# two caps already bought, and `test_read_budget.py` asserts that, because the
# obvious way to "fix" a starved collector is to give it more and the obvious
# way to do that is to raise the total. MONTHLY_ALLOWANCE_USD is untouched and
# is not this rule's business.
#
# WHY ONLY THOSE TWO. A cap only rations a collector whose demand reaches it.
# On the runs above, sec_edgar bought 2 reads against a ceiling of 40 and
# sec_form_d bought 1 against 40 — those are headroom, and taking headroom away
# from a source that never uses it frees no money. gdelt bought 9 against 9 and
# deferred 0-1, so it is exactly at its ration with nothing waiting; moving it
# would be churn. national_press deferred 162 candidates on its last run and
# google_news deferred 12 on its, which is the same finding from the other end.
#
# WHAT THIS DOES NOT FIX, and must not be read as fixing: national_press got
# TWO runs in those seven days against google_news's six. Most of its weekly
# read deficit is run count, not ration, and run count is a scheduling
# question in .github/workflows/, not a number in this file. A per-run share
# cannot compensate for a run that never happened.
#
# THIS SITS ABOVE candidate_rank.py AND DOES NOT REPLACE IT. This decides how
# many reads a collector may buy; `pipeline/candidate_rank.py` decides WHICH,
# giving every country's best story a place before any country's second (75
# reads across 6 countries on its first production run, none above 17%). The
# two answer different questions and both are still asked.
#
# RE-DERIVE RATHER THAN TRUSTING THIS COMMENT. The conversion figures are a
# measurement with a date on them, and a ration set from a stale one is a
# ration nobody chose:
#
#     python3 cost_projection.py        # section [5]
#     sqlite3 data/talent_intel.db "SELECT collector, SUM(reads_bought), \
#       SUM(rows_from_reads) FROM source_health \
#       WHERE run_at >= datetime('now','-7 days') GROUP BY collector"

#: Rows stored per read bought, per collector. MEASURED 2026-08-01 over the
#: seven days to that date. Only collectors whose demand reaches their cap
#: appear here; see the note above on why headroom is not a ration.
READ_CONVERSION = {
    "national_press": 0.556,   # 160 rows / 288 reads
    "google_news": 0.465,      # 354 rows / 761 reads
}

#: The per-run total those two already bought, held constant by the split.
#: 129 (google_news, set in collect.yml) + 88 (national_press, the module
#: default). Changing this IS a spend decision and belongs to the owner.
BINDING_READ_BUDGET = 217


def _derive_read_caps() -> dict[str, int]:
    """Split BINDING_READ_BUDGET by measured conversion, losing nothing.

    Largest-remainder rather than plain rounding, because two independent
    `round()` calls can lose or gain a read and a budget that does not add up
    is a budget nobody can check.
    """
    total = sum(READ_CONVERSION.values())
    exact = {name: BINDING_READ_BUDGET * share / total
             for name, share in READ_CONVERSION.items()}
    caps = {name: int(value) for name, value in exact.items()}
    leftover = BINDING_READ_BUDGET - sum(caps.values())
    for name in sorted(exact, key=lambda n: exact[n] - caps[n], reverse=True):
        if leftover <= 0:
            break
        caps[name] += 1
        leftover -= 1
    return caps


COLLECTOR_READ_CAPS = _derive_read_caps()


def read_cap(collector: str | None) -> int:
    """This run's read ceiling for one collector.

    TIT_READTHROUGH_CAP STILL WINS when it is set, and that is deliberate:
    the backfills set it to 5000 because a month of filings would otherwise
    defer almost entirely, and a derived daily ration silently overriding an
    explicitly requested one is the kind of surprise this repo keeps paying
    for. An explicit number beats a derived one; a derived one beats a
    default.
    """
    if os.environ.get("TIT_READTHROUGH_CAP"):
        return READTHROUGH_CAP
    return COLLECTOR_READ_CAPS.get((collector or "").strip(), READTHROUGH_CAP)

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


class BudgetExhausted(BudgetDeferred):
    """The MONTH's allowance is gone, so paid reads are off for this job.

    Set by `spend.py --degrade`, which writes TIT_PAID_READS=off into the job's
    environment instead of failing the step. A BudgetDeferred, so it lands in
    exactly the retry-next-run path the per-run cap uses: the candidate is
    printed as DEFER, is NOT marked seen, and is read on a later run. Nothing
    is dropped and nothing is stored half-read.

    Distinct from BudgetDeferred only so the run log can say which ceiling was
    reached — a per-run cap is normal rationing and a monthly one is the
    product running degraded, and those two want different reactions from a
    human. The free collectors, the free prefilter, deterministic extraction
    and both dedup layers are all unaffected: this raises inside `classify`,
    which is the only function here that can spend.
    """


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


def read_always() -> bool:
    """Buy the interpretation for EVERY record, as it did before 2026-07-30.

    The one-variable revert if the conditional turns out to cost quality the
    deterministic triage cannot see. Default off, i.e. conditional: see
    `interpret_late` for the measurement that decided it.
    """
    return (os.environ.get("TIT_READ_ALWAYS") or "").strip().lower() in (
        "1", "true", "yes", "on")


def paid_reads_enabled() -> bool:
    """False once the month's allowance is spent.

    `spend.py --degrade` writes TIT_PAID_READS=off into the job environment
    rather than failing the step, so the free half of the pipeline carries on.
    Default ON: a missing variable, an unreadable one or a value nobody
    recognises means spend, because failing closed here would silently stop
    collection over a typo, and the OpenRouter key's own hard cap is the
    backstop underneath this either way.
    """
    return (os.environ.get("TIT_PAID_READS") or "on").strip().lower() not in (
        "off", "0", "no", "false")


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

    # Off by default. When on, the interpretation is not bought now: it is
    # spooled for the batch API at half price and a LATER run publishes the
    # record. See the batch section at the foot of this module for the latency
    # this costs and why the synchronous path stays the default.
    if read_batch_enabled():
        served = batch_take(prompt)
        if served is None:
            raise ReadThroughUnavailable(
                "queued for the batch API (TIT_READ_BATCH=1) — a later run "
                "publishes this record, nothing is stored now")
        STATS["read_served"] += 1
        return _accept(served, classified, raw)

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


def usage_snapshot(*, candidates: int | None = None,
                   budget_deferred: int | None = None) -> dict | None:
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
        # The funnel. `budget_deferred` and `candidates` belong to the run
        # rather than to this module, so they are passed in; passing neither
        # leaves both NULL, which is what a caller that does not know them
        # should record rather than a zero that reads as "none deferred".
        "gate_calls": STATS["gate_calls"],
        "gate_rejects": STATS["gate_rejects"],
        **({"candidates": int(candidates)} if candidates is not None else {}),
        **({"budget_deferred": int(budget_deferred)}
           if budget_deferred is not None else {}),
    }


def gate_verdict(text: str, *, timeout: int = 30) -> str:
    """The gate's answer as one of YES, NO or ERROR.

    ERROR is the third value and it is the whole reason this function exists
    beside `gate()`. The gate FAILS OPEN — a throttled or erroring gate lets the
    candidate through — and the boolean cannot tell "the model said yes" from
    "the model never answered". Recording the second as a YES would teach the
    classifier being trained on these labels that provider outages are talent
    signals, so the two are separated here and `gate()` folds them back for
    every caller that only wants the routing decision.

    401/402 still propagate: those end the run whichever stage sees them.
    """
    STATS["gate_calls"] += 1
    try:
        content = _call(
            GATE_MODEL, GATE_SYSTEM, text[:GATE_CHARS],
            timeout=timeout, max_tokens=4, json_mode=False,
        )
    except (AuthFailed, CreditsExhausted):
        raise
    except (Throttled, ClassifyError):
        return gate_ledger.ERROR
    if "YES" in content.upper():
        return gate_ledger.YES
    STATS["gate_rejects"] += 1
    return gate_ledger.NO


def gate(text: str, *, timeout: int = 30) -> bool:
    """One-word KEEP/DROP from the cheap model. Fails OPEN: if the gate itself
    errors or is throttled, the candidate goes through to the full model, so a
    flaky gate can cost money but can never cost coverage. 401/402 still
    propagate — those end the run whichever stage sees them."""
    return gate_verdict(text[:GATE_CHARS], timeout=timeout) != gate_ledger.NO


def classify(raw: dict, *, timeout: int = 45,
             interpret_now: bool = True) -> dict | None:
    """Classify one candidate. Returns None if it is not a talent signal.

    `interpret_now=False` returns the EXTRACTION ONLY, leaving extraction's own
    `talent_readthrough` in the dict. The caller then runs the free guards —
    `validate.build_signal`, then the two dedup layers — and buys the
    interpretation from `interpret()` only for a record that is actually going
    to be stored. Measured over the nine runs in the ledger to 2026-07-30:
    477 interpretations were bought and 320 rows stored, so **32.9% of the
    most expensive call in the pipeline was spent on records the page never
    got**. Nothing about the interpretation depends on when it is bought — it
    reads the extracted facts and the teaser, both unchanged by validation —
    so this is a pure saving with no effect on what is stored or how it reads.

    The default stays True so a caller that has no database (ab_models.py, the
    backfills' probe paths, every test that predates this) behaves exactly as
    it did. `run_collect` is the one caller that passes False.
    """
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

    # The monthly ceiling, checked before the cheapest paid call rather than
    # after it. Raised as a BudgetDeferred, so the candidate defers UNMARKED
    # and a later run reads it: hitting the allowance costs depth, never
    # coverage. Everything free about this pipeline is upstream of here and
    # keeps running.
    if not paid_reads_enabled():
        raise BudgetExhausted(
            "the month's allowance is spent (TIT_PAID_READS=off, set by "
            "spend.py --degrade) — deferring this candidate to a later run; "
            "the free collectors and deterministic extraction are unaffected"
        )

    # Stage 1: the one-word gate. A rejection here costs ~1/40th of a full
    # read-through and is the whole reason the candidate cap can be generous.
    #
    # Every verdict is also written to the label ledger. That is bookkeeping
    # around a call that already happened — no extra model call, no change to
    # what is kept — and it is the training set for the classifier that
    # replaces most of this gate (docs/PLAN-gate-to-five-dollars.md, step 1).
    # `gate_ledger` swallows its own failures, so it cannot cost a run.
    collector = (raw.get("collector") or "").strip()
    if gate_enabled():
        # Stage 0.5: the LOCAL classifier gate (plan step 2), three ways.
        # confident-RELEVANT skips the paid gate call; confident-IRRELEVANT
        # drops; UNCERTAIN falls through to the LLM gate exactly as before.
        # `route_item` fails open to UNCERTAIN on every doubt — no committed
        # artifact, an unarmed or stale flag, a language it never trained on,
        # any exception — so until the weekly trainer arms the flag this block
        # is a no-op and after any failure it degrades to yesterday's
        # behaviour. A classifier failure may cost money, never coverage.
        clf_route = gate_classifier.route_item(raw)
        if clf_route == gate_classifier.RELEVANT:
            STATS["clf_relevant"] += 1
            gate_ledger.record(raw, collector, gate_ledger.CLF_YES)
        elif clf_route == gate_classifier.IRRELEVANT:
            STATS["clf_irrelevant"] += 1
            gate_ledger.record(raw, collector, gate_ledger.CLF_NO)
            return None
        else:
            verdict = gate_verdict(text, timeout=min(timeout, 30))
            gate_ledger.record(raw, collector, verdict)
            if verdict == gate_ledger.NO:
                return None
    else:
        # Single-stage runs have no verdict to record, but the candidate and
        # its eventual outcome are still worth a line: "did this become a
        # stored row" is the target the classifier is actually trained on.
        gate_ledger.record(raw, collector, gate_ledger.OFF)

    # Stage 2 is the expensive call, so it carries the per-run ceiling.
    # Raised as Throttled because that is already the "not now, retry next
    # run, do not mark seen" path in run_collect.
    #
    # Per COLLECTOR since 2026-08-01: the ration follows measured conversion
    # rather than whichever file happened to set a number. See read_cap().
    cap = read_cap(collector)
    if STATS["full_calls"] >= cap:
        raise BudgetDeferred(
            f"read-through cap ({cap}/run for {collector or 'this collector'}) "
            f"reached — deferring to the next run"
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
    #
    # `interpret_now=False` postpones it to `interpret_late()` instead, once
    # the free guards have said the record will store. Nothing else changes.
    if read_enabled() and interpret_now:
        parsed["talent_readthrough"] = interpret(parsed, raw, timeout=timeout)
    return parsed


def interpret_late(signal, classified: dict, raw: dict, *,
                   timeout: int = 45) -> None:
    """Buy the interpretation for a record that has already cleared every free
    guard, and write it onto the built signal in place.

    Called by `run_collect` after `validate.build_signal` and after both dedup
    layers have returned "this will store". Raises the same
    `ReadThroughUnavailable` the inline call does, so the caller's existing
    Throttled handling defers the record whole and the next run retries it.

    Safe to write onto a built signal because `content_hash` is computed from
    the employer, pillar, date, headline and outlet — never from the
    read-through — so the fingerprint the dedup layers just agreed on does not
    move underneath them. `validate.build_signal` requires the read-through to
    be non-empty and never checks its figures (that is `_accept`'s job, and it
    runs on whatever sentence comes back here), so the guard set is identical
    whichever order the two calls happen in. A test pins both properties.

    CONDITIONAL SINCE 2026-07-30, and this is the largest cost decision in the
    pipeline, so the reasoning is here rather than in a commit message.

    The question that had not been asked: extraction and the read-through were
    $31.69 and $31.29 a month at full worldwide coverage — 83% of the bill for
    reading every story twice. What does the second pass buy? **One field.**
    `interpret()` returns `{"talent_readthrough": ...}` and this function
    writes that single attribute; it is never asked for the employer, country,
    pillar, amount or direction, and it sees 500 characters of teaser against
    extraction's 4,000. It cannot change a stored fact and cannot know anything
    extraction did not. Extraction already produced its own version of that
    same field, for free, in the call that was already paid for.

    Measured on 4,171 rows carrying the fused deepseek sentence and 452
    carrying claude-sonnet-5's, against `prompts.weak_reasons`:

        deepseek, fused     8.8% flagged  (8.7% of the Latin-script subset)
        claude-sonnet-5     2.2% flagged  (1.0% of the Latin-script subset)

    That gap is the evidence the triage measures what it claims to: on
    comparable text it flags deepseek's prose nine times as often as Sonnet's.
    So the frontier model is bought for the ~9% that need it instead of the
    100% that were getting it.

    TWO WAYS IT REFUSES TO BE CLEVER. Anything it cannot score — an unsegmented
    script, too few words to compare — is sent to the model, because the
    languages it cannot score are exactly the ones the coverage gap is made of.
    And extraction's own sentence has to pass `ungrounded_reason` before it is
    allowed to stand: that check used to run only on the paid sentence, so
    keeping the free one without it would quietly reopen the invented-figure
    hole the split closed.

    `TIT_READ_ALWAYS=1` restores the unconditional call in one variable.
    """
    if not read_enabled():
        return

    if not read_always():
        own = (signal.talent_readthrough or "").strip()
        why = prompts.weak_reasons(own, signal.headline or "")
        if not why:
            # It also has to be grounded. The paid sentence is checked by
            # `_accept`; the free one has never been checked by anything,
            # because until now it was always overwritten.
            problem = ungrounded_reason(own, classified, raw.get("raw_text") or "")
            if problem:
                why = (f"ungrounded: {problem}",)
        if not why:
            STATS["read_skipped_strong"] += 1
            return
        STATS["read_bought_weak"] += 1

    signal.talent_readthrough = interpret(classified, raw, timeout=timeout)


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
    provider: dict = {}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
        # OpenRouter routes a model across several providers, and not all of
        # them honour response_format. One that ignores it returns empty
        # content, which looks like a parse bug rather than a routing one —
        # 8 of the first 10 live classifications failed this way. This pins
        # routing to providers that actually support the parameters we send.
        provider["require_parameters"] = True
    order = provider_order(model)
    if order:
        provider["order"] = list(order)
        # Always true, deliberately. See PROVIDER_ORDER: a preference may cost
        # the cache, it may never cost the run.
        provider["allow_fallbacks"] = True
    if provider:
        body["provider"] = provider

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
    served = payload.get("provider")
    if isinstance(served, str) and served.strip():
        seen = {p for p in STATS["providers"].split(",") if p}
        STATS["providers"] = ",".join(sorted(seen | {served.strip()}))

    content = (choice.get("message", {}).get("content") or "").strip()
    if not content:
        # Say WHY it was empty. "Expecting value: line 1 column 1" tells you
        # nothing; finish_reason usually tells you everything.
        raise ClassifyError(
            f"model returned empty content (finish_reason="
            f"{choice.get('finish_reason')!r}, provider={resp.json().get('provider')!r})"
        )
    return content


# --- The batch API, behind a flag, DEFAULT OFF -------------------------------
#
# OpenRouter exposes an asynchronous batch API (POST /api/beta/batches, GET
# /api/beta/batches/{id}) and prices the batch variant of a model at exactly
# half the synchronous rate — measured from its own /models endpoint on
# 2026-07-30: anthropic/claude-sonnet-5 is $2.00/$10.00 per M tokens and
# anthropic/claude-sonnet-5:batch is $1.00/$5.00. Going through OpenRouter
# rather than Anthropic directly is the whole reason this is affordable to
# maintain: same key, same 402 handling, same usage accounting, so spend.py and
# the health ledger keep working. An Anthropic-direct implementation would need
# a second secret and would spend money that spend.py cannot see.
#
# WHAT IT COSTS, plainly: the completion window is 24 hours, so BATCHING BREAKS
# SAME-RUN PUBLISHING. A record's interpretation is submitted on one run and
# collected by a later one; twice-daily collection means a story typically
# appears 12-24h after it was read, and up to 24h later than that in the worst
# case. Nothing is lost — the candidate's URL is never marked seen, so the next
# run re-reads it and finds its own answer waiting — but freshness is the price,
# and freshness is what a talent-signal tracker sells. That is why the flag is
# off and the synchronous path is the default.
#
# The spool is a file, not the database. `store` is not involved: a queued
# interpretation is not a record, and a half-written record must never exist.
READ_BATCH_URL = "https://openrouter.ai/api/beta/batches"
READ_BATCH_DIR = os.environ.get("TIT_READ_BATCH_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "read_batch")

# A run that is rehearsing must not queue, submit or harvest anything.
DRY_RUN = False


def set_dry_run(value: bool) -> None:
    """Module state, like STATS, and read only by the batch spool."""
    global DRY_RUN
    DRY_RUN = bool(value)


def read_batch_enabled() -> bool:
    return (os.environ.get("TIT_READ_BATCH") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _batch_path(name: str) -> str:
    return os.path.join(READ_BATCH_DIR, name)


def _batch_load(name: str, default):
    try:
        with open(_batch_path(name)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _batch_save(name: str, payload) -> None:
    if DRY_RUN:
        return
    os.makedirs(READ_BATCH_DIR, exist_ok=True)
    with open(_batch_path(name), "w") as fh:
        json.dump(payload, fh)


def batch_key(prompt: str) -> str:
    """The custom_id for one interpretation.

    A hash of the prompt, so the answer belongs to the exact question: if the
    teaser is re-fetched slightly differently, or a fact changes, the key
    changes and the stale answer is never applied to the new record. Prompt
    text is the whole input, so nothing else identifies it as precisely.
    """
    return "read-" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:40]


def batch_take(prompt: str) -> str | None:
    """A harvested answer for this prompt, or None having queued it."""
    key = batch_key(prompt)
    results = _batch_load("results.json", {})
    if key in results:
        answer = results.pop(key)
        _batch_save("results.json", results)
        return answer

    pending = _batch_load("pending.json", {})
    if key not in pending:
        pending[key] = prompt
        _batch_save("pending.json", pending)
        STATS["read_queued"] += 1
    return None


def harvest_batches(*, timeout: int = 60) -> tuple[int, list[str]]:
    """Collect finished batches. Returns (answers harvested, notes to print).

    Runs FIRST on a collect job, so the answers this run's candidates need are
    already on disk by the time they are re-read. A batch that failed, expired
    or was cancelled is dropped and says so: its candidates were never marked
    seen, so they come round again and re-queue themselves.
    """
    if DRY_RUN:
        return 0, ["batch harvest skipped on a dry run"]
    submitted = _batch_load("submitted.json", [])
    if not submitted:
        return 0, []

    results = _batch_load("results.json", {})
    still_running, notes, harvested = [], [], 0
    for batch_id in submitted:
        try:
            resp = requests.get(
                f"{READ_BATCH_URL}/{batch_id}",
                headers={"Authorization": f"Bearer {_api_key()}",
                         "User-Agent": USER_AGENT},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            notes.append(f"batch {batch_id}: not reachable ({exc}), will retry")
            still_running.append(batch_id)
            continue
        if resp.status_code >= 400:
            notes.append(f"batch {batch_id}: HTTP {resp.status_code}, will retry")
            still_running.append(batch_id)
            continue

        payload = resp.json() or {}
        status = payload.get("status")
        if status not in ("completed", "failed", "expired", "cancelled"):
            notes.append(f"batch {batch_id}: {status}, nothing to collect yet")
            still_running.append(batch_id)
            continue
        if status != "completed":
            notes.append(f"batch {batch_id}: {status} — its candidates will be "
                         "read again and re-queued")
            continue

        for item in payload.get("results") or []:
            body = ((item.get("response") or {}).get("body") or {})
            content = (((body.get("choices") or [{}])[0].get("message") or {})
                       .get("content") or "").strip()
            if item.get("custom_id") and content:
                results[item["custom_id"]] = content
                harvested += 1
        # The batch's own usage figure, so a batched month is still measured
        # rather than estimated. It lands on the health row of the run that
        # HARVESTED it, not the one that submitted it — an unavoidable
        # consequence of asynchrony, and another reason the default is sync.
        usage = payload.get("usage") or {}
        STATS["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        STATS["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        try:
            STATS["usd"] += float(usage.get("cost") or 0.0)
        except (TypeError, ValueError):
            pass
        counts = payload.get("request_counts") or {}
        notes.append(f"batch {batch_id}: completed, {counts.get('completed', '?')} "
                     f"of {counts.get('total', '?')} answers, "
                     f"${float(usage.get('cost') or 0):.4f}")

    _batch_save("results.json", results)
    _batch_save("submitted.json", still_running)
    return harvested, notes


def submit_pending(*, timeout: int = 120) -> tuple[int, str]:
    """Send everything queued this run. Returns (requests sent, note).

    Runs LAST, so one run submits one batch however many candidates it read.
    """
    if DRY_RUN:
        return 0, "batch submit skipped on a dry run"
    pending = _batch_load("pending.json", {})
    if not pending:
        return 0, ""

    # `endpoint` and `model` MUST be serialised before `requests`: OpenRouter
    # stream-parses the body so it can accept very large arrays, and returns
    # 400 if `requests` comes first. Insertion order is the contract here.
    body = {
        "endpoint": "/v1/chat/completions",
        "model": READ_MODEL,
        "requests": [
            {"custom_id": key,
             "body": {"temperature": 0, "max_tokens": READ_MAX_TOKENS,
                      "messages": [
                          {"role": "system", "content": prompts.READ_SYSTEM},
                          {"role": "user", "content": prompt},
                      ]}}
            for key, prompt in sorted(pending.items())
        ],
    }
    try:
        resp = requests.post(
            READ_BATCH_URL,
            headers={"Authorization": f"Bearer {_api_key()}",
                     "Content-Type": "application/json",
                     "User-Agent": USER_AGENT},
            data=json.dumps(body),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return 0, f"batch submit failed ({exc}) — {len(pending)} still queued"
    if resp.status_code >= 400:
        return 0, (f"batch submit refused (HTTP {resp.status_code}: "
                   f"{resp.text[:160]}) — {len(pending)} still queued")

    batch_id = ((resp.json() or {}).get("id") or "").strip()
    if not batch_id:
        return 0, f"batch submit returned no id — {len(pending)} still queued"

    submitted = _batch_load("submitted.json", [])
    submitted.append(batch_id)
    _batch_save("submitted.json", submitted)
    _batch_save("pending.json", {})
    return len(pending), (f"batch {batch_id} submitted with {len(pending)} "
                          "read-through(s); a later run will publish them")


def _strip_fences(content: str) -> str:
    """Models wrap JSON in ```json fences often enough to be worth handling."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text
