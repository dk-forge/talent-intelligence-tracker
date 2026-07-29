"""Deterministic teaser extraction — lever 1 of the cost work.

A large share of funding and hiring headlines state every field a record
needs: "Enigma Raises $71M in Seed Funding" IS the record. Paying a model
$0.0013 to restate it is the single biggest avoidable cost in the pipeline,
because those headlines are also the most numerous kind of gate-survivor.

This module parses headline + teaser (`raw_text`) with regexes and either
returns a COMPLETE classified-shaped dict — the same shape `classify.classify`
returns, consumed by the same `validate -> store -> publish` path — or returns
None and the candidate takes the paid path unchanged.

THE RULES, in order of importance:

1. PRECISION OVER RECALL. A wrong $0 extraction is worse than a $0.0013 read,
   so anything ambiguous is declined, loudly cheap and silently correct.
   Declining is a success here, exactly as rejection is in validate.py.
2. Only what the text LITERALLY STATES is captured. No inference, ever:
   - non-USD amounts stay as written with their currency; the USD integer is
     NULL (vocab.parse_funding_usd already enforces this — we never invent an
     exchange rate);
   - counts parse the FIRST number only, matching the sibling's rule;
   - a place is captured only when the text states one AND it normalises
     through the fixed vocabulary. A stated place that will not normalise
     DECLINES the whole item rather than shipping an unplaced record the paid
     path would have placed.
3. COMPLETE or nothing. If the text states something this parser cannot carry
   (a site event, a hiring plan inside a funding story, a deal), the item is
   declined so the model can read the nuance. A record that is right about
   the amount and silent about the stated site opening is not complete.
4. English only, deliberately. The name-span validation below leans on
   capitalisation and English generic-word lists; applying it to German or
   Hebrew headlines would be guessing. Non-English candidates always take the
   paid path.

Confidence: always "reported". The extraction method does not change what the
SOURCE is — a news teaser parsed by a regex is exactly as credible as the same
teaser read by a model, and validate.infer_confidence caps news at "reported"
either way. The distinct marker is EVIDENCE_NOTE, stored on the row's `notes`
column, so a reader of the database can see no model ever read the item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import prefilter, vocab
from .validate import (_ANONYMITY_MARKERS, _GENERIC_ORG_NOUNS,
                       _GENERIC_QUALIFIERS)

# Stored in signals.notes for every row this module produced. Not published to
# WordPress (publish.FIELDS deliberately excludes notes); it is provenance for
# anyone reading the database, the same audience as revision notes.
EVIDENCE_NOTE = (
    "deterministic extraction: every field appears verbatim in the "
    "headline/teaser; no model read this item (pipeline/cheap_extract.py)"
)

# Per-run counters, printed by run_collect beside the gate stats.
STATS = {"attempted": 0, "closed": 0, "declined": 0}


# --- Amounts ----------------------------------------------------------------
#
# A currency SYMBOL or CODE is required. "raises 12 million" with no currency
# is declined: the paid path can read what "million" is millions of, a regex
# cannot. The captured text is stored exactly as written (funding_amount is the
# quotable column); vocab.parse_funding_usd derives the integer, and returns
# None for every non-USD currency by existing rule.
_CURRENCY = (
    r"(?:US\$|USD\s?|[$€£₹₪¥]|A\$|AU\$|C\$|CA\$|NZ\$|S\$|HK\$|R\$|"
    r"EUR\s|GBP\s|CHF\s|SEK\s|DKK\s|NOK\s|NIS\s)"
)
# "mil" is Malaysian/Singaporean business English; crore and lakh are how the
# Indian press writes every round (₹ amounts stay non-USD, so no conversion is
# ever attempted on them — the text is stored verbatim).
_AMOUNT_SUFFIX = r"(?:billion|million|thousand|bn|mn|mil|m|k|b|crores?|lakhs?|cr)"
# The trailing \+? keeps "$300M+" intact: the plus is part of what the text
# states, and storing "$300M" for it would quietly understate the claim.
_AMOUNT_BODY = (
    rf"{_CURRENCY}\s?\d[\d,]*(?:\.\d+)?(?:\s?{_AMOUNT_SUFFIX})?\+?"
)
_AMOUNT = re.compile(rf"(?P<amount>{_AMOUNT_BODY})(?![\w.])", re.I)

# Completed-funding verbs only. "to raise", "plans", "seeks" and friends are
# handled by _UNCERTAIN below: a round in talks is not a round closed, and
# recording it as one is exactly the wrong-$0-extraction this module must
# never produce.
_RAISE_VERB = (
    r"(?:raises|raised|secures|secured|closes|closed|lands|landed|"
    r"nabs|nabbed|bags|bagged|banks|banked|gets|receives|received|"
    r"snags|snagged|pockets|pocketed|collects|collected|attracts|attracted)"
)

# Anything that says the money is not in the bank yet, or that the story is
# second-hand. One of these anywhere in the headline declines the item.
_UNCERTAIN = re.compile(
    r"(?:\b(?:in talks|reportedly|rumou?r\w*|may\b|might|could|plans?\b|"
    r"planning|aims?\b|aiming|seeks?\b|seeking|eyes?\b|eyeing|set to|"
    r"poised|hopes?\b|looking to|considering|weighs?\b|weighing|prepares?\b|"
    r"preparing|files? to|wants?\b|targets?\b|targeting|expects?\b|nears?\b|"
    r"nearing|about to|close to|on track)\b|\bto raise\b|\?)",
    re.I,
)

# A deal changes what the amount MEANS (consideration, not capital), and this
# parser cannot read direction. Deals always take the paid path.
_DEAL_WORDS = re.compile(
    r"\b(?:acquir\w+|acquisition|merger|merges?|takeover|buy(?:s|out)|"
    r"ipo|goes public|spin[- ]?off|divest\w+)\b", re.I
)

# Debt-market and fund-vehicle raises. Both clear the verb+amount shape and
# both would be wrong records: "Kuwait raises $6 billion in three-tranche
# bond sale" is a sovereign bond, not an employer's round, and "Inflexor
# Ventures raises ₹400 crore in first close of Fund III" is a VC raising a
# VEHICLE, which is a different talent story than a company raising capital
# — both live hits from the 2026-07-29 measurement sweep. The model can read
# the nuance; this parser declines it.
_NOT_A_ROUND = re.compile(
    r"\b(?:bonds?|sukuk|tranches?|treasur\w+|t-bills?|gilts?|notes? offering|"
    r"debt (?:sale|offering|issue)|bond sale|"
    r"fund\b|first close|final close|fund of funds)\b", re.I
)

# Stated hiring language inside a funding story (or a stated amount inside a
# hiring story) means the text carries MORE than this parser can: decline and
# let the model read both halves. See rule 3 in the module docstring.
_HIRE_WORDS = re.compile(r"\b(?:hir(?:e|es|ed|ing)|recruit\w*|headcount)\b", re.I)

# What may follow the amount. A whitelist, because the failure lives in the
# tail: "Acme gets $10 price target" and "Acme secures $5M stake in Beta" both
# clear the verb+amount shape, and both would be wrong records. An unknown
# next word is an unknown meaning for the amount, so it declines.
_TAIL_OK = frozenset("""
    in to from for as at with and led round rounds funding financing
    investment seed series capital raise backing amid after ahead
    bringing valuing taking boosting doubling despite on usd
