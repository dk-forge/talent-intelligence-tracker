"""The credibility gate (spec 2). Everything the model produced is suspect
until this module clears it.

A rejection here is a success, not a failure. Publishing a plausible-but-wrong
company fact is the one mistake this product cannot survive.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import identity, prefilter, vocab


class Rejected(Exception):
    """Carries the reason so a dry run can show why a candidate was dropped."""


@dataclass
class Signal:
    signal_id: str
    headline: str
    summary: str
    talent_readthrough: str
    company: str
    company_key: str
    ticker: str | None
    cik: str | None
    employer_type: str | None
    pillar: str
    signal_direction: str
    city: str | None
    region: str | None
    country: str | None
    hq_city: str | None
    hq_country: str | None
    state: str | None
    functions: str | None
    industry: str | None
    headcount: int | None
    headcount_scope: str | None
    funding_amount: str | None
    funding_amount_usd: int | None
    funding_stage: str | None
    work_mode: str | None
    deal_type: str | None
    site_event: str | None
    materiality: str
    confidence: str
    source_url: str
    source_name: str
    discovery_url: str | None
    published_date: str | None
    effective_date: str | None
    captured_at: str
    as_of: str
    content_hash: str
    predicted_outcome: str | None
    check_after_date: str | None
    collector: str
    # Provenance annotation, not a fact about the story. Carries the
    # deterministic-extraction marker (cheap_extract.EVIDENCE_NOTE) so a
    # reader of the database can see when no model read the item; revisions
    # overwrite it with the correction note in store.revise. Deliberately not
    # in publish.FIELDS.
    notes: str | None = None


# A figure the model returns must appear in the source text. Matches 1,200 /
# 1200 / 1.2bn / €5B / 5 billion.
#
# THE MAGNITUDE MAY NOT BE ON THE NEXT LINE. The suffix used to sit behind a bare
# `\s*`, and `\s` matches a newline, so the pattern swallowed a line break and
# took the first letter of the next line as if it were a magnitude:
#
#   "28.07.2026\n\nK M Sugar Mills" -> '28072026k'
#
# That is silent record loss, because `assert_figures_are_sourced` compares the
# model's numbers against the source's as SETS. Every collector joins its fields
# with a blank line, so the glue lands on the SOURCE side, whose layout the model
# does not reproduce: a figure that IS verbatim in the source then reads as
# invented, and the whole record is discarded rather than repaired. `_sourced_int`
# and `_sourced_figure` read the same token set, so the quieter version of the
# same bug drops a stated headcount or funding amount off a record that still
# stores. collectors/bse_india.py quotes its own filed description to dodge this;
# that workaround can stay, it is belt and braces now.
#
# MEASURED, before and after (analysis/figures/replay.py over 15,711 current
# rows): the glue FIRED on 465 sec_execcomp raw_texts — headline ending in a
# filing date, body opening with a company name beginning B, M or K — and cost
# nothing there, because that body repeats the date and the clean token survives
# elsewhere in the text. What it cost on the sources whose bodies we no longer
# hold is NOT KNOWABLE: a rejected candidate leaves a URL in `seen_urls` with no
# text and no reason, so nobody can attribute those rejections, to this rule or
# any other.
#
# WHAT WAS DELIBERATELY NOT CHANGED, having been tried and measured. There is no
# `\b` after the suffix, so any word starting with b, m or k still glues INSIDE a
# line: "hire 300 by 2027" -> '300b'. That is the same defect and it is commoner
# (261 sites over 163 stored rows), but the obvious fix — `(?:...)\b` — is a
# REGRESSION here: it newly rejects 14 of the 15,711 stored rows, every one a
# foreign-language funding round. The missing boundary is doing multilingual
# magnitude folding by accident. "500 millones", "3 millions d'euros",
# "3,2 Millionen", "150 miliona", "25 millioner" and "411 millions" all truncate
# to 'm', and that is what makes them compare equal to the model's English
# "500 million". The feed set spans 43 languages (data/feeds.csv), so replacing
# the accident on purpose means a magnitude vocabulary in 43 languages — and a
# partial vocabulary fails silently and looks like sparse data, which is a trap
# this repo has already paid for once. Adding `\b` alone also breaks '$4.5M' in a
# headline against '$4.5 million' in a summary (23 rows), which is the same
# accident in English. Both need a fold written down before a boundary can land.
#
# The class below is every character `\s` matches EXCEPT the ones that end a
# line. Written out because the point of it is exactly what it excludes: NBSP and
# the typographic spaces stay IN (scraped prose is full of them, and "5 million"
# with an NBSP is still five million), CR/LF/FF/VT and the Unicode line and
# paragraph separators go OUT.
_H_SPACE = r"[^\S\r\n\f\v\x1c\x1d\x1e\x85\u2028\u2029]"
_NUMBER = re.compile(
    r"\d[\d,.]*" + rf"(?:{_H_SPACE}*(?:bn|b|m|k|billion|million|thousand))?",
    re.I)

# A single job advert is not market intelligence. GDELT surfaces job boards
# freely, and one live run stored "Claims Strategy Manager - Remote at Allstate"
# from an insurancejournal.com/jobs/ URL. Storing adverts would make this a bad
# job board rather than a signal tracker: we track that an employer is hiring at
# scale, not each vacancy.
_JOB_POSTING_PATH = re.compile(
    r"/(jobs?|careers?|vacanc(?:y|ies)|job-openings?|job-listings?|employment)"
    r"(/|$|\?)", re.I
)
_JOB_BOARD_HOSTS = frozenset({
    "indeed.com", "www.indeed.com", "linkedin.com", "www.linkedin.com",
    "glassdoor.com", "www.glassdoor.com", "ziprecruiter.com",
    "www.ziprecruiter.com", "monster.com", "www.monster.com",
    "totaljobs.com", "www.totaljobs.com", "reed.co.uk", "www.reed.co.uk",
    "seek.com.au", "www.seek.com.au", "naukri.com", "www.naukri.com",
})

# Aggregators are discovery pointers, never stored sources (spec 2 rule 5).
_BLOCKED_SOURCE_HOSTS = frozenset({
    "news.google.com",
    "news.yahoo.com",
    "flipboard.com",
    "msn.com",
    "www.msn.com",
})


def _normalize_number(token: str) -> str:
    return re.sub(r"[,\s.]", "", token.lower())


def _numbers_in(text: str) -> set[str]:
    return {_normalize_number(m.group(0)) for m in _NUMBER.finditer(text or "")}


def assert_figures_are_sourced(claim_text: str, raw_text: str) -> None:
    """Spec 2 rule 2: never let the model invent a number.

    Every numeric token the model emitted must also appear in the source text.
    A model that hallucinates '€5B' into a summary fails here and the whole
    record is discarded — we do not try to repair it.
    """
    invented = _numbers_in(claim_text) - _numbers_in(raw_text)
    # Years are the one safe exception: a model restating "in 2026" from a
    # published date is not inventing a figure.
    invented = {n for n in invented if not re.fullmatch(r"(19|20)\d\d", n)}
    if invented:
        raise Rejected(f"figure(s) not present in source text: {sorted(invented)}")


def infer_confidence(source_url: str, model_confidence: str | None) -> str:
    """Confidence is earned by the source, then capped by the model's read.

    A primary source can be 'verified'. Anything else is at best 'reported',
    and a model saying 'verified' about a news article never promotes it
    (spec 2 rule 3).
    """
    host = (urlparse(source_url).hostname or "").lower()
    ceiling = "verified" if host in vocab.PRIMARY_SOURCE_DOMAINS else "reported"

    stated = vocab.normalize_confidence(model_confidence or "") or "reported"
    order = {"rumored": 0, "reported": 1, "verified": 2}
    return stated if order[stated] <= order[ceiling] else ceiling


def strip_outlet_suffix(headline: str, source_name: str | None = None) -> str:
    """Drop a trailing ' - Outlet' / ' – Outlet' from a headline.

    Google News RSS appends the publisher to every title. The model echoes the
    headline it was given, and whether it keeps that suffix is nondeterministic
    - the SAME OpenAI Dublin story hashed two different ways ten minutes apart
    ("...Dublin" vs "...Dublin - The Straits Times"), so exact-hash dedup let
    both through and the live page carried the duplicate (audit 2026-07-28).
    Hashing the stripped form makes the fingerprint stable across runs.
    """
    text = (headline or "").strip()
    # Only the last segment, and only when it is short enough to be a masthead
    # rather than part of the story ("Acme raises $5M - and hires 40" stays).
    m = re.search(r"\s+[-–—]\s+([^-–—]{2,40})$", text)
    if not m:
        return text
    tail = m.group(1).strip()
    if source_name and tail.casefold() == source_name.strip().casefold():
        return text[:m.start()].strip()
    # No source_name to match against: a trailing segment with no sentence
    # punctuation and few words is a masthead in practice.
    if len(tail.split()) <= 5 and not re.search(r"[.!?,;:]", tail):
        return text[:m.start()].strip()
    return text


def content_hash(company_key: str, pillar: str, published_date: str | None,
                 headline: str, source_name: str | None = None) -> str:
    norm_headline = strip_outlet_suffix(headline, source_name)
    norm_headline = re.sub(r"[^a-z0-9 ]", "", norm_headline.lower())
    norm_headline = re.sub(r"\s+", " ", norm_headline).strip()
    payload = f"{company_key}|{pillar}|{published_date or ''}|{norm_headline}"
    return hashlib.md5(payload.encode()).hexdigest()


# --- Placeholder employers -------------------------------------------------
#
# "No named employer, no record" was already the rule, and it still let
# "$7B firm expands Cary office space, plans to hire" onto the live page under
# the company name "$7B firm". A description is not a name: nobody can filter
# by it, join it to the sibling tracker, or act on it.
#
# The whole risk here is over-reach, because numbers and symbols are perfectly
# ordinary in real names — 3M, 7-Eleven, 8x8, 23andMe, 374Water and "$1 Dollar
# Stores" are all real employers, and three of those are already in the table.
# So the test is not "does it look odd", it is "is EVERY word of it generic":
# a name is rejected only when nothing in it is a proper noun.

# The organisational noun a placeholder ends with. A real name can end with one
# of these too ("Deere & Company", "The Kroger Co."), which is why the tokens
# BEFORE it decide the outcome.
_GENERIC_ORG_NOUNS = frozenset("""
    firm firms company co corp business businesses employer employers
    bank banks lender lenders insurer insurers retailer retailers
    manufacturer manufacturers maker makers giant giants startup startups
    chain chains operator operators provider providers agency agencies
    conglomerate multinational group outfit
