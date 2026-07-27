"""Deterministic gate, run before the LLM ever sees a candidate.

Free filtering beats paid filtering (spec 4 rule 3). The first live run proved
why: a query containing the bare word "expansion" returned MLB expansion, World
of Warcraft expansion, Medicaid expansion, cattle herd expansion and war
escalation. Every one of those would have cost a classification call to reject.

The rule is simple: a talent signal is about **people at an employer**. If the
text contains no employment noun at all, no model needs to look at it.
"""

from __future__ import annotations

import re

# At least one of these must appear. They are the words that make a story about
# employment rather than about stadiums, herds or health policy.
_EMPLOYMENT_TERMS = (
    r"jobs?", r"hiring", r"hire[sd]?", r"roles?", r"headcount", r"staff",
    r"employees?", r"workers?", r"workforce", r"recruit\w*", r"vacanc\w+",
    r"appoint\w*", r"names? (?:its |a |new )?(?:chief|ceo|cfo|cto|president)",
    r"steps? down", r"resign\w*", r"succeeds?", r"chief \w+ officer",
    # Structural leadership news names no individual and no headcount, but is
    # squarely the leadership pillar. Kept narrow on purpose: bare "leadership"
    # matches an endless supply of AI-strategy think pieces.
    r"leadership (?:structure|team|reshuffle|shake-?up|transition)",
    r"management team", r"executive team", r"board appoint\w*",
    r"salar\w+", r"pay(?:rise|\srise)?", r"wages?", r"bonus\w*", r"compensation",
    r"remote work", r"hybrid work\w*", r"return to office", r"four-day week",
)
_EMPLOYMENT = re.compile(r"\b(?:" + "|".join(_EMPLOYMENT_TERMS) + r")\b", re.I)

# Site-establishment terms. A company opening a capability centre IS a hiring
# event, even when the headline never says "jobs" — and this is precisely the
# phrasing the standalone euphemism queries exist to surface. The first version
# of this filter dropped every one of them, which would have made those queries
# dead on arrival exactly as the sibling's did.
#
# "GCC" is deliberately absent: it is also the Gulf Cooperation Council.
_SITE_TERMS = (
    r"capability cent(?:re|er)s?", r"cent(?:re|er)s? of excellence",
    r"delivery cent(?:re|er)s?", r"shared services", r"tech(?:nology)? cent(?:re|er)s?",
    r"engineering cent(?:re|er)s?", r"r&d cent(?:re|er)s?", r"innovation cent(?:re|er)s?",
    r"development cent(?:re|er)s?", r"opens? (?:a |its |new )?(?:office|hub|campus|site)",
    r"sets? up (?:a |its |new )?(?:office|hub|centre|center)",
    r"new (?:office|hub|campus|facility|plant|site)",
    # Bare "GCC" stays out (Gulf Cooperation Council), but a verb in front of
    # it is unambiguous: in Indian business press "opens a new GCC" is a Global
    # Capability Centre, which is exactly the category we want.
    r"(?:new|opens?|open|launch(?:es|ing)?|sets? up|establish(?:es|ing)?)\s+(?:a\s+|its\s+|the\s+)?(?:new\s+)?gcc\b",
)
_SITE = re.compile(r"\b(?:" + "|".join(_SITE_TERMS) + r")\b", re.I)

# Domains where "expansion", "hiring" and "roster" mean something else entirely.
# Cheap to check, and they were most of the noise in the first live run.
_OFF_TOPIC_TERMS = (
    r"nba", r"nfl", r"mlb", r"wnba", r"nhl", r"premier league", r"playoffs?",
    r"franchise", r"roster", r"draft pick", r"touchdown", r"season opener",
    r"medicaid", r"medicare", r"nuclear weapons?", r"ceasefire", r"airstrikes?",
    r"herd", r"cattle", r"livestock", r"acreage",
    r"world of warcraft", r"dlc", r"expansion pack", r"video game",
    # Government and civil-service exam notices. These are instructions to
    # applicants ("registration closes tomorrow", "admit card released"), not
    # intelligence about an employer's plans. A live run stored UPPSC PCS and
    # Indian Navy SSC notices before this existed.
    r"recruitment 20\d\d", r"admit card", r"answer key", r"exam date",
    r"registration closes", r"apply online", r"notification (?:out|released)",
    r"\d+\s+posts?\b", r"uppsc", r"upsc", r"ssc\s+(?:cgl|chsl|gd|mts|officer)",
    r"bharti", r"sarkari", r"vacanc(?:y|ies) notification",
    r"police constable", r"assistant teacher recruitment",
)
_OFF_TOPIC = re.compile(r"\b(?:" + "|".join(_OFF_TOPIC_TERMS) + r")\b", re.I)


# --- Geography gate --------------------------------------------------------
#
# We claim eight markets. A signal in a place we do not cover gets rejected by
# validate.py anyway ("no geography"), so classifying it first is pure waste —
# the first successful live run paid for exactly that on Uzbekistan, Somalia,
# Ohio and Anglesey. Checking here costs nothing.
#
# Grows automatically as source_registry.MARKETS grows: nothing to hand-edit.

def _geography_terms() -> tuple[re.Pattern, re.Pattern]:
    from . import vocab

    long_terms, short_codes = set(), set()

    def add(term: str) -> None:
        (long_terms if len(term) >= 4 else short_codes).add(re.escape(term))

    for alias, (city, _region, _iso2) in vocab._CITY_ALIASES.items():
        add(alias)
        add(city)
    for name in vocab.COUNTRY_NAMES.values():
        add(name)
    for alias in vocab._COUNTRY_ALIASES:
        add(alias)

    # Adjectival forms carry the geography just as well: "across German sites".
    long_terms.update({
        "irish", "german", "french", "dutch", "belgian", "british", "english",
        "scottish", "welsh", "spanish", "portuguese", "italian", "swedish",
        "danish", "norwegian", "finnish", "swiss", "polish", "czech",
        "romanian", "indian", "japanese", "australian", "american",
    })

    return (
        re.compile(r"\b(?:" + "|".join(sorted(long_terms)) + r")\b", re.I),
        # Short codes match case-sensitively on purpose: a lowercase "us" is
        # the pronoun, and "\bus\b" would let "join us" through as the USA.
        re.compile(r"\b(?:" + "|".join(sorted(c.upper() for c in short_codes)) + r")\b"),
    )


_GEO_LONG, _GEO_SHORT = _geography_terms()


def has_covered_geography(text: str) -> bool:
    return bool(_GEO_LONG.search(text) or _GEO_SHORT.search(text))


def passes(text: str) -> tuple[bool, str]:
    """Return (keep, reason). Reason is empty when kept."""
    if not text or not text.strip():
        return False, "empty text"

    # Word-boundary matching, not substring: the sibling's equivalent loop went
    # inert for a day because "RIF" matched inside "tariff".
    if _OFF_TOPIC.search(text):
        hit = _OFF_TOPIC.search(text).group(0)
        return False, f"off-topic domain ({hit})"

    if not (_EMPLOYMENT.search(text) or _SITE.search(text)):
        return False, "no employment or site-opening term"

    # NOTE: geography is deliberately NOT a gate here, though the helper above
    # exists and is tested. Gating on it looked like an easy saving — several
    # items were classified and then rejected for uncovered geography — but it
    # drops "Revolut CEO steps down" (no place in the headline at all), "Intel
    # opens new facility in Leixlip" and "BMS opens Mumbai capability centre".
    # A headline often carries no place while the body does, and the model can
    # infer it from the employer. Recall is the harder problem; validate.py
    # rejects on geography later with full context, for a fraction of a cent.
    return True, ""