""".split())

# A USD round below this is more likely a mis-read token ("gets $5") than
# news. Non-USD amounts are exempt: they do not parse to a number by rule,
# and their stated text is stored verbatim either way.
_MIN_PLAUSIBLE_USD = 100_000

# The round's name, only when the text names it. Bare "seed" needs a funding
# noun beside it: "seed round" is a stage, "seed potatoes" is agriculture.
_STAGE = re.compile(
    r"\b(?:(?P<series>series\s+[a-k])\b|"
    r"(?P<seed>pre[- ]?seed|seed)(?:[- ]stage)?\s+"
    r"(?:round|funding|financing|investment|capital|extension|raise)|"
    r"in\s+(?P<inseed>pre[- ]?seed|seed)\b)",
    re.I,
)


# --- The name span ----------------------------------------------------------
#
# The employer is whatever stands before the verb, and it is accepted only
# when EVERY token looks like part of a proper name. The generic lists are
# imported from validate.py so the two ends of the pipeline agree on what a
# name is not; the sets below are this module's own, and they are decline
# lists, so over-filling them costs a $0.0013 read, never a wrong record.

_CONNECTORS = frozenset({"of", "the", "and", "&", "de", "da", "di", "for", "by"})

# Words a headline leads with that are not a company, plus pronouns.
_LEAD_JUNK = frozenset("""
    exclusive report breaking watch why how when what where who sources
    source opinion analysis revealed update updated live meet inside
    it he she they we you i this that these those its his her their our
    after amid despite as while before now today yesterday