""".split())

# Words that describe an employer without naming one.
#
# Deliberately WITHOUT nationality, geography and scale — "national", "global",
# "international", "first", "US", "American". Those are the distinctive word in
# plenty of real names: the first draft of this list rejected "National Bank
# Holdings Corp", which is a real employer sitting in the table, and it would
# have taken American Airlines and Global Payments with it. Anything ambiguous
# belongs OUT of this list: a false reject loses a real employer silently and
# forever, while a false accept is one visible bad row.
_GENERIC_QUALIFIERS = frozenset("""
    a an the this that another one certain
    major large big small mid-sized midsize leading top prominent
    well-known unnamed undisclosed unidentified unspecified anonymous
    confidential mystery
    tech technology ai software fintech biotech pharma healthcare
    media pr marketing advertising retail insurance investment consulting
    law legal accounting telecom staffing recruitment
""".split())

# Words that say outright that the employer was not named.
_ANONYMITY_MARKERS = frozenset({
    "unnamed", "undisclosed", "unidentified", "unspecified", "anonymous",
    "confidential", "mystery",
})

# A currency amount standing in for a name: "$7B firm", "€500M retailer".
# The currency symbol is required. Without it "3M" is a money token and 3M is a
# real company.
_MONEY_TOKEN = re.compile(r"^[$€£¥₹](?:\d[\d,.]*)(?:bn|b|m|mm|k|tn)?$", re.I)

_LEGAL_SUFFIXES = frozenset("""
    inc inc. incorporated llc l.l.c ltd ltd. limited plc gmbh ag sa s.a n.v
    bv b.v ab as oy oyj kk pte pty srl spa s.p.a aps kft sas sarl co. corp.
    corporation holdings holding
