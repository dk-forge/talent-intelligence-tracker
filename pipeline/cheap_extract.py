"""Deterministic teaser extraction — lever 1 of the cost work.

A large share of funding, hiring and leadership headlines state every field a
record needs: "Enigma Raises $71M in Seed Funding" IS the record, and so is
"Acme Appoints Jane Doe as Chief Executive Officer". Paying a model $0.0013 to
restate either is the single biggest avoidable cost in the pipeline, because
those headlines are also the most numerous kinds of gate-survivor.

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

from . import leadership_intl, prefilter, vocab
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


# --- The stated city --------------------------------------------------------
#
# Until this section existed, the only city the cheap path could see was a
# `-based`/possessive prefix at the very START of a headline, so 93.8% of
# stored rows (14,742 of 15,711, measured 2026-07-29) carried no city and only
# 25 cities existed in the whole database. The city was usually right there in
# the sentence.
#
# THE RULE THAT DOES NOT BEND: a place is captured only where the SOURCE
# STATES it. Never from the outlet's own base, never from the country, never
# from what anyone knows about where a company is really headquartered.
# `national_press.dateline()` deliberately folds the publisher's seat into
# raw_text as "(Outlet: X, based in Sofia, Bulgaria — a hint, not a stated
# fact.)", which is the exact string shape this scanner had to be taught to
# refuse: reading it would file every Bulgarian-carried story in Sofia and
# turn a sourced claim into an invented one. `_HINT_SPANS` is that refusal,
# and a test pins it.
#
# Six phrasings, chosen because each one names a place OUTRIGHT:
#     "<City>-based"        "based in <City>"
#     "<City>-headquartered" "headquartered in <City>"
#     "opens a <City> office"  "its <City> office"
#
# THE FOUR TIGHTENINGS, the city reading of the four the funding sweep forced
# (docs/TECHLOG.md, "the cost levers", lever 1):
#
#   a name that IS a place    ->  a PLACE INSIDE A NAME. "Berlin Packaging",
#                                 "Jakarta Post", "Austin Russell": the
#                                 gazetteer entry must be the WHOLE compound
#                                 touching the frame, never a fragment of a
#                                 longer proper name, in either direction.
#   hyphen-embedded           ->  "-based" IS NOT A PLACE FRAME. AI-based,
#   descriptors                   cloud-based, faith-based, US-based and
#                                 Israeli-based all clear the shape; only a
#                                 gazetteer city yields a city, and a country
#                                 or nationality yields nothing here (the
#                                 country path is unchanged).
#   Title Case blindness      ->  A CONTRADICTED QUALIFIER. Capitalisation
#                                 cannot tell Dublin from Dublin, Ohio, so the
#                                 source's own qualifier decides: a trailing
#                                 place that resolves to a DIFFERENT country
#                                 than the gazetteer's declines the item's
#                                 city outright, and one that agrees keeps it.
#                                 Dublin/Ohio, Melbourne/Florida,
#                                 Perth/Scotland and Athens/Georgia are all
#                                 real, and all would have been wrong.
#   stage-from-previous-round ->  A CITY BELONGING TO SOMEONE ELSE. The
#                                 stolen-detail lesson: "raises $10M led by
#                                 London-based Index" states London about the
#                                 INVESTOR, and "Berlin-based rival" about a
#                                 competitor. An attributed place is skipped,
#                                 not stored — and two DIFFERENT cities stated
#                                 anywhere in the text decline, because
#                                 choosing one of them is exactly the guess
#                                 this module exists not to make.

# A decline: the text stated a place this parser must not resolve. Distinct
# from None ("no place stated"), because a contradiction has to veto a good
# city found elsewhere in the same text.
_DECLINE = object()

# Longest alias first, so a fixed anchor yields the LONGEST match: "london,
# ontario" must win over "london", and "cambridge, massachusetts" over nothing
# at all. Built from the gazetteer itself, so a city added there is readable
# here the same day.
_ALIAS_KEYS = sorted(vocab._CITY_ALIASES, key=len, reverse=True)
_ALIAS_AT = re.compile(
    r"(?:" + "|".join(re.escape(k) for k in _ALIAS_KEYS) + r")(?![\w'’-])",
    re.I)

# Words that may sit in front of a city without being part of its name. Tried
# ONLY after the full compound has failed, which is why "New York-based" is
# never read as "York": the whole span is always attempted first.
_CITY_LEAD_STRIP = (
    frozenset({"the", "a", "an", "its", "their", "his", "her", "this", "that",
               "new", "leading", "local", "global", "fellow", "rival"})
    | _NATIONALITIES | _SECTOR_DESCRIPTORS
)

# The place belongs to somebody else in the sentence. Checked in the 40
# characters before the match, which is long enough for "with participation
# from" and short enough that an ordinary "from" two clauses back cannot veto
# a real city.
_ATTRIBUTED = re.compile(
    r"(?:led by|co-led by|backed by|joined by|investors?|investment from|"
    r"participation (?:from|of|by)|alongside|including|from existing|"
    r"acquired by|sold to|bought by|merges? with|partners? with|"
    r"partnership with|client|customer|supplier|rival|competitor|"
    r"compares? with|according to|reports?|owner|parent|subsidiary of|"
    r"unit of|arm of|advis\w+|counsel|law firm|underwrit\w+|"
    r"outlet)\b[^.;:]{0,40}$",
    re.I,
)

# The same lesson from the other side: "a Lagos-based RIVAL", "Berlin-based
# INVESTOR Foo". The noun after the frame says whose place it is. Deliberately
# short — "firm", "startup" and "company" are how a headline names its own
# SUBJECT, so they are not here.
_ATTRIBUTED_AFTER = re.compile(
    r"^\s*(?:rival|competitor|peer|investor|investors|acquirer|buyer|bidder|"
    r"suitor|customer|client|"
    r"venture capital|vc\b|fund\b|underwriter|law firm|adviser|advisor)",
    re.I,
)

# The dateline hint, verbatim in shape from national_press.dateline(). Anything
# inside it is the PUBLISHER's seat, which places the publisher and nothing
# else. Matched non-greedily and bounded to one parenthetical so a later, real
# "based in" cannot be swallowed by it.
_HINT_SPANS = re.compile(r"\(Outlet:[^)]*\)|^Published by:[^\n]*", re.I | re.M)

# The frames. Each yields a position; the alias match is what decides.
_SUFFIX_FRAME = re.compile(r"-(?:based|headquartered)(?![\w])", re.I)
_IN_FRAME = re.compile(r"\b(?:based|headquartered|head ?quartered)\s+in\s+", re.I)
_OPENS_OFFICE_FRAME = re.compile(
    r"\bopen(?:s|ed|ing)?\s+(?:a|an|its|their|the)\s+"
    r"(?:new\s+|first\s+|second\s+|third\s+)?", re.I)
_ITS_OFFICE_FRAME = re.compile(r"\b(?:its|their|the)\s+", re.I)
_OFFICE_NOUN = re.compile(r"\s+offices?(?![\w])", re.I)


def _resolve_alias(span: str):
    """The gazetteer entry for a WHOLE span, or None.

    The full span is tried first, then the span with one leading non-name word
    removed, and no further. Nothing is ever resolved from a fragment: that is
    what keeps "Berlin Packaging" out and "New York" in.
    """
    span = (span or "").strip().strip("‘’'\"")
    if not span:
        return None
    hit = vocab.normalize_city(span)
    if hit:
        return hit
    tokens = span.split()
    while len(tokens) > 1 and tokens[0].lower().strip(".,") in _CITY_LEAD_STRIP:
        tokens = tokens[1:]
        hit = vocab.normalize_city(" ".join(tokens))
        if hit:
            return hit
    return None


def _alias_ending_at(text: str, end: int):
    """The gazetteer city whose name ends exactly at `end`, or None.

    Ending EXACTLY there is the whole guard: in "Berlin Packaging-based" the
    compound touching the hyphen is "Packaging", so nothing matches and no
    city is invented from a name that merely contains one.
    """
    for key in _ALIAS_KEYS:                      # longest first
        start = end - len(key)
        if start < 0 or text[start:end].lower() != key:
            continue
        if start and (text[start - 1].isalnum() or text[start - 1] in "-'’"):
            continue
        if not text[start].isupper():            # a place is a proper noun
            continue
        if _ATTRIBUTED.search(text[max(0, start - 40):start]):
            return None                          # somebody else's city
        return vocab._CITY_ALIASES[key]
    # Nothing in the gazetteer ends here. It may still be a stated place we do
    # not curate ("Cambridge-based"), and it may equally be "AI-based" — this
    # parser cannot tell them apart, so it says nothing rather than declining
    # a record the paid path could not have placed either.
    return None


# A second place joined on: two cities, one record, and no way to choose which
# the roles are in.
_CONJOINED = re.compile(r"\s*(?:,\s*)?(?:and|&|or|/|as well as|plus)\s+[A-Z]")
# ", Ohio" / ", Ireland" / ", New South Wales" — a comma-introduced qualifier is
# how a newsroom disambiguates a city, so a comma is the shape that must be
# read. Bounded to three words: "Dublin, Ohio, said the company" stops at Ohio.
_COMMA_QUALIFIER = re.compile(
    r"\s*,\s*(?P<q>[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*){0,2})")
_BARE_QUALIFIER = re.compile(r"\s+(?P<q>[A-Z][\w.'’-]*)")


def _alias_starting_at(text: str, start: int, *, strict_tail: bool = False):
    """The gazetteer city beginning at `start`, `_DECLINE`, or None.

    A match is only kept when what FOLLOWS it agrees. "Dublin, Ohio" is not
    Dublin, "Melbourne, Florida" is not Melbourne, and "Berlin and Munich" is
    neither — each of those declines, because a place the source disambiguated
    AWAY from ours is worse than no place at all.
    """
    m = _ALIAS_AT.match(text, start)
    if not m:
        return None
    if not text[start].isupper():
        return None
    if _ATTRIBUTED.search(text[max(0, start - 40):start]):
        return None
    city, region, iso2 = vocab._CITY_ALIASES[m.group(0).lower()]

    rest = text[m.end():]
    if _CONJOINED.match(rest):
        return _DECLINE

    qual = _COMMA_QUALIFIER.match(rest)
    if qual:
        # A comma-introduced qualifier is a claim about WHICH city this is, so
        # it must resolve AND agree. One that resolves to nothing is a
        # qualifier we cannot read, which is still not agreement.
        words = qual.group("q").split()
        code = next((c for c in (vocab.place_qualifier_country(" ".join(words[:n]))
                                 for n in range(len(words), 0, -1)) if c), None)
        if code != iso2:
            return _DECLINE
        if _CONJOINED.match(rest[qual.end():]):
            return _DECLINE
        return (city, region, iso2)

    # No comma, so the next capitalised word is evidence of nothing much: in
    # "OPENS A LAGOS OFFICE" capitalisation is decoration, which is the city
    # reading of the Title-Case lesson. A bare follower may therefore VETO but
    # is never required to confirm — "based in Athens Georgia" resolves to the
    # United States, contradicts Greece, and declines.
    bare = _BARE_QUALIFIER.match(rest)
    if bare:
        code = vocab.place_qualifier_country(bare.group("q"))
        if code is not None and code != iso2:
            return _DECLINE
        if code is None and strict_tail:
            # In "based in <X>" the place ENDS the phrase, so a capitalised
            # word carrying on from it means the phrase was never a place:
            # "based in Boston Consulting Group's building" is a landlord, not
            # a city, and this is tightening 1 read from the other direction.
            # The office frames pass strict_tail=False because they have their
            # own right-hand boundary — the word "office".
            return None
    return (city, region, iso2)


def _scan_for_cities(text: str):
    """Every city the text STATES, plus whether anything declined.

    Returns (set of (city, region, iso2), declined: bool).
    """
    if not text:
        return set(), False
    # Blank the publisher hint before anything reads it, keeping the length so
    # every offset below still lines up with the original string.
    text = _HINT_SPANS.sub(lambda m: " " * len(m.group(0)), text)

    found, declined = set(), False

    def take(result):
        nonlocal declined
        if result is _DECLINE:
            declined = True
        elif result:
            found.add(result)

    for m in _SUFFIX_FRAME.finditer(text):
        if _ATTRIBUTED_AFTER.match(text[m.end():m.end() + 30]):
            continue
        take(_alias_ending_at(text, m.start()))
    for m in _IN_FRAME.finditer(text):
        take(_alias_starting_at(text, m.end(), strict_tail=True))
    for frame in (_OPENS_OFFICE_FRAME, _ITS_OFFICE_FRAME):
        for m in frame.finditer(text):
            hit = _ALIAS_AT.match(text, m.end())
            # "office" must follow the place immediately: "its Berlin office"
            # is a site, "its Berlin Packaging division" is a business unit,
            # and "its Chief Executive Officer" is neither.
            if not hit or not _OFFICE_NOUN.match(text, hit.end()):
                continue
            take(_alias_starting_at(text, m.end()))
    return found, declined


def stated_city(*texts: str):
    """(city, region, iso2) the SOURCE states, or None.

    None is the answer to every ambiguity: nothing stated, a place we do not
    curate, a place the source qualified away from ours, a place belonging to
    an investor or a rival, or two different places. Precision over recall —
    the module's first rule, applied to geography.
    """
    found: set = set()
    for text in texts:
        hits, declined = _scan_for_cities(text or "")
        if declined:
            return None
        found |= hits
    return found.pop() if len(found) == 1 else None

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

    # The headline prefix is one phrasing out of six. "Sigvi raises €1.2M...
    # The Vilnius-based company" states Vilnius just as plainly, and every
    # such round was stored unplaced until this line existed. A city only
    # overrides a NULL city, and only when it agrees with a country the prefix
    # already sourced.
    if city is None:
        hit = stated_city(headline, raw_text)
        if hit and (country is None or hit[2] == country):
            city, _region, country = hit

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

    # "Acme to hire 500 engineers" says nothing about where in the headline,
    # and then says "the Bengaluru-based firm" in the teaser. Same rule as
    # funding: fills a NULL city only, never contradicts a sourced country.
    if city is None:
        hit = stated_city(headline, raw_text)
        if hit and (country is None or hit[2] == country):
            city, _region, country = hit

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


# --- Leadership ---------------------------------------------------------------
#
# "<Employer> Appoints <Person> as <C-title>" is the leadership pillar's
# formulaic headline, and the wire services write it by the dozen. The same
# design as funding, with the funding extractor's four hard-won tightenings
# translated into their leadership equivalents:
#
#   country-name employers      -> _valid_name already declines a span that IS
#                                  a country or a city ("India Names New RBI
#                                  Chief" is a government story);
#   hyphen-embedded descriptors -> the same _valid_name hyphen-part check, and
#                                  a PERSON span is poisoned by any role word
#                                  ("Former Google Executive Jane Doe" is a
#                                  description wrapped around a name — where
#                                  the description ends is a model's job);
#   title-case blindness        -> in a title-cased headline only a
#                                  single-token employer and an exactly
#                                  two-token person are trusted, because Title
#                                  Case erases the boundary between descriptor
#                                  and name on both sides of the verb;
#   stage-from-previous-round   -> the leadership version of a stolen detail
#                                  is a stated START DATE or an interim
#                                  arrangement: "effective September 1" and
#                                  "as interim CEO" are facts this parser
#                                  cannot carry, so their presence declines
#                                  the whole item rather than shipping a
#                                  record that silently drops them.
#
# One person, one role, stated outright, nothing else in the headline. Two
# people ("...as John Smith Steps Down"), two roles ("President and CEO"), a
# division ("CEO of Its Gaming Division") or any trailing clause all decline:
# the rest-of-headline must be empty, with "of the board" the one allowed
# tail because a Chairman of the Board is exactly a Chair.

# Finite verb forms only, on purpose: "Acme to appoint Jane Doe" is a plan,
# not an appointment, and leaving the bare form out means it can never match.
_APPOINT_PAST = {
    "appoints": "appointed", "appointed": "appointed",
    "names": "named", "named": "named",
    "taps": "tapped", "tapped": "tapped",
    "promotes": "promoted", "promoted": "promoted",
    "elevates": "elevated", "elevated": "elevated",
    "hires": "hired", "hired": "hired",
}
_APPOINT_VERB = r"(?:%s)" % "|".join(sorted(_APPOINT_PAST))

# The closed title list: C-suite, president, chair. "Head of", "VP", "director"
# and every divisional variant stay out — those spans shade into descriptions
# ("director of the new Austin hub") and the model reads shading better than a
# regex declines it.
_C_TITLE = (
    r"chief\s+[a-z]+\s+(?:[a-z]+\s+)?officer|chief\s+executive"
    r"|ceo|cfo|coo|cto|cio|cmo|chro|cpo|cro|cdo|cso|cco|ciso"
    r"|executive\s+chair(?:man|woman|person)?|chair(?:man|woman|person)?"
    r"|president"
)

_LEADERSHIP_SHAPE = re.compile(
    rf"^(?:(?P<base>[A-Z][\w .'’-]{{1,30}})-based\s+|"
    rf"(?P<poss>[A-Z][\w .-]{{1,28}})[''’]s\s+)?"
    rf"(?P<name>\S[^,;:]{{0,60}}?)\s+(?P<verb>{_APPOINT_VERB})\s+"
    rf"(?P<person>[A-Z][^,;:()]{{0,40}}?)\s+"
    rf"(?:(?:as|to)\s+(?:its\s+|the\s+|their\s+)?(?:new\s+|next\s+|first\s+)?)?"
    rf"(?P<title>{_C_TITLE})"
    rf"(?P<rest>.*)$",
    re.I,
)

# The one tail a closed appointment may carry. Anything else after the title —
# "and President", ", effective September 1", "of Its Gaming Division", "As
# John Smith Retires" — is a second role, a second person or a detail this
# parser cannot carry, and declines.
_TITLE_TAIL_OK = re.compile(r"(?:\s+of\s+(?:the\s+|its\s+)?board)?[\s.!'\"’”]*$",
                            re.I)

# Words that mark a PERSON span as a description rather than a bare name.
# Honorifics are here too: rare in headlines, and "Dr Jane Doe" stored as the
# person's name would be wrong by one word — the model path keeps the nuance.
_PERSON_ROLE_WORDS = frozenset("""
    former ex exec executive veteran vet alum alumnus alumna insider founder
    cofounder co-founder chief officer president chairman chairwoman chair
    director head leader boss chairperson vp svp evp gm md ceo cfo coo cto
    cio cmo chro cpo cro cdo cso cco ciso interim acting incoming outgoing
    longtime industry board member managing partner new next
    dr mr ms mrs prof professor sir dame