""".split())

# Nationality adjectives. "Israeli startup Coho raises" must not store an
# employer called "Israeli Coho"; validate's lists deliberately admit these
# (they are the distinctive word in real names like American Airlines), so a
# headline-lead nationality is this module's own reason to decline.
_NATIONALITIES = frozenset("""
    american british english irish scottish welsh german french dutch
    belgian spanish portuguese italian swiss austrian swedish danish
    norwegian finnish polish czech romanian hungarian greek ukrainian
    russian turkish israeli egyptian saudi emirati qatari indian pakistani
    chinese japanese korean vietnamese thai indonesian malaysian singaporean
    filipino australian kiwi canadian mexican brazilian argentine chilean
    colombian nigerian kenyan ghanaian south african
""".split())

# Sector-tech compounds a headline uses as descriptors ("MedTech Xeltis
# raises..."). validate's generic list stops at fintech/biotech, and it must:
# over there a false match rejects a real employer for good ("Proptech Group"
# is a listed company). Here a match merely declines a $0 close, so the
# longer tail the live sweep showed is safe to name.
_SECTOR_DESCRIPTORS = frozenset("""
    medtech healthtech proptech edtech insurtech agritech agtech femtech
    foodtech regtech cleantech deeptech adtech martech legaltech greentech
    spacetech cybertech hrtech traveltech contech climatetech saas b2b b2c
    d2c crypto web3 blockchain startup scaleup unicorn