""".split())


def assert_employer_is_named(company: str) -> None:
    """Reject a company field that describes an employer instead of naming one.

    Raises Rejected. Passes silently for anything with a single non-generic
    token in it, which is every real name we have ever stored.
    """
    name = (company or "").strip()
    if not re.search(r"[^\W_]", name, re.UNICODE):
        raise Rejected(f"company name has no letters or digits: {company!r}")

    core = []
    for token in re.split(r"[\s,]+", name.lower()):
        token = token.strip(".,")
        if not token or token in {"&", "-"} or token in _LEGAL_SUFFIXES:
            continue
        core.append(token)
    if not core:
        raise Rejected(f"company name is only a legal suffix: {company!r}")

    # Said outright: the source did not name the employer.
    if _ANONYMITY_MARKERS & set(core):
        raise Rejected(f"employer was not named in the source: {company!r}")

    def is_generic(token: str) -> bool:
        return (
            token in _GENERIC_ORG_NOUNS
            or token in _GENERIC_QUALIFIERS
            or bool(_MONEY_TOKEN.match(token))
        )

    # One proper noun anywhere is enough to make it a name. "National Bank
    # Holdings", "US Bank", "The Kroger Co." and "Legal & General" all clear
    # here, because "national", "us", "kroger" and "general" are not on any of
    # the generic lists.
    if not any(t in _GENERIC_ORG_NOUNS for t in core):
        return
    if not all(is_generic(t) for t in core):
        return

    # Everything left is generic from end to end. Even so, only three shapes
    # are rejected, because a long all-generic string is more likely to be a
    # real name we have not thought of ("Investment Technology Group") than a
    # placeholder — placeholders are short, or they announce themselves with an
    # article or a price tag.
    if core[0] in {"a", "an", "the"}:
        raise Rejected(f"a description, not a company name: {company!r}")
    if _MONEY_TOKEN.match(core[0]):
        raise Rejected(f"a size, not a company name: {company!r}")
    if len(core) <= 2:
        raise Rejected(f"placeholder, not a company name: {company!r}")


# --- Materiality -----------------------------------------------------------
#
# Computed here, in Python, from values we already hold. No model call, no cost,
# and the same input always gives the same answer, so it can be recomputed over
# the existing table without refetching anything.
#
# It is a heuristic and it is written down as one. The problem it solves is not
# correctness — every routine row is individually true — it is that 2,015 of
# 2,362 rows are a bare officer change, most at companies nobody is recruiting
# against, and they bury the rows that state a headcount or a nine-figure raise.
#
#   high     the source states a headcount, OR states funding of $10M or more,
#            OR the employer is identifiable enough to carry a ticker or CIK
#            and the story is more than a bare officer change.
#   routine  a bare officer or director change: no headcount, no money, no
#            location detail. Correct, and almost never why anyone came here.
#   medium   everything else.
#
# The $10M line is arbitrary and chosen, not derived: it is roughly where a
# round starts funding a hiring plan rather than a founding team.
_MATERIAL_FUNDING_USD = 10_000_000

# The SEC's own wording for the filing item that produces the routine bulk.
_OFFICER_CHANGE = re.compile(
    r"item\s*5\.0?2|officer or director change|"
    r"(?:leadership|management)\s+(?:change|transition)|"
    r"(?:chief executive officer|ceo|cfo|cto|coo)\s+transition",
    re.I,
)


def compute_materiality(
    *,
    headcount: int | None,
    funding_usd: int | None,
    ticker: str | None,
    cik: str | None,
    pillar: str,
    headline: str,
    city: str | None,
) -> str:
    """Return 'high', 'medium' or 'routine'. Deterministic, never a model call."""
    if headcount is not None:
        return "high"
    if funding_usd is not None and funding_usd >= _MATERIAL_FUNDING_USD:
        return "high"

    # A bare officer change: it names a person and nothing else. A CITY makes
    # it actionable for a recruiter working that market, so a city lifts it out
    # of routine. `state` deliberately does not: on an 8-K it is the filer's
    # registered state, present on 1,576 of the 1,727 leadership rows in the
    # table, so treating it as location detail would leave nothing routine at
    # all and the column would do no work.
    bare_officer_change = (
        pillar == "leadership_change"
        and bool(_OFFICER_CHANGE.search(headline or ""))
        and not city
    )
    if bare_officer_change:
        return "routine"

    # A ticker or CIK means the employer is a filer someone can look up, which
    # is the closest deterministic stand-in we have for "large or well known".
    if ticker or cik:
        return "high"
    return "medium"


# --- Pillars the document decides, not the model ---------------------------
#
# collectors/sec_edgar.py searches 8-K filings for Item 5.02 — "Departure of
# Directors or Certain Officers; Election of Directors; Appointment of Certain
# Officers" — and writes its own headline saying exactly that, because a filing
# is dense legal prose with no headline in it. What pillar such a document
# belongs to is settled before the model reads a word of it.
#
# The model was asked anyway, and it sent 573 of 3,496 of them somewhere else,
# 568 to rewards_comp: a 5.02(e) filing spends most of its words on the
# incoming officer's pay package, so the model graded the volume rather than
# the event. Those records were true, published, and unreachable to anyone
# browsing leadership changes — 18% of that pillar's primary source, held and
# unfindable (measured 2026-07-28, in the recall pass).
#
# Deliberately narrow. It fires only while the record still carries the
# collector's own officer-change headline, which is the same phrase test
# compute_materiality already uses. Where the model REPLACED that headline it
# found something specific in the document ("Masimo to be Acquired by Danaher",
# "Littelfuse Announces Equity Grants") and that reading is the judgement we
# still want: a blanket rule on the collector name would file both of those
# under leadership changes. Nothing else the model said is touched.
_PILLAR_BY_DOCUMENT = {"sec_edgar": "leadership_change"}


def forced_pillar(collector: str, headline: str) -> str | None:
    """The pillar this document has by construction, or None to let the model
    decide. See _PILLAR_BY_DOCUMENT."""
    pillar = _PILLAR_BY_DOCUMENT.get(collector)
    if pillar and _OFFICER_CHANGE.search(headline or ""):
        return pillar
    return None


def precheck(raw: dict) -> None:
    """Every rejection reachable from the RAW item alone, before any model.

    Each check below used to live inside build_signal, which runs AFTER the
    read-through — so a candidate with no source URL, or a job-board link, or
    a filing that announces a workforce reduction, was read at full price and
    then rejected on facts that were sitting in the collector's dict the whole
    time. The last real run bought 60 reads and stored 34 rows; this is one of
    the places the other 26 went. run_collect calls this before any money is
    spent; build_signal calls it again as its first step, so no path into the
    store exists that skips it, and the two ends can never disagree.

    Raises Rejected with the same messages build_signal always raised. Nothing
    here reads `classified` — a check that needs the model's output cannot be
    prechecked and stays in build_signal.
    """
    source_url = (raw.get("source_url") or "").strip()
    if not source_url:
        raise Rejected("no source_url — no source, no record")

    host = (urlparse(source_url).hostname or "").lower()
    if not host:
        raise Rejected(f"unparseable source_url: {source_url!r}")
    if host in _BLOCKED_SOURCE_HOSTS:
        raise Rejected(f"aggregator stored as source: {host}")

    # A homepage is not a receipt. "Every record links to a primary source" is
    # only true if the link goes to the article that makes the claim; an outlet
    # front page proves nothing and is stale within hours.
    #
    # This shipped broken: Google News RSS gives the outlet homepage in its
    # <source> element, its redirect no longer resolves, and the real URL is not
    # recoverable from the encoded token. Two records went live linking to
    # crn.com and ft.com front pages before this guard existed.
    path = urlparse(source_url).path.strip("/")
    if not path:
        raise Rejected(f"source_url is a bare domain, not an article: {source_url}")

    if host in _JOB_BOARD_HOSTS:
        raise Rejected(f"job board, not market intelligence: {host}")
    if _JOB_POSTING_PATH.search(urlparse(source_url).path):
        raise Rejected(f"single job advert, not a market signal: {source_url}")

    raw_text = (raw.get("raw_text") or "").strip()
    if not raw_text:
        # The sibling shipped a source that set every field except this one and
        # silently posted zero records for weeks (spec 6 rule 2).
        raise Rejected("raw_text is empty — the classifier had nothing to read")

    # The scope boundary's third arm, reading the DOCUMENT. Arms one and two
    # read headlines — one the source wrote, one the model wrote — and stay in
    # build_signal because the model's reading is part of what they judge. This
    # arm reads only raw_text, so it belongs here: an 8-K announcing a
    # reduction is the sibling's record whatever the model would have said
    # about it, and the reads this saves are precisely the sec_edgar bodies
    # that are the most expensive texts the pipeline sends. (Measured 0.16% of
    # filings, but each one was a full read bought and then thrown away.)
    # prefilter.filing_reduction_plan documents why this cannot be the
    # headline rule pointed at a body.
    cut = prefilter.filing_reduction_plan(raw_text)
    if cut:
        raise Rejected(
            "workforce reduction is the sibling tracker's scope, not ours "
            f"(the source document announces it: {cut!r})")


def build_signal(classified: dict, raw: dict, collector: str, conn=None) -> Signal:
    """Turn a classified candidate into a storable Signal, or raise Rejected.

    `raw` is the collector's dict and is the ONLY source of truth for what the
    article actually said. `classified` is the model's reading of it.

    `conn` is optional and is used for one thing: the employer identity cache
    (pipeline/identity.py), which fills ticker / cik / hq / employer_type when
    nothing above supplied them. Pass it and blanks get filled from the cache;
    leave it out and this stays a pure function of two dicts, which is what
    every test of it relies on.
    """
    # Everything checkable without the model, re-checked here even though
    # run_collect already prechecked: build_signal is also fed by backfills
    # and corrections that never went through run_collect's loop.
    precheck(raw)
    source_url = (raw.get("source_url") or "").strip()
    host = (urlparse(source_url).hostname or "").lower()
    raw_text = (raw.get("raw_text") or "").strip()

    company = (classified.get("company") or "").strip()
    if not company:
        raise Rejected("no company identified")
    assert_employer_is_named(company)

    pillar = vocab.normalize_pillar(classified.get("pillar", ""))
    if not pillar:
        raise Rejected(f"pillar not in vocabulary: {classified.get('pillar')!r}")

    direction = vocab.normalize_direction(classified.get("signal_direction", ""))
    if not direction:
        raise Rejected(f"signal_direction not in vocabulary: {classified.get('signal_direction')!r}")

    headline = (classified.get("headline") or raw.get("headline") or "").strip()
    summary = (classified.get("summary") or "").strip()
    readthrough = (classified.get("talent_readthrough") or "").strip()
    if not (headline and summary and readthrough):
        raise Rejected("headline, summary and talent_readthrough are all required")

    # Applied here rather than beside the normalisation above, because it reads
    # the headline the record will actually carry: on this source the model
    # rewriting the headline is the signal that it read past the item and found
    # something else in the document.
    pillar = forced_pillar(collector, headline) or pillar

    # The read-through is our interpretation, so it may reason beyond the text.
    # The summary restates the source and may not.
    assert_figures_are_sourced(summary, raw_text)

    # The scope boundary. This page's own footer says "Layoff and redundancy
    # data is not collected here; see the AI Layoff Tracker", and a Spanish
    # Verizon story about 3,000 cuts was live on it anyway. The sibling owns
    # workforce reduction; we own everything else about the talent market.
    #
    # THREE arms, because a cut can arrive three ways. The headline is the
    # subject of the story, so a reduction headline is a reduction story. A
    # headline that hides it ("Verizon announces restructuring") is caught by
    # the model's own reading: direction 'displacement' means the source said
    # roles are going, which is the sibling's definition of a record. And a
    # headline that is not a headline at all is caught by reading the document.
    #
    # All three use the same free vocabulary the prefilter uses, so most such
    # stories never reach here and never cost a classification.
    cut = prefilter.workforce_reduction_term(headline)
    if cut:
        raise Rejected(
            f"workforce reduction is the sibling tracker's scope, not ours ({cut!r})")
    if direction == "displacement":
        cut = prefilter.workforce_reduction_term(f"{summary} {readthrough}")
        if cut:
            raise Rejected(
                "workforce reduction is the sibling tracker's scope, not ours "
                f"(displacement: {cut!r})")

    # The third arm reads the DOCUMENT, and it moved to precheck() above:
    # `sec_edgar` stamps one synthetic headline onto every filing, so arm one
    # was matching the collector's own boilerplate forever, arm two only fires
    # when the model happened to choose 'displacement', and the reduction
    # language sat untouched in raw_text. Atlassian (~10% of its workforce),
    # Groupon, IO Biotech and Lyra Therapeutics all reached the live page
    # through that hole. The rule itself — announce, not mention — lives in
    # prefilter.filing_reduction_plan; it needs nothing the model said, which
    # is exactly why it now runs before the model is paid.

    # Job location: from the source text only.
    city = region = None
    country = vocab.normalize_country(classified.get("country", "") or raw.get("country", ""))
    hit = vocab.normalize_city(classified.get("city", ""))
    if hit:
        city, region, city_country = hit
        # The city list is curated and outranks a freeform country string.
        country = city_country
    if country and not region:
        region = _region_for_country(country)

    # Employer HQ: the model's own knowledge of the company, kept in separate
    # columns. "Revolut CEO steps down" names no place, but it is a London
    # talent signal — the same union the sibling exposes as country_basis=any.
    # Never merged into `country`: one is sourced, the other is not.
    hq_city = hq_country = None
    hq_hit = vocab.normalize_city(classified.get("headquarters_city", ""))
    if hq_hit:
        hq_city, _hq_region, hq_country = hq_hit
    else:
        hq_country = vocab.normalize_country(classified.get("headquarters_country", ""))

    # Deliberately NOT a rejection. Geography is how this product segments, but
    # it is not what makes a record true: the credibility rules are the source
    # URL, figures appearing verbatim, and confidence capped by the source, and
    # an unplaced record breaks none of them.
    #
    # The live dry run on 2026-07-27 threw away six of twelve classified
    # candidates here, all real leadership changes at real employers, and all
    # after the model had already been paid for. "Sidus Space Names New CEO" is
    # a talent signal whether or not we can say which state Sidus Space is in.
    #
    # Unplaced records are stored with country NULL, appear under World, are
    # excluded from every country and region filter, and the page says
    # "Location not stated" rather than guessing. Guessing from the Google News
    # edition was considered and rejected: the US edition returns stories from
    # Zimbabwe, Nigeria and Fiji, so the edition says where we asked, not where
    # the story is.

    # US state, for the state filter. Only meaningful inside the US.
    state = None
    if country == "US":
        state = vocab.normalize_state(classified.get("state", "")) or vocab.state_for_city(city or "")

    functions = vocab.normalize_functions(classified.get("functions"))
    industry = vocab.normalize_industry(classified.get("industry", ""))

    # Figures get the same treatment as every other number on a record: if the
    # source text does not contain it, it is not stored. A headcount a model
    # inferred is exactly the plausible-but-wrong fact this product cannot carry.
    headcount = _sourced_int(classified.get("headcount"), raw_text)
    funding = _sourced_figure(classified.get("funding_amount"), raw_text)

    # The same funding figure as an integer of US dollars, re-derived in Python
    # from the string we just accepted. Deliberately not asked of the model:
    # 'never state a number that is not in the text' would forbid it converting
    # '$1.45 Million' to 1450000, so we do the conversion ourselves. NULL for
    # non-USD currencies rather than a guessed exchange rate.
    funding_usd = vocab.parse_funding_usd(funding) if funding else None
    funding_stage = vocab.normalize_funding_stage(classified.get("funding_stage", "") or "")

    # Only meaningful alongside a number. A scope with no headcount describes
    # nothing, and storing it would put "Total workforce" on a row that never
    # said how many people that is.
    headcount_scope = (
        vocab.normalize_headcount_scope(classified.get("headcount_scope", "") or "")
        if headcount is not None else None
    )

    work_mode = vocab.normalize_work_mode(classified.get("work_mode", "") or "")

    # What kind of corporate event this is, when the source says. Recorded from
    # the perspective of `company`: 'acquisition' is this employer buying,
    # 'acquired' is this employer being bought. It never touches
    # signal_direction — a deal implies nothing about headcount until the
    # source states a number, and inferring otherwise is the same mistake as
    # inferring a headcount.
    deal_type = vocab.normalize_deal_type(classified.get("deal_type", "") or "")

    # What the employer did with a place of work, when the source says so.
    # Treated exactly like deal_type and for the same reason: it is an event
    # type, not a headcount claim, so it never touches signal_direction. An
    # opening with no stated roles stays 'neutral' and the page still says
    # "headcount not stated", which is the true thing to say about it.
    site_event = vocab.normalize_site_event(classified.get("site_event", "") or "")

    # Employer identity, and the join key to the sibling layoff tracker.
    # company_key is a normalised name and collapses whenever two employers
    # share one; cik and ticker do not.
    ticker = _sourced_ticker(classified.get("ticker"), raw_text)
    cik = _clean_cik(raw.get("cik"))
    employer_type = vocab.normalize_employer_type(classified.get("employer_type", "") or "")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ckey = vocab.company_key(company)
    published = _normalize_date(raw.get("published_date"), raw.get("source_url"))
    chash = content_hash(ckey, pillar, published, headline, raw.get("source_name"))

    effective = _sourced_effective_date(classified.get("effective_date"), raw_text, published)

    signal = Signal(
        signal_id=chash,
        headline=headline,
        summary=summary,
        talent_readthrough=readthrough,
        company=company,
        company_key=ckey,
        ticker=ticker,
        cik=cik,
        employer_type=employer_type,
        pillar=pillar,
        signal_direction=direction,
        city=city,
        region=region,
        country=country,
        hq_city=hq_city,
        hq_country=hq_country,
        state=state,
        functions=json.dumps(functions) if functions else None,
        industry=industry,
        headcount=headcount,
        headcount_scope=headcount_scope,
        funding_amount=funding,
        funding_amount_usd=funding_usd,
        funding_stage=funding_stage,
        work_mode=work_mode,
        deal_type=deal_type,
        site_event=site_event,
        materiality=compute_materiality(
            headcount=headcount,
            funding_usd=funding_usd,
            ticker=ticker,
            cik=cik,
            pillar=pillar,
            headline=headline,
            city=city,
        ),
        confidence=infer_confidence(source_url, classified.get("confidence")),
        source_url=source_url,
        source_name=(raw.get("source_name") or host).strip(),
        discovery_url=raw.get("discovery_url"),
        published_date=published,
        effective_date=effective,
        captured_at=now,
        as_of=now,
        content_hash=chash,
        predicted_outcome=(classified.get("predicted_outcome") or "").strip() or None,
        check_after_date=classified.get("check_after_date") or None,
        collector=collector,
    )

    # The identity spine fills what nobody stated: ticker, cik, headquarters,
    # kind of employer. It fills BLANKS ONLY. Everything above it is sourced —
    # the cik came out of the EFTS hit for the filing we fetched, the ticker
    # out of "(NASDAQ: AAPL)" printed in the article itself, the hq out of the
    # model's reading — and a value derived from a name string never displaces
    # one of those. Cache-only on this path: no network call is made during
    # ingestion, so a slow or blocked lookup cannot cost a record. The cache is
    # filled by `python -m pipeline.identity --backfill`, and until a caller
    # passes `conn` this line does nothing at all.
    identity.enrich(signal, conn)

    # Recomputed after enrichment, because a ticker or CIK the identity spine
    # filled is exactly the "large or well-known employer" input the rule reads.
    # Computing it once before this line would grade a row on less than we know.
    signal.materiality = compute_materiality(
        headcount=signal.headcount,
        funding_usd=signal.funding_amount_usd,
        ticker=signal.ticker,
        cik=signal.cik,
        pillar=signal.pillar,
        headline=signal.headline,
        city=signal.city,
    )
    return signal


def _sourced_int(value, raw_text: str) -> int | None:
    """A headcount is stored only if that number appears in the source text."""
    try:
        n = int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n if _normalize_number(str(n)) in _numbers_in(raw_text) else None


def _sourced_figure(value, raw_text: str) -> str | None:
    """A funding figure is stored verbatim, and only if its digits appear in
    the source text. '$10.5M' passes when the text says 10.5M."""
    text = (str(value or "")).strip()
    if not text:
        return None
    digits = _numbers_in(text)
    if not digits or not digits <= _numbers_in(raw_text):
        return None
    return text[:32]


# A ticker is written in capitals in the one place it ever appears, which is
# how "(NASDAQ: AAPL)" is distinguishable from the word "aapl" occurring in
# prose. Matching case-insensitively would let a ticker of "IT" or "ON" or "SO"
# be confirmed by any ordinary sentence.
_TICKER_SHAPE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)


def _sourced_ticker(value, raw_text: str) -> str | None:
    """A ticker is stored only when the source text prints it.

    Never looked up from the company name: a lookup is a claim we did not read
    anywhere, and the wrong ticker on a real employer is exactly the
    plausible-but-wrong fact this product cannot carry.
    """
    text = (str(value or "")).strip().upper()
    # Models return "NASDAQ: AAPL" or "NYSE:IBM" as often as the bare symbol.
    text = re.sub(r"^(?:NASDAQ|NYSE|AMEX|LSE|TSX|ASX|OTC|NSE|BSE)\s*[:.]?\s*", "", text)
    if not _TICKER_SHAPE.match(text):
        return None
    return text if re.search(rf"\b{re.escape(text)}\b", raw_text or "") else None


def _clean_cik(value) -> str | None:
    """SEC central index key. Digits only, leading zeros dropped.

    Unlike everything else here this needs no text check: the SEC collectors
    read it straight out of the EFTS hit that produced the filing, so it is a
    fact about which document we fetched, not a reading of its prose.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    digits = digits.lstrip("0")
    return digits[:12] if digits else None


