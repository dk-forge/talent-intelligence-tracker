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

from . import vocab


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
    funding_amount: str | None
    confidence: str
    source_url: str
    source_name: str
    discovery_url: str | None
    published_date: str | None
    captured_at: str
    as_of: str
    content_hash: str
    predicted_outcome: str | None
    check_after_date: str | None
    collector: str


# A figure the model returns must appear in the source text. Matches 1,200 /
# 1200 / 1.2bn / €5B / 5 billion.
_NUMBER = re.compile(r"\d[\d,.]*\s*(?:bn|b|m|k|billion|million|thousand)?", re.I)

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


def content_hash(company_key: str, pillar: str, published_date: str | None, headline: str) -> str:
    norm_headline = re.sub(r"[^a-z0-9 ]", "", headline.lower())
    norm_headline = re.sub(r"\s+", " ", norm_headline).strip()
    payload = f"{company_key}|{pillar}|{published_date or ''}|{norm_headline}"
    return hashlib.md5(payload.encode()).hexdigest()


def build_signal(classified: dict, raw: dict, collector: str) -> Signal:
    """Turn a classified candidate into a storable Signal, or raise Rejected.

    `raw` is the collector's dict and is the ONLY source of truth for what the
    article actually said. `classified` is the model's reading of it.
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

    company = (classified.get("company") or "").strip()
    if not company:
        raise Rejected("no company identified")

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

    # The read-through is our interpretation, so it may reason beyond the text.
    # The summary restates the source and may not.
    assert_figures_are_sourced(summary, raw_text)

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

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ckey = vocab.company_key(company)
    published = _normalize_date(raw.get("published_date"))
    chash = content_hash(ckey, pillar, published, headline)

    return Signal(
        signal_id=chash,
        headline=headline,
        summary=summary,
        talent_readthrough=readthrough,
        company=company,
        company_key=ckey,
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
        funding_amount=funding,
        confidence=infer_confidence(source_url, classified.get("confidence")),
        source_url=source_url,
        source_name=(raw.get("source_name") or host).strip(),
        discovery_url=raw.get("discovery_url"),
        published_date=published,
        captured_at=now,
        as_of=now,
        content_hash=chash,
        predicted_outcome=(classified.get("predicted_outcome") or "").strip() or None,
        check_after_date=classified.get("check_after_date") or None,
        collector=collector,
    )


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


def _region_for_country(iso2: str) -> str | None:
    for _city, region, code in vocab._CITY_ALIASES.values():
        if code == iso2:
            return region
    return None


def _normalize_date(value) -> str | None:
    """Return YYYY-MM-DD, or None. Never guesses."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else None
