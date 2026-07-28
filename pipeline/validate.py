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

from . import identity, vocab


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