def _sourced_effective_date(value, raw_text: str, published: str | None) -> str | None:
    """When the change takes effect, and only when the source dates it.

    "Tim Cook steps down as CEO in September" is a July article about a
    September event; filing it under published_date is the wrong answer to
    "who is leaving next quarter". So the column exists, and it is filled from
    the text or not at all.

    The guard is that the month must actually be named in the source, or the
    ISO date printed there. A model that returns a date whose month appears
    nowhere in the article has inferred it, and an inferred effective date is
    the same class of mistake as an inferred headcount.

    Equal to published_date means the model echoed the publication date rather
    than reading an effective one, so it is dropped: this column is only worth
    having when it says something published_date does not.
    """
    parsed = _normalize_date(value)
    if not parsed:
        return None

    # An effective date decades away is a parse artefact, not a plan.
    year = int(parsed[:4])
    this_year = datetime.now(timezone.utc).year
    if not (2015 <= year <= this_year + 5):
        return None

    if published and parsed == published:
        return None

    text = (raw_text or "").lower()
    if parsed in text:
        return parsed
    month = _MONTHS[int(parsed[5:7]) - 1]
    # Anchored on word boundaries. An unanchored "sep" is satisfied by the word
    # "separate", and "dec" by "declined", which would confirm a month the
    # article never named.
    pattern = rf"\b(?:{month}|{month[:3]}t?)\b"
    return parsed if re.search(pattern, text) else None