""".split())

_TOKEN_OK = re.compile(r"^(?:[A-Z][\w&.'’-]*|\d[\w&.'’-]*)$")

# Small words a headline leaves lowercase even when it title-cases the rest.
_TITLE_STOPWORDS = frozenset(
    "a an the in on of for to and as at by with its is are".split())


def _title_cased(headline: str) -> bool:
    """True when the headline capitalises everything, which blinds the
    name-span heuristic: 'Building Materials Quick Commerce Startup Fixxly
    Raises $5.5M' validates as a six-token proper name in Title Case. In such
    a headline only a SINGLE token before the verb is trusted as a name —
    descriptors are multi-word, names survive alone."""
    words = [w for w in re.findall(r"[A-Za-z][\w'’-]*", headline)
             if w.lower() not in _TITLE_STOPWORDS]
    if len(words) < 5:
        return False
    capped = sum(1 for w in words if w[0].isupper())
    return capped >= 0.9 * len(words)


def _valid_name(span: str) -> str | None:
    """The span before the verb, accepted as an employer name or None.

    Every rejection here is recall given up for precision, which is the trade
    this whole module exists to make.
    """
    name = (span or "").strip().strip("‘’'\"")
    if not name or len(name) > 60:
        return None
    # A comma, colon, dash or semicolon means a clause, an attribution or a
    # list — all shapes where "the bit before the verb" is not the employer.
    if re.search(r"[,:;—–\|]", name):
        return None
    if "'s" in name or "’s" in name:
        return None

    tokens = name.split()
    if not 1 <= len(tokens) <= 6:
        return None

    for i, token in enumerate(tokens):
        low = token.lower().strip(".")
        if low in _CONNECTORS:
            if i == 0:
                return None
            continue
        # Anonymity, nationalities and headline-lead words poison the whole
        # span wherever they sit: they mark a description, not a name. A
        # nationality also hides inside a hyphenated token — "Dutch-US
        # MedTech Xeltis" stored the descriptor as part of the name on the
        # live sweep — so hyphen parts are checked too.
        if low in _LEAD_JUNK or low in _NATIONALITIES or low in _ANONYMITY_MARKERS:
            return None
        if "-" in low and any(part in _NATIONALITIES or part in _LEAD_JUNK
                              for part in low.split("-")):
            return None
        if low in _SECTOR_DESCRIPTORS:
            return None
        # Generic org words poison only the LEADING position. Descriptors
        # lead ("Fintech startup Alma raises..."), while a generic word
        # inside a name is ordinary English: Tiger Technology, National Bank
        # Holdings and Dimension Data are all real employers that a
        # generic-anywhere rule declined on the live feeds.
        if i == 0 and (low in _GENERIC_ORG_NOUNS or low in _GENERIC_QUALIFIERS):
            return None
        if not _TOKEN_OK.match(token):
            return None

    # A place is not an employer. "Kuwait raises $6 billion in three-tranche
    # bond sale" cleared every rule above with company='Kuwait' on the live
    # sweep; a span that IS a country or city in the vocabulary is a
    # geography, whatever the verb beside it says.
    if vocab.normalize_country(name) or vocab.normalize_city(name):
        return None
    return name


# --- Funding ----------------------------------------------------------------

# Optional stated-place prefix, two shapes both common on the wired feeds:
#   "Boston-based Acme raises..."      "Spain's Multiverse Computing raises..."
# The place is only accepted when it normalises; a prefix that does not
# (including "AI-based" and "Musk's") declines the item, because the text
# stated something this record would otherwise silently drop or misread.
# "USD" rides along as a pre-amount filler for the "raises USD $1.5 million"
# phrasing some outlets use.
_FUNDING_SHAPE = re.compile(
    rf"^(?:(?P<base>[A-Z][\w .'’-]{{1,30}})-based\s+|"
    rf"(?P<poss>[A-Z][\w .-]{{1,28}})[''’]s\s+)?"
    rf"(?P<name>\S[^,;:]{{0,60}}?)\s+{_RAISE_VERB}\s+"
    rf"(?:(?:a|an|another|fresh|new|its|over|nearly|about|around|"
    rf"approximately|more than|up to|some|roughly|usd)\s+){{0,3}}"
    rf"(?P<amount>{_AMOUNT_BODY})(?![\w.])",
    re.I,
)


# A stage mention in the teaser is only THIS round's stage when it sits
# beside the money. "values the company at a fivefold step-up from its Series
# B" names the PREVIOUS round, and the first version of this parser stamped
# that Series B onto a $570m round on the live sweep (Multiverse Computing).
# Kept tight (12 chars: "from its ", "since the ") so an unrelated "from"
# further back — "emerged from stealth with a $28M Series A" — cannot veto a
# stage the text really does tie to this round.
_STAGE_PREVIOUS = re.compile(
    r"(?:from|since|previous|last|earlier|prior|after|follow\w*|step[- ]?up)"
    r"[^.]{0,12}$", re.I)


def _stated_stage(headline: str, raw_text: str) -> str:
    """The round's stage, only where the text ties it to THIS round.

    Trusted anywhere in the headline (the headline's subject is this round).
    In the teaser it must sit within 60 characters after a money amount, and
    must not be introduced by previous-round phrasing.
    """
    sm = _STAGE.search(headline)
    if sm:
        return (sm.group("series") or sm.group("seed")
                or sm.group("inseed") or "").strip()
    for sm in _STAGE.finditer(raw_text):
        before = raw_text[max(0, sm.start() - 30):sm.start()]
        if _STAGE_PREVIOUS.search(before):
            continue
        window = raw_text[max(0, sm.start() - 60):sm.start()]
        if _AMOUNT.search(window):
            return (sm.group("series") or sm.group("seed")
                    or sm.group("inseed") or "").strip()
    return ""


@dataclass
class Funding:
    company: str
    company_key: str
    amount_text: str
    amount_usd: int | None   # None for every non-USD currency, by rule
    amount_canon: str        # normalised for clustering: "$71M" == "$71 million"
    stage: str | None        # normalised vocab value, or None
    stage_text: str          # exactly as the text wrote it, "" if unstated
    city: str | None
    country: str | None


_MULT = {"billion": "B", "bn": "B", "b": "B",
         "million": "M", "mn": "M", "mil": "M", "m": "M",
         "thousand": "K", "k": "K",
         "crore": "CR", "crores": "CR", "cr": "CR",
         "lakh": "L", "lakhs": "L"}


def _canon_amount(text: str) -> str:
    """'$71 million', '$71M' and '$71m' become one key. The currency token is
    kept verbatim (upper-cased) so €71M and $71M never collapse."""
    t = text.strip()
    m = re.match(
        rf"({_CURRENCY})\s?(\d[\d,]*(?:\.\d+)?)\s?({_AMOUNT_SUFFIX})?",
        t, re.I)
    if not m:
        return re.sub(r"\s+", "", t.upper())
    cur = m.group(1).strip().upper()
    number = m.group(2).replace(",", "")
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    suffix = _MULT.get((m.group(3) or "").lower(), "")
    return f"{cur}{number}{suffix}"


def parse_funding(item: dict) -> Funding | None:
    """A closed funding round the HEADLINE states outright, or None.

    Shared by extract() (which adds the completeness guards) and by the
    story-clustering pass in run_collect, which needs only (employer, amount).
    """
    headline = (item.get("headline") or "").strip()
    raw_text = (item.get("raw_text") or "").strip()
    if not headline or not raw_text:
        return None

    # Cheap knock-outs first, on the headline: the subject of the story.
    if _UNCERTAIN.search(headline) or _DEAL_WORDS.search(headline):
        return None
    if _NOT_A_ROUND.search(headline):
        return None
    if ";" in headline:                      # two stories in one line
        return None
    if prefilter.workforce_reduction_term(raw_text):
        return None

    m = _FUNDING_SHAPE.match(headline)
    if not m:
        return None

    # The word after the amount decides what the amount IS. Anything not on
    # the whitelist ("price target", "stake", "contract", words we have not
    # thought of) declines.
    tail = headline[m.end():].strip()
    if tail:
        first = re.sub(r"^[^\w]+", "", tail).split()
        if not first or first[0].lower().strip(".,") not in _TAIL_OK:
            return None

    name = _valid_name(m.group("name"))
    if not name:
        return None
    if _title_cased(headline) and len(name.split()) > 1:
        return None

    city = country = None
    place = m.group("base") or m.group("poss")
    if place:
        hit = vocab.normalize_city(place)
        if hit:
            city, _region, country = hit
        else:
            country = vocab.normalize_country(place)
            if not country:
                # The text stated a prefix this record cannot carry — a place
                # not in the vocabulary, or a possessive that is not a place
                # at all ("Musk's xAI"). Decline; the paid path reads it.
                return None

    amount = m.group("amount").strip()
    usd = vocab.parse_funding_usd(amount)
    if usd is not None and usd < _MIN_PLAUSIBLE_USD:
        return None

    stage_text = _stated_stage(headline, raw_text)
    stage = vocab.normalize_funding_stage(stage_text) if stage_text else None
    if stage is None:
        stage_text = ""

    return Funding(
        company=name,
        company_key=vocab.company_key(name),
        amount_text=amount[:32],
        amount_usd=usd,
        amount_canon=_canon_amount(amount),
        stage=stage,
        stage_text=stage_text or "",
        city=city,
        country=country,
    )


def cluster_key(item: dict) -> tuple[str, str] | None:
    """(company_key, canonical amount) when the headline states both, else
    None. Two outlets rewriting the same round produce the same key; that is
    the whole trick of lever 2."""
    parsed = parse_funding(item)
    if parsed is None or not parsed.company_key:
        return None
    return (parsed.company_key, parsed.amount_canon)


# The loose tier exists for the headlines whose employer hides behind a
# descriptor phrase — "Building materials quick commerce startup Fixxly raises
# $5.5 Mn" — which the strict name rules rightly refuse to store. For
# CLUSTERING they still carry a usable fingerprint: the one token touching the
# verb plus the amount. Four outlets wrote exactly that Fixxly headline in one
# real sweep, and the strict key could not see it.
_LOOSE_SHAPE = re.compile(
    rf"(?P<tok>[\w&.'’-]+)\s+{_RAISE_VERB}\s+"
    rf"(?:(?:a|an|another|fresh|new|its|over|nearly|about|around|"
    rf"approximately|more than|up to|some|roughly|usd)\s+){{0,3}}"
    rf"(?P<amount>{_AMOUNT_BODY})(?![\w.])",
    re.I,
)


def loose_cluster_key(item: dict) -> tuple[str, str] | None:
    """A weaker fingerprint, for clustering ONLY — never for storing anything.

    Because the employer identification is weaker, run_collect treats loose
    clusters differently: the set-aside copies are NOT marked seen, so if two
    different stories ever did collide on (final token, amount), the loss is
    one deferred read rather than a story dropped for good.
    """
    headline = (item.get("headline") or "").strip()
    if not headline or _UNCERTAIN.search(headline) or ";" in headline:
        return None
    m = _LOOSE_SHAPE.search(headline)
    if not m:
        return None
    tok = m.group("tok")
    low = tok.lower().strip(".")
    if (not _TOKEN_OK.match(tok) or low in _CONNECTORS or low in _LEAD_JUNK
            or low in _NATIONALITIES or low in _ANONYMITY_MARKERS
            or low in _GENERIC_ORG_NOUNS or low in _GENERIC_QUALIFIERS):
        return None
    key = vocab.company_key(tok)
    if not key:
        return None
    return (key, _canon_amount(m.group("amount")))


# --- Hiring -----------------------------------------------------------------
#
# "<Name> to hire 500 engineers in Dublin". The count is the FIRST number, the
# qualifiers ("up to", "about") are allowed because the sibling's first-number
# rule already decides what a range stores, and the place is captured only
# when stated AND normalisable — stated-but-unknown declines (rule 2).

_HIRE_SHAPE = re.compile(
    r"^(?P<name>\S[^,;:]{0,60}?)\s+(?:is\s+|are\s+)?(?:to\s+|will\s+)?"
    r"(?:hire[sd]?|hiring|add(?:s|ing)?|creat(?:es?|ing))\s+"
    r"(?:(?:up to|about|around|nearly|over|more than|some|roughly|"
    r"an additional|another|as many as)\s+){0,2}"
    r"(?P<count>\d[\d,]*)\s+(?P<countqual>new\s+|more\s+)?"
    r"(?P<noun>jobs?|roles?|positions?|staff|employees?|workers?|engineers?|people)\b"
    r"(?P<rest>.*)$",
    re.I,
)

_HIRE_PLACE = re.compile(r"\bin\s+(?P<place>[A-Z][\w .'’-]{1,30}?)\s*$")

_NOUN_FUNCTION = {"engineers": "engineering", "engineer": "engineering"}


def _parse_hiring(item: dict) -> dict | None:
    headline = (item.get("headline") or "").strip()
    raw_text = (item.get("raw_text") or "").strip()
    if not headline or not raw_text:
        return None
    if _UNCERTAIN.search(headline) or _DEAL_WORDS.search(headline):
        return None
    if prefilter.workforce_reduction_term(raw_text):
        return None
    # A stated amount of money means the story is bigger than a hire count.
    if _AMOUNT.search(raw_text):
        return None

    m = _HIRE_SHAPE.match(headline)
    if not m:
        return None
    name = _valid_name(m.group("name"))
    if not name:
        return None
    if _title_cased(headline) and len(name.split()) > 1:
        return None

    count = int(m.group("count").replace(",", ""))
    if count <= 0:
        return None

    city = country = None
    rest = (m.group("rest") or "").strip()
    if rest:
        pm = _HIRE_PLACE.search(rest)
        # Anything trailing that is not a clean, normalisable "in <place>" is
        # detail this parser cannot read. Decline rather than drop it.
        if not pm:
            return None
        place = pm.group("place").strip()
        hit = vocab.normalize_city(place)
        if hit:
            city, _region, country = hit
        else:
            country = vocab.normalize_country(place)
            if not country:
                return None
        if rest[: pm.start()].strip():
            return None

    noun = m.group("noun").lower()
    functions = [_NOUN_FUNCTION[noun]] if noun in _NOUN_FUNCTION else []

    where = city or (vocab.COUNTRY_NAMES.get(country, "") if country else "")
    # The summary echoes the stated count phrase VERBATIM ("250 more jobs",
    # not "250 jobs"): validate's number matcher reads "250 more" as the token
    # "250m", so restating the count without its qualifier is rejected as an
    # invented figure. Echoing the text is also simply more honest.
    count_phrase = f"{m.group('count')} {(m.group('countqual') or '').strip()}".strip()
    summary = f"{name} is adding {count_phrase} {noun}" + (
        f" in {where}." if where else ".")
    readthrough = (
        f"Adds {m.group('count')} roles to the {where} market; watch "
        f"{name}'s careers page for openings." if where else
        f"{name} is adding {m.group('count')} roles; the report does not "
        f"name the location.")

    return {
        "is_talent_signal": True,
        "company": name,
        "pillar": "company_development",
        "signal_direction": "hiring",
        "city": city or "",
        "country": vocab.COUNTRY_NAMES.get(country, "") if country else "",
        "headquarters_city": "",
        "headquarters_country": "",
        "confidence": "reported",
        "functions": functions,
        "industry": "",
        "state": "",
        "headcount": count,
        "headcount_scope": "new_roles",
        "funding_amount": "",
        "funding_stage": "",
        "effective_date": "",
        "ticker": "",
        "work_mode": "",
        "deal_type": "",
        "site_event": "",
        "employer_type": "",
        "headline": headline,
        "summary": summary,
        "talent_readthrough": readthrough,
        "predicted_outcome": "",
        "check_after_date": "",
    }


# --- The public entry point --------------------------------------------------

def extract(item: dict, *, count: bool = True) -> dict | None:
    """A complete classified-shaped dict, or None to take the paid path.

    The dict is consumed by validate.build_signal exactly as a model response
    would be, so every downstream guard (figures verbatim in raw_text, named
    employer, vocabulary normalisation, confidence ceiling, scope boundary)
    still applies to it. This module earns no exemptions.

    `count=False` makes the call a probe (cluster representative selection
    asks "could this close?" without it being an attempt), so STATS stays a
    per-candidate tally rather than a per-call one.
    """
    def _tally(key: str) -> None:
        if count:
            STATS[key] += 1

    _tally("attempted")
    raw_text = (item.get("raw_text") or "").strip()
    headline = (item.get("headline") or "").strip()
    if not raw_text or not headline:
        _tally("declined")
        return None

    # Rule 3: text that states things this parser cannot carry declines.
    if prefilter.site_event_term(raw_text):
        _tally("declined")
        return None

    parsed = parse_funding(item)
    if parsed is not None and not _HIRE_WORDS.search(raw_text):
        stage_label = vocab.FUNDING_STAGE_LABELS.get(parsed.stage or "", "")
        summary = f"{parsed.company} has raised {parsed.amount_text}" + (
            f" in {parsed.stage_text} funding." if parsed.stage_text else ".")
        where = parsed.city or (
            vocab.COUNTRY_NAMES.get(parsed.country, "") if parsed.country else "")
        readthrough = (
            f"{parsed.company} has {parsed.amount_text} of new capital"
            + (f" in {where}" if where else "")
            + ". The report does not disclose hiring plans; watch its careers"
              " page for new roles.")
        _tally("closed")
        return {
            "is_talent_signal": True,
            "company": parsed.company,
            "pillar": "company_development",
            # Funding is NOT hiring until the source states roles — the same
            # rule the model prompt spells out, applied deterministically.
            "signal_direction": "neutral",
            "city": parsed.city or "",
            "country": (vocab.COUNTRY_NAMES.get(parsed.country, "")
                        if parsed.country else ""),
            "headquarters_city": "",
            "headquarters_country": "",
            "confidence": "reported",
            "functions": [],
            "industry": "",
            "state": "",
            "headcount": 0,
            "headcount_scope": "",
            "funding_amount": parsed.amount_text,
            "funding_stage": stage_label or parsed.stage_text,
            "effective_date": "",
            "ticker": "",
            "work_mode": "",
            "deal_type": "",
            "site_event": "",
            "employer_type": "",
            "headline": headline,
            "summary": summary,
            "talent_readthrough": readthrough,
            "predicted_outcome": "",
            "check_after_date": "",
        }

    hiring = _parse_hiring(item)
    if hiring is not None:
        _tally("closed")
        return hiring

    _tally("declined")
    return None