""".split())

# A stated start, an interim arrangement, or an appointment not yet made:
# every one is a fact the record has no way to carry, so it declines (the
# leadership reading of funding's stage-from-previous-round lesson).
_LEADERSHIP_UNCARRIED = re.compile(
    r"\beffective\b|\bwith effect from\b|\binterim\b|\bacting\b|"
    r"\bwill (?:join|assume|take|start|begin|become|succeed)\b|"
    r"\b(?:joins?|starts?|begins?)\s+(?:on|in|from)\b|"
    r"\btakes? (?:over|charge|the helm)\b",
    re.I,
)


def _valid_person(span: str, title_cased: bool) -> str | None:
    """The span between the verb and the title, accepted as ONE person's bare
    name or None. Precision over recall, exactly as _valid_name above."""
    name = (span or "").strip().strip("‘’'\"")
    if not name:
        return None
    # A comma or connective means a list of people, an attribution or a
    # clause. Two people are two records, and which is which is a read.
    if re.search(r"[,:;&—–\|]|\band\b", name, re.I):
        return None
    tokens = name.split()
    if not 2 <= len(tokens) <= 3:
        return None
    # Title Case erases the descriptor/name boundary, so only the shortest
    # possible span — first name, surname — is trusted there.
    if title_cased and len(tokens) != 2:
        return None
    for token in tokens:
        low = token.lower().strip(".")
        if (low in _PERSON_ROLE_WORDS or low in _NATIONALITIES
                or low in _LEAD_JUNK or low in _ANONYMITY_MARKERS
                or low in _GENERIC_ORG_NOUNS or low in _GENERIC_QUALIFIERS
                or low in _SECTOR_DESCRIPTORS):
            return None
        if "-" in low and any(part in _PERSON_ROLE_WORDS or part in _NATIONALITIES
                              for part in low.split("-")):
            return None
        if any(ch.isdigit() for ch in token):
            return None
        # Every token capitalised, letters only after that. "van der Berg"
        # declines; the model path handles nobiliary particles.
        if not re.match(r"^[A-Z][A-Za-z'’.-]*$", token):
            return None
    return name


def _parse_leadership(item: dict) -> dict | None:
    headline = (item.get("headline") or "").strip()
    raw_text = (item.get("raw_text") or "").strip()
    if not headline or not raw_text:
        return None
    # The knock-outs funding uses, plus the leadership-specific ones. A stated
    # money amount means the story is bigger than one appointment (a comp
    # package, a raise the new hire will deploy); hiring language beside an
    # appointment is two signals; a deal means the org chart is in motion.
    if _UNCERTAIN.search(headline) or ";" in headline:
        return None
    if _DEAL_WORDS.search(raw_text) or _AMOUNT.search(raw_text):
        return None
    if _HIRE_WORDS.search(raw_text.replace(headline, "", 1)):
        # The headline's own verb may be "hires"; hiring language anywhere
        # ELSE in the text is the second signal that declines.
        return None
    if _LEADERSHIP_UNCARRIED.search(raw_text):
        return None
    # ANY mention of a cut declines, not just a cut-led story. The subject-
    # race in workforce_reduction_term exists to KEEP appointment-led stories
    # for the model, which reads the whole context; a $0 close gets no such
    # benefit of the doubt, because "new CEO weeks after layoffs" is a
    # turnaround story whose nuance this parser cannot carry.
    if prefilter._REDUCTION.search(raw_text) or prefilter._RIF.search(raw_text):
        return None

    m = _LEADERSHIP_SHAPE.match(headline)
    if not m:
        return None
    if not _TITLE_TAIL_OK.fullmatch(m.group("rest") or ""):
        return None

    title_cased = _title_cased(headline)
    name = _valid_name(m.group("name"))
    if not name:
        return None
    if title_cased and len(name.split()) > 1:
        return None
    person = _valid_person(m.group("person"), title_cased)
    if not person:
        return None

    # A second title anywhere else in the headline is a second role — "names
    # Jane Doe CEO, President" survives the tail check when punctuation is
    # unusual, so count matches rather than trusting one anchor.
    title = m.group("title").strip()
    if len(re.findall(rf"\b(?:{_C_TITLE})\b", headline, re.I)) > 1:
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
                return None

    # "Revolut appoints Jane Doe CFO" places nowhere; "the London-based
    # neobank" in the next sentence places it. Same rule as funding.
    if city is None:
        hit = stated_city(headline, raw_text)
        if hit and (country is None or hit[2] == country):
            city, _region, country = hit

    verb = m.group("verb").lower()
    past = _APPOINT_PAST[verb]
    joiner = "to" if past in ("promoted", "elevated") else "as"
    summary = f"{name} has {past} {person} {joiner} {title}."
    where = city or (vocab.COUNTRY_NAMES.get(country, "") if country else "")
    readthrough = (
        f"{name} has a new {title}: {person}"
        + (f", in {where}" if where else "")
        + ". One appointment, not a headcount change; the report names no"
          " wider hiring plans."
    )

    return {
        "is_talent_signal": True,
        "company": name,
        "pillar": "leadership_change",
        # An appointment is one person in a planned succession, never
        # displacement and never hiring — the same rule the model prompt
        # spells out, applied deterministically.
        "signal_direction": "neutral",
        "city": city or "",
        "country": vocab.COUNTRY_NAMES.get(country, "") if country else "",
        "headquarters_city": "",
        "headquarters_country": "",
        "confidence": "reported",
        "functions": ["executive"],
        "industry": "",
        "state": "",
        "headcount": 0,
        "headcount_scope": "",
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

    leadership = _parse_leadership(item)
    if leadership is not None:
        _tally("closed")
        return leadership

    # Rule 4 is unchanged for everything above this line: those parsers are
    # English only and stay that way. `leadership_intl` carries its own
    # per-language grammars and its own name-span rules, and it declines every
    # language it was not written for, so the rule is now enforced by a module
    # boundary rather than by an early return. It is last because a candidate
    # any English parser can close is already closed.
    intl = leadership_intl.extract(item, count=False)
    if intl is not None:
        _tally("closed")
        return intl

    _tally("declined")
    return None