def _region_for_country(iso2: str) -> str | None:
    for _city, region, code in vocab._CITY_ALIASES.values():
        if code == iso2:
            return region
    return None


def _normalize_date(value, source_url: str | None = None) -> str | None:
    """Return YYYY-MM-DD, or None. Never guesses.

    `source_url` is a CORRECTION channel, not a source. Google News re-surfaces
    old stories with a fresh RSS pubDate, and trusting it put a 2021 article on
    the page as this week's news, dated 2026-07-26, while its own URL said
    /2021/07/ (audit 2026-07-28). When the article path carries a plausible
    year/month that is OLDER than the pubDate by more than a couple of months,
    the path wins: a publisher's own permalink is better evidence of when it
    was written than an aggregator's feed timestamp.
    """
    if not value:
        return None
    text = str(value).strip()
    parsed = None
    for fmt in ("%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue
    if parsed is None:
        # Anchored: an unanchored search matched any YYYY-MM-DD anywhere in
        # arbitrary text, including one sitting inside a URL or a body quote.
        m = re.match(r"\s*(\d{4}-\d{2}-\d{2})\b", text)
        parsed = m.group(1) if m else None
    if parsed is None:
        return None

    if source_url:
        m = re.search(r"/((?:19|20)\d{2})/(0[1-9]|1[0-2])(?:/|\b)", str(source_url))
        if m:
            url_ym = f"{m.group(1)}-{m.group(2)}"
            feed_ym = parsed[:7]
            if url_ym < feed_ym:
                # More than ~2 months apart is a re-surfaced old story, not a
                # slow publisher. Date it to the first of the URL's month: the
                # day is unknown, and claiming one would be a guess.
                if (int(feed_ym[:4]) * 12 + int(feed_ym[5:7])) - \
                   (int(url_ym[:4]) * 12 + int(url_ym[5:7])) > 2:
                    return f"{url_ym}-01"
    return parsed
